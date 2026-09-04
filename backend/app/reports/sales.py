"""F.1 — Sales report service.

Owns the orchestration between the route layer and the SQL helpers
in :mod:`app.reports.sales_repo`. Computes the canonical
``SalesReportResponse`` (data + optional comparison) for a given
``[from, to]`` window.

Comparison semantics (per API-Architecture §12.1 line 1092-1103):

* ``comparison`` is present only when the route layer supplied
  ``compare_to``. The service does NOT decide whether to include
  it — it always returns ``comparison=None`` if the comparison
  window is None.
* ``delta[k] = current[k] - previous[k]`` for every key in the
  primary aggregate.
* ``delta_pct[k]``:
  - ``None`` when both current and previous are 0 (no movement to
    measure — avoid divide-by-zero silently).
  - ``0.0`` when previous=0 but current!=0 (delta is reported as
    100% growth from a zero base — matches the audit decision).
  - ``delta[k] / previous[k]`` otherwise.

These are pure-computation rules. They live in the service so the
SQL stays single-purpose.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo

__all__ = ["SalesReportService"]


class SalesReportService:
    __slots__ = ("_uow",)

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # ------------------------------------------------------------------
    # Primary + comparison
    # ------------------------------------------------------------------

    async def get_report(
        self,
        *,
        primary_from: Any,
        primary_to: Any,
        comparison_from: Any | None,
        comparison_to: Any | None,
    ) -> dict[str, Any]:
        """Build the full sales report response.

        ``comparison_from``/``comparison_to`` are the resolved
        comparison window bounds (already computed by the period
        resolver). If either is None, no comparison block is
        returned.
        """
        current = await sales_repo.fetch_sales_aggregates(
            self._uow, from_iso=primary_from, to_iso=primary_to
        )
        current_dict: dict[str, float] = {
            "revenue": float(current["revenue"]),
            "cogs": float(current["cogs"]),
            "gross_profit": float(current["gross_profit"]),
            "sales_count": float(current["sales_count"]),
        }
        average_ticket = (
            current["revenue"] / current["sales_count"]
            if current["sales_count"] > 0
            else 0.0
        )

        data = {
            "revenue": current["revenue"],
            "cogs": current["cogs"],
            "gross_profit": current["gross_profit"],
            "sales_count": current["sales_count"],
            "average_ticket": average_ticket,
        }

        comparison: dict[str, Any] | None = None
        if comparison_from is not None and comparison_to is not None:
            previous = await sales_repo.fetch_sales_aggregates(
                self._uow,
                from_iso=comparison_from,
                to_iso=comparison_to,
            )
            previous_dict: dict[str, float] = {
                "revenue": float(previous["revenue"]),
                "cogs": float(previous["cogs"]),
                "gross_profit": float(previous["gross_profit"]),
                "sales_count": float(previous["sales_count"]),
            }
            delta = {
                k: current_dict[k] - previous_dict[k] for k in current_dict
            }
            delta_pct: dict[str, float | None] = {}
            for k in current_dict:
                if previous_dict[k] == 0 and current_dict[k] == 0:
                    delta_pct[k] = None
                elif previous_dict[k] == 0:
                    delta_pct[k] = 0.0
                else:
                    delta_pct[k] = delta[k] / previous_dict[k]
            comparison = {
                "previous_period": {
                    "from": comparison_from.isoformat(),
                    "to": comparison_to.isoformat(),
                },
                "delta": delta,
                "delta_pct": delta_pct,
            }

        return {"data": data, "comparison": comparison}
