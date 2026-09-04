"""F.1 — Inventory movements report service.

Reuses the E.7 repository helpers (``app.inventory.repo.count_stock_movements``
+ ``list_stock_movement_rows``) extended in F.1 to accept
``from_iso``/``to_iso``. The row shape is the E.7 public response
format (``app.api.v1.stock_movements._row_to_response``) so the F.1
report emits the exact same contract as ``GET /stock-movements`` —
no parallel row model invented.

The sort whitelist is the E.7 subset (``id``, ``movement_date``,
``product_id``, ``created_at``) plus its default. ``q`` searches the
two text columns on the ledger: ``trigger`` and ``reference_type``
(matches the E.7 service contract; documented there).
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.inventory.repo import (
    count_stock_movements,
    list_stock_movement_rows,
)
from app.validation.pagination import make_pagination

__all__ = ["InventoryMovementsReportService"]


class InventoryMovementsReportService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        # Imported lazily to keep the reports package free of the
        # route module at import time (the route layer is a thin
        # wrapper around this service). The function is the canonical
        # E.7 row shaper — using it here keeps the F.1 response
        # byte-identical to ``GET /stock-movements``.
        from app.api.v1.stock_movements import _row_to_response

        self._row_to_response = _row_to_response

    async def get_report(
        self,
        *,
        page: int,
        per_page: int,
        sort: str,
        from_iso: str | None,
        to_iso: str | None,
        product_id: int | None,
        trigger: str | None,
        reference_type: str | None,
        reference_id: int | None,
        q: str | None,
    ) -> dict[str, Any]:
        """Paginated list of stock movements in the period.

        ``from_iso``/``to_iso`` are pre-formatted ISO strings (or
        ``None``). The repo applies the inclusive bound on
        ``sm.movement_date``. ``q`` does a case-insensitive substring
        search on ``trigger`` and ``reference_type``.
        """
        total = await count_stock_movements(
            self._uow,
            product_id=product_id,
            trigger=trigger,
            reference_type=reference_type,
            reference_id=reference_id,
            from_iso=from_iso,
            to_iso=to_iso,
            q=q,
        )
        rows = await list_stock_movement_rows(
            self._uow,
            page=page,
            per_page=per_page,
            sort=sort,
            product_id=product_id,
            trigger=trigger,
            reference_type=reference_type,
            reference_id=reference_id,
            from_iso=from_iso,
            to_iso=to_iso,
            q=q,
        )
        data = [self._row_to_response(r) for r in rows]
        pag = make_pagination(page=page, per_page=per_page, total=total)
        return {"data": data, "pagination": pag}
