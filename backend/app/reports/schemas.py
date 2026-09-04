"""F.1 — Report response schemas.

Mirrors the OpenAPI component definitions in ``openapi.yaml``:

* ``SalesReportResponse``  — aggregate of posted, non-cancelled sales
  with returns deducted (per D-8). Optional ``comparison`` block.
* ``InventoryReportResponse`` — point-in-time snapshot over
  ``product_valuation``. No ``comparison`` (per audit decision).
* ``PeriodComparison``     — used by both the sales and purchases
  report responses. ``delta`` and ``delta_pct`` are field-by-field
  current - previous (pct clamped to 0 when previous=0, null when
  both are 0).
* ``PurchasesReportResponse`` — paginated purchase rows with an
  optional ``comparison`` block (D-7: the architecture says
  purchases supports comparison; the OpenAPI PagedResponse does not
  carry a ``comparison`` field, so F.1 extends it with a new
  ``PurchasesReportResponse`` schema).

These models are intentionally minimal — the response is shaped by
the route layer (Decimal/datetime coercion happens there, not in the
Pydantic model). They are used as the type contract for the
handlers' return annotation and for OpenAPI serialization.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.validation.pagination import Pagination

__all__ = [
    "DashboardBestSeller",
    "DashboardCharts",
    "DashboardExpenseBreakdownItem",
    "DashboardKpis",
    "DashboardResponse",
    "DashboardSalesTrendPoint",
    "InventoryReportData",
    "InventoryReportResponse",
    "PeriodComparison",
    "PurchasesReportResponse",
    "SalesReportData",
    "SalesReportResponse",
]


class SalesReportData(BaseModel):
    """Aggregate over posted, non-cancelled sales in the period.

    Per D-8: ``revenue`` is GROSS ``sales.total_amount`` MINUS
    ``sales_returns.total_selling_price_returned`` (posted returns
    only — cancelled returns are excluded). ``cogs`` is the parallel
    computation over ``sale_lines.cogs_total_snapshot`` and
    ``sales_returns.total_cost_returned``.

    ``average_ticket`` is 0 when ``sales_count`` is 0 (never null —
    matches the existing cash-balance convention).
    """

    model_config = ConfigDict(extra="forbid")

    revenue: float = Field(..., ge=0, description="Net revenue after returns.")
    cogs: float = Field(..., ge=0, description="Net COGS after returns.")
    gross_profit: float = Field(..., description="revenue - cogs (can be negative).")
    sales_count: int = Field(..., ge=0, description="Distinct qualifying sales.")
    average_ticket: float = Field(..., ge=0, description="revenue / sales_count (0 if no sales).")


class PeriodComparison(BaseModel):
    """Period comparison block (per API-Architecture §12.1).

    ``delta`` and ``delta_pct`` are dicts keyed by the report's metric
    names. ``delta_pct[k] = delta[k] / previous[k]`` when
    ``previous[k] != 0``; ``0.0`` when ``previous[k] == 0`` but
    ``current[k] != 0``; ``None`` when both are 0 (per the audit
    contract — avoid divide-by-zero and report no movement).
    """

    model_config = ConfigDict(extra="forbid")

    previous_period: dict[str, str] = Field(
        ...,
        description='Echo of the comparison window: {"from": ISO, "to": ISO}.',
    )
    delta: dict[str, float] = Field(..., description="Field-by-field current - previous.")
    delta_pct: dict[str, float | None] = Field(
        ..., description="Field-by-field delta / previous (0 if previous=0, null if both 0)."
    )


class SalesReportResponse(BaseModel):
    """Response envelope for ``GET /reports/sales``."""

    model_config = ConfigDict(extra="forbid")

    data: SalesReportData
    comparison: PeriodComparison | None = None


class InventoryReportData(BaseModel):
    """Point-in-time snapshot over ``product_valuation``.

    * ``total_inventory_value`` — SUM of ``product_valuation.inventory_value``
      across all products (active + inactive, per B.7).
    * ``products_count`` — COUNT(*) of products represented in
      ``product_valuation`` (active + inactive).
    * ``low_stock_count`` — active products with a non-null
      ``low_stock_threshold`` whose ``on_hand_quantity <= threshold``.
    * ``out_of_stock_count`` — active products with
      ``on_hand_quantity <= 0``.
    """

    model_config = ConfigDict(extra="forbid")

    total_inventory_value: float = Field(..., ge=0)
    products_count: int = Field(..., ge=0)
    low_stock_count: int = Field(..., ge=0)
    out_of_stock_count: int = Field(..., ge=0)


class InventoryReportResponse(BaseModel):
    """Response envelope for ``GET /reports/inventory``."""

    model_config = ConfigDict(extra="forbid")

    data: InventoryReportData


class PurchasesReportResponse(BaseModel):
    """Response envelope for ``GET /reports/purchases``.

    Extends the standard PagedResponse with an optional ``comparison``
    block (D-7). ``data`` items are the existing purchase row dicts
    (id, reference_no, supplier_id, purchase_date, lifecycle_status,
    posted_at, posted_by, cancellation_date, cancelled_by, created_at,
    updated_at, created_by, notes) - no new row model invented.
    """

    model_config = ConfigDict(extra="forbid")

    data: list[dict[str, Any]]
    pagination: Pagination
    comparison: PeriodComparison | None = None


# ---------------------------------------------------------------------------
# F.5 - Dashboard schemas
# Mirrors openapi.yaml: DashboardResponse (9129-9176) and the
# InventoryReportResponse shape used by /dashboard/inventory (5684).
# ---------------------------------------------------------------------------


class DashboardKpis(BaseModel):
    """Dashboard KPI card block per ``openapi.yaml`` 9135-9153.

    ``net_profit`` and ``cash_on_hand`` are nullable in the OAS so the
    contract can forward-evolve (e.g. field-level nulls when a
    cross-capability gate kicks in). The current implementation
    returns them as non-null for owners and reserves ``null`` for
    future capability differentiation; staff gets 403 (the entire
    endpoint returns 403 per API-Architecture 12.2 line 1112 when any
    required capability is missing).
    """

    model_config = ConfigDict(extra="forbid")

    total_sales: float = Field(..., ge=0, description="Net sales (after returns) for the period.")
    total_purchases: float = Field(
        ...,
        ge=0,
        description="Sum of posted, non-cancelled purchase_lines.line_total for the period.",
    )
    net_profit: float | None = Field(
        ...,
        description="P&L net profit (gross_profit + other_income - operating_expenses); null forward-compat.",
    )
    cash_on_hand: float | None = Field(
        ...,
        description="Current cash ledger balance (SUM cash_movements.amount); null forward-compat.",
    )
    inventory_value: float = Field(
        ..., ge=0, description="Point-in-time SUM(product_valuation.inventory_value)."
    )
    low_stock_count: int = Field(
        ..., ge=0, description="Active products with on_hand_quantity <= low_stock_threshold."
    )


class DashboardSalesTrendPoint(BaseModel):
    """One bucket in the ``sales_trend`` chart array.

    Each point covers a calendar bucket (daily when the window is
    <= 7 days, weekly otherwise - per API-Architecture 12.2 line 1109).
    """

    model_config = ConfigDict(extra="forbid")

    period_start: str = Field(..., description="Bucket start (ISO-8601).")
    revenue: float = Field(..., ge=0, description="Net revenue for the bucket.")
    sales_count: int = Field(
        ..., ge=0, description="Number of posted, non-cancelled sales in the bucket."
    )


class DashboardBestSeller(BaseModel):
    """Top product by revenue in the period (MS-9)."""

    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(..., ge=1)
    name: str = Field(..., min_length=1)
    quantity: float = Field(..., ge=0, description="Total units sold.")
    revenue: float = Field(..., ge=0, description="SUM(quantity * line_total).")


class DashboardExpenseBreakdownItem(BaseModel):
    """Expense-by-category row (MS-10)."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(..., min_length=1, description="financial_categories.name")
    total: float = Field(
        ..., ge=0, description="SUM(manual_finance_entries.amount) for the category in the period."
    )


class DashboardCharts(BaseModel):
    """Three chart arrays per openapi.yaml 9154-9171.

    All three are arrays of items with ``additionalProperties: true``
    in the OAS; we surface them as typed lists of concrete models so
    mypy + OpenAPI generation both stay honest.
    """

    model_config = ConfigDict(extra="forbid")

    sales_trend: list[DashboardSalesTrendPoint] = Field(
        ..., description="Daily/weekly bucketed revenue series."
    )
    best_sellers: list[DashboardBestSeller] = Field(
        ..., description="Top-10 products by SUM(quantity * line_total) in the period."
    )
    expense_breakdown: list[DashboardExpenseBreakdownItem] = Field(
        ..., description="SUM(manual_finance_entries.amount) by financial_categories.name."
    )


class DashboardResponse(BaseModel):
    """Response envelope for ``GET /dashboard``.

    Mirrors ``openapi.yaml:9129-9176``. ``as_of`` is a server timestamp
    per the canonical ``server_time`` convention; the route layer
    formats it.
    """

    model_config = ConfigDict(extra="forbid")

    kpis: DashboardKpis
    charts: DashboardCharts
    comparison: PeriodComparison | None = None
    as_of: datetime = Field(..., description="Server timestamp (UTC) the snapshot was taken.")
