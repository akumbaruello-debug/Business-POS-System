"""API routes for payment_methods (M2 foundation).

Per ``openapi.yaml`` §15.7.3 + Database-Design §3.1.

Endpoints:
  GET    /payment-methods                 — list (paginated, optional ``active_only``)
  POST   /payment-methods                 — create  (Idempotency-Key required, payment_method.manage)
  GET    /payment-methods/{id}            — get one
  PATCH  /payment-methods/{id}            — update  (payment_method.manage)
  DELETE /payment-methods/{id}            — hard delete if unreferenced (Idempotency-Key, payment_method.manage)
  POST   /payment-methods/{id}/deactivate — soft delete (Idempotency-Key, payment_method.manage)
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Request, status

from app.api.deps import get_uow, require_capability
from app.audit.service import AuditContext
from app.auth.principal import Principal
from app.db import UnitOfWork
from app.services.finance import PaymentMethodService
from app.validation.headers import parse_idempotency_key
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.schemas import (
    DeactivateRequest,
    PaymentMethodPatch,
    PaymentMethodRequest,
    PaymentMethodResponse,
)

router = APIRouter(prefix="/payment-methods", tags=["payment_methods"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_response(row: dict[str, Any]) -> PaymentMethodResponse:
    return PaymentMethodResponse(
        id=int(row["id"]),
        code=str(row["code"]),
        name=str(row["name"]),
        is_cash=bool(row["is_cash"]),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _audit_ctx(request: Request, principal: Principal) -> AuditContext:
    return AuditContext(
        user_id=principal.user_id,
        request_id=request.headers.get("x-request-id"),
        ip_address=_client_ip(request),
    )


# ---------------------------------------------------------------------------
# Capability gates (as dependencies so 403 is raised before the handler runs)
# ---------------------------------------------------------------------------

RequireView = require_capability("payment_method.view")
RequireManage = require_capability("payment_method.manage")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get(
    "/",
    operation_id="listPaymentMethods",
    summary="List payment methods",
    status_code=status.HTTP_200_OK,
)
async def list_payment_methods(
    request: Request,
    principal: Annotated[Principal, Depends(RequireView)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(1, ge=1, le=1000),
    per_page: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, max_length=200),
    active_only: bool = Query(False, alias="filter[is_active]"),
) -> MetaEnvelope[PaymentMethodResponse]:
    """List payment methods, paginated.

    ``$?filter[is_active]=true`` restricts to active rows; otherwise all
    rows (including deactivated) are returned.
    """
    svc = PaymentMethodService(uow)
    rows, total = await svc.list(page=page, per_page=per_page, q=q, active_only=active_only)
    return MetaEnvelope[PaymentMethodResponse](
        data=[_to_response(r) for r in rows],
        pagination=make_pagination(page, per_page, total),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "/",
    operation_id="createPaymentMethods",
    summary="Create payment method",
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_method(
    request: Request,
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> PaymentMethodResponse:
    """Create a payment method. Idempotency-Key required per OpenAPI."""
    parse_idempotency_key(idempotency_key)
    body = PaymentMethodRequest.model_validate(await request.json())

    svc = PaymentMethodService(uow)
    row = await svc.create(
        code=body.code,
        name=body.name,
        is_cash=body.is_cash,
        is_active=body.is_active,
        ctx=_audit_ctx(request, principal),
    )
    await uow.commit()
    return _to_response(row)


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


@router.get(
    "/{id}",
    operation_id="getPaymentMethods",
    summary="Get payment method",
    status_code=status.HTTP_200_OK,
)
async def get_payment_method(
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireView)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> PaymentMethodResponse:
    """Get a single payment method by id."""
    svc = PaymentMethodService(uow)
    row = await svc.get(id)
    return _to_response(row)


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


@router.patch(
    "/{id}",
    operation_id="updatePaymentMethods",
    summary="Update payment method (PATCH)",
    status_code=status.HTTP_200_OK,
)
async def update_payment_method(
    request: Request,
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> PaymentMethodResponse:
    """Update a payment method. ``code`` is immutable."""
    body = PaymentMethodPatch.model_validate(await request.json())
    svc = PaymentMethodService(uow)
    row = await svc.update(
        id,
        name=body.name,
        is_cash=body.is_cash,
        is_active=body.is_active,
        ctx=_audit_ctx(request, principal),
    )
    await uow.commit()
    return _to_response(row)


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


@router.post(
    "/{id}/deactivate",
    operation_id="deactivatePaymentMethods",
    summary="Deactivate payment method. Preserves history.",
    status_code=status.HTTP_200_OK,
)
async def deactivate_payment_method(
    request: Request,
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> PaymentMethodResponse:
    """Deactivate a payment method (sets is_active=FALSE)."""
    parse_idempotency_key(idempotency_key)
    body = DeactivateRequest.model_validate_json(await request.body())
    svc = PaymentMethodService(uow)
    row = await svc.deactivate(id, reason=body.reason, ctx=_audit_ctx(request, principal))
    await uow.commit()
    return _to_response(row)


# ---------------------------------------------------------------------------
# Delete (hard, only if unreferenced)
# ---------------------------------------------------------------------------


@router.delete(
    "/{id}",
    operation_id="deletePaymentMethods",
    summary="Hard delete payment method (only if unreferenced)",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_payment_method(
    request: Request,
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> None:
    """Hard-delete only when no history references the payment method."""
    parse_idempotency_key(idempotency_key)
    svc = PaymentMethodService(uow)
    await svc.delete(id, ctx=_audit_ctx(request, principal))
    await uow.commit()
    return None


__all__ = ["router"]
