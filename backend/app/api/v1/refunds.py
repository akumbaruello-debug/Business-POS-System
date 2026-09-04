"""Refunds API routes (M4 / Phase D.1).

Endpoints match ``openapi.yaml`` §16.7 exactly:
* ``listRefunds``  GET    /refunds
* ``createRefund`` POST   /refunds
* ``getRefund``    GET    /refunds/{id}
"""

from __future__ import annotations

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
from app.errors import IdempotencyViolation
from app.logging import get_logger
from app.services.idempotency import IdempotencyViolationConflict
from app.services.refunds import RefundService
from app.util import client_ip
from app.validation.headers import parse_idempotency_key
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.purchases_schemas import RefundRequest

logger = get_logger(__name__)

router = APIRouter(prefix="/refunds", tags=["refunds"])


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


@router.get(
    "",
    operation_id="listRefunds",
    summary="List refunds (customer disbursements). Refunds are cash-out, never income.",
    status_code=status.HTTP_200_OK,
)
async def list_refunds(
    request: Request,
    principal: Annotated[Principal, Depends(require_capability("sale.view"))],
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=500),
    sort: str = Query(default="id"),
    q: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    sale_id: int | None = Query(default=None),
    payment_method_id: int | None = Query(default=None),
) -> MetaEnvelope[dict[str, Any]]:
    async with UnitOfWork() as uow:
        svc = RefundService(uow)
        rows, total = await svc.list_refunds(
            page=page,
            per_page=per_page,
            sort=sort,
            q=q,
            sale_id=sale_id,
            payment_method_id=payment_method_id,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        pag = make_pagination(page=page, per_page=per_page, total=total)
        await uow.commit()
        return MetaEnvelope(data=rows, pagination=pag)


@router.post(
    "",
    operation_id="createRefund",
    summary="Disburse a refund. Bounded by current CRL and current cash balance. Mutates parent; requires If-Match.",
    status_code=status.HTTP_201_CREATED,
)
async def create_refund(
    request: Request,
    principal: Annotated[Principal, Depends(require_capability("sale.refund"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = RefundRequest.model_validate(body_dict)
    body_for_idem = payload.model_dump(mode="json", exclude_none=True, by_alias=True)
    async with UnitOfWork() as uow:
        svc = RefundService(uow)
        try:
            result, is_replay = await svc.create_refund(
                principal_user_id=principal.user_id,
                sale_id=payload.sale_id,
                amount=Decimal(str(payload.amount)),
                payment_method_id=payload.payment_method_id,
                refund_date=payload.refund_date,
                reason=payload.reason,
                if_match=parse_if_match_optional(if_match),
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
        resp = _json_response(result, status.HTTP_201_CREATED)
        if is_replay:
            resp.headers["Idempotent-Replay"] = "true"
            resp.status_code = status.HTTP_200_OK
        return resp


@router.get(
    "/{refund_id}",
    operation_id="getRefund",
    summary="Get refund.",
    status_code=status.HTTP_200_OK,
)
async def get_refund(
    request: Request,
    refund_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.view"))],
) -> JSONResponse:
    async with UnitOfWork() as uow:
        svc = RefundService(uow)
        result = await svc.get_refund(refund_id)
        await uow.commit()
        return _json_response(result)


__all__ = ["router"]
