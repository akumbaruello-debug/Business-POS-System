"""F.4 - Customer Refund Liabilities report service.

Owns the orchestration for
``GET /reports/customer-refund-liabilities`` per
``openapi.yaml:5614-5639``.

The response is a paginated list of posted, non-cancelled sales
whose open CRL balance > 0 in the period:

* ``open_crl = MAX(0, Σ(sale_payments.amount) - Σ(refunds.amount))``

(Per the V1.9 accounting model, CRL is created when a paid sale
is cancelled/returned. Sales returns on a paid sale generate CRL
while sales returns on an unpaid sale reduce AR. Refunds are CRL
outflows. The ``CRL = paid - refunds`` formulation is the clean
GL balance - the sales-returns effect on CRL cancels with their
effect on AR for the same sale.)

Date semantics: ``sales.sale_date`` in the window (the period
the obligation was created). Only sales with ``open_crl > 0``
are surfaced.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import sales_repo
from app.validation.pagination import make_pagination

__all__ = ["CustomerRefundLiabilitiesReportService"]

_DEFAULT_PAGE: int = 1
_DEFAULT_PER_PAGE: int = 50


class CustomerRefundLiabilitiesReportService:
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
        total = await sales_repo.count_crl_report(
            self._uow, from_iso=from_iso, to_iso=to_iso
        )
        rows = await sales_repo.list_crl_report(
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
            current = await sales_repo.fetch_crl_aggregates(
                self._uow, from_iso=from_iso, to_iso=to_iso
            )
            previous = await sales_repo.fetch_crl_aggregates(
                self._uow,
                from_iso=comparison_from,
                to_iso=comparison_to,
            )
            current_d = {
                "crl_count": float(current["crl_count"]),
                "total_open": float(current["total_open"]),
            }
            previous_d = {
                "crl_count": float(previous["crl_count"]),
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
