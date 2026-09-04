"""F.1 — Purchases report service.

Owns the orchestration for the paginated purchases report + the
optional comparison block (per D-7). The list itself reuses the
E.2-style purchase row shape (id, reference_no, supplier_id,
purchase_date, lifecycle_status, posted_at, posted_by,
cancellation_date, cancelled_by, notes, created_at, updated_at,
created_by, version) — no new row model invented.

Comparison is a count + Σ purchase_lines.line_total, used to show
the period's totals alongside the previous period.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo
from app.validation.pagination import make_pagination

__all__ = ["PurchasesReportService"]


class PurchasesReportService:
    __slots__ = ("_uow",)

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def get_report(
        self,
        *,
        page: int,
        per_page: int,
        sort: str,
        from_iso: Any,
        to_iso: Any,
        supplier_id: int | None,
        lifecycle_status: str | None,
        posted_by: int | None,
        q: str | None,
        comparison_from: Any | None,
        comparison_to: Any | None,
    ) -> dict[str, Any]:
        """Build the full purchases report response.

        ``comparison_from``/``comparison_to`` are the resolved
        comparison window bounds. If either is None, no comparison
        block is returned.
        """
        total = await sales_repo.count_purchases_report(
            self._uow,
            from_iso=from_iso,
            to_iso=to_iso,
            supplier_id=supplier_id,
            lifecycle_status=lifecycle_status,
            posted_by=posted_by,
            q=q,
        )
        rows = await sales_repo.list_purchases_report(
            self._uow,
            page=page,
            per_page=per_page,
            sort=sort,
            from_iso=from_iso,
            to_iso=to_iso,
            supplier_id=supplier_id,
            lifecycle_status=lifecycle_status,
            posted_by=posted_by,
            q=q,
        )
        pagination = make_pagination(page=page, per_page=per_page, total=total)

        comparison: dict[str, Any] | None = None
        if comparison_from is not None and comparison_to is not None:
            current = await sales_repo.fetch_purchases_aggregates(
                self._uow, from_iso=from_iso, to_iso=to_iso
            )
            previous = await sales_repo.fetch_purchases_aggregates(
                self._uow, from_iso=comparison_from, to_iso=comparison_to
            )
            current_d = {
                "purchase_count": float(current["purchase_count"]),
                "total_amount": float(current["total_amount"]),
            }
            previous_d = {
                "purchase_count": float(previous["purchase_count"]),
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
