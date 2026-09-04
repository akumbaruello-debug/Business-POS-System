"""F.3 — Manual income/expense report services.

Owns the orchestration for ``GET /reports/manual-income`` and
``GET /reports/manual-expense``.

Both reports are **paginated lists** of ``manual_finance_entries``
header rows joined to ``financial_categories`` (so the
``entry_type`` discriminator is available without a second round-trip
for client-side filtering).

Pagination parameters are NOT exposed on the wire for F.3 — the
OpenAPI spec only declares ``Period``/``From``/``To``/``CompareTo``
(line 5390-5394, 5416-5420). Server-side default pagination is
``page=1``, ``per_page=50`` (locked from F.1/F.2).

The row shape reuses the existing source-table columns (no new row
model invented — same rule as F.1/F.2):

* ``manual_finance_entries`` (with ``financial_categories`` join):
  id, category_id, category_code, category_name, entry_type,
  entry_date, amount, payment_method_id, notes, lifecycle_status,
  cancellation_date, cancellation_reason, cancelled_by, created_at,
  created_by, version.

Date semantics (per F.1/F.2 contract decision):

* Period filter on ``manual_finance_entries.entry_date``.
* Inclusivity is ``>= from AND <= to``.
* Discriminator: ``financial_categories.entry_type = 'income'`` for
  the income report, ``'expense'`` for the expense report.

Lifecycle (per F.2 pattern): the list surfaces every row regardless
of lifecycle; the comparison aggregate EXCLUDES cancelled entries
(matches the production-runs aggregate decision — cancelled manual
entries create a reversing ``cash_movement`` that lives in the
cash-flow report, so counting them here would double-count the
reversal).

Comparison (D-7): when ``compare_to`` is supplied, both reports
return a ``comparison`` block with ``entry_count`` and
``total_amount`` for current and previous periods.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo
from app.validation.pagination import make_pagination

__all__ = ["ManualExpenseReportService", "ManualIncomeReportService"]

_DEFAULT_PAGE: int = 1
_DEFAULT_PER_PAGE: int = 50


class _ManualReportBase:
    """Shared orchestration for income/expense reports.

    The only difference between the two reports is the
    ``entry_type`` discriminator. The SQL helpers in
    :mod:`app.reports.sales_repo` are parameterised on that flag.
    """

    __slots__ = ("_entry_type", "_uow")

    _SERVICE_NAME: str  # set by subclasses

    def __init__(self, uow: UnitOfWork, entry_type: str) -> None:
        self._uow = uow
        self._entry_type = entry_type

    async def get_report(
        self,
        *,
        from_iso: Any,
        to_iso: Any,
        comparison_from: Any | None,
        comparison_to: Any | None,
    ) -> dict[str, Any]:
        total = await sales_repo.count_manual_report(
            self._uow,
            entry_type=self._entry_type,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        rows = await sales_repo.list_manual_report(
            self._uow,
            entry_type=self._entry_type,
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
            current = await sales_repo.fetch_manual_aggregates(
                self._uow,
                entry_type=self._entry_type,
                from_iso=from_iso,
                to_iso=to_iso,
            )
            previous = await sales_repo.fetch_manual_aggregates(
                self._uow,
                entry_type=self._entry_type,
                from_iso=comparison_from,
                to_iso=comparison_to,
            )
            current_d: dict[str, float] = {
                "entry_count": float(current["entry_count"]),
                "total_amount": float(current["total_amount"]),
            }
            previous_d: dict[str, float] = {
                "entry_count": float(previous["entry_count"]),
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


class ManualIncomeReportService(_ManualReportBase):
    __slots__ = ()

    _SERVICE_NAME = "manual-income"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow, entry_type="income")


class ManualExpenseReportService(_ManualReportBase):
    __slots__ = ()

    _SERVICE_NAME = "manual-expense"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow, entry_type="expense")
