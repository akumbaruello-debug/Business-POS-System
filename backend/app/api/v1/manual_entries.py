"""Manual finance entry routes (E.8).

Matches openapi.yaml §15.12 exactly, mirroring the established
conventions of ``app/api/v1/production_runs.py`` and
``app/api/v1/purchases.py``:

* route handlers own the ``UnitOfWork`` transaction (``async with``)
* capability gates live on the dependency
* Pydantic request bodies are validated before the service is invoked
* idempotency / ETag helpers are reused from existing modules

Endpoints:

* GET    /manual-entries                — listManualEntries
* POST   /manual-entries                — createManualEntry (Idempotency-Key)
* GET    /manual-entries/{id}           — getManualEntry
* POST   /manual-entries/{id}/cancel    — cancelManualEntry (Idempotency-Key + If-Match)

Capabilities:
  list/get   → manual_entry.view
  create     → manual_entry.create_income OR manual_entry.create_expense (oneOf)
  cancel     → manual_entry.cancel
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.audit.service import AuditContext
from app.auth.principal import Principal
from app.authz.deps import current_principal, require_capability
from app.db import UnitOfWork
from app.errors import (
    IdempotencyViolation,
    InvalidHeader,
    MissingHeader,
    NotFound,
    PermissionDenied,
)
from app.logging import get_logger
from app.manual_entry.service import ManualEntryService
from app.services.idempotency import IdempotencyViolationConflict
from app.util import client_ip
from app.validation.headers import parse_idempotency_key, parse_if_match
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.schemas import (
    ManualEntryCancelRequest,
    ManualEntryRequest,
    ManualEntryResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["Manual Entries"])

RequireView = require_capability("manual_entry.view")
RequireCancel = require_capability("manual_entry.cancel")


def require_manual_entry_create() -> Any:
    """Dependency: caller has either create_income or create_expense."""

    async def checker(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Principal:
        if (
            "manual_entry.create_income" not in principal.capabilities
            and "manual_entry.create_expense" not in principal.capabilities
        ):
            raise PermissionDenied(
                "Missing required capability: manual_entry.create_income "
                "or manual_entry.create_expense",
                details={
                    "required": [
                        "manual_entry.create_income",
                        "manual_entry.create_expense",
                    ]
                },
            )
        return principal

    return checker


def _client_ip(request: Request) -> str | None:
    return client_ip(request.scope.get("headers", []))


def _audit_ctx(principal: Principal, request: Request) -> AuditContext:
    return AuditContext(
        user_id=principal.user_id,
        ip_address=_client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )


def _to_response(row: dict[str, Any]) -> ManualEntryResponse:
    return ManualEntryResponse(
        id=int(row["id"]),
        category_id=int(row["category_id"]),
        category_name=row.get("category_name"),
        entry_type=row["entry_type"],
        amount=float(row["amount"]),
        payment_method_id=int(row["payment_method_id"]),
        entry_date=row["entry_date"],
        notes=row.get("notes"),
        lifecycle_status=str(row["lifecycle_status"]),
        cancellation_date=row.get("cancellation_date"),
        cancellation_reason=row.get("cancellation_reason"),
        cancelled_by=row.get("cancelled_by"),
        created_at=row["created_at"],
        created_by=int(row["created_by"]),
    )


def _etag_for(result: dict[str, Any]) -> str:
    etag = result.get("etag")
    if etag:
        return str(etag)
    fallback = result.get("updated_at") or result.get("version") or ""
    return f'"{fallback}"'


def _idempotent_response(
    result: dict[str, Any] | list[dict[str, Any]] | str,
    status_code: int,
    is_replay: bool,
    etag: str | None = None,
) -> JSONResponse:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            pass
    headers: dict[str, str] = {}
    if etag:
        headers["ETag"] = etag
    if is_replay:
        headers["Idempotent-Replay"] = "true"
        status_code = status.HTTP_200_OK
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(result),
        headers=headers,
    )


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get(
    "/manual-entries",
    operation_id="listManualEntries",
    summary="List manual entries.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def list_manual_entries(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=500),
    sort: str = Query(default="id"),
    q: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    filter_entry_type: str | None = Query(default=None, alias="filter[entry_type]"),
    filter_category_id: int | None = Query(default=None, alias="filter[category_id]"),
    filter_lifecycle_status: (
        str | None
    ) = Query(default=None, alias="filter[lifecycle_status]"),
) -> MetaEnvelope[ManualEntryResponse]:
    async with UnitOfWork() as uow:
        svc = ManualEntryService(uow)
        result = await svc.list_manual_entries(
            page=page,
            per_page=per_page,
            sort=sort,
            entry_type=filter_entry_type,
            category_id=filter_category_id,
            lifecycle_status=filter_lifecycle_status,
            from_iso=from_iso,
            to_iso=to_iso,
            q=q,
        )
        data = [_to_response(r) for r in result["data"]]
        pag = make_pagination(page=page, per_page=per_page, total=result["total"])
        await uow.commit()
        return MetaEnvelope(data=data, pagination=pag)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "/manual-entries",
    operation_id="createManualEntry",
    summary="Create a manual income or expense entry.",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_manual_entry_create())],
)
async def create_manual_entry(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = ManualEntryRequest.model_validate(body_dict)
    body_for_idem = payload.model_dump(mode="json", exclude_none=True, by_alias=True)

    async with UnitOfWork() as uow:
        svc = ManualEntryService(uow)
        try:
            result, is_replay = await svc.create_manual_entry(
                principal_user_id=principal.user_id,
                category_id=payload.category_id,
                entry_type=payload.entry_type.value,
                amount=Decimal(str(payload.amount)),
                payment_method_id=payload.payment_method_id,
                entry_date=payload.entry_date,
                notes=payload.notes,
                idempotency_key=str(parsed_key.value),
                ctx=_audit_ctx(principal, request),
                request_body=body_for_idem,
            )
        except IdempotencyViolationConflict as exc:
            raise IdempotencyViolation(
                "Idempotency-Key reused with a different request body.",
                details=exc.details,
            ) from exc
        await uow.commit()
        return _idempotent_response(
            result,
            status.HTTP_201_CREATED,
            is_replay,
            etag=result.get("etag"),
        )


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


@router.get(
    "/manual-entries/{entry_id}",
    operation_id="getManualEntry",
    summary="Get a manual entry.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def get_manual_entry(
    request: Request,
    entry_id: int = Path(..., ge=1),
) -> JSONResponse:
    async with UnitOfWork() as uow:
        svc = ManualEntryService(uow)
        result = await svc.get_manual_entry(entry_id=entry_id)
        await uow.commit()
        if result is None:
            raise NotFound(f"Manual entry {entry_id} not found.")
        response = _to_response(result).model_dump(mode="json")
        if result.get("etag"):
            response = {**response, "etag": result["etag"]}
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=jsonable_encoder(response),
            headers={"ETag": _etag_for(result)},
        )


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


@router.post(
    "/manual-entries/{entry_id}/cancel",
    operation_id="cancelManualEntry",
    summary="Cancel a posted manual entry.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireCancel)],
)
async def cancel_manual_entry(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    entry_id: int = Path(..., ge=1),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    if if_match is None:
        raise MissingHeader("If-Match header is required for cancel.")
    parsed_etag = parse_if_match(if_match)
    if parsed_etag is None:
        raise InvalidHeader("If-Match header must be a quoted ETag or '*'.")

    body_dict = await request.json()
    payload = ManualEntryCancelRequest.model_validate(body_dict)
    body_for_idem = payload.model_dump(mode="json", exclude_none=True, by_alias=True)

    async with UnitOfWork() as uow:
        svc = ManualEntryService(uow)
        try:
            result, is_replay = await svc.cancel_manual_entry(
                entry_id=entry_id,
                principal_user_id=principal.user_id,
                reason=payload.reason,
                if_match=parsed_etag.etag,
                idempotency_key=str(parsed_key.value),
                ctx=_audit_ctx(principal, request),
                request_body=body_for_idem,
            )
        except IdempotencyViolationConflict as exc:
            raise IdempotencyViolation(
                "Idempotency-Key reused with a different request body.",
                details=exc.details,
            ) from exc
        await uow.commit()
        return _idempotent_response(
            result,
            status.HTTP_200_OK,
            is_replay,
            etag=result.get("etag"),
        )


__all__ = ["RequireCancel", "RequireView", "router"]
