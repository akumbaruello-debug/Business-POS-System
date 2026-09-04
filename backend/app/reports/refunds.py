"""F.3 — Refunds report service.

Owns the orchestration for ``GET /reports/refunds``. The report is
a **paginated list** of ``refunds`` header rows (per OpenAPI
``PagedResponse`` at line 5432-5457).

Pagination parameters are NOT exposed on the wire for F.3 — the
OpenAPI spec only declares ``Period``/``From``/``To``/``CompareTo``.
Server-side default pagination is ``page=1``, ``per_page=50`` (locked
from F.1/F.2).

The row shape reuses the existing ``refunds`` header columns (no new
row model invented — same rule as F.1/F.2): id, sale_id, amount,
payment_method_id, refund_date, reason, refundable_amount_snapshot,
created_at, created_by.

Date semantics:

* Period filter on ``refunds.refund_date``.
* Inclusivity is ``>= from AND <= to``.

Lifecycle: ``refunds`` has NO lifecycle column — refunds are
append-only (cross-row trigger enforces INV-04 ceiling against the
refundable_amount_snapshot at insert time). All rows in the period
are listed and aggregated.

Comparison (D-7): when ``compare_to`` is supplied, the response
includes a ``comparison`` block with ``refund_count`` and
``total_amount`` (``SUM(amount)``) for current and previous periods.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo
from app.validation.pagination import make_pagination

__all__ = ["RefundsReportService"]

_DEFAULT_PAGE: int = 1
_DEFAULT_PER_PAGE: int = 50


class RefundsReportService:
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
        """Build the full refunds report response."""
        total = await sales_repo.count_refunds_report(
            self._uow, from_iso=from_iso, to_iso=to_iso
        )
        rows = await sales_repo.list_refunds_report(
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
            current = await sales_repo.fetch_refunds_aggregates(
                self._uow, from_iso=from_iso, to_iso=to_iso
            )
            previous = await sales_repo.fetch_refunds_aggregates(
                self._uow, from_iso=comparison_from, to_iso=comparison_to
            )
            current_d: dict[str, float] = {
                "refund_count": float(current["refund_count"]),
                "total_amount": float(current["total_amount"]),
            }
            previous_d: dict[str, float] = {
                "refund_count": float(previous["refund_count"]),
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
