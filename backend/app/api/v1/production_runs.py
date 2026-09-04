"""Production-runs API routes (M5 / Phase E.1 + E.3).

Endpoints match ``openapi.yaml`` §15 exactly. The route layer is thin:
it authenticates, parses the Pydantic request body, builds the
:class:`AuditContext`, and delegates to :class:`ProductionRunService`.

E.1 endpoints:

* ``GET    /production-runs``            — listProductionRuns
* ``POST   /production-runs``            — createProductionRun (Draft)
* ``GET    /production-runs/{id}``       — getProductionRun
* ``PATCH  /production-runs/{id}``       — updateProductionRunDraft

Sub-resource endpoints (``/inputs``, ``/cost-lines``) live in sibling
routers.

E.3 endpoint:

* ``POST   /production-runs/{id}/post``  — postProductionRun
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.audit.service import AuditContext
from app.auth.principal import Principal
from app.authz.deps import require_capability
from app.concurrency.etag import parse_if_match_optional
from app.db import UnitOfWork
from app.errors import IdempotencyViolation, MissingHeader
from app.logging import get_logger
from app.production.service import ProductionRunService
from app.services.idempotency import IdempotencyViolationConflict
from app.util import client_ip
from app.validation.headers import (
    parse_idempotency_key,
    parse_if_match,
)
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.production_schemas import (
    ProductionCancelRequest,
    ProductionRunCreateRequest,
    ProductionRunPatch,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/production-runs", tags=["production-runs"])


# ---------------------------------------------------------------------------
# Helpers (mirrored from app/api/v1/purchases.py)
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str | None:
    return client_ip(request.scope.get("headers", []))


def _audit_ctx(principal: Principal, request: Request) -> AuditContext:
    return AuditContext(
        user_id=principal.user_id,
        ip_address=_client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )


def _etag_for(result: dict[str, Any]) -> str:
    etag = result.get("etag")
    if etag:
        return str(etag)
    fallback = result.get("updated_at") or result.get("version") or ""
    return f'"{fallback}"'


def _json_response(
    result: dict[str, Any] | list[dict[str, Any]],
    status_code: int = status.HTTP_200_OK,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(result),
        headers={"ETag": _etag_for(result)} if isinstance(result, dict) else {},
    )


def _idempotent_response(
    result: dict[str, Any] | list[dict[str, Any]] | str,
    status_code: int,
    is_replay: bool,
) -> JSONResponse:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            pass
    headers: dict[str, str] = {}
    if isinstance(result, dict):
        etag = _etag_for(result)
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


def _dec(v: float | int | str | None) -> Decimal | None:
    if v is None:
        return None
    return Decimal(str(v))


# ---------------------------------------------------------------------------
# /production-runs — list / create
# ---------------------------------------------------------------------------


@router.get(
    "",
    operation_id="listProductionRuns",
    summary="List production runs.",
    status_code=status.HTTP_200_OK,
)
async def list_production_runs(
    request: Request,
    principal: Annotated[Principal, Depends(require_capability("production.view"))],
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=500),
    sort: str = Query(default="id"),
    q: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    lifecycle_status: str | None = Query(default=None, alias="filter[lifecycle_status]"),
    output_product_id: int | None = Query(
        default=None, alias="filter[output_product_id]"
    ),
) -> MetaEnvelope[dict[str, Any]]:
    async with UnitOfWork() as uow:
        svc = ProductionRunService(uow)
        rows, total = await svc.list_production_runs(
            page=page,
            per_page=per_page,
            q=q,
            sort=sort,
            lifecycle_status=lifecycle_status,
            output_product_id=output_product_id,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        pag = make_pagination(page=page, per_page=per_page, total=total)
        await uow.commit()
        return MetaEnvelope(data=rows, pagination=pag)


@router.post(
    "",
    operation_id="createProductionRun",
    summary="Create production run in Draft.",
    status_code=status.HTTP_201_CREATED,
)
async def create_production_run(
    request: Request,
    principal: Annotated[Principal, Depends(require_capability("production.create"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = ProductionRunCreateRequest.model_validate(body_dict)
    body_for_idem = payload.model_dump(mode="json", exclude_none=True, by_alias=True)

    async with UnitOfWork() as uow:
        svc = ProductionRunService(uow)
        try:
            result, is_replay = await svc.create_draft(
                principal_user_id=principal.user_id,
                run_date=payload.run_date,
                output_product_id=payload.output_product_id,
                output_quantity=Decimal(str(payload.output_quantity)),
                notes=payload.notes,
                inputs=[ln.model_dump(mode="json") for ln in payload.inputs],
                cost_lines=(
                    [cl.model_dump(mode="json") for cl in payload.cost_lines]
                    if payload.cost_lines is not None
                    else None
                ),
                ctx=_audit_ctx(principal, request),
                idempotency_key=str(parsed_key.value),
                request_body=body_for_idem,
            )
        except IdempotencyViolationConflict as exc:
            raise IdempotencyViolation(
                "Idempotency-Key reused with a different request body.",
                details=exc.details,
            ) from exc
        await uow.commit()
        return _idempotent_response(result, status.HTTP_201_CREATED, is_replay)


# ---------------------------------------------------------------------------
# /production-runs/{id} — get / patch
# ---------------------------------------------------------------------------


@router.get(
    "/{run_id}",
    operation_id="getProductionRun",
    summary="Get production run. Use `?include=inputs,cost_lines,output`.",
    status_code=status.HTTP_200_OK,
)
async def get_production_run(
    request: Request,
    run_id: int,
    principal: Annotated[Principal, Depends(require_capability("production.view"))],
    include: str | None = Query(default=None),
) -> JSONResponse:
    inc: set[str] = set()
    if include:
        inc = {x.strip() for x in include.split(",") if x.strip()}
    async with UnitOfWork() as uow:
        svc = ProductionRunService(uow)
        result = await svc.get_production_run(run_id, include=inc)
        await uow.commit()
        return _json_response(result)


@router.patch(
    "/{run_id}",
    operation_id="updateProductionRunDraft",
    summary="Update Draft production run.",
    status_code=status.HTTP_200_OK,
)
async def update_production_run(
    request: Request,
    run_id: int,
    principal: Annotated[
        Principal, Depends(require_capability("production.edit_own_draft"))
    ],
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader(
            "If-Match header is required for PATCH /production-runs/{id}."
        )
    body_dict = await request.json()
    payload = ProductionRunPatch.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = ProductionRunService(uow)
        result = await svc.update_draft(
            run_id=run_id,
            run_date=payload.run_date,
            output_product_id=payload.output_product_id,
            output_quantity=_dec(payload.output_quantity),
            notes=payload.notes,
            if_match=etag_parsed,
            ctx=_audit_ctx(principal, request),
        )
        await uow.commit()
        return _json_response(result)


# ---------------------------------------------------------------------------
# /production-runs/{id}/post — E.3 post lifecycle
# ---------------------------------------------------------------------------


@router.post(
    "/{run_id}/post",
    operation_id="postProductionRun",
    summary=(
        "Post production run: raw down, finished up, cash for paid overhead. "
        "finished_unit_cost = (raw + overhead) / output_qty. "
        "Idempotent. Requires If-Match."
    ),
    status_code=status.HTTP_200_OK,
)
async def post_production_run(
    request: Request,
    run_id: int,
    principal: Annotated[
        Principal, Depends(require_capability("production.post"))
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    """postProductionRun — E.3 thin route.

    Parses required headers (Idempotency-Key + If-Match), builds the
    audit context, then delegates the atomic transaction to
    ``ProductionRunService.post``. The body is optional; we read it
    defensively for the idempotency fingerprint so a retry of the
    same body re-uses the cached response.
    """
    parsed_idem = parse_idempotency_key(idempotency_key)
    parsed_etag = parse_if_match(if_match)
    if parsed_etag is None:
        raise MissingHeader(
            "If-Match header is required for POST /production-runs/{id}/post."
        )
    # The OpenAPI spec defines no requestBody for this endpoint, so the
    # canonical fingerprint uses an empty dict. If a client sends a
    # body, parse it so two distinct bodies with the same key produce
    # different fingerprints (and therefore 409 idempotency_violation).
    request_body: dict[str, Any] = {}
    try:
        raw = await request.body()
        if raw:
            try:
                request_body = json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                request_body = {}
    except Exception:  # pragma: no cover - defensive
        request_body = {}

    async with UnitOfWork() as uow:
        svc = ProductionRunService(uow)
        try:
            result, is_replay = await svc.post(
                run_id=run_id,
                principal_user_id=principal.user_id,
                if_match=parsed_etag.etag,
                idempotency_key=str(parsed_idem.value),
                ctx=_audit_ctx(principal, request),
                request_body=request_body,
            )
        except IdempotencyViolationConflict as exc:
            raise IdempotencyViolation(
                "Idempotency-Key reused with a different request body.",
                details=exc.details,
            ) from exc
        await uow.commit()
        return _idempotent_response(result, status.HTTP_200_OK, is_replay)


@router.post(
    "/{run_id}/cancel",
    operation_id="cancelProductionRun",
    summary="Cancel a posted production run.",
    description=(
        "Cancel a posted production run. Inserts reversal stock_movements "
        "for the original input/output movements. Overhead cash_movements "
        "are NOT reversed (cash immutability). Idempotent. Requires If-Match."
    ),
    status_code=status.HTTP_200_OK,
)
async def cancel_production_run(
    request: Request,
    run_id: int,
    principal: Annotated[
        Principal, Depends(require_capability("production.cancel"))
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    """cancelProductionRun — E.4 thin route.

    Mirrors the E.3 ``postProductionRun`` route pattern: parse the
    required Idempotency-Key + If-Match headers, validate the
    ``ProductionCancelRequest`` body, build the audit context, then
    delegate the atomic transaction to ``ProductionRunService.cancel``.
    """
    parsed_idem = parse_idempotency_key(idempotency_key)
    parsed_etag = parse_if_match(if_match)
    if parsed_etag is None:
        raise MissingHeader(
            "If-Match header is required for POST /production-runs/{id}/cancel."
        )
    body_dict = await request.json()
    payload = ProductionCancelRequest.model_validate(body_dict)
    body_for_idem = payload.model_dump(mode="json", exclude_none=True, by_alias=True)

    async with UnitOfWork() as uow:
        svc = ProductionRunService(uow)
        try:
            result, is_replay = await svc.cancel(
                run_id=run_id,
                principal_user_id=principal.user_id,
                reason=payload.reason,
                if_match=parsed_etag.etag,
                idempotency_key=str(parsed_idem.value),
                ctx=_audit_ctx(principal, request),
                request_body=body_for_idem,
            )
        except IdempotencyViolationConflict as exc:
            raise IdempotencyViolation(
                "Idempotency-Key reused with a different request body.",
                details=exc.details,
            ) from exc
        await uow.commit()
        return _idempotent_response(result, status.HTTP_200_OK, is_replay)


__all__ = ["router"]
