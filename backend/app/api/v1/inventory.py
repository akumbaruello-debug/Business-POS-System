"""Inventory API routes (E.5 read + E.6 write).

Endpoints match ``openapi.yaml`` §15.2 (lines 4192-4346) exactly. The
route layer is thin: authenticate, enforce capability, parse headers
+ pagination/filter query params, delegate to
:class:`app.inventory.service.InventoryService`, return the
contract-shaped envelope.

E.5 endpoints (read-only — no Idempotency-Key, no If-Match, no body):

* ``GET /inventory``              — ``listInventory``    (paginated)
* ``GET /inventory/products/{id}`` — ``getProductStock``  (single snapshot)
* ``GET /inventory/low-stock``     — ``listLowStock``    (paginated)

E.6 endpoint (lifecycle write — Idempotency-Key + If-Match required):

* ``POST /inventory/adjustments``  — ``createStockAdjustment``
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
from app.db import UnitOfWork
from app.errors import IdempotencyViolation, MissingHeader
from app.inventory.service import InventoryService
from app.services.idempotency import IdempotencyViolationConflict
from app.util import client_ip
from app.validation.headers import parse_idempotency_key, parse_if_match
from app.validation.inventory_schemas import StockAdjustmentRequest

RequireInventoryView = require_capability("inventory.view")
RequireInventoryAdjust = require_capability("inventory.adjust")

router = APIRouter(prefix="/inventory", tags=["Inventory"])


def _client_ip(request: Request) -> str | None:
    return client_ip(request.scope.get("headers", []))


def _audit_ctx(principal: Principal, request: Request) -> AuditContext:
    return AuditContext(
        user_id=principal.user_id,
        ip_address=_client_ip(request),
        request_id=getattr(request.state, "request_id", None),
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
    if is_replay:
        headers["Idempotent-Replay"] = "true"
        status_code = status.HTTP_200_OK
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(result),
        headers=headers,
    )


# ---------------------------------------------------------------------------
# GET /inventory
# ---------------------------------------------------------------------------


@router.get(
    "",
    operation_id="listInventory",
    summary="Per-product inventory summary (paginated).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireInventoryView)],
)
async def list_inventory(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=500),
    sort: str = Query(default="id"),
    q: str | None = Query(default=None, max_length=200),
    category_id: int | None = Query(default=None, alias="filter[category_id]"),
    is_active: bool | None = Query(default=None, alias="filter[is_active]"),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
) -> JSONResponse:
    """Per-product inventory summary (paginated)."""
    async with UnitOfWork() as uow:
        svc = InventoryService(uow)
        result = await svc.list_inventory(
            page=page,
            per_page=per_page,
            sort=sort,
            q=q,
            category_id=category_id,
            is_active=is_active,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        await uow.commit()
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=jsonable_encoder(result),
        )


# ---------------------------------------------------------------------------
# GET /inventory/products/{product_id}
# ---------------------------------------------------------------------------


@router.get(
    "/products/{product_id}",
    operation_id="getProductStock",
    summary="Per-product stock snapshot (on-hand, moving avg, low_stock).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireInventoryView)],
)
async def get_product_stock(
    request: Request,
    product_id: int,
) -> JSONResponse:
    """Per-product stock snapshot.

    404 if the product does not exist. Returns the OpenAPI
    ``ProductStock`` shape: ``{ product_id, on_hand_quantity,
    moving_average_unit_cost, inventory_value, low_stock,
    low_stock_threshold, as_of }``.
    """
    async with UnitOfWork() as uow:
        svc = InventoryService(uow)
        stock = await svc.get_product_stock(product_id=product_id)
        await uow.commit()
        if stock is None:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={
                    "error": {
                        "code": "not_found",
                        "message": f"Product {product_id} not found.",
                    }
                },
            )
        return JSONResponse(status_code=status.HTTP_200_OK, content=jsonable_encoder(stock))


# ---------------------------------------------------------------------------
# GET /inventory/low-stock
# ---------------------------------------------------------------------------


@router.get(
    "/low-stock",
    operation_id="listLowStock",
    summary="Products at or below their low-stock threshold.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireInventoryView)],
)
async def list_low_stock(
    request: Request,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=500),
    sort: str = Query(default="id"),
    q: str | None = Query(default=None, max_length=200),
    category_id: int | None = Query(default=None, alias="filter[category_id]"),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
) -> JSONResponse:
    """Products at or below their low-stock threshold (paginated)."""
    async with UnitOfWork() as uow:
        svc = InventoryService(uow)
        result = await svc.list_low_stock(
            page=page,
            per_page=per_page,
            sort=sort,
            q=q,
            category_id=category_id,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        await uow.commit()
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=jsonable_encoder(result),
        )


__all__ = ["router"]


# ---------------------------------------------------------------------------
# E.6 — POST /inventory/adjustments (createStockAdjustment)
# ---------------------------------------------------------------------------


@router.post(
    "/adjustments",
    operation_id="createStockAdjustment",
    summary=(
        "Apply a stock adjustment (signed qty). Reason required and logged in audit. "
        "P&L effect is NOT auto-posted in V1. Mutates product; requires If-Match."
    ),
    description=(
        "Apply a stock adjustment (signed qty). Reason required and logged in audit. "
        "P&L effect is NOT auto-posted in V1. Mutates product; requires If-Match."
    ),
    status_code=status.HTTP_201_CREATED,
)
async def create_stock_adjustment(
    request: Request,
    principal: Annotated[
        Principal, Depends(RequireInventoryAdjust)
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JSONResponse:
    """createStockAdjustment — E.6 thin route.

    Parses the required Idempotency-Key + If-Match headers, validates
    the ``StockAdjustmentRequest`` body, builds the audit context,
    then delegates the atomic transaction to
    ``InventoryService.create_adjustment``.
    """
    parsed_idem = parse_idempotency_key(idempotency_key)
    parsed_etag = parse_if_match(if_match)
    if parsed_etag is None:
        raise MissingHeader(
            "If-Match header is required for POST /inventory/adjustments."
        )
    body_dict = await request.json()
    payload = StockAdjustmentRequest.model_validate(body_dict)
    body_for_idem = payload.model_dump(mode="json", exclude_none=True, by_alias=True)

    async with UnitOfWork() as uow:
        svc = InventoryService(uow)
        try:
            result, is_replay = await svc.create_adjustment(
                principal_user_id=principal.user_id,
                product_id=int(payload.product_id),
                quantity=Decimal(str(payload.quantity)),
                reason=str(payload.reason),
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
        return _idempotent_response(result, status.HTTP_201_CREATED, is_replay)
