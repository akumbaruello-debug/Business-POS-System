"""F.3 — Cash-flow report service.

Owns the orchestration for ``GET /reports/cash-flow``. The report
is a **paginated list** of ``cash_movements`` header rows (per
OpenAPI ``PagedResponse`` at line 5484-5509).

Pagination parameters are NOT exposed on the wire for F.3 — the
OpenAPI spec only declares ``Period``/``From``/``To``/``CompareTo``.
Server-side default pagination is ``page=1``, ``per_page=50`` (locked
from F.1/F.2).

The row shape reuses the existing ``cash_movements`` columns (no
new row model invented — same rule as F.1/F.2): id, movement_date,
amount, direction, trigger, payment_method_id, reference_type,
reference_id, created_at, created_by.

Date semantics:

* Period filter on ``cash_movements.movement_date``.
* Inclusivity is ``>= from AND <= to``.

Lifecycle: ``cash_movements`` is append-only (no UPDATE / DELETE
allowed — enforced by the absence of any UPDATE/DELETE trigger plus
the pre-insert trigger that enforces INV-03 `Cash >= 0`). All rows
in the period are listed and aggregated.

Comparison (D-7): when ``compare_to`` is supplied, the response
includes a ``comparison`` block with ``movement_count``, ``cash_in``
(``SUM(amount) WHERE direction='in'``), ``cash_out``
(``SUM(amount) WHERE direction='out'``), and ``net``
(``cash_in + cash_out`` — note ``cash_out`` is negative by the
``ck_cash_movements_out_neg`` constraint, so the sum equals the
period's net cash effect) for current and previous periods.

``net`` is the headline metric: current_net - previous_net is the
period-over-period change in the cash ledger's net movement.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo
from app.validation.pagination import make_pagination

__all__ = ["CashFlowReportService"]

_DEFAULT_PAGE: int = 1
_DEFAULT_PER_PAGE: int = 50


class CashFlowReportService:
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
        """Build the full cash-flow report response."""
        total = await sales_repo.count_cash_flow_report(
            self._uow, from_iso=from_iso, to_iso=to_iso
        )
        rows = await sales_repo.list_cash_flow_report(
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
            current = await sales_repo.fetch_cash_flow_aggregates(
                self._uow, from_iso=from_iso, to_iso=to_iso
            )
            previous = await sales_repo.fetch_cash_flow_aggregates(
                self._uow, from_iso=comparison_from, to_iso=comparison_to
            )
            current_d: dict[str, float] = {
                "movement_count": float(current["movement_count"]),
                "cash_in": float(current["cash_in"]),
                "cash_out": float(current["cash_out"]),
                "net": float(current["net"]),
            }
            previous_d: dict[str, float] = {
                "movement_count": float(previous["movement_count"]),
                "cash_in": float(previous["cash_in"]),
                "cash_out": float(previous["cash_out"]),
                "net": float(previous["net"]),
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
