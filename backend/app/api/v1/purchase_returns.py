"""Purchase Returns API routes (M4 / Phase D.7 + D.8).

Endpoints match ``openapi.yaml`` §13 exactly:
* ``createPurchaseReturn``  POST  /purchases/{id}/returns
* ``listPurchaseReturns``   GET   /purchase-returns
* ``getPurchaseReturn``     GET   /purchase-returns/{id}
* ``cancelPurchaseReturn``  POST  /purchase-returns/{id}/cancel
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.audit.service import AuditContext
from app.auth.principal import Principal
from app.authz.deps import require_capability
from app.concurrency.etag import parse_if_match_optional
from app.db import UnitOfWork
from app.errors import IdempotencyViolation
from app.logging import get_logger
from app.services.idempotency import IdempotencyViolationConflict
from app.services.purchases import PurchaseService
from app.util import client_ip
from app.validation.headers import parse_idempotency_key
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.purchases_schemas import (
    PurchaseReturnArrivalRequest,
    PurchaseReturnCancelRequest,
    PurchaseReturnFinalizeRequest,
    PurchaseReturnOverrideRequest,
    PurchaseReturnRequest,
)

logger = get_logger(__name__)

# Two base paths: /purchases/{id}/returns (create) and /purchase-returns (read/cancel).
sub_router = APIRouter(prefix="/purchases", tags=["purchase_returns"])
top_router = APIRouter(prefix="/purchase-returns", tags=["purchase_returns"])


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


def _json_response(result: Any, status_code: int = status.HTTP_200_OK) -> JSONResponse:
    headers = {}
    if isinstance(result, dict):
        headers["ETag"] = _etag_for(result)
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(result),
        headers=headers,
    )


def _idempotent_response(
    result: Any, status_code: int, is_replay: bool
) -> JSONResponse:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            pass
    headers = {}
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


@sub_router.post(
    "/{purchase_id}/returns",
    operation_id="createPurchaseReturn",
    summary="Return goods to a supplier. Stock decreases at original landed cost.",
    status_code=status.HTTP_201_CREATED,
)
async def create_purchase_return(
    request: Request,
    purchase_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.return"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = PurchaseReturnRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        try:
            result, is_replay = await svc.create_return(
                purchase_id=purchase_id,
                principal_user_id=principal.user_id,
                reason=payload.reason,
                return_lines=[
                    {"purchase_line_id": rl.purchase_line_id, "quantity": rl.quantity}
                    for rl in payload.lines
                ],
                if_match=parse_if_match_optional(if_match),
                idempotency_key=str(parsed_key.value),
                ctx=_audit_ctx(principal, request),
                request_body=body_dict,
            )
        except IdempotencyViolationConflict as exc:
            raise IdempotencyViolation(
                "Idempotency-Key reused with a different request body.",
                details=exc.details,
            ) from exc
        await uow.commit()
        return _idempotent_response(result, status.HTTP_201_CREATED, is_replay)


@top_router.get(
    "",
    operation_id="listPurchaseReturns",
    summary="List all purchase returns.",
    status_code=status.HTTP_200_OK,
)
async def list_purchase_returns(
    request: Request,
    principal: Annotated[Principal, Depends(require_capability("purchase.view"))],
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=500),
    sort: str = Query(default="id"),
    purchase_id: int | None = Query(default=None),
    supplier_id: int | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
) -> MetaEnvelope[dict[str, Any]]:
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        rows, total = await svc.list_returns(
            page=page,
            per_page=per_page,
            sort=sort,
            purchase_id=purchase_id,
            supplier_id=supplier_id,
            lifecycle_status=lifecycle_status,
        )
        pag = make_pagination(page=page, per_page=per_page, total=total)
        await uow.commit()
        return MetaEnvelope(data=rows, pagination=pag)


@top_router.get(
    "/{return_id}",
    operation_id="getPurchaseReturn",
    summary="Get purchase return.",
    status_code=status.HTTP_200_OK,
)
async def get_purchase_return(
    request: Request,
    return_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.view"))],
) -> JSONResponse:
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        result = await svc.get_return(return_id)
        await uow.commit()
        return _json_response(result)


@top_router.post(
    "/{return_id}/cancel",
    operation_id="cancelPurchaseReturn",
    summary="Cancel a purchase return: insert reversal movements, update lifecycle.",
    status_code=status.HTTP_200_OK,
)
async def cancel_purchase_return(
    request: Request,
    return_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.return"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = PurchaseReturnCancelRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        try:
            result, is_replay = await svc.cancel_return(
                return_id=return_id,
                principal_user_id=principal.user_id,
                reason=payload.reason,
                if_match=parse_if_match_optional(if_match),
                idempotency_key=str(parsed_key.value),
                ctx=_audit_ctx(principal, request),
                request_body=body_dict,
            )
        except IdempotencyViolationConflict as exc:
            raise IdempotencyViolation(
                "Idempotency-Key reused with a different request body.",
                details=exc.details,
            ) from exc
        await uow.commit()
        return _idempotent_response(result, status.HTTP_200_OK, is_replay)


@top_router.post(
    "/{return_id}/finalize",
    operation_id="finalizePurchaseReturn",
    summary="Confirm courier handoff on a posted purchase return (finalized_at set).",
    status_code=status.HTTP_200_OK,
)
async def finalize_purchase_return(
    request: Request,
    return_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.return.finalize"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = PurchaseReturnFinalizeRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        try:
            result, is_replay = await svc.finalize_return(
                return_id=return_id,
                principal_user_id=principal.user_id,
                reason=payload.reason,
                if_match=parse_if_match_optional(if_match),
                idempotency_key=str(parsed_key.value),
                ctx=_audit_ctx(principal, request),
                request_body=body_dict,
            )
        except IdempotencyViolationConflict as exc:
            raise IdempotencyViolation(
                "Idempotency-Key reused with a different request body.",
                details=exc.details,
            ) from exc
        await uow.commit()
        return _idempotent_response(result, status.HTTP_200_OK, is_replay)


@top_router.post(
    "/{return_id}/arrival",
    operation_id="recordPurchaseReturnArrival",
    summary="Record supplier arrival on a posted purchase return (server-authoritative timestamp).",
    status_code=status.HTTP_200_OK,
)
async def record_purchase_return_arrival(
    request: Request,
    return_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.return.arrival"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    PurchaseReturnArrivalRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        try:
            result, is_replay = await svc.record_return_arrival(
                return_id=return_id,
                principal_user_id=principal.user_id,
                if_match=parse_if_match_optional(if_match),
                idempotency_key=str(parsed_key.value),
                ctx=_audit_ctx(principal, request),
                request_body=body_dict,
            )
        except IdempotencyViolationConflict as exc:
            raise IdempotencyViolation(
                "Idempotency-Key reused with a different request body.",
                details=exc.details,
            ) from exc
        await uow.commit()
        return _idempotent_response(result, status.HTTP_200_OK, is_replay)


@top_router.post(
    "/{return_id}/override-expired-window",
    operation_id="overridePurchaseReturnExpiredWindow",
    summary="Owner-only: override an expired supplier-arrival confirmation window.",
    status_code=status.HTTP_200_OK,
)
async def override_purchase_return_expired_window(
    request: Request,
    return_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.return.override"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = PurchaseReturnOverrideRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        try:
            result, is_replay = await svc.override_return_expired_window(
                return_id=return_id,
                principal_user_id=principal.user_id,
                reason=payload.reason,
                if_match=parse_if_match_optional(if_match),
                idempotency_key=str(parsed_key.value),
                ctx=_audit_ctx(principal, request),
                request_body=body_dict,
            )
        except IdempotencyViolationConflict as exc:
            raise IdempotencyViolation(
                "Idempotency-Key reused with a different request body.",
                details=exc.details,
            ) from exc
        await uow.commit()
        return _idempotent_response(result, status.HTTP_200_OK, is_replay)


__all__ = ["sub_router", "top_router"]
