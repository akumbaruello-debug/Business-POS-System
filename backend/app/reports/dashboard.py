"""F.5 - Dashboard service (orchestrator).

Owns ``GET /dashboard`` and ``GET /dashboard/inventory``. The
service composes the repo helpers in :mod:`app.reports.dashboard_repo`
plus the F.4 P&L aggregate (for ``net_profit``) and the F.1 inventory
report (for ``inventory_value`` and ``low_stock_count``).

Per the F.4 decision (locked by the F.4 status report and the V1.9
accounting event matrix), all financial KPIs derive from the same
authoritative ledgers as the corresponding reports. The dashboard
introduces **no new accounting formulas** - it is a thin consumer of
the F.1/F.4 report machinery.

Comparison envelope (D-7) follows the F.4 PeriodComparison shape:
``{previous_period: {from, to}, delta: {key: float},
delta_pct: {key: float|null}}``. ``delta_pct[k] = delta[k] /
previous[k]`` when ``previous[k] != 0``; ``0.0`` when ``previous==0``
and ``current!=0``; ``None`` when both are 0.

Inventory snapshot (``GET /dashboard/inventory``) reuses
:func:`app.reports.inventory_repo.fetch_inventory_report` directly -
the inventory endpoint takes only the ``Period`` parameter for OpenAPI
contract parity but ignores the window (point-in-time snapshot).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import UnitOfWork
from app.reports import dashboard_repo, inventory_repo, sales_repo

__all__ = ["DashboardService"]

# Bucket selection per API-Architecture 12.2 line 1109 + dashboard UX:
# daily for windows <= 31 days (today, this_week, this_month, and
# monthly custom ranges so the monthly chart shows a useful daily
# trend); weekly for windows > 31 days (e.g. this_year, multi-month).
_DAILY_BUCKET_MAX_DAYS = 31
_BEST_SELLERS_LIMIT = 10
_EXPENSE_BREAKDOWN_LIMIT = 10


def _bucket_for_window(primary_from: datetime, primary_to: datetime) -> str:
    length_days = (primary_to - primary_from).total_seconds() / 86400.0
    return "day" if length_days <= _DAILY_BUCKET_MAX_DAYS else "week"


def _comparison_envelope(
    *,
    current: dict[str, float],
    previous: dict[str, float],
    comparison_from: datetime,
    comparison_to: datetime,
) -> dict[str, Any]:
    """Build a PeriodComparison dict matching the F.4 shape.

    ``delta[k] = current[k] - previous[k]``. ``delta_pct`` follows the
    F.4 rule: ``0.0`` when previous is 0 and current is not; ``None``
    when both are 0; otherwise ``delta[k] / previous[k]``.
    """
    delta = {k: current[k] - previous[k] for k in current}
    delta_pct: dict[str, float | None] = {}
    for k in current:
        prev = previous[k]
        curr = current[k]
        if prev == 0 and curr == 0:
            delta_pct[k] = None
        elif prev == 0:
            delta_pct[k] = 0.0
        else:
            delta_pct[k] = delta[k] / prev
    return {
        "previous_period": {
            "from": comparison_from.isoformat(),
            "to": comparison_to.isoformat(),
        },
        "delta": delta,
        "delta_pct": delta_pct,
    }


class DashboardService:
    """Thin orchestrator for the dashboard endpoints.

    Constructor takes a :class:`UnitOfWork`; the route layer is
    responsible for the HTTP framing (period resolution, comparison
    resolution, capability gating, Decimal/datetime serialization).
    """

    __slots__ = ("_uow",)

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def get_dashboard(
        self,
        *,
        primary_from: datetime,
        primary_to: datetime,
        comparison_from: datetime | None,
        comparison_to: datetime | None,
        as_of: datetime,
    ) -> dict[str, Any]:
        """Build the full ``DashboardResponse`` payload.

        Returns a dict with the exact ``{kpis, charts, comparison,
        as_of}`` shape that ``DashboardResponse`` validates against.
        The ``as_of`` value is the server timestamp the snapshot was
        taken (UTC, tz-aware) - it is supplied by the route layer so
        the snapshot's age is unambiguous across the report's several
        DB round-trips.
        """
        # 1) KPI aggregates.
        sales = await dashboard_repo.fetch_total_sales(
            self._uow, from_iso=primary_from, to_iso=primary_to
        )
        purchases = await dashboard_repo.fetch_total_purchases(
            self._uow, from_iso=primary_from, to_iso=primary_to
        )
        pnl = await sales_repo.fetch_pnl_aggregates(
            self._uow, from_iso=primary_from, to_iso=primary_to
        )
        cash_on_hand = await dashboard_repo.fetch_cash_on_hand(self._uow)
        inventory_snapshot = await inventory_repo.fetch_inventory_report(self._uow)

        # 2) Chart aggregates.
        bucket = _bucket_for_window(primary_from, primary_to)
        sales_trend = await dashboard_repo.fetch_sales_trend(
            self._uow,
            from_iso=primary_from,
            to_iso=primary_to,
            bucket=bucket,
        )
        best_sellers = await dashboard_repo.fetch_best_sellers(
            self._uow, from_iso=primary_from, to_iso=primary_to
        )
        expense_breakdown = await dashboard_repo.fetch_expense_breakdown(
            self._uow, from_iso=primary_from, to_iso=primary_to
        )

        # 3) Compare block (D-7) when supplied.
        comparison: dict[str, Any] | None = None
        if comparison_from is not None and comparison_to is not None:
            prev_sales = await dashboard_repo.fetch_total_sales(
                self._uow, from_iso=comparison_from, to_iso=comparison_to
            )
            prev_purchases = await dashboard_repo.fetch_total_purchases(
                self._uow, from_iso=comparison_from, to_iso=comparison_to
            )
            prev_pnl = await sales_repo.fetch_pnl_aggregates(
                self._uow, from_iso=comparison_from, to_iso=comparison_to
            )
            current_kpis = {
                "total_sales": float(sales["total_sales"]),
                "total_purchases": float(purchases["total_purchases"]),
                "net_profit": float(pnl["net_profit"]),
            }
            previous_kpis = {
                "total_sales": float(prev_sales["total_sales"]),
                "total_purchases": float(prev_purchases["total_purchases"]),
                "net_profit": float(prev_pnl["net_profit"]),
            }
            comparison = _comparison_envelope(
                current=current_kpis,
                previous=previous_kpis,
                comparison_from=comparison_from,
                comparison_to=comparison_to,
            )

        return {
            "kpis": {
                "total_sales": float(sales["total_sales"]),
                "total_purchases": float(purchases["total_purchases"]),
                # Per openapi.yaml 9142-9149: nullable forward-compat.
                # The current implementation returns the value (owner
                # has both caps); the route layer enforces the 403 for
                # staff. ``null`` would only surface if a future
                # cross-capability gate suppressed one of these.
                "net_profit": float(pnl["net_profit"]),
                "cash_on_hand": float(cash_on_hand),
                "inventory_value": float(inventory_snapshot["total_inventory_value"]),
                "low_stock_count": int(inventory_snapshot["low_stock_count"]),
            },
            "charts": {
                "sales_trend": sales_trend,
                "best_sellers": best_sellers[:_BEST_SELLERS_LIMIT],
                "expense_breakdown": expense_breakdown[:_EXPENSE_BREAKDOWN_LIMIT],
            },
            "comparison": comparison,
            "as_of": as_of,
        }

    async def get_inventory_snapshot(self) -> dict[str, Any]:
        """Build the ``InventoryReportResponse`` payload for the inventory endpoint.

        Direct passthrough to the F.1 inventory report (the OAS line
        5684 contract - period params accepted but ignored, per the
        audit decision recorded in :mod:`app.reports.inventory_repo`).
        """
        return await inventory_repo.fetch_inventory_report(self._uow)
