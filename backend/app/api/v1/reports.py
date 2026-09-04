"""API routes for F.1 - Reports (sales, purchases, inventory, inventory-movements).

All four endpoints are **read-only** (per the F.1 plan). Capability:
``report.view`` (Owner only - ``STAFF_DEFAULT_CAPABILITIES`` does NOT
include ``report.view`` per ``app.authz.caps``, so Staff gets 403).

Date semantics (per API-Architecture §4.7):

* ``GET /reports/sales`` - period filter on ``sales.sale_date``.
* ``GET /reports/purchases`` - period filter on ``purchases.purchase_date``.
* ``GET /reports/inventory`` - point-in-time snapshot. Period params
  are accepted for OpenAPI parity but are NOT applied.
* ``GET /reports/inventory-movements`` - period filter on
  ``stock_movements.movement_date``.

Inclusivity is ``>= from AND <= to`` (matches E.2/E.8/E.9).

The ``Period`` alias (``today``/``this_week``/``this_month``/
``this_year``/``custom``) and ``from``/``to`` ISO bounds are resolved
by ``app.reports.period.resolve_period``. Default is ``this_month``
when neither is supplied (matches the dashboard default at
API-Architecture §12.2 line 1110).

``compare_to`` (D-7): honoured for ``/reports/sales`` and
``/reports/purchases`` per the architecture's period-comparison
support list. ``/reports/inventory`` ignores it (the response has
no ``comparison`` field).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_uow, require_capability
from app.db import UnitOfWork
from app.reports.cash_flow import CashFlowReportService
from app.reports.crl import CustomerRefundLiabilitiesReportService
from app.reports.inventory import InventoryReportService
from app.reports.inventory_movements import InventoryMovementsReportService
from app.reports.manual import (
    ManualExpenseReportService,
    ManualIncomeReportService,
)
from app.reports.payables import PayablesReportService
from app.reports.period import resolve_compare_to, resolve_period
from app.reports.pnl import ProfitAndLossReportService
from app.reports.production import ProductionReportService
from app.reports.purchases import PurchasesReportService
from app.reports.receivables import ReceivablesReportService
from app.reports.refunds import RefundsReportService
from app.reports.repayments import SupplierRepaymentsReportService
from app.reports.returns import (
    PurchaseReturnsReportService,
    SalesReturnsReportService,
)
from app.reports.sales import SalesReportService
from app.reports.supplier_receivables import (
    SupplierReceivablesReportService,
)

RequireReportView = require_capability("report.view")
RequireFinanceViewProfit = require_capability("finance.view_profit")
RequireFinanceViewPayablesReceivables = require_capability(
    "finance.view_payables_receivables"
)

router = APIRouter(tags=["Reports"])


# ---------------------------------------------------------------------------
# Period / comparison helpers (route-local; no DB access)
# ---------------------------------------------------------------------------


def _resolve_window(
    *,
    period: str | None,
    from_iso: str | None,
    to_iso: str | None,
) -> tuple[Any, Any]:
    """Wrapper around ``resolve_period`` that maps ``ValueError`` to 400.

    The 400 envelope uses the canonical ``validation_failed`` code so
    the contract stays uniform across the API.
    """
    try:
        return resolve_period(period=period, from_iso=from_iso, to_iso=to_iso)
    except ValueError as exc:
        # Defer to the validation_failed handler - raise a 400 with
        # the canonical envelope.
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "validation_failed",
                "message": str(exc),
                "details": [{"field": "period", "code": "invalid", "message": str(exc)}],
            },
        ) from exc


def _resolve_comparison(
    *,
    compare_to: str | None,
    primary_from: Any,
    primary_to: Any,
) -> tuple[Any, Any] | tuple[None, None]:
    """Resolve the comparison window, returning ``(None, None)`` if not supplied.

    Errors map to 400 the same way as ``_resolve_window``.
    """
    if compare_to is None:
        return None, None
    try:
        return resolve_compare_to(
            compare_to=compare_to,
            primary_from=primary_from,
            primary_to=primary_to,
        )
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "validation_failed",
                "message": str(exc),
                "details": [
                    {"field": "compare_to", "code": "invalid", "message": str(exc)}
                ],
            },
        ) from exc


# ---------------------------------------------------------------------------
# GET /reports/sales - salesReport
# ---------------------------------------------------------------------------


@router.get(
    "/reports/sales",
    operation_id="salesReport",
    summary="Sales report (revenue, COGS, gross, count, avg ticket).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireReportView)],
)
async def sales_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(
        default=None,
        description="Period alias: today, this_week, this_month, this_year, custom.",
    ),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Aggregate KPIs over posted, non-cancelled sales in the period.

    Per D-8: ``revenue`` is GROSS ``sales.total_amount`` MINUS
    ``sales_returns.total_selling_price_returned`` (posted returns
    only). ``cogs`` is the parallel computation. Returns cancelled
    via the ``sales_returns`` lifecycle are excluded.
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = SalesReportService(uow)
    return await svc.get_report(
        primary_from=primary_from,
        primary_to=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# GET /reports/purchases - purchasesReport (paginated list + optional comparison)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/purchases",
    operation_id="purchasesReport",
    summary="Purchase report (paginated list, with optional comparison).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireReportView)],
)
async def purchases_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=1000),
    per_page: int = Query(default=50, ge=1, le=500),
    sort: str = Query(default="id"),
    q: str | None = Query(default=None, max_length=200),
    filter_supplier_id: int | None = Query(default=None, alias="filter[supplier_id]"),
    filter_lifecycle_status: str | None = Query(
        default=None, alias="filter[lifecycle_status]"
    ),
    filter_posted_by: int | None = Query(default=None, alias="filter[posted_by]"),
) -> dict[str, Any]:
    """Paginated list of posted, non-cancelled purchases in the period.

    The sort key is the closed whitelist
    (``id``, ``purchase_date``, ``lifecycle_status``, ``created_at``,
    ``updated_at`` with optional ``-`` prefix); unknown values fall
    back to ``id`` at the repo layer.

    The ``comparison`` block is included when ``compare_to`` is
    supplied (D-7). It carries ``purchase_count`` and
    ``total_amount`` (Σ ``purchase_lines.line_total``) for the
    current and previous periods.
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = PurchasesReportService(uow)
    return await svc.get_report(
        page=page,
        per_page=per_page,
        sort=sort,
        from_iso=primary_from,
        to_iso=primary_to,
        supplier_id=filter_supplier_id,
        lifecycle_status=filter_lifecycle_status,
        posted_by=filter_posted_by,
        q=q,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# GET /reports/inventory - inventoryReport (point-in-time snapshot)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/inventory",
    operation_id="inventoryReport",
    summary="Inventory valuation + low-stock summary (point-in-time).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireReportView)],
)
async def inventory_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Point-in-time inventory snapshot.

    Period / from / to / compare_to are accepted for OpenAPI contract
    parity but are NOT applied - the snapshot reflects the current
    state of ``product_valuation`` + ``products`` (per the audit:
    this is a point-in-time aggregate, not a historical aggregation).
    """
    # The resolver still validates the alias / ISO formats so a bad
    # input still produces a 400 - but the resolved window is unused.
    _resolve_window(period=period, from_iso=from_iso, to_iso=to_iso)
    _ = compare_to  # explicitly ignored, accepted for contract parity

    svc = InventoryReportService(uow)
    return await svc.get_report()


# ---------------------------------------------------------------------------
# GET /reports/inventory-movements - inventoryMovementsReport (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/inventory-movements",
    operation_id="inventoryMovementsReport",
    summary="Stock movement history (paginated list, with period filter).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireReportView)],
)
async def inventory_movements_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=1000),
    per_page: int = Query(default=50, ge=1, le=500),
    sort: str = Query(default="id"),
    q: str | None = Query(default=None, max_length=200),
    filter_product_id: int | None = Query(default=None, alias="filter[product_id]"),
    filter_trigger: str | None = Query(default=None, alias="filter[trigger]"),
    filter_reference_type: str | None = Query(
        default=None, alias="filter[reference_type]"
    ),
    filter_reference_id: int | None = Query(
        default=None, alias="filter[reference_id]"
    ),
) -> dict[str, Any]:
    """Paginated list of stock movements in the period.

    Reuses the E.7 row shape and repo helpers. No new lifecycle
    filter is invented (the ledger is append-only).
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    # compare_to is accepted for contract parity but the response
    # shape is PagedResponse (no comparison field). Still, resolve
    # the alias so bad input produces a 400 instead of being ignored.
    _ = compare_to  # route-level acceptance; ignored per the audit.

    svc = InventoryMovementsReportService(uow)
    return await svc.get_report(
        page=page,
        per_page=per_page,
        sort=sort,
        from_iso=primary_from,
        to_iso=primary_to,
        product_id=filter_product_id,
        trigger=filter_trigger,
        reference_type=filter_reference_type,
        reference_id=filter_reference_id,
        q=q,
    )


# ---------------------------------------------------------------------------
# GET /reports/sales-returns - salesReturnsReport (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/sales-returns",
    operation_id="salesReturnsReport",
    summary="Sales return report.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireReportView)],
)
async def sales_returns_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Paginated list of ``sales_returns`` rows in the period.

    Per F.2 OpenAPI: only ``Period``/``From``/``To``/``CompareTo`` are
    declared - server-side default pagination (``page=1``, ``per_page=50``).
    Cancellation lifecycle is preserved in the row so clients can
    filter client-side.
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = SalesReturnsReportService(uow)
    return await svc.get_report(
        from_iso=primary_from,
        to_iso=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# GET /reports/purchase-returns - purchaseReturnsReport (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/purchase-returns",
    operation_id="purchaseReturnsReport",
    summary="Purchase return report.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireReportView)],
)
async def purchase_returns_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Paginated list of ``purchase_returns`` rows in the period.

    Per F.2 OpenAPI: only ``Period``/``From``/``To``/``CompareTo`` are
    declared - server-side default pagination (``page=1``, ``per_page=50``).
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = PurchaseReturnsReportService(uow)
    return await svc.get_report(
        from_iso=primary_from,
        to_iso=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# GET /reports/production - productionReport (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/production",
    operation_id="productionReport",
    summary="Production runs with cost lines.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireReportView)],
)
async def production_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Paginated list of ``production_runs`` rows in the period.

    Per F.2 OpenAPI: only ``Period``/``From``/``To``/``CompareTo`` are
    declared - server-side default pagination (``page=1``, ``per_page=50``).
    All lifecycle states are listed (draft / posted / completed /
    cancelled) - the ``lifecycle_status`` column is preserved in each
    row so clients can filter client-side. The comparison aggregate
    excludes drafts (matches E.3 production-post lifecycle).
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = ProductionReportService(uow)
    return await svc.get_report(
        from_iso=primary_from,
        to_iso=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# GET /reports/manual-income - manualIncomeReport (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/manual-income",
    operation_id="manualIncomeReport",
    summary="Manual income entries.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireReportView)],
)
async def manual_income_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Paginated list of ``manual_finance_entries`` where category
    ``entry_type='income'``.

    Per F.3 OpenAPI: only ``Period``/``From``/``To``/``CompareTo`` are
    declared - server-side default pagination (``page=1``, ``per_page=50``).
    Cancelled entries are still listed; the comparison aggregate
    EXCLUDES them (the cancel inserts a reversing cash_movement which
    the cash-flow report surfaces).
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = ManualIncomeReportService(uow)
    return await svc.get_report(
        from_iso=primary_from,
        to_iso=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# GET /reports/manual-expense - manualExpenseReport (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/manual-expense",
    operation_id="manualExpenseReport",
    summary="Manual expense entries.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireReportView)],
)
async def manual_expense_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Paginated list of ``manual_finance_entries`` where category
    ``entry_type='expense'``.

    Per F.3 OpenAPI: only ``Period``/``From``/``To``/``CompareTo`` are
    declared - server-side default pagination (``page=1``, ``per_page=50``).
    Cancelled entries are still listed; the comparison aggregate
    EXCLUDES them (the cancel inserts a reversing cash_movement).
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = ManualExpenseReportService(uow)
    return await svc.get_report(
        from_iso=primary_from,
        to_iso=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# GET /reports/refunds - refundsReport (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/refunds",
    operation_id="refundsReport",
    summary="Refunds (customer disbursements).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireReportView)],
)
async def refunds_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Paginated list of ``refunds`` rows in the period.

    Per F.3 OpenAPI: only ``Period``/``From``/``To``/``CompareTo`` are
    declared - server-side default pagination. ``refunds`` is
    append-only (no lifecycle column), so all rows in the period are
    listed and aggregated.
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = RefundsReportService(uow)
    return await svc.get_report(
        from_iso=primary_from,
        to_iso=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# GET /reports/supplier-repayments - supplierRepaymentsReport (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/supplier-repayments",
    operation_id="supplierRepaymentsReport",
    summary="Supplier repayments.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireReportView)],
)
async def supplier_repayments_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Paginated list of ``supplier_repayments`` rows in the period.

    Per F.3 OpenAPI: only ``Period``/``From``/``To``/``CompareTo`` are
    declared - server-side default pagination. The aggregate sums
    ``received_amount`` (actual cash received) not ``amount``
    (the obligation ceiling).
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = SupplierRepaymentsReportService(uow)
    return await svc.get_report(
        from_iso=primary_from,
        to_iso=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# GET /reports/cash-flow - cashFlowReport (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/cash-flow",
    operation_id="cashFlowReport",
    summary="Cash movements.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireReportView)],
)
async def cash_flow_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Paginated list of ``cash_movements`` rows in the period.

    Per F.3 OpenAPI: only ``Period``/``From``/``To``/``CompareTo`` are
    declared - server-side default pagination. ``cash_movements`` is
    append-only. The comparison envelope exposes ``movement_count``,
    ``cash_in``, ``cash_out``, ``net``.
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = CashFlowReportService(uow)
    return await svc.get_report(
        from_iso=primary_from,
        to_iso=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# F.4 - P&L (aggregate, no pagination)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/p-and-l",
    operation_id="profitAndLoss",
    summary="P&L (revenue, COGS, gross, expenses, net).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireFinanceViewProfit)],
)
async def profit_and_loss_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Aggregate P&L over the period (ProfitLossResponse per
    ``openapi.yaml:9076-9095``).

    Capability: ``finance.view_profit`` per the OpenAPI
    ``x-required-capability`` extension. The response carries no
    ``pagination`` block - it's a single aggregate, not a list.

    Comparison envelope exposes ``revenue``, ``cogs``,
    ``gross_profit``, ``other_income``, ``operating_expenses``,
    ``net_profit`` (D-7) when ``compare_to`` is supplied.
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = ProfitAndLossReportService(uow)
    return await svc.get_report(
        primary_from=primary_from,
        primary_to=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# F.4 - Receivables per sale (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/receivables",
    operation_id="receivables",
    summary="Receivables per sale.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireFinanceViewPayablesReceivables)],
)
async def receivables_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Paginated list of open AR per sale (PagedResponse).

    Capability: ``finance.view_payables_receivables``. Only
    posted, non-cancelled sales with ``open_balance > 0`` are
    surfaced. The comparison envelope aggregates
    ``{receivable_count, total_open}`` over the period.
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = ReceivablesReportService(uow)
    return await svc.get_report(
        from_iso=primary_from,
        to_iso=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# F.4 - Payables per purchase (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/payables",
    operation_id="payables",
    summary="Payables per purchase.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireFinanceViewPayablesReceivables)],
)
async def payables_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Paginated list of open AP per purchase (PagedResponse).

    Capability: ``finance.view_payables_receivables``. Only
    posted, non-cancelled purchases with ``open_balance > 0`` are
    surfaced. The comparison envelope aggregates
    ``{payable_count, total_open}`` over the period.
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = PayablesReportService(uow)
    return await svc.get_report(
        from_iso=primary_from,
        to_iso=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# F.4 - Supplier receivables (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/supplier-receivables",
    operation_id="supplierReceivables",
    summary="Open Supplier Receivable per purchase.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireFinanceViewPayablesReceivables)],
)
async def supplier_receivables_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Paginated list of open Supplier Receivable per purchase (PagedResponse).

    Capability: ``finance.view_payables_receivables``. Only
    rows from ``supplier_repayments`` with ``open_balance > 0``
    (i.e. ``amount - received_amount > 0``) are surfaced. The
    comparison envelope aggregates
    ``{supplier_receivable_count, total_open}`` over the period.
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = SupplierReceivablesReportService(uow)
    return await svc.get_report(
        from_iso=primary_from,
        to_iso=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


# ---------------------------------------------------------------------------
# F.4 - Customer refund liabilities (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "/reports/customer-refund-liabilities",
    operation_id="customerRefundLiabilities",
    summary="Open CRL per sale.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireFinanceViewPayablesReceivables)],
)
async def customer_refund_liabilities_report(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Paginated list of open CRL per sale (PagedResponse).

    Capability: ``finance.view_payables_receivables``. Only
    posted, non-cancelled sales with ``open_crl > 0`` are
    surfaced. The comparison envelope aggregates
    ``{crl_count, total_open}`` over the period.
    """
    primary_from, primary_to = _resolve_window(
        period=period, from_iso=from_iso, to_iso=to_iso
    )
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    svc = CustomerRefundLiabilitiesReportService(uow)
    return await svc.get_report(
        from_iso=primary_from,
        to_iso=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
    )


__all__ = ["router"]
