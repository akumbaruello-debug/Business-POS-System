"""F.2 — Sales-returns and purchase-returns report services.

Owns the orchestration for the two paginated returns reports
(``GET /reports/sales-returns`` and ``GET /reports/purchase-returns``).

Both reports are **paginated lists** (per OpenAPI ``PagedResponse``).
Pagination parameters are NOT exposed on the wire for F.2 — the
OpenAPI spec only declares ``Period``/``From``/``To``/``CompareTo``
(line 5312-5316, 5338-5342). Server-side default pagination is
``page=1``, ``per_page=50`` to match the existing E.2/E.8 list
convention; the response carries the full ``Pagination`` envelope so
clients can detect the page size.

The row shape reuses the existing header columns from the source
table (no new row model invented — same rule as F.1 purchases):

* ``sales_returns``: id, sale_id, return_date, reason,
  total_selling_price_returned, total_cost_returned, lifecycle_status,
  created_at, created_by, cancellation_date, cancelled_by, version.
* ``purchase_returns``: id, purchase_id, return_date, reason,
  total_value_returned, lifecycle_status, created_at, created_by,
  version.

Comparison (D-7): when ``compare_to`` is supplied, both reports
return a ``comparison`` block with ``return_count`` and
``total_amount`` (the sum of the report's monetary header column)
for current and previous periods.

Date semantics (per F.1 contract decision):

* ``sales-returns`` — period filter on ``sales_returns.return_date``.
* ``purchase-returns`` — period filter on ``purchase_returns.return_date``.
* Inclusivity is ``>= from AND <= to`` (matches E.2/E.8/E.9/F.1).
* Returns whose parent sale/purchase is cancelled are still listed —
  the returns ledger is independent of the parent lifecycle (returns
  can be created against an already-cancelled sale only if the
  cross-row trigger on ``sales_return_lines`` allows it; in practice
  sales are returned BEFORE being cancelled per the documented
  workflow). The report does not invent a join to the parent
  lifecycle — it just lists the returns ledger rows.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo
from app.validation.pagination import make_pagination

__all__ = ["PurchaseReturnsReportService", "SalesReturnsReportService"]


# Default page size when no ``page``/``per_page`` are exposed on the
# wire (F.2 endpoints accept only Period/From/To/CompareTo). Matches
# the F.1 purchases/inventory-movements default.
_DEFAULT_PAGE: int = 1
_DEFAULT_PER_PAGE: int = 50


class SalesReturnsReportService:
    __slots__ = ("_uow",)

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def get_report(
        self,
        *,
        from_iso: Any,
        to_iso: Any,
        comparison_from: Any | None,
        comparison_to: Any | None,
    ) -> dict[str, Any]:
        """Build the full sales-returns report response.

        ``comparison_from``/``comparison_to`` are the resolved
        comparison window bounds. If either is None, no comparison
        block is returned.
        """
        total = await sales_repo.count_sales_returns_report(
            self._uow, from_iso=from_iso, to_iso=to_iso
        )
        rows = await sales_repo.list_sales_returns_report(
            self._uow,
            page=_DEFAULT_PAGE,
            per_page=_DEFAULT_PER_PAGE,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        pagination = make_pagination(
            page=_DEFAULT_PAGE, per_page=_DEFAULT_PER_PAGE, total=total
        )

        comparison: dict[str, Any] | None = None
        if comparison_from is not None and comparison_to is not None:
            current = await sales_repo.fetch_sales_returns_aggregates(
                self._uow, from_iso=from_iso, to_iso=to_iso
            )
            previous = await sales_repo.fetch_sales_returns_aggregates(
                self._uow, from_iso=comparison_from, to_iso=comparison_to
            )
            current_d: dict[str, float] = {
                "return_count": float(current["return_count"]),
                "total_amount": float(current["total_amount"]),
            }
            previous_d: dict[str, float] = {
                "return_count": float(previous["return_count"]),
                "total_amount": float(previous["total_amount"]),
            }
            delta = {k: current_d[k] - previous_d[k] for k in current_d}
            delta_pct: dict[str, float | None] = {}
            for k in current_d:
                if previous_d[k] == 0 and current_d[k] == 0:
                    delta_pct[k] = None
                elif previous_d[k] == 0:
                    delta_pct[k] = 0.0
                else:
                    delta_pct[k] = delta[k] / previous_d[k]
            comparison = {
                "previous_period": {
                    "from": comparison_from.isoformat(),
                    "to": comparison_to.isoformat(),
                },
                "delta": delta,
                "delta_pct": delta_pct,
            }

        return {
            "data": rows,
            "pagination": pagination,
            "comparison": comparison,
        }


class PurchaseReturnsReportService:
    __slots__ = ("_uow",)

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def get_report(
        self,
        *,
        from_iso: Any,
        to_iso: Any,
        comparison_from: Any | None,
        comparison_to: Any | None,
    ) -> dict[str, Any]:
        """Build the full purchase-returns report response.

        ``comparison_from``/``comparison_to`` are the resolved
        comparison window bounds. If either is None, no comparison
        block is returned.
        """
        total = await sales_repo.count_purchase_returns_report(
            self._uow, from_iso=from_iso, to_iso=to_iso
        )
        rows = await sales_repo.list_purchase_returns_report(
            self._uow,
            page=_DEFAULT_PAGE,
            per_page=_DEFAULT_PER_PAGE,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        pagination = make_pagination(
            page=_DEFAULT_PAGE, per_page=_DEFAULT_PER_PAGE, total=total
        )

        comparison: dict[str, Any] | None = None
        if comparison_from is not None and comparison_to is not None:
            current = await sales_repo.fetch_purchase_returns_aggregates(
                self._uow, from_iso=from_iso, to_iso=to_iso
            )
            previous = await sales_repo.fetch_purchase_returns_aggregates(
                self._uow, from_iso=comparison_from, to_iso=comparison_to
            )
            current_d: dict[str, float] = {
                "return_count": float(current["return_count"]),
                "total_amount": float(current["total_amount"]),
            }
            previous_d: dict[str, float] = {
                "return_count": float(previous["return_count"]),
                "total_amount": float(previous["total_amount"]),
            }
            delta = {k: current_d[k] - previous_d[k] for k in current_d}
            delta_pct: dict[str, float | None] = {}
            for k in current_d:
                if previous_d[k] == 0 and current_d[k] == 0:
                    delta_pct[k] = None
                elif previous_d[k] == 0:
                    delta_pct[k] = 0.0
                else:
                    delta_pct[k] = delta[k] / previous_d[k]
            comparison = {
                "previous_period": {
                    "from": comparison_from.isoformat(),
                    "to": comparison_to.isoformat(),
                },
                "delta": delta,
                "delta_pct": delta_pct,
            }

        return {
            "data": rows,
            "pagination": pagination,
            "comparison": comparison,
        }
