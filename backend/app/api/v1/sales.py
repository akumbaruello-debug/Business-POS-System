"""Sales lifecycle API routes (B.8).

Endpoints match ``openapi.yaml`` §15 exactly. Route layer is thin: it
authenticates, parses the Pydantic request body, builds the
:class:`AuditContext`, and delegates to :class:`SaleService`.

Refunds are **out of scope** for B.8 (deferred to M4/Phase D.1).
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
from app.services.sales import SaleService
from app.util import client_ip
from app.validation.headers import parse_idempotency_key
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.sales_schemas import (
    SaleCancelRequest,
    SaleCreateRequest,
    SaleLineInput,
    SaleLinePatch,
    SalePatch,
    SalePaymentInput,
    SaleReturnRequest,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/sales", tags=["sales"])
returns_router = APIRouter(prefix="/sales-returns", tags=["sales-returns"])


# ---------------------------------------------------------------------------
# Helpers
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
        return f'"{etag}"'
    return f'"{result.get("updated_at") or result.get("version")}"'


def _json_response(
    result: dict[str, Any] | list[dict[str, Any]], status_code: int = status.HTTP_200_OK
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
    """Build a response for an idempotent endpoint.

    On a successful idempotent replay (same Idempotency-Key + same
    fingerprint) the original response body is returned with HTTP 200
    and an ``Idempotent-Replay: true`` header. The fresh-response status
    code is discarded in favour of 200 per the B.8 idempotency contract.

    The stored idempotency body may be a JSON-encoded *string* (the
    canonical form persisted by ``IdempotencyStore.complete``). On replay
    we decode it back to a structured object so the client receives the
    same shape it originally did.
    """
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
    """Route-layer coercion: numeric/str -> Decimal. None passes through."""
    if v is None:
        return None
    return Decimal(str(v))


# ---------------------------------------------------------------------------
# /sales — list / create
# ---------------------------------------------------------------------------


@router.get(
    "",
    operation_id="listSales",
    summary="List sales the caller may view (paginated).",
    status_code=status.HTTP_200_OK,
)
async def list_sales(
    request: Request,
    principal: Annotated[Principal, Depends(require_capability("sale.view"))],
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=500),
    q: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    customer_id: int | None = Query(default=None),
    payment_state: str | None = Query(default=None),
) -> MetaEnvelope[dict[str, Any]]:
    """List sales."""
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        rows, total = await svc.list_sales(
            page=page,
            per_page=per_page,
            q=q,
            lifecycle_status=lifecycle_status,
            customer_id=customer_id,
            payment_state=payment_state,
        )
        pag = make_pagination(page=page, per_page=per_page, total=total)
        await uow.commit()
        return MetaEnvelope(data=rows, pagination=pag)


@router.post(
    "",
    operation_id="createSale",
    summary="Create a draft sale (ownable by creator).",
    status_code=status.HTTP_201_CREATED,
)
async def create_sale(
    request: Request,
    principal: Annotated[Principal, Depends(require_capability("sale.create"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """Create a draft sale. Idempotency-Key supported (1d TTL)."""
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = SaleCreateRequest.model_validate(body_dict)
    body_for_idem = payload.model_dump(mode="json", exclude_none=True, by_alias=True)

    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        try:
            result, is_replay = await svc.create_draft(
                principal_user_id=principal.user_id,
                customer_id=payload.customer_id,
                sale_date=payload.sale_date,
                discount_amount=Decimal(str(payload.discount_amount)),
                notes=payload.notes,
                reference_no=payload.reference_no,
                lines=[ln.model_dump(mode="json") for ln in payload.lines],
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
# /sales/{sale_id} — get / patch / delete
# ---------------------------------------------------------------------------


@router.get(
    "/{sale_id}",
    operation_id="getSale",
    summary="Get a single sale by id.",
    status_code=status.HTTP_200_OK,
)
async def get_sale(
    request: Request,
    sale_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.view"))],
) -> JSONResponse:
    """Retrieve a sale by id."""
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        result = await svc.get_sale(sale_id=sale_id)
        await uow.commit()
        return _json_response(result)


@router.patch(
    "/{sale_id}",
    operation_id="updateSaleDraft",
    summary="Patch a draft sale (owner-only fields).",
    status_code=status.HTTP_200_OK,
)
async def update_sale(
    request: Request,
    sale_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.edit_own_draft"))],
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    """Patch an in-draft sale the caller owns."""
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader("If-Match header is required for PATCH /sales/{sale_id}.")
    body_dict = await request.json()
    payload = SalePatch.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        result = await svc.update_draft(
            sale_id=sale_id,
            customer_id=payload.customer_id,
            sale_date=payload.sale_date,
            discount_amount=_dec(payload.discount_amount),
            notes=payload.notes,
            if_match=etag_parsed,
            ctx=_audit_ctx(principal, request),
        )
        await uow.commit()
        return _json_response(result)


@router.post(
    "/{sale_id}",
    operation_id="deleteSaleDraft",
    summary="Delete a draft sale (idempotent).",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_sale(
    request: Request,
    sale_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.create"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    """Delete a draft sale. Idempotency-Key supported."""
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        deleted, etag = await svc.delete_draft(
            sale_id=sale_id,
            principal_user_id=principal.user_id,
            idempotency_key=str(parsed_key.value),
            if_match=parse_if_match_optional(if_match),
            ctx=_audit_ctx(principal, request),
            request_body=body_dict,
        )
        await uow.commit()
        if deleted:
            return Response(
                status_code=status.HTTP_204_NO_CONTENT, content=b"", headers={"ETag": etag}
            )
        return Response(
            status_code=status.HTTP_200_OK,
            content=b"",
            headers={"ETag": etag, "Idempotent-Replay": "true"},
        )


# ---------------------------------------------------------------------------
# /sales/{sale_id}/lines
# ---------------------------------------------------------------------------


@router.get(
    "/{sale_id}/lines",
    operation_id="listSaleLines",
    summary="List lines for a sale.",
    status_code=status.HTTP_200_OK,
)
async def list_sale_lines(
    request: Request,
    sale_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.view"))],
) -> JSONResponse:
    """List sale lines for a sale."""
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        result = await svc.list_sale_lines(sale_id=sale_id)
        await uow.commit()
        return _json_response(result)


@router.post(
    "/{sale_id}/lines",
    operation_id="addSaleLine",
    summary="Add a line to a draft sale.",
    status_code=status.HTTP_201_CREATED,
)
async def add_sale_line(
    request: Request,
    sale_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.edit_own_draft"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    """Add a line to a draft sale."""
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    line_input = SaleLineInput.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        try:
            result, is_replay = await svc.add_line(
                sale_id=sale_id,
                principal_user_id=principal.user_id,
                principal_caps=principal.capabilities,
                line=line_input.model_dump(mode="json"),
                idempotency_key=str(parsed_key.value),
                if_match=parse_if_match_optional(if_match),
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


@router.patch(
    "/{sale_id}/lines/{line_id}",
    operation_id="updateSaleLine",
    summary="Patch a line on a draft sale.",
    status_code=status.HTTP_200_OK,
)
async def update_sale_line(
    request: Request,
    sale_id: int,
    line_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.edit_own_draft"))],
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    """Patch a draft sale line."""
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader("If-Match header is required.")
    body_dict = await request.json()
    payload = SaleLinePatch.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        result = await svc.update_line(
            sale_id=sale_id,
            line_id=line_id,
            principal_user_id=principal.user_id,
            principal_caps=principal.capabilities,
            quantity=_dec(payload.quantity),
            unit_price=_dec(payload.unit_price),
            discount_amount=_dec(payload.discount_amount),
            if_match=etag_parsed,
            ctx=_audit_ctx(principal, request),
        )
        await uow.commit()
        return _json_response(result)


@router.delete(
    "/{sale_id}/lines/{line_id}",
    operation_id="deleteSaleLine",
    summary="Delete a line on a draft sale (idempotent).",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_sale_line(
    request: Request,
    sale_id: int,
    line_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.edit_own_draft"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    """Delete a draft sale line. Idempotency-Key supported."""
    parsed_key = parse_idempotency_key(idempotency_key)
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        _result, is_replay = await svc.delete_line(
            sale_id=sale_id,
            line_id=line_id,
            principal_user_id=principal.user_id,
            principal_caps=principal.capabilities,
            idempotency_key=str(parsed_key.value),
            if_match=parse_if_match_optional(if_match),
            ctx=_audit_ctx(principal, request),
        )
        await uow.commit()
        if is_replay:
            headers = {"Idempotent-Replay": "true"}
            return Response(
                status_code=status.HTTP_200_OK, content=b"", headers=headers
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT, content=b"")


# ---------------------------------------------------------------------------
# /sales/{sale_id}/post
# ---------------------------------------------------------------------------


@router.post(
    "/{sale_id}/post",
    operation_id="postSale",
    summary="Post a draft sale.",
    status_code=status.HTTP_200_OK,
)
async def post_sale(
    request: Request,
    sale_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.post"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    """Post a draft sale."""
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        try:
            result, is_replay = await svc.post_sale(
                sale_id=sale_id,
                principal_user_id=principal.user_id,
                idempotency_key=str(parsed_key.value),
                if_match=parse_if_match_optional(if_match),
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
# /sales/{sale_id}/payments
# ---------------------------------------------------------------------------


@router.get(
    "/{sale_id}/payments",
    operation_id="listSalePayments",
    summary="List payments for a sale.",
    status_code=status.HTTP_200_OK,
)
async def list_sale_payments(
    request: Request,
    sale_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.view"))],
) -> JSONResponse:
    """List payments for a sale."""
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        result = await svc.list_sale_payments(sale_id=sale_id)
        await uow.commit()
        return _json_response(result)


@router.post(
    "/{sale_id}/payments",
    operation_id="addSalePayment",
    summary="Add a payment (auto-completes when paid in full).",
    status_code=status.HTTP_201_CREATED,
)
async def add_sale_payment(
    request: Request,
    sale_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.create"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    """Add a payment to a posted (or completed) sale."""
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = SalePaymentInput.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        try:
            result, is_replay = await svc.add_payment(
                sale_id=sale_id,
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
# /sales/{sale_id}/cancel
# ---------------------------------------------------------------------------


@router.post(
    "/{sale_id}/cancel",
    operation_id="cancelSale",
    summary="Cancel a sale — creates reversal movements.",
    status_code=status.HTTP_200_OK,
)
async def cancel_sale(
    request: Request,
    sale_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.cancel"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    """Cancel a sale."""
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = SaleCancelRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        try:
            result, is_replay = await svc.cancel_sale(
                sale_id=sale_id,
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


# ---------------------------------------------------------------------------
# /sales/{sale_id}/returns
# ---------------------------------------------------------------------------


@router.get(
    "/{sale_id}/returns",
    operation_id="listSaleReturns",
    summary="List returns for a sale.",
    status_code=status.HTTP_200_OK,
)
async def list_sale_returns(
    request: Request,
    sale_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.view"))],
) -> JSONResponse:
    """List returns for a sale."""
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        result = await svc.list_sale_returns(sale_id=sale_id)
        await uow.commit()
        return _json_response(result)


@router.post(
    "/{sale_id}/returns",
    operation_id="createSaleReturn",
    summary="Create a partial/full sales return.",
    status_code=status.HTTP_201_CREATED,
)
async def create_sale_return(
    request: Request,
    sale_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.return"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    """Create a sales return against a posted/completed sale."""
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = SaleReturnRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        try:
            result, is_replay = await svc.create_return(
                sale_id=sale_id,
                principal_user_id=principal.user_id,
                reason=payload.reason,
                return_lines=[
                    {"sale_line_id": rl.sale_line_id, "quantity": rl.quantity} for rl in payload.lines
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


# ---------------------------------------------------------------------------
# /sales-returns  (separate base path)
# ---------------------------------------------------------------------------


@returns_router.get(
    "",
    operation_id="listSalesReturns",
    summary="List sales returns.",
    status_code=status.HTTP_200_OK,
)
async def list_sales_returns(
    request: Request,
    principal: Annotated[Principal, Depends(require_capability("sale.view"))],
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=500),
) -> MetaEnvelope[dict[str, Any]]:
    """List sales returns across all sales the caller may view."""
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        rows, total = await svc.list_sales_returns(page=page, per_page=per_page)
        pag = make_pagination(page=page, per_page=per_page, total=total)
        await uow.commit()
        return MetaEnvelope(data=rows, pagination=pag)


@returns_router.get(
    "/{return_id}",
    operation_id="getSalesReturn",
    summary="Get a single sales return.",
    status_code=status.HTTP_200_OK,
)
async def get_sales_return(
    request: Request,
    return_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.view"))],
) -> JSONResponse:
    """Retrieve a sales return by id."""
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
        result = await svc.get_sales_return(return_id=return_id)
        await uow.commit()
        return _json_response(result)


@returns_router.post(
    "/{return_id}/cancel",
    operation_id="cancelSalesReturn",
    summary="Cancel a sales return (posted only).",
    status_code=status.HTTP_200_OK,
)
async def cancel_sales_return(
    request: Request,
    return_id: int,
    principal: Annotated[Principal, Depends(require_capability("sale.return"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    """Cancel a posted sales return."""
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = SaleCancelRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = SaleService(uow)
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


__all__ = ["returns_router", "router"]
