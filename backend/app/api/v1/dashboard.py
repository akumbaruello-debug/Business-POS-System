"""API routes for F.5 - Dashboard.

Two read-only endpoints per ``openapi.yaml`` line 5640-5688:

* ``GET /dashboard``           -> ``DashboardResponse``
  Capability: ``finance.view_profit`` (Owner only - Staff defaults
  do NOT include this cap, so Staff gets 403 per the canonical
  enforcement chain in ``app.authz.deps.require_capability``).
* ``GET /dashboard/inventory`` -> ``InventoryReportResponse``
  Capability: ``inventory.view`` (Staff has it - 200 expected).

Period parameters: ``period``, ``from``, ``to`` (per
``openapi.yaml`` 5651-5653); comparison parameter: ``compare_to``
(D-7). Resolution semantics match F.1/F.4 exactly - the
:func:`app.reports.period.resolve_period` and
:func:`app.reports.period.resolve_compare_to` helpers are reused
without modification. Default period: ``this_month`` (matches the
dashboard default at API-Architecture 12.2 line 1110).

Inventory snapshot: period params are accepted for OpenAPI contract
parity but are NOT applied (per the audit decision documented in
:mod:`app.reports.inventory_repo`).

Accounting discipline: every KPI and chart is derived from the same
authoritative ledgers used by the F.1/F.4 reports. The dashboard
introduces no new aggregation formulas and no new lifecycle filters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_uow, require_capability
from app.db import UnitOfWork
from app.errors import ValidationFailed
from app.reports.dashboard import DashboardService
from app.reports.period import resolve_compare_to, resolve_period

# Per openapi.yaml 5649: dashboard requires finance.view_profit.
# Per 5675: dashboard/inventory requires inventory.view.
RequireFinanceViewProfit = require_capability("finance.view_profit")
RequireInventoryView = require_capability("inventory.view")

router = APIRouter(tags=["Dashboard"])


# ---------------------------------------------------------------------------
# Helpers (route-local; no DB access)
# ---------------------------------------------------------------------------


def _resolve_window(
    *,
    period: str | None,
    from_iso: str | None,
    to_iso: str | None,
) -> tuple[Any, Any]:
    """Wrapper around ``resolve_period`` mapping ``ValueError`` to 400.

    The 400 envelope uses the canonical ``ValidationFailed`` error so
    the contract stays uniform across the API.
    """
    try:
        return resolve_period(period=period, from_iso=from_iso, to_iso=to_iso)
    except ValueError as exc:
        raise ValidationFailed(
            str(exc),
            errors=[{"field": "period", "code": "invalid", "message": str(exc)}],
        ) from exc


def _resolve_comparison(
    *,
    compare_to: str | None,
    primary_from: Any,
    primary_to: Any,
) -> tuple[Any, Any] | tuple[None, None]:
    """Resolve the comparison window; ``(None, None)`` if not supplied."""
    if compare_to is None:
        return None, None
    try:
        return resolve_compare_to(
            compare_to=compare_to,
            primary_from=primary_from,
            primary_to=primary_to,
        )
    except ValueError as exc:
        raise ValidationFailed(
            str(exc),
            errors=[
                {
                    "field": "compare_to",
                    "code": "invalid",
                    "message": str(exc),
                }
            ],
        ) from exc


# ---------------------------------------------------------------------------
# GET /dashboard
# ---------------------------------------------------------------------------


@router.get(
    "/dashboard",
    operation_id="getDashboard",
    summary="KPI cards + charts. Requires finance.view_profit (returns 403 if missing).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireFinanceViewProfit)],
)
async def get_dashboard(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    compare_to: str | None = Query(default=None),
) -> dict[str, Any]:
    """Build the dashboard snapshot (``DashboardResponse`` per OAS 9129).

    Capability: ``finance.view_profit`` per the OpenAPI
    ``x-required-capability`` extension. Owner has it; Staff does not
    (403). Returns the canonical ``{kpis, charts, comparison,
    as_of}`` envelope.

    ``as_of`` is the server timestamp the snapshot was taken (UTC,
    tz-aware); it is the same value used by the E.9 cash-movements
    ``server_time`` convention.
    """
    primary_from, primary_to = _resolve_window(period=period, from_iso=from_iso, to_iso=to_iso)
    comparison_from, comparison_to = _resolve_comparison(
        compare_to=compare_to, primary_from=primary_from, primary_to=primary_to
    )
    as_of = datetime.now(UTC)
    svc = DashboardService(uow)
    return await svc.get_dashboard(
        primary_from=primary_from,
        primary_to=primary_to,
        comparison_from=comparison_from,
        comparison_to=comparison_to,
        as_of=as_of,
    )


# ---------------------------------------------------------------------------
# GET /dashboard/inventory
# ---------------------------------------------------------------------------


@router.get(
    "/dashboard/inventory",
    operation_id="getDashboardInventory",
    summary="Inventory-only KPIs (low-stock count, total value).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireInventoryView)],
)
async def get_dashboard_inventory(
    uow: UnitOfWork = Depends(get_uow),
    period: str | None = Query(default=None),
) -> dict[str, Any]:
    """Build the inventory snapshot (``InventoryReportResponse`` per OAS 9096).

    Capability: ``inventory.view``. Both Owner and Staff have this
    capability, so both can call the endpoint. Period params are
    accepted for OpenAPI parity but NOT applied (the inventory
    snapshot is point-in-time).
    """
    svc = DashboardService(uow)
    snapshot = await svc.get_inventory_snapshot()
    return {"data": snapshot}
