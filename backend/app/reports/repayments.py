"""F.3 — Supplier-repayments report service.

Owns the orchestration for ``GET /reports/supplier-repayments``. The
report is a **paginated list** of ``supplier_repayments`` header
rows (per OpenAPI ``PagedResponse`` at line 5458-5483).

Note: the F.3 plan entry uses the name "repayments" (Backend-
Implementation-Plan §6 line 685) but the OpenAPI contract uses
``/reports/supplier-repayments`` with operationId
``supplierRepaymentsReport``. The OpenAPI is the authoritative
contract — this service implements the OpenAPI path.

Pagination parameters are NOT exposed on the wire for F.3 — the
OpenAPI spec only declares ``Period``/``From``/``To``/``CompareTo``.
Server-side default pagination is ``page=1``, ``per_page=50`` (locked
from F.1/F.2).

The row shape reuses the existing ``supplier_repayments`` header
columns (no new row model invented — same rule as F.1/F.2): id,
purchase_id, amount, received_amount, payment_method_id,
repayment_date, reason, refundable_amount_snapshot, created_at,
created_by.

Date semantics:

* Period filter on ``supplier_repayments.repayment_date``.
* Inclusivity is ``>= from AND <= to``.

Lifecycle: ``supplier_repayments`` has NO lifecycle column — it
is a stateful obligation (`amount` = total obligation,
`received_amount` = cumulative cash received, INV-04). All rows
in the period are listed and aggregated.

Comparison (D-7): when ``compare_to`` is supplied, the response
includes a ``comparison`` block with ``repayment_count`` and
``total_received`` (``SUM(received_amount)`` — the actual cash
received in the period, not the obligation `amount`) for current
and previous periods.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo
from app.validation.pagination import make_pagination

__all__ = ["SupplierRepaymentsReportService"]

_DEFAULT_PAGE: int = 1
_DEFAULT_PER_PAGE: int = 50


class SupplierRepaymentsReportService:
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
        """Build the full supplier-repayments report response."""
        total = await sales_repo.count_supplier_repayments_report(
            self._uow, from_iso=from_iso, to_iso=to_iso
        )
        rows = await sales_repo.list_supplier_repayments_report(
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
            current = await sales_repo.fetch_supplier_repayments_aggregates(
                self._uow, from_iso=from_iso, to_iso=to_iso
            )
            previous = await sales_repo.fetch_supplier_repayments_aggregates(
                self._uow, from_iso=comparison_from, to_iso=comparison_to
            )
            current_d: dict[str, float] = {
                "repayment_count": float(current["repayment_count"]),
                "total_received": float(current["total_received"]),
            }
            previous_d: dict[str, float] = {
                "repayment_count": float(previous["repayment_count"]),
                "total_received": float(previous["total_received"]),
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
