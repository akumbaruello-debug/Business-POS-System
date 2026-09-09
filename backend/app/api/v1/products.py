"""API routes for Products (B.6 + B.7).

Per OpenAPI §15.7:

B.6 — 6 operations:
  GET    /products                   — list (product.view)
  POST   /products                   — create (product.manage, Idempotency-Key)
  GET    /products/{id}              — get one (product.view)
  PATCH  /products/{id}              — update (product.manage, If-Match required)
  DELETE /products/{id}              — hard delete (product.manage, Idempotency-Key)
  POST   /products/{id}/deactivate   — deactivate (product.manage, Idempotency-Key)

B.7 — 3 read-only sub-resources:
  GET    /products/{id}/price-history  — list (product.view)
  GET    /products/{id}/stock-movements — list (inventory.view)
  GET    /products/{id}/valuation      — snapshot (inventory.view)

Follows the same patterns as the Categories (B.1) and Units (B.2)
routes already present in the live codebase.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, status

from app.api.deps import current_principal, get_uow, require_capability
from app.auth.principal import Principal
from app.concurrency.etag import parse_if_match_optional
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.db import UnitOfWork
from app.errors import MissingHeader
from app.services.products import (
    ProductService,
    commit_import,
    validate_import_rows,
)
from app.util import client_ip
from app.validation.headers import parse_idempotency_key
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.products_schemas import (
    DeactivateRequest,
    ProductCreateRequest,
    ProductImportReport,
    ProductPatch,
    ProductPriceHistory,
    ProductResponse,
    ProductValuation,
    StockMovement,
)
from pydantic import BaseModel, ConfigDict, Field

# Capability gates.
#
# CONTRADICTION REPORTED:
#   OpenAPI §15.7 declares ``product.manage`` on DELETE /products/{id}
#   (line 2019 of openapi.yaml). The canonical capability catalog
#   (db/migrations/* + app/authz/caps.py) only seeds these four codes:
#       product.view, product.create, product.edit, product.deactivate
#   ``product.manage`` does not exist in the DB. Honoring the literal
#   OpenAPI capability would yield a 403 for every caller including the
#   Owner (who holds the *union* of the canonical set).
#   Resolution: gate DELETE on ``product.edit`` (the strongest existing
#   product-mutation capability). This preserves the *intent* of the
#   capability check (only role holders who can mutate products can
#   delete them) without modifying either locked artifact. Surfaced
#   for tracking in the B.6 final report.
#
RequireView = require_capability("product.view")
RequireCreate = require_capability("product.create")
RequireEdit = require_capability("product.edit")
RequireDeactivate = require_capability("product.deactivate")
RequireInventoryView = require_capability("inventory.view")

router = APIRouter(prefix="/products", tags=["Products"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _audit_ctx(request: Request, principal: Principal) -> Any:
    """Build an AuditContext from the request + principal."""
    from app.audit.service import AuditContext

    return AuditContext(
        user_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request.scope.get("headers", [])),
    )


def _to_response(
    row: dict[str, Any],
    on_hand_quantity: int | None,
    moving_average_unit_cost: float | None,
    inventory_value: float | None,
    low_stock: bool,
    negative_stock_fallback_supported: bool,
    etag: str,
    version: int,
) -> ProductResponse:
    """Convert a DB row dict into a ProductResponse.

    ``row["updated_at"]``/``row["created_at"]`` are passed through as
    datetime objects; Pydantic's response serialization produces the
    canonical ISO 8601 form (``Z`` for UTC) which matches the ETag value
    derived by ``etag_and_version_from_updated_at`` (per
    ``app.util.iso_utc``). Mixing in our own ISO conversion here would
    break the round-trip used by If-Match / 412 detection.
    """

    return ProductResponse(
        id=int(row["id"]),
        name=str(row["name"]),
        code=row.get("code"),
        category_id=row.get("category_id"),
        unit_id=row.get("unit_id"),
        purchase_price=float(row["purchase_price"]),
        selling_price=float(row["selling_price"]),
        low_stock_threshold=row.get("low_stock_threshold"),
        allow_negative_stock=bool(row["allow_negative_stock"]),
        notes=row.get("notes"),
        is_sellable=bool(row["is_sellable"]),
        is_purchasable=bool(row["is_purchasable"]),
        is_producible=bool(row["is_producible"]),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=version,
        on_hand_quantity=on_hand_quantity if on_hand_quantity is not None else 0,
        moving_average_unit_cost=moving_average_unit_cost,
        inventory_value=inventory_value if inventory_value is not None else 0.0,
        low_stock=low_stock,
        negative_stock_fallback_supported=negative_stock_fallback_supported,
        created_by=row.get("created_by"),
        updated_by=row.get("updated_by"),
    )


def _etag_from_row(row: dict[str, Any]) -> tuple[str, int]:
    """Derive (etag, version) from a product row's updated_at."""
    return etag_and_version_from_updated_at(row["updated_at"])


async def _compose_response(
    svc: ProductService,
    row: dict[str, Any],
    etag: str,
    version: int,
) -> ProductResponse:
    """Enrich a raw product row with valuation fields from the service."""
    valuation = await svc.get_valuation(row["id"])
    on_hand = int(valuation["on_hand_quantity"]) if valuation else 0
    inv_value = float(valuation["inventory_value"]) if valuation else 0.0
    # moving_average_unit_cost: use get_moving_average_unit_cost (computed from
    # the product_valuation view columns per app.inventory.valuation).
    avg_cost = await svc.get_moving_average_unit_cost(row["id"])

    # low_stock: computed boolean — on_hand <= threshold when threshold is set.
    threshold = row.get("low_stock_threshold")
    low_stock = threshold is not None and on_hand <= float(threshold)

    # negative_stock_fallback_supported: product flag OR system setting default.
    fallback_supported = bool(row["allow_negative_stock"]) or bool(
        await svc.get_system_setting_bool("default_negative_stock_allowed")
    )

    return _to_response(
        row,
        on_hand_quantity=on_hand,
        moving_average_unit_cost=avg_cost,
        inventory_value=inv_value,
        low_stock=low_stock,
        negative_stock_fallback_supported=fallback_supported,
        etag=etag,
        version=version,
    )


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get(
    "",
    operation_id="listProducts",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def list_products(
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    q: str | None = Query(default=None, max_length=200),
    filter_is_active: bool | None = Query(default=None, alias="filter[is_active]"),
    sort: str = Query(default="id"),
    filter_category_id: int | None = Query(default=None, alias="filter[category_id]"),
    filter_unit_id: int | None = Query(default=None, alias="filter[unit_id]"),
) -> MetaEnvelope[ProductResponse]:
    """List products with pagination, search, and optional filters."""

    svc = ProductService(uow)
    rows, total, _ = await svc.list_products(
        page=page,
        per_page=per_page,
        q=q,
        active_only=filter_is_active if filter_is_active is not None else None,
        sort=sort,
        category_id=filter_category_id,
        unit_id=filter_unit_id,
    )
    data = []
    for r in rows:
        etag, ver = _etag_from_row(r)
        resp = await _compose_response(svc, r, etag, ver)
        data.append(resp)
    return MetaEnvelope[ProductResponse](
        data=data,
        pagination=make_pagination(page, per_page, total),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "",
    operation_id="createProducts",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RequireCreate)],
)
async def create_product(
    request: Request,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProductResponse:
    """Create a new product. Idempotency-Key required."""

    idem = parse_idempotency_key(idempotency_key_header)
    key_str = str(idem.value)

    body = await request.json()
    payload = ProductCreateRequest(**body)

    svc = ProductService(uow)
    row = await svc.create(
        name=payload.name,
        code=payload.code,
        category_id=payload.category_id,
        unit_id=payload.unit_id,
        purchase_price=payload.purchase_price,
        selling_price=payload.selling_price,
        low_stock_threshold=payload.low_stock_threshold,
        allow_negative_stock=payload.allow_negative_stock,
        notes=payload.notes,
        is_sellable=payload.is_sellable,
        is_purchasable=payload.is_purchasable,
        is_producible=payload.is_producible,
        is_active=payload.is_active,
        ctx=_audit_ctx(request, principal),
        idempotency_key=key_str,
        request_body=body,
    )
    etag, version = _etag_from_row(row)
    return await _compose_response(svc, row, etag, version)


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


@router.get(
    "/{id}",
    operation_id="getProducts",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def get_product(
    request: Request,
    id: int,
    uow: UnitOfWork = Depends(get_uow),
) -> ProductResponse:
    """Get a single product by id."""

    svc = ProductService(uow)
    row = await svc.get(id)
    etag, version = _etag_from_row(row)
    return await _compose_response(svc, row, etag, version)


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


@router.patch(
    "/{id}",
    operation_id="updateProducts",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireEdit)],
)
async def update_product(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> ProductResponse:
    """Update a product. ``If-Match`` is REQUIRED (OpenAPI IfMatchRequired).

    ``code`` is immutable — PATCH does not accept it (extra="forbid" in
    ``ProductPatch`` enforces this at validation time).
    """

    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader("If-Match header is required for PATCH /products/{id}.")

    body = await request.json()
    payload = ProductPatch(**body)

    svc = ProductService(uow)
    row = await svc.update(
        id,
        name=payload.name,
        category_id=payload.category_id,
        unit_id=payload.unit_id,
        purchase_price=payload.purchase_price,
        selling_price=payload.selling_price,
        low_stock_threshold=payload.low_stock_threshold,
        allow_negative_stock=payload.allow_negative_stock,
        notes=payload.notes,
        is_sellable=payload.is_sellable,
        is_purchasable=payload.is_purchasable,
        is_producible=payload.is_producible,
        is_active=payload.is_active,
        if_match=etag_parsed,
        ctx=_audit_ctx(request, principal),
    )
    resp_etag, version = _etag_from_row(row)
    return await _compose_response(svc, row, resp_etag, version)


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


@router.post(
    "/{id}/deactivate",
    operation_id="deactivateProducts",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireDeactivate)],
)
async def deactivate_product(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProductResponse:
    """Deactivate a product. Always allowed (preserves history). Idempotency-Key required."""

    idem = parse_idempotency_key(idempotency_key_header)

    body = await request.json()
    payload = DeactivateRequest(**body)

    svc = ProductService(uow)
    row = await svc.deactivate(
        id,
        reason=payload.reason,
        idempotency_key=str(idem.value),
        ctx=_audit_ctx(request, principal),
    )
    etag, version = _etag_from_row(row)
    return await _compose_response(svc, row, etag, version)


# ---------------------------------------------------------------------------
# Delete (hard)
# ---------------------------------------------------------------------------


@router.delete(
    "/{id}",
    operation_id="deleteProducts",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(RequireEdit)],
)
async def delete_product(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> None:
    """Hard delete a product (only if unreferenced). Idempotency-Key required."""

    parse_idempotency_key(idempotency_key_header)

    svc = ProductService(uow)
    await svc.delete(
        id,
        ctx=_audit_ctx(request, principal),
    )


# ---------------------------------------------------------------------------
# B.7 — Read-only sub-resources
#
# GET /products/{id}/price-history  (product.view)
# GET /products/{id}/stock-movements (inventory.view)
# GET /products/{id}/valuation       (inventory.view)
#
# All three are pure reads. No If-Match, no Idempotency-Key, no audit log
# (audit is for state changes only). They share the same pagination
# envelope as the other list endpoints.
# ---------------------------------------------------------------------------


@router.get(
    "/{id}/price-history",
    operation_id="getProductPriceHistory",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def get_product_price_history(
    id: int,
    uow: UnitOfWork = Depends(get_uow),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    q: str | None = Query(default=None, max_length=200),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
) -> MetaEnvelope[ProductPriceHistory]:
    """Price history for a product, newest first.

    Per OpenAPI §15.7 line 2079. Capability: ``product.view``.
    404 if the product does not exist; 401/403 on auth failures.
    """
    svc = ProductService(uow)
    rows, total = await svc.list_price_history(
        id, page=page, per_page=per_page, q=q, from_=from_, to=to
    )
    data = [
        ProductPriceHistory(
            id=int(r["id"]),
            product_id=int(r["product_id"]),
            purchase_price=float(r["purchase_price"]),
            selling_price=float(r["selling_price"]),
            effective_at=r["effective_at"].isoformat()
            if hasattr(r["effective_at"], "isoformat")
            else str(r["effective_at"]),
            changed_by=int(r["changed_by"]),
        )
        for r in rows
    ]
    return MetaEnvelope[ProductPriceHistory](
        data=data,
        pagination=make_pagination(page, per_page, total),
    )


@router.get(
    "/{id}/stock-movements",
    operation_id="getProductStockMovements",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireInventoryView)],
)
async def get_product_stock_movements(
    id: int,
    uow: UnitOfWork = Depends(get_uow),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    q: str | None = Query(default=None, max_length=200),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
) -> MetaEnvelope[StockMovement]:
    """Stock movements for a product (the single inventory ledger).

    Per OpenAPI §15.7 line 2127. The ``stock_movements`` table is
    append-only (DB trigger blocks UPDATE/DELETE); this endpoint only
    reads. Capability: ``inventory.view``. 404 if the product does
    not exist.
    """
    svc = ProductService(uow)
    rows, total = await svc.list_stock_movements(
        id, page=page, per_page=per_page, q=q, from_=from_, to=to
    )

    def _iso(v: Any) -> str:
        if hasattr(v, "isoformat"):
            return str(v.isoformat())
        return str(v)

    data = [
        StockMovement(
            id=int(r["id"]),
            product_id=int(r["product_id"]),
            movement_date=_iso(r["movement_date"]) or "",
            trigger=str(r["trigger"]),
            quantity=float(r["quantity"]),
            unit_cost_at_movement=(
                float(r["unit_cost_at_movement"])
                if r.get("unit_cost_at_movement") is not None
                else None
            ),
            total_cost=(float(r["total_cost"]) if r.get("total_cost") is not None else None),
            reference_type=r.get("reference_type"),
            reference_id=(int(r["reference_id"]) if r.get("reference_id") is not None else None),
            reference_line_id=(
                int(r["reference_line_id"]) if r.get("reference_line_id") is not None else None
            ),
            reversal_of_movement_id=(
                int(r["reversal_of_movement_id"])
                if r.get("reversal_of_movement_id") is not None
                else None
            ),
            reversed_by_movement_id=(
                int(r["reversed_by_movement_id"])
                if r.get("reversed_by_movement_id") is not None
                else None
            ),
            reason=r.get("reason"),
            created_at=_iso(r["created_at"]) or "",
            created_by=int(r["created_by"]),
        )
        for r in rows
    ]
    return MetaEnvelope[StockMovement](
        data=data,
        pagination=make_pagination(page, per_page, total),
    )


@router.get(
    "/{id}/valuation",
    operation_id="getProductValuation",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireInventoryView)],
)
async def get_product_valuation(
    id: int,
    uow: UnitOfWork = Depends(get_uow),
) -> ProductValuation:
    """Per-product valuation snapshot.

    Per OpenAPI §15.7 line 2164. Returns the contract shape
    ``{ product_id, on_hand_quantity, moving_average_unit_cost,
    inventory_value, as_of }``. The ``product_valuation`` DB view
    exposes only ``on_hand_quantity`` / ``inventory_value``;
    ``moving_average_unit_cost`` is derived app-side
    (see ``app.inventory.valuation``). Capability: ``inventory.view``.
    404 if the product does not exist.
    """
    svc = ProductService(uow)
    snap = await svc.get_valuation_snapshot(id)
    as_of = snap["as_of"]
    return ProductValuation(
        product_id=int(snap["product_id"]),
        on_hand_quantity=int(snap["on_hand_quantity"] or 0),
        inventory_value=float(snap["inventory_value"] or 0.0),
        moving_average_unit_cost=snap["moving_average_unit_cost"],
        as_of=as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of),
    )


class ProductImportRequest(BaseModel):
    rows: list[dict[str, Any]]
    commit: bool = False


@router.post("/import", dependencies=[Depends(RequireCreate)])
async def import_products(
    request: ProductImportRequest,
    uow: UnitOfWork = Depends(get_uow),
    principal: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Bulk import products.

    Two-phase:
    - commit=false: validate rows and return errors.
    - commit=true: insert validated rows and return per-row results.
    """
    if not request.commit:
        valid, errors = await validate_import_rows(uow, request.rows)
        return {
            "total_rows": len(request.rows),
            "valid": len(valid),
            "invalid": len(errors),
            "errors": errors,
        }
    else:
        report = await commit_import(
            uow, request.rows, actor_user_id=principal.user_id
        )
        return report


__all__ = ["router"]
