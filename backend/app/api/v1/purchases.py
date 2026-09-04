"""Purchases lifecycle API routes (M4 / Phase D).

Endpoints match ``openapi.yaml`` §13 exactly. Route layer is thin:
it authenticates, parses the Pydantic request body, builds the
:class:`AuditContext`, and delegates to :class:`PurchaseService`.

The line / shipping / returns / payments endpoints live in sibling
routers (``purchase_lines``, ``purchase_shipping``, ``purchase_returns``,
``supplier_repayments``) — all mounted under the same ``/purchases``
prefix.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
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
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.purchases_schemas import (
    PurchaseCancelRequest,
    PurchaseCreateRequest,
    PurchasePatch,
    PurchasePaymentInput,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/purchases", tags=["purchases"])


# ---------------------------------------------------------------------------
# Helpers (mirrored from app/api/v1/sales.py)
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
# /purchases — list / create
# ---------------------------------------------------------------------------


@router.get(
    "",
    operation_id="listPurchases",
    summary="List purchases.",
    status_code=status.HTTP_200_OK,
)
async def list_purchases(
    request: Request,
    principal: Annotated[Principal, Depends(require_capability("purchase.view"))],
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=500),
    q: str | None = Query(default=None),
    sort: str = Query(default="id"),
    lifecycle_status: str | None = Query(default=None),
    supplier_id: int | None = Query(default=None),
    payment_state: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
) -> MetaEnvelope[dict[str, Any]]:
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        rows, total = await svc.list_purchases(
            page=page,
            per_page=per_page,
            q=q,
            sort=sort,
            lifecycle_status=lifecycle_status,
            supplier_id=supplier_id,
            payment_state=payment_state,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        pag = make_pagination(page=page, per_page=per_page, total=total)
        await uow.commit()
        return MetaEnvelope(data=rows, pagination=pag)


@router.post(
    "",
    operation_id="createPurchase",
    summary="Create a purchase in Draft. Supplier optional.",
    status_code=status.HTTP_201_CREATED,
)
async def create_purchase(
    request: Request,
    principal: Annotated[Principal, Depends(require_capability("purchase.create"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = PurchaseCreateRequest.model_validate(body_dict)
    body_for_idem = payload.model_dump(mode="json", exclude_none=True, by_alias=True)
    shipping_body = None
    if payload.shipping is not None:
        shipping_body = payload.shipping.model_dump(mode="json", exclude_none=True)

    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        try:
            result, is_replay = await svc.create_draft(
                principal_user_id=principal.user_id,
                supplier_id=payload.supplier_id,
                purchase_date=payload.purchase_date,
                notes=payload.notes,
                reference_no=payload.reference_no,
                lines=[ln.model_dump(mode="json") for ln in payload.lines],
                shipping=shipping_body,
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
# /purchases/{purchase_id} — get / patch / delete
# ---------------------------------------------------------------------------


@router.get(
    "/{purchase_id}",
    operation_id="getPurchase",
    summary="Get purchase. Use `?include=lines,shipping,payments,returns`.",
    status_code=status.HTTP_200_OK,
)
async def get_purchase(
    request: Request,
    purchase_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.view"))],
    include: str | None = Query(default=None),
) -> JSONResponse:
    inc: set[str] = set()
    if include:
        inc = {x.strip() for x in include.split(",") if x.strip()}
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        result = await svc.get_purchase(purchase_id, include=inc)
        await uow.commit()
        return _json_response(result)


@router.patch(
    "/{purchase_id}",
    operation_id="updatePurchaseDraft",
    summary="Update Draft purchase.",
    status_code=status.HTTP_200_OK,
)
async def update_purchase(
    request: Request,
    purchase_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.edit_own_draft"))],
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader("If-Match header is required for PATCH /purchases/{id}.")
    body_dict = await request.json()
    payload = PurchasePatch.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        result = await svc.update_draft(
            purchase_id=purchase_id,
            supplier_id=payload.supplier_id,
            purchase_date=payload.purchase_date,
            notes=payload.notes,
            if_match=etag_parsed,
            ctx=_audit_ctx(principal, request),
        )
        await uow.commit()
        return _json_response(result)


@router.delete(
    "/{purchase_id}",
    operation_id="deletePurchaseDraft",
    summary="Hard delete Draft purchase.",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={204: {"description": "Deleted"}},
)
async def delete_purchase(
    purchase_id: int,
    request: Request,
    principal: Annotated[Principal, Depends(require_capability("purchase.create"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    parsed_key = parse_idempotency_key(idempotency_key)
    # DELETE may have no body; tolerate empty/missing JSON.
    try:
        body_dict: dict[str, Any] = await request.json()
    except Exception:
        body_dict = {}
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        deleted, etag = await svc.delete_draft(
            purchase_id=purchase_id,
            principal_user_id=principal.user_id,
            idempotency_key=str(parsed_key.value),
            if_match=parse_if_match_optional(if_match),
            ctx=_audit_ctx(principal, request),
            request_body=body_dict,
        )
        await uow.commit()
        if deleted:
            return Response(
                status_code=status.HTTP_204_NO_CONTENT,
                content=b"",
                headers={"ETag": etag},
            )
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            content=b"",
            headers={"ETag": etag, "Idempotent-Replay": "true"},
        )


# ---------------------------------------------------------------------------
# /purchases/{purchase_id}/post
# ---------------------------------------------------------------------------


@router.post(
    "/{purchase_id}/post",
    operation_id="postPurchase",
    summary="Post purchase: capitalize shipping into landed cost, receive stock, create AP. Idempotent. Requires If-Match.",
    status_code=status.HTTP_200_OK,
)
async def post_purchase(
    request: Request,
    purchase_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.post"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        try:
            result, is_replay = await svc.post_purchase(
                purchase_id=purchase_id,
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


# ---------------------------------------------------------------------------
# /purchases/{purchase_id}/payments — list + add
# ---------------------------------------------------------------------------


@router.get(
    "/{purchase_id}/payments",
    operation_id="listPurchasePayments",
    summary="List payments on a purchase.",
    status_code=status.HTTP_200_OK,
)
async def list_purchase_payments(
    request: Request,
    purchase_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.view"))],
) -> JSONResponse:
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        result = await svc.list_payments(purchase_id)
        await uow.commit()
        return _json_response(result)


@router.post(
    "/{purchase_id}/payments",
    operation_id="addPurchasePayment",
    summary="Record a payment on a purchase. Over-tender supported (cash only). Mutates parent; requires If-Match.",
    status_code=status.HTTP_201_CREATED,
)
async def add_purchase_payment(
    request: Request,
    purchase_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.create"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = PurchasePaymentInput.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        try:
            result, is_replay = await svc.add_payment(
                purchase_id=purchase_id,
                principal_user_id=principal.user_id,
                payment_method_id=payload.payment_method_id,
                amount=Decimal(str(payload.amount)),
                tendered_amount=_dec(payload.tendered_amount),
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


# ---------------------------------------------------------------------------
# /purchases/{purchase_id}/cancel
# ---------------------------------------------------------------------------


@router.post(
    "/{purchase_id}/cancel",
    operation_id="cancelPurchase",
    summary="Cancel a posted purchase. On-hand portion reverses via purchase_reversal; consumed via value_adjustment (qty=0).",
    status_code=status.HTTP_200_OK,
)
async def cancel_purchase(
    request: Request,
    purchase_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.cancel"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = PurchaseCancelRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        try:
            result, is_replay = await svc.cancel_purchase(
                purchase_id=purchase_id,
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


__all__ = ["router"]
