"""G1 - Customer summary repository methods."""
from __future__ import annotations

from typing import Any

from app.db import UnitOfWork

# ---------------------------------------------------------------------------
# Customer summary: total_sales, receivable, sales_count for a single customer
# Definition rules:
#   * Total Sales = posted non-cancelled sales revenue - posted returns selling price
#   * Sales count = number of posted, non-cancelled sales rows
#   * Receivable = GREATEST(sale total - payments, 0) summed across all posts
#     Returns are included in the net amount before payment subtraction (mirroring _AGGREGATE_SQL structure)
#   * No date parameters
#   * Cancelled sales excluded; draft excluded
# ---------------------------------------------------------------------------

_CUSTOMER_SUMMARY_SQL = """
WITH base AS (
    SELECT
        s.id,
        s.customer_id,
        s.total_amount,
        COALESCE((
            SELECT SUM(sp.amount)
            FROM sale_payments sp
            WHERE sp.sale_id = s.id
        ), 0) AS payments_total
    FROM sales s
    WHERE s.customer_id = :customer_id
      AND s.lifecycle_status <> 'cancelled'
      AND s.posted_at IS NOT NULL
),
returned AS (
    SELECT
        sr.sale_id,
        COALESCE(SUM(sr.total_selling_price_returned) FILTER (
            WHERE sr.lifecycle_status = 'posted'
        ), 0) AS selling_returned
    FROM sales_returns sr
    JOIN sales s ON s.id = sr.sale_id
    WHERE s.customer_id = :customer_id
      AND s.lifecycle_status <> 'cancelled'
      AND s.posted_at IS NOT NULL
    GROUP BY sr.sale_id
),
net AS (
    SELECT
        b.id,
        b.total_amount,
        b.payments_total,
        COALESCE(r.selling_returned, 0) AS selling_returned
    FROM base b
    LEFT JOIN returned r ON r.sale_id = b.id
)
SELECT
    COALESCE(SUM(n.total_amount - n.selling_returned), 0) AS total_sales,
    COALESCE(SUM(n.selling_returned), 0) AS total_returns,
    COALESCE(SUM(GREATEST(n.total_amount - n.payments_total - n.selling_returned, 0)), 0) AS receivable,
    COUNT(*) AS sales_count
FROM net n
"""


async def fetch_customer_summary_totals(
    uow: UnitOfWork,
    *,
    customer_id: int,
) -> dict[str, Any]:
    """Return {total_sales, sales_count, receivable} for a customer.

    If the contact type is not customer/both, this should be handled at the service layer.
    For unknown contacts, return zeros.
    """
    row = await uow.first_row(_CUSTOMER_SUMMARY_SQL, {"customer_id": customer_id})
    if row is None:
        return {
            "total_sales": 0.0,
            "sales_count": 0,
            "receivable": 0.0,
        }
    return {
        "total_sales": float(row.get("total_sales") or 0.0),
        "sales_count": int(row.get("sales_count") or 0),
        "receivable": float(row.get("receivable") or 0.0),
    }
