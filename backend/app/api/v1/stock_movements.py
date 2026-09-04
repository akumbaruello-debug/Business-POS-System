"""Stock-movement read routes (E.7).

Endpoints match ``openapi.yaml`` §15.2 (lines 4103-4191) exactly:

* ``GET /stock-movements``     — ``listStockMovements`` (paginated)
* ``GET /stock-movements/{id}`` — ``getStockMovement``    (single)

Both are pure reads over the append-only ``stock_movements`` ledger
(``Backend-Architecture §12`` + the immutability triggers in
``schema.sql`` §18.2). No state mutation, no audit, no ETag, no
``Idempotency-Key``, no body.

Capability: ``inventory.view`` (Owner + Staff roles per the
``STAFF_DEFAULT_CAPABILITIES`` union in ``app.authz.caps``).

Filters on the list endpoint (mirrors the OpenAPI ``parameters``
block exactly): ``filter[product_id]``, ``filter[trigger]``,
``filter[reference_type]``, ``filter[reference_id]`` plus the
``Page``/``PerPage``/``Sort``/``Q``/``From``/``To`` standard set
(``Q``/``From``/``To`` are accepted by the route signature for
contract parity but ignored on the SQL side — the OpenAPI list
parameters block does not include them and we honour the
authoritative contract).

The per-product sub-resource
``GET /products/{id}/stock-movements`` (``getProductStockMovements``)
lives in :mod:`app.api.v1.products` and is NOT touched by E.7.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api.deps import get_uow, require_capability
from app.db import UnitOfWork
from app.inventory.service import InventoryService

# Read-only endpoint: the canonical ``inventory.view`` capability.
RequireInventoryView = require_capability("inventory.view")

router = APIRouter(tags=["Stock Movements"])


# ---------------------------------------------------------------------------
# GET /stock-movements
# ---------------------------------------------------------------------------


@router.get(
    "/stock-movements",
    operation_id="listStockMovements",
    summary="List stock movements (read-only). The single inventory ledger.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireInventoryView)],
)
async def list_stock_movements(
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    sort: str = Query("id"),
    product_id: int | None = Query(default=None, alias="filter[product_id]"),
    trigger: str | None = Query(default=None, alias="filter[trigger]"),
    reference_type: str | None = Query(
        default=None, alias="filter[reference_type]"
    ),
    reference_id: int | None = Query(
        default=None, alias="filter[reference_id]"
    ),
) -> JSONResponse:
    """``listStockMovements`` — E.7 list.

    Returns the contract envelope ``{ data, pagination }`` over the
    ``stock_movements`` ledger. Filters are optional and combinable;
    the sort key is whitelisted at the repo layer.
    """
    svc = InventoryService(uow)
    result = await svc.list_stock_movements(
        page=page,
        per_page=per_page,
        sort=sort,
        product_id=product_id,
        trigger=trigger,
        reference_type=reference_type,
        reference_id=reference_id,
    )
    # Re-shape each row through the canonical ``StockMovement`` Pydantic
    # model so the response is the exact contract (Decimal/datetime
    # coercion happens here, not in the repo).
    data = [_row_to_response(r) for r in result["data"]]
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(
            {"data": data, "pagination": result["pagination"]}
        ),
    )


# ---------------------------------------------------------------------------
# GET /stock-movements/{id}
# ---------------------------------------------------------------------------


@router.get(
    "/stock-movements/{id}",
    operation_id="getStockMovement",
    summary="Get a single stock movement (read-only).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireInventoryView)],
)
async def get_stock_movement(
    request: Request,
    id: int,
    uow: UnitOfWork = Depends(get_uow),
) -> JSONResponse:
    """``getStockMovement`` — E.7 single.

    404 if the movement does not exist (canonical ``not_found``
    envelope). The DB never allows UPDATE/DELETE on this table so a
    missing id is the only failure mode.
    """
    svc = InventoryService(uow)
    row = await svc.get_stock_movement(movement_id=id)
    if row is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": {
                    "code": "not_found",
                    "message": f"Stock movement {id} not found.",
                }
            },
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=jsonable_encoder(_row_to_response(row)),
    )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _row_to_response(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a raw ``stock_movements`` row dict into the contract shape.

    Mirrors the B.7 sub-resource path (see ``app.api.v1.products``) so
    both endpoints emit the same response envelope. Pydantic's
    ``StockMovement`` would also work but is reserved for typed
    handlers; here we keep the raw dict + ``jsonable_encoder`` path so
    ``Optional[Decimal]`` / ``Optional[datetime]`` round-trip cleanly.
    """

    def _iso(v: Any) -> str | None:
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            return str(v.isoformat())
        return str(v)

    def _num(v: Any) -> float | None:
        return float(v) if v is not None else None

    def _int(v: Any) -> int | None:
        return int(v) if v is not None else None

    return {
        "id": int(row["id"]),
        "product_id": int(row["product_id"]),
        "movement_date": _iso(row.get("movement_date")) or "",
        "trigger": str(row["trigger"]),
        "quantity": float(row["quantity"]),
        "unit_cost_at_movement": _num(row.get("unit_cost_at_movement")),
        "total_cost": _num(row.get("total_cost")),
        "reference_type": row.get("reference_type"),
        "reference_id": _int(row.get("reference_id")),
        "reference_line_id": _int(row.get("reference_line_id")),
        "reversal_of_movement_id": _int(row.get("reversal_of_movement_id")),
        "reversed_by_movement_id": _int(row.get("reversed_by_movement_id")),
        "reason": row.get("reason"),
        "created_at": _iso(row.get("created_at")) or "",
        "created_by": int(row["created_by"]),
    }


__all__ = ["router"]
