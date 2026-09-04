"""F.4 - Receivables report service.

Owns the orchestration for ``GET /reports/receivables`` per
``openapi.yaml:5536-5561``.

The response is a paginated list of posted, non-cancelled sales
whose open AR balance > 0 in the period:

* ``open_balance = sales.total_amount - Σ(sale_payments.amount)``

(Per the V1.9 accounting model, sales returns on an unpaid sale
reduce AR while sales returns on a paid sale create CRL. Refunds
are CRL outflows. For F.4 v1 the open_balance formula is the
clean GL: ``total - paid`` - fully paid sales are not listed.)

Date semantics: ``sales.sale_date`` in the window.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo
from app.validation.pagination import make_pagination

__all__ = ["ReceivablesReportService"]

_DEFAULT_PAGE: int = 1
_DEFAULT_PER_PAGE: int = 50


class ReceivablesReportService:
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
        total = await sales_repo.count_receivables_report(
            self._uow, from_iso=from_iso, to_iso=to_iso
        )
        rows = await sales_repo.list_receivables_report(
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
            current = await sales_repo.fetch_receivables_aggregates(
                self._uow, from_iso=from_iso, to_iso=to_iso
            )
            previous = await sales_repo.fetch_receivables_aggregates(
                self._uow,
                from_iso=comparison_from,
                to_iso=comparison_to,
            )
            current_d = {
                "receivable_count": float(current["receivable_count"]),
                "total_open": float(current["total_open"]),
            }
            previous_d = {
                "receivable_count": float(previous["receivable_count"]),
                "total_open": float(previous["total_open"]),
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
