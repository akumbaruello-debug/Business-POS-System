"""Purchase Shipping API routes (M4 / Phase D.4).

Endpoints match ``openapi.yaml`` §13 exactly:
* ``setPurchaseShipping``    POST  /purchases/{id}/shipping
* ``updatePurchaseShipping`` PATCH /purchases/{id}/shipping
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.audit.service import AuditContext
from app.auth.principal import Principal
from app.authz.deps import require_capability
from app.concurrency.etag import parse_if_match_optional
from app.db import UnitOfWork
from app.errors import IdempotencyViolation, MissingHeader
from app.logging import get_logger
from app.services.idempotency import IdempotencyViolationConflict
from app.services.purchases import PurchaseService
from app.util import client_ip
from app.validation.headers import parse_idempotency_key
from app.validation.purchases_schemas import PurchaseShippingRequest

logger = get_logger(__name__)

router = APIRouter(prefix="/purchases", tags=["purchase_shipping"])


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


@router.post(
    "/{purchase_id}/shipping",
    operation_id="setPurchaseShipping",
    summary="Add or replace shipping on a Draft purchase. Always capitalized into landed cost. Requires If-Match.",
    status_code=status.HTTP_200_OK,
)
async def set_purchase_shipping(
    request: Request,
    purchase_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.edit_own_draft"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = PurchaseShippingRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        try:
            result, is_replay = await svc.set_shipping(
                purchase_id=purchase_id,
                principal_user_id=principal.user_id,
                amount=Decimal(str(payload.amount)),
                paid_in_cash=bool(payload.paid_in_cash),
                supplier_id=payload.supplier_id,
                description=payload.description,
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


@router.patch(
    "/{purchase_id}/shipping",
    operation_id="updatePurchaseShipping",
    summary="Update shipping on a Draft purchase.",
    status_code=status.HTTP_200_OK,
)
async def update_purchase_shipping(
    request: Request,
    purchase_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.edit_own_draft"))],
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader("If-Match header is required.")
    body_dict = await request.json()
    payload = PurchaseShippingRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        result = await svc.update_shipping(
            purchase_id=purchase_id,
            principal_user_id=principal.user_id,
            amount=Decimal(str(payload.amount)),
            paid_in_cash=bool(payload.paid_in_cash),
            supplier_id=payload.supplier_id,
            description=payload.description,
            if_match=etag_parsed,
            ctx=_audit_ctx(principal, request),
        )
        await uow.commit()
        return _json_response(result)


__all__ = ["router"]
