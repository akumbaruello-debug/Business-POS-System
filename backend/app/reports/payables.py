"""F.4 - Payables report service.

Owns the orchestration for ``GET /reports/payables`` per
``openapi.yaml:5562-5587``.

The response is a paginated list of posted, non-cancelled
purchases whose open AP balance > 0 in the period:

* ``open_balance = (Σ purchase_lines.line_total +
   purchase_shipping.amount) - Σ(purchase_payments.amount)``

(Per the V1.9 accounting model, purchase returns on an unpaid
purchase reduce AP while purchase returns on a paid purchase
create Supplier Receivable. Supplier repayments are a separate
Supplier Receivable flow. For F.4 v1 the open_balance formula
is the clean GL: ``total - paid`` - fully paid purchases are
not listed.)

Date semantics: ``purchases.purchase_date`` in the window.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo
from app.validation.pagination import make_pagination

__all__ = ["PayablesReportService"]

_DEFAULT_PAGE: int = 1
_DEFAULT_PER_PAGE: int = 50


class PayablesReportService:
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
        total = await sales_repo.count_payables_report(
            self._uow, from_iso=from_iso, to_iso=to_iso
        )
        rows = await sales_repo.list_payables_report(
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
            current = await sales_repo.fetch_payables_aggregates(
                self._uow, from_iso=from_iso, to_iso=to_iso
            )
            previous = await sales_repo.fetch_payables_aggregates(
                self._uow,
                from_iso=comparison_from,
                to_iso=comparison_to,
            )
            current_d = {
                "payable_count": float(current["payable_count"]),
                "total_open": float(current["total_open"]),
            }
            previous_d = {
                "payable_count": float(previous["payable_count"]),
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
