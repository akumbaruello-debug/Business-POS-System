"""F.4 - Profit & Loss report service.

Owns the orchestration for ``GET /reports/p-and-l`` per
``openapi.yaml:9076-9095`` (ProfitLossResponse).

The response is an aggregate (not a paginated list):

* ``data``: ``{revenue, cogs, gross_profit, other_income,
  operating_expenses, net_profit}`` - exactly the six keys
  declared in the OpenAPI ``ProfitLossResponse.data`` schema.
* ``comparison``: ``{previous_period, delta, delta_pct}`` - only
  when ``compare_to`` is supplied (D-7).

Net profit is ``gross_profit + other_income - operating_expenses``,
computed at the service layer (the SQL returns the five components
and the net is derived). This keeps the SQL single-purpose.

Date semantics (per F.1 sales + F.3 manual aggregates):

* Revenue / COGS / Gross: ``sales.sale_date`` in the window
  (posted, non-cancelled; posted sales returns deducted).
* Other income / Operating expenses:
  ``manual_finance_entries.entry_date`` in the window
  (posted lifecycle only - cancelled manual entries create a
  reversing cash_movement that lives in the cash-flow report,
  so counting them here would double-count per F.3 decision).
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo

__all__ = ["ProfitAndLossReportService"]


class ProfitAndLossReportService:
    __slots__ = ("_uow",)

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def get_report(
        self,
        *,
        primary_from: Any,
        primary_to: Any,
        comparison_from: Any | None,
        comparison_to: Any | None,
    ) -> dict[str, Any]:
        current = await sales_repo.fetch_pnl_aggregates(
            self._uow, from_iso=primary_from, to_iso=primary_to
        )
        current_d: dict[str, float] = {
            "revenue": float(current["revenue"]),
            "cogs": float(current["cogs"]),
            "gross_profit": float(current["gross_profit"]),
            "other_income": float(current["other_income"]),
            "operating_expenses": float(current["operating_expenses"]),
            "net_profit": float(current["net_profit"]),
        }

        comparison: dict[str, Any] | None = None
        if comparison_from is not None and comparison_to is not None:
            previous = await sales_repo.fetch_pnl_aggregates(
                self._uow,
                from_iso=comparison_from,
                to_iso=comparison_to,
            )
            previous_d: dict[str, float] = {
                "revenue": float(previous["revenue"]),
                "cogs": float(previous["cogs"]),
                "gross_profit": float(previous["gross_profit"]),
                "other_income": float(previous["other_income"]),
                "operating_expenses": float(previous["operating_expenses"]),
                "net_profit": float(previous["net_profit"]),
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

        return {"data": current_d, "comparison": comparison}
