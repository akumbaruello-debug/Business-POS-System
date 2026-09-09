"""F.5 - Dashboard repository.

SQL helpers backing ``GET /dashboard`` and ``GET /dashboard/inventory``.

Authoritative sources (per F.1-F.4 contracts):

* Sales totals: ``sales`` + ``sales_returns`` (posted, non-cancelled;
  returns deducted per D-8). Date filter on ``sales.sale_date``.
* Purchase totals: ``purchases`` + ``purchase_lines`` (posted,
  non-cancelled; sum of ``purchase_lines.line_total`` per the
  F.1 purchases report contract). Date filter on
  ``purchases.purchase_date``.
* P&L components: ``sales``, ``sale_lines``, ``sales_returns``,
  ``manual_finance_entries`` joined to ``financial_categories``.
  Reuses the F.4 ``fetch_pnl_aggregates`` (the entire P&L aggregate
  is identical to what ``GET /reports/p-and-l`` returns).
* Cash on hand: ``SUM(cash_movements.amount)`` - the cash ledger is
  append-only and the running balance is the authoritative point-in-time
  figure. ``direction='in'`` rows have positive amounts;
  ``direction='out'`` rows have negative amounts (CHECK constraints).
  No date filter (current balance).
* Inventory value / low-stock count: ``product_valuation`` view
  (reuses F.1 ``fetch_inventory_report``).
* Sales trend: GROUP BY date_trunc bucket on ``sales.sale_date``
  (daily when window <= 7 days, weekly otherwise).
* Best sellers: ``SUM(sale_lines.line_total) GROUP BY product_id ORDER
  BY SUM DESC LIMIT 10`` per MS-9.
* Expense breakdown: ``SUM(manual_finance_entries.amount) JOIN
  financial_categories GROUP BY name`` for entry_type='expense',
  lifecycle_status='posted' per MS-10 and F.3 cancellation rule.

No new tables, views, or triggers. Read-only over the existing
authoritative ledgers.

ruff: S608 ignored - every interpolated fragment is a hardcoded
identifier (period alias or column name); no user-supplied data
reaches the SQL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import UnitOfWork

# ruff: noqa: S608 - dashboard SQL helpers use f-strings for hardcoded
# identifier interpolation only (period column names, bucket whitelist).
# All user-supplied values are bound as :from_iso / :to_iso parameters.
__all__ = [
    "fetch_cash_on_hand",
    "fetch_expense_breakdown",
    "fetch_sales_trend",
    "fetch_total_purchases",
    "fetch_total_sales",
]


# ---------------------------------------------------------------------------
# KPI aggregates
# ---------------------------------------------------------------------------


# Total sales for the period (mirrors F.1 ``fetch_sales_aggregates``
# math: GROSS sales.total_amount minus posted sales_returns).
# Returns ``total_sales`` (float) and ``sales_count`` (int) so we can
# surface both in the KPI card and the trend chart.
_TOTAL_SALES_SQL = """
WITH base AS (
    SELECT
        s.id,
        s.total_amount
    FROM sales s
    WHERE s.lifecycle_status <> 'cancelled'
      AND s.posted_at IS NOT NULL
      AND s.sale_date >= :from_iso
      AND s.sale_date <= :to_iso
),
returned AS (
    SELECT
        sr.sale_id,
        COALESCE(SUM(sr.total_selling_price_returned) FILTER (
            WHERE sr.lifecycle_status = 'posted'
        ), 0) AS selling_returned
    FROM sales_returns sr
    JOIN sales s ON s.id = sr.sale_id
    WHERE s.lifecycle_status <> 'cancelled'
      AND s.posted_at IS NOT NULL
      AND s.sale_date >= :from_iso
      AND s.sale_date <= :to_iso
    GROUP BY sr.sale_id
)
SELECT
    COALESCE(SUM(GREATEST(b.total_amount - COALESCE(r.selling_returned, 0), 0)), 0) AS total_sales,
    COUNT(*) AS sales_count
FROM base b
LEFT JOIN returned r ON r.sale_id = b.id
"""


async def fetch_total_sales(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    """Return ``{total_sales, sales_count}`` for the period.

    Mirrors the F.1 sales aggregate - net of posted returns, posted
    non-cancelled sales only. Date filter on ``sales.sale_date``.
    """
    row = await uow.first_row(_TOTAL_SALES_SQL, {"from_iso": from_iso, "to_iso": to_iso})
    if row is None:
        return {"total_sales": 0.0, "sales_count": 0}
    return {
        "total_sales": float(row.get("total_sales") or 0),
        "sales_count": int(row.get("sales_count") or 0),
    }


# Total purchases for the period (mirrors the F.1 purchases
# comparison block: ``SUM(purchase_lines.line_total)`` for posted,
# non-cancelled purchases). Date filter on ``purchases.purchase_date``.
_TOTAL_PURCHASES_SQL = """
SELECT
    COALESCE(SUM(pl.line_total), 0) AS total_purchases,
    COUNT(DISTINCT p.id) AS purchase_count
FROM purchases p
JOIN purchase_lines pl ON pl.purchase_id = p.id
WHERE p.lifecycle_status <> 'cancelled'
  AND p.posted_at IS NOT NULL
  AND p.purchase_date >= :from_iso
  AND p.purchase_date <= :to_iso
"""


async def fetch_total_purchases(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> dict[str, Any]:
    """Return ``{total_purchases, purchase_count}`` for the period."""
    row = await uow.first_row(_TOTAL_PURCHASES_SQL, {"from_iso": from_iso, "to_iso": to_iso})
    if row is None:
        return {"total_purchases": 0.0, "purchase_count": 0}
    return {
        "total_purchases": float(row.get("total_purchases") or 0),
        "purchase_count": int(row.get("purchase_count") or 0),
    }


# Cash on hand = SUM(cash_movements.amount) - the running balance of
# the append-only cash ledger. Pre-insert trigger enforces Cash >= 0
# (INV-03). ``direction='in'`` => amount > 0, ``direction='out'`` =>
# amount < 0; the SUM is therefore the current balance.
_CASH_ON_HAND_SQL = """
SELECT COALESCE(SUM(amount), 0) AS cash_on_hand
FROM cash_movements
"""


async def fetch_cash_on_hand(uow: UnitOfWork) -> float:
    """Return the current ``SUM(cash_movements.amount)`` balance."""
    row = await uow.first_row(_CASH_ON_HAND_SQL)
    if row is None:
        return 0.0
    return float(row.get("cash_on_hand") or 0)


# ---------------------------------------------------------------------------
# Chart aggregates
# ---------------------------------------------------------------------------


# Sales trend: revenue bucketed by day or week.
# Window length is decided at the route layer (daily <= 7 days, weekly
# otherwise). Pass the bucket via ``bucket`` ('day' or 'week').
def _sales_trend_sql(bucket: str) -> str:
    # bucket is constrained by the route layer (only 'day' or 'week').
    return f"""
WITH base AS (
    SELECT
        s.id,
        s.total_amount,
        s.sale_date
    FROM sales s
    WHERE s.lifecycle_status <> 'cancelled'
      AND s.posted_at IS NOT NULL
      AND s.sale_date >= :from_iso
      AND s.sale_date <= :to_iso
),
returned AS (
    SELECT
        sr.sale_id,
        COALESCE(SUM(sr.total_selling_price_returned) FILTER (
            WHERE sr.lifecycle_status = 'posted'
        ), 0) AS selling_returned
    FROM sales_returns sr
    JOIN sales s ON s.id = sr.sale_id
    WHERE s.lifecycle_status <> 'cancelled'
      AND s.posted_at IS NOT NULL
      AND s.sale_date >= :from_iso
      AND s.sale_date <= :to_iso
    GROUP BY sr.sale_id
),
net AS (
    SELECT
        date_trunc('{bucket}', b.sale_date) AS bucket_start,
        GREATEST(b.total_amount - COALESCE(r.selling_returned, 0), 0) AS net_revenue
    FROM base b
    LEFT JOIN returned r ON r.sale_id = b.id
)
SELECT
    bucket_start,
    COALESCE(SUM(net_revenue), 0) AS revenue,
    COUNT(*) AS sales_count
FROM net
GROUP BY bucket_start
ORDER BY bucket_start ASC
"""


async def fetch_sales_trend(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
    bucket: str,
) -> list[dict[str, Any]]:
    """Return bucketed revenue series for the sales_trend chart.

    ``bucket`` must be 'day' or 'week' - the route layer selects the
    granularity from the window length. Each row has:
    ``{period_start: datetime, revenue: float, sales_count: int}``.
    """
    if bucket not in ("day", "week"):
        raise ValueError(f"invalid bucket '{bucket}'; expected 'day' or 'week'")
    rows = await uow.fetch_all(
        _sales_trend_sql(bucket),
        {"from_iso": from_iso, "to_iso": to_iso},
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "period_start": r.get("bucket_start"),
                "revenue": float(r.get("revenue") or 0),
                "sales_count": int(r.get("sales_count") or 0),
            }
        )
    return out


# Best sellers: per MS-9 ``SUM(sale_lines.line_total)`` (line_total is
# already quantity x unit_price per sale line, so summing it directly
# yields true per-product revenue) ``GROUP BY product_id
# ORDER BY SUM DESC LIMIT 10`` over the period.
_BEST_SELLERS_SQL = """
SELECT
    p.id AS product_id,
    p.name AS name,
    COALESCE(SUM(sl.quantity), 0) AS quantity,
    COALESCE(SUM(sl.line_total), 0) AS revenue
FROM sale_lines sl
JOIN sales s ON s.id = sl.sale_id
JOIN products p ON p.id = sl.product_id
WHERE s.lifecycle_status <> 'cancelled'
  AND s.posted_at IS NOT NULL
  AND s.sale_date >= :from_iso
  AND s.sale_date <= :to_iso
GROUP BY p.id, p.name
ORDER BY revenue DESC, p.id ASC
LIMIT 10
"""


async def fetch_best_sellers(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> list[dict[str, Any]]:
    """Top-10 products by ``SUM(sale_lines.line_total)`` revenue."""
    rows = await uow.fetch_all(_BEST_SELLERS_SQL, {"from_iso": from_iso, "to_iso": to_iso})
    return [
        {
            "product_id": int(r["product_id"]),
            "name": str(r["name"]),
            "quantity": float(r.get("quantity") or 0),
            "revenue": float(r.get("revenue") or 0),
        }
        for r in rows
    ]


# Expense breakdown: per MS-10 ``SUM(manual_finance_entries.amount)
# GROUP BY financial_categories.name`` for the current period.
# entry_type='expense' (income is tracked separately, e.g. P&L
# other_income). lifecycle_status='posted' (cancelled manual entries
# are excluded per F.3 - cancellation creates a reversing cash_movement
# that lives in the cash-flow report; counting the original here would
# double-count).
_EXPENSE_BREAKDOWN_SQL = """
SELECT
    fc.name AS category,
    COALESCE(SUM(mfe.amount), 0) AS total
FROM manual_finance_entries mfe
JOIN financial_categories fc ON fc.id = mfe.category_id
WHERE mfe.lifecycle_status = 'posted'
  AND fc.entry_type = 'expense'
  AND fc.is_active = TRUE
  AND mfe.entry_date >= :from_iso
  AND mfe.entry_date <= :to_iso
GROUP BY fc.name
ORDER BY total DESC, fc.name ASC
LIMIT 10
"""


async def fetch_expense_breakdown(
    uow: UnitOfWork,
    *,
    from_iso: datetime,
    to_iso: datetime,
) -> list[dict[str, Any]]:
    """Top-10 expense categories for the period (MS-10)."""
    rows = await uow.fetch_all(_EXPENSE_BREAKDOWN_SQL, {"from_iso": from_iso, "to_iso": to_iso})
    return [
        {
            "category": str(r["category"]),
            "total": float(r.get("total") or 0),
        }
        for r in rows
    ]
