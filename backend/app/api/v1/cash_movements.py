"""API routes for cash movements (E.9 — read-only ledger).

Matches ``openapi.yaml`` §15.2 exactly:

* ``GET  /cash-movements``     — ``listCashMovements`` (paginated, read-only)
* ``GET  /cash-movements/balance`` — ``getCashBalance`` (aggregate, read-only)

Both are **pure reads**: no Idempotency-Key / If-Match / body and no
audit row. Capability: ``finance.view_cash``.

# The ``cash_movements`` table is append-only (schema.sql §10.1 + §18.4);
# writes happen in E.3-E.8 via dedicated helpers. These endpoints never
# mutate the ledger.

Query parameters mirror the OpenAPI ``collection_get_op`` contract:
``page``, ``per_page``, ``sort``, ``q``, ``from``, ``to`` plus the
four ``filter[...]`` params. Sort is whitelisted to ``movement_date``
and ``id`` (ascending/descending) per ``build_openapi.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import get_uow, require_capability
from app.cash_movements.service import CashMovementService
from app.db import UnitOfWork
from app.validation.pagination import MetaEnvelope
from app.validation.schemas import CashMovement

RequireCashView = require_capability("finance.view_cash")

router = APIRouter(tags=["Cash Movements"])


# ---------------------------------------------------------------------------
# GET /cash-movements  — listCashMovements
# ---------------------------------------------------------------------------


@router.get(
    "/cash-movements",
    operation_id="listCashMovements",
    summary="List cash movements (read-only). The single cash ledger.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireCashView)],
)
async def list_cash_movements(
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
    page: int = Query(default=1, ge=1, le=1000),
    per_page: int = Query(default=50, ge=1, le=500),
    sort: str = Query(default="id"),
    q: str | None = Query(default=None, max_length=200),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    filter_trigger: str | None = Query(default=None, alias="filter[trigger]"),
    filter_payment_method_id: int | None = Query(
        default=None, alias="filter[payment_method_id]"
    ),
    filter_reference_type: str | None = Query(
        default=None, alias="filter[reference_type]"
    ),
    filter_reference_id: int | None = Query(
        default=None, alias="filter[reference_id]"
    ),
) -> MetaEnvelope[CashMovement]:
    """List cash movements, paginated.

    The ``cash_movements`` ledger is append-only. Filters are optional
    and combinable; ``sort`` is whitelisted to ``movement_date`` and
    ``id`` (prefix ``-`` for descending). ``from``/``to`` filter on
    ``movement_date``; ``q`` searches ``trigger`` and
    ``reference_type``.
    """
    svc = CashMovementService(uow)
    result = await svc.list_cash_movements(
        page=page,
        per_page=per_page,
        sort=sort,
        trigger=filter_trigger,
        payment_method_id=filter_payment_method_id,
        reference_type=filter_reference_type,
        reference_id=filter_reference_id,
        from_iso=from_iso,
        to_iso=to_iso,
        q=q,
    )
    data = [_row_to_response(r) for r in result["data"]]
    return MetaEnvelope[CashMovement](
        data=data,
        pagination=result["pagination"],
    )


# ---------------------------------------------------------------------------
# GET /cash-movements/balance  — getCashBalance
# ---------------------------------------------------------------------------


@router.get(
    "/cash-movements/balance",
    operation_id="getCashBalance",
    summary="Current cash balance (derived: SUM(cash_movements.amount)).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireCashView)],
)
async def get_cash_balance(
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
) -> JSONResponse:
    """Return the current cash balance.

    Balance is ``SUM(amount)`` over ``cash_movements`` (read-only
    aggregate — no ledger writes). ``as_of`` is the DB clock timestamp
    from the same SELECT.
    """
    svc = CashMovementService(uow)
    result = await svc.get_cash_balance()
    body: dict[str, Any] = {
        "balance": result["balance"],
        "as_of": result["as_of"].isoformat(),
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=body,
    )


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------


def _to_response(row: dict[str, Any]) -> CashMovement:
    """Coerce a raw ``cash_movements`` row into the contract model."""
    return CashMovement(
        id=int(row["id"]),
        movement_date=row["movement_date"],
        amount=float(row["amount"]),
        direction=str(row["direction"]),
        trigger=str(row["trigger"]),
        payment_method_id=int(row["payment_method_id"]),
        reference_type=row.get("reference_type"),
        reference_id=int(row["reference_id"]) if row.get("reference_id") is not None else None,
        created_at=row["created_at"],
        created_by=int(row["created_by"]),
    )


def _row_to_response(row: dict[str, Any]) -> dict[str, Any]:
    """Serialize a row dict to the JSON-compatible ``CashMovement`` shape.

    Used for the ``listCashMovements`` response so each item matches
    the OpenAPI ``CashMovement`` schema exactly.
    """
    return _to_response(row).model_dump(mode="json")


__all__ = ["router"]
