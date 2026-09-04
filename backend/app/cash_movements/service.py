"""Cash-movements service (E.9 — read-only).

Thin orchestration layer between the route handlers and the SQL
helpers in :mod:`app.cash_movements.repo`. E.9 has no writes and no
business rules beyond what the DB triggers enforce
(``fn_cash_movements_immutable`` + ``fn_cash_movements_balance_check``),
so the service is intentionally minimal — it shapes the response
shapes the route layer will hand to Pydantic and owns the
``MetaEnvelope`` / pagination construction.
"""

from __future__ import annotations

from typing import Any

from app.cash_movements.repo import (
    count_cash_movements,
    list_cash_movement_rows,
    sum_cash_balance,
)
from app.db import UnitOfWork
from app.validation.pagination import make_pagination

__all__ = ["CashMovementService"]


class CashMovementService:
    __slots__ = ("_uow",)

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # -------------------------------------------------------------------------
    # listCashMovements
    # -------------------------------------------------------------------------

    async def list_cash_movements(
        self,
        *,
        page: int,
        per_page: int,
        sort: str,
        trigger: str | None,
        payment_method_id: int | None,
        reference_type: str | None,
        reference_id: int | None,
        from_iso: str | None,
        to_iso: str | None,
        q: str | None,
    ) -> dict[str, Any]:
        """Paginated list with filters + sort.

        Returns the canonical ``{ data, pagination }`` envelope. Each
        row is a raw ``cash_movements`` dict (datetime / Decimal left
        as-is) — the route layer coerces them through the ``CashMovement``
        Pydantic model.
        """
        total = await count_cash_movements(
            self._uow,
            trigger=trigger,
            payment_method_id=payment_method_id,
            reference_type=reference_type,
            reference_id=reference_id,
            from_iso=from_iso,
            to_iso=to_iso,
            q=q,
        )
        rows = await list_cash_movement_rows(
            self._uow,
            page=page,
            per_page=per_page,
            sort=sort,
            trigger=trigger,
            payment_method_id=payment_method_id,
            reference_type=reference_type,
            reference_id=reference_id,
            from_iso=from_iso,
            to_iso=to_iso,
            q=q,
        )
        return {
            "data": rows,
            "pagination": make_pagination(page=page, per_page=per_page, total=total),
        }

    # -------------------------------------------------------------------------
    # getCashBalance
    # -------------------------------------------------------------------------

    async def get_cash_balance(self) -> dict[str, Any]:
        """Return ``{ balance, as_of }`` over the cash ledger."""
        balance, as_of = await sum_cash_balance(self._uow)
        return {"balance": balance, "as_of": as_of}
