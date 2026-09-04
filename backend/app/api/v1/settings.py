"""F.9 - Settings API routes.

Per ``openapi.yaml`` (lines 5877-5928):

* ``GET  /settings``          - ``getSettings``   (settings.view)
* ``PATCH /settings``         - ``updateSettings`` (settings.manage,
  requires ``Idempotency-Key`` + ``If-Match``)

Business rules
--------------
* ``GET /settings`` returns the full ``SettingsMap`` + ``ETag``
  response header (the quoted ``MAX(updated_at)`` across all rows
  — single logical resource for optimistic concurrency).
* ``PATCH /settings`` updates one or more keys; returns the full
  post-image ``SettingsMap``.
* ``costing_method`` is read-only in V1 (400 on PATCH).
* ``Idempotency-Key`` same key + same body → 200 with
  ``Idempotent-Replay: true``; same key + different body → 409
  ``idempotency_violation``.
* ``If-Match`` mismatch → 412 ``version_mismatch``. Missing
  ``If-Match`` → 400 ``missing_header``.

This file is intentionally tiny; all business logic lives in
``app.services.settings.SettingsService`` (mirrors
``app/inventory/service.py``). The route layer only wires
dependencies: auth, capability gates, header parsing, idempotent
replay envelope, ETag header injection.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api.deps import current_principal, get_uow, require_capability
from app.audit.service import AuditContext
from app.auth.principal import Principal
from app.db import UnitOfWork
from app.errors import IdempotencyViolation, MissingHeader
from app.services.idempotency import IdempotencyViolationConflict
from app.services.settings import SettingsService
from app.util import client_ip
from app.validation.headers import parse_idempotency_key, parse_if_match
from app.validation.settings_schemas import SettingsPatch

RequireSettingsView = require_capability("settings.view")
RequireSettingsManage = require_capability("settings.manage")

router = APIRouter(prefix="/settings", tags=["Settings"])


def _client_ip(request: Request) -> str | None:
    return client_ip(request.scope.get("headers", []))


def _audit_ctx(principal: Principal, request: Request) -> AuditContext:
    return AuditContext(
        user_id=principal.user_id,
        ip_address=_client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )


# ---------------------------------------------------------------------------
# GET /settings  —  listNotifications
# ---------------------------------------------------------------------------


@router.get(
    "",
    operation_id="getSettings",
    summary="Get system settings map.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireSettingsView)],
)
async def get_settings(
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
) -> JSONResponse:
    """Return the full ``SettingsMap`` and the canonical ETag header.

    The ETag is the quoted ISO-8601 ``MAX(updated_at)`` across all
    ``system_settings`` rows — ``system_settings`` is a single logical
    resource for optimistic concurrency.
    """
    svc = SettingsService(uow)
    rows, etag = await svc.get_settings()
    body = {"settings": jsonable_encoder(rows)}
    resp = JSONResponse(
        status_code=status.HTTP_200_OK,
        content=body,
    )
    resp.headers["ETag"] = etag
    return resp


# ---------------------------------------------------------------------------
# PATCH /settings  —  updateSettings
# ---------------------------------------------------------------------------


@router.patch(
    "",
    operation_id="updateSettings",
    summary="Update system settings.",
    description=(
        "``Idempotency-Key`` + ``If-Match`` required. "
        "``costing_method`` is read-only in V1 (400)."
    ),
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireSettingsManage)],
)
async def patch_settings(
    request: Request,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    """Apply a partial update to ``system_settings``.

    Reuses the project's canonical idempotency + ETag pattern
    (``app/services/idempotency.py`` + ``app/concurrency/etag.py``).
    """
    # ----- Idempotency-Key required + parsed (UUID, max 100) -----------
    idem = parse_idempotency_key(idempotency_key_header)
    key_str = str(idem.value)

    # ----- If-Match required (OpenAPI: IfMatchRequired) ----------------
    parsed_if_match = parse_if_match(if_match)
    if parsed_if_match is None:
        raise MissingHeader("If-Match header is required.")

    # ----- body validation ----------------------------------------------
    body = await request.json()
    if not isinstance(body, dict):
        from app.errors import ValidationFailed

        raise ValidationFailed(
            "PATCH /settings body must be a JSON object.",
        )
    # Pydantic catches unknown keys (extra=forbid) + type errors.
    patch_model = SettingsPatch(root=body)
    patch = patch_model.root

    svc = SettingsService(uow)
    try:
        rows, new_etag, is_replay = await svc.patch_settings(
            patch=patch,
            if_match=parsed_if_match.etag,
            idempotency_key=key_str,
            principal_user_id=principal.user_id,
            request_body=body,
            ctx=_audit_ctx(principal, request),
        )
    except IdempotencyViolationConflict as exc:
        raise IdempotencyViolation(
            "Idempotency-Key has been used with a different request body.",
        ) from exc

    # The service writes the UPDATE + audit + idempotency-cache in
    # the same UoW; commit once at the end of the request per the
    # project convention (inventory.py / financial_categories.py).
    # A failed ETag check or validation inside the service raises
    # BEFORE this commit so the rolled-back transaction leaves the
    # table untouched.
    await uow.commit()

    headers: dict[str, str] = {"ETag": new_etag}
    if is_replay:
        headers["Idempotent-Replay"] = "true"
    resp = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"settings": jsonable_encoder(rows)},
        headers=headers,
    )
    return resp


__all__ = ["router"]
