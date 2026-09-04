"""F.4 - Supplier Receivables report service.

Owns the orchestration for ``GET /reports/supplier-receivables``
per ``openapi.yaml:5588-5613``.

The response is a paginated list of open Supplier Receivable
obligations. Per the V1.9 accounting model, Supplier Receivable
is created when a paid purchase is cancelled/returned (Event 12,
Event 15 paid branch). The ``supplier_repayments`` table is the
authoritative ledger for this obligation: ``amount`` is the
ceiling, ``received_amount`` is the cumulative cash recovered,
``open_balance = amount - received_amount``.

Date semantics: ``supplier_repayments.repayment_date`` in the
window (the period the obligation was created). Only rows with
``open_balance > 0`` are surfaced.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo
from app.validation.pagination import make_pagination

__all__ = ["SupplierReceivablesReportService"]

_DEFAULT_PAGE: int = 1
_DEFAULT_PER_PAGE: int = 50


class SupplierReceivablesReportService:
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
        total = await sales_repo.count_supplier_receivables_report(
            self._uow, from_iso=from_iso, to_iso=to_iso
        )
        rows = await sales_repo.list_supplier_receivables_report(
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
            current = await sales_repo.fetch_supplier_receivables_aggregates(
                self._uow, from_iso=from_iso, to_iso=to_iso
            )
            previous = await sales_repo.fetch_supplier_receivables_aggregates(
                self._uow,
                from_iso=comparison_from,
                to_iso=comparison_to,
            )
            current_d = {
                "supplier_receivable_count": float(
                    current["supplier_receivable_count"]
                ),
                "total_open": float(current["total_open"]),
            }
            previous_d = {
                "supplier_receivable_count": float(
                    previous["supplier_receivable_count"]
                ),
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
