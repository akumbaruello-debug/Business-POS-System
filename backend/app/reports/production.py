"""F.2 — Production-runs report service.

Owns the orchestration for ``GET /reports/production``. The report is
a **paginated list** of ``production_runs`` header rows (per OpenAPI
``PagedResponse`` at line 5354-5379).

Pagination parameters are NOT exposed on the wire for F.2 — the
OpenAPI spec only declares ``Period``/``From``/``To``/``CompareTo``.
Server-side default pagination is ``page=1``, ``per_page=50`` to match
the F.2 returns reports (and F.1 purchases/inventory-movements).

The row shape reuses the existing ``production_runs`` header columns
(no new row model invented — same rule as F.1 purchases and F.2
returns): id, run_date, output_product_id, output_quantity,
finished_unit_cost, total_raw_cost, total_overhead_cost,
lifecycle_status, posted_at, posted_by, cancellation_date,
cancellation_reason, cancelled_by, notes, created_at, updated_at,
created_by, version.

Date semantics (per F.1 contract decision):

* Period filter on ``production_runs.run_date``.
* Inclusivity is ``>= from AND <= to`` (matches E.2/E.8/E.9/F.1/F.2).
* Both posted and cancelled runs are listed — the lifecycle column
  is preserved so clients can filter client-side. ``production_runs``
  has four lifecycle states (draft, posted, completed, cancelled)
  per ``schema.sql`` §9.1 — the report does not invent a filter.

Comparison (D-7): when ``compare_to`` is supplied, the response
includes a ``comparison`` block with ``run_count`` and
``total_amount`` (``total_raw_cost + total_overhead_cost`` — the
canonical run cost) for current and previous periods.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo
from app.validation.pagination import make_pagination

__all__ = ["ProductionReportService"]

_DEFAULT_PAGE: int = 1
_DEFAULT_PER_PAGE: int = 50


class ProductionReportService:
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
        """Build the full production report response.

        ``comparison_from``/``comparison_to`` are the resolved
        comparison window bounds. If either is None, no comparison
        block is returned.
        """
        total = await sales_repo.count_production_report(
            self._uow, from_iso=from_iso, to_iso=to_iso
        )
        rows = await sales_repo.list_production_report(
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
            current = await sales_repo.fetch_production_aggregates(
                self._uow, from_iso=from_iso, to_iso=to_iso
            )
            previous = await sales_repo.fetch_production_aggregates(
                self._uow, from_iso=comparison_from, to_iso=comparison_to
            )
            current_d: dict[str, float] = {
                "run_count": float(current["run_count"]),
                "total_amount": float(current["total_amount"]),
            }
            previous_d: dict[str, float] = {
                "run_count": float(previous["run_count"]),
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
