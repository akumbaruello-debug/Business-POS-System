"""F.1 — Inventory report repository.

Single aggregate query over ``product_valuation`` (canonical view,
``schema.sql`` §17.2) joined to ``products``. The result is the
point-in-time snapshot for ``GET /reports/inventory``.

Aggregates (per the audit contract):

* ``total_inventory_value`` = ``SUM(product_valuation.inventory_value)``
  over ALL products (active + inactive — per B.7 contract: deactivated
  products are still counted in stock value and historical reports).
* ``products_count`` = ``COUNT(*)`` over the joined rows (active +
  inactive).
* ``low_stock_count`` = COUNT of products where
  ``is_active = TRUE AND low_stock_threshold IS NOT NULL AND
  on_hand_quantity <= low_stock_threshold``. This predicate mirrors
  the partial index ``ix_products_low_stock`` in ``schema.sql`` §5.3
  line 351-354 (so the planner can serve the count from the index).
* ``out_of_stock_count`` = COUNT of active products with
  ``on_hand_quantity <= 0``. Distinct concept from ``low_stock_count``:
  a product with threshold=5 and on_hand=3 is low_stock but NOT
  out_of_stock; a product with threshold=10 and on_hand=0 is BOTH.

The inventory report does NOT use the ``notifications`` table; the
``out_of_stock_count`` here is a SQL count, not a notification
category. The ``out_of_stock`` vs ``insufficient_stock`` divergence
remains an F.8 audit finding (per ``status.md`` line 49).

ruff: S608 ignored — every interpolated fragment is a hard-coded
identifier. No user-supplied values reach the SQL.
"""


from __future__ import annotations

from typing import Any

from app.db import UnitOfWork

__all__ = ["fetch_inventory_report"]


_INVENTORY_AGG_SQL = """
SELECT
    COALESCE(SUM(pv.inventory_value), 0) AS total_inventory_value,
    COUNT(*) AS products_count,
    COUNT(*) FILTER (
        WHERE p.is_active = TRUE
          AND p.low_stock_threshold IS NOT NULL
          AND pv.on_hand_quantity <= p.low_stock_threshold
    ) AS low_stock_count,
    COUNT(*) FILTER (
        WHERE p.is_active = TRUE
          AND pv.on_hand_quantity <= 0
    ) AS out_of_stock_count
FROM product_valuation pv
JOIN products p ON p.id = pv.product_id
"""


async def fetch_inventory_report(uow: UnitOfWork) -> dict[str, Any]:
    """Return ``{total_inventory_value, products_count, low_stock_count, out_of_stock_count}``.

    Point-in-time snapshot — no time filters are accepted (the
    inventory endpoint is not a historical aggregation per the
    audit decision).
    """
    row = await uow.first_row(_INVENTORY_AGG_SQL)
    if row is None:
        return {
            "total_inventory_value": 0.0,
            "products_count": 0,
            "low_stock_count": 0,
            "out_of_stock_count": 0,
        }
    return {
        "total_inventory_value": float(row.get("total_inventory_value") or 0),
        "products_count": int(row.get("products_count") or 0),
        "low_stock_count": int(row.get("low_stock_count") or 0),
        "out_of_stock_count": int(row.get("out_of_stock_count") or 0),
    }
