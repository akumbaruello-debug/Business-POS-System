"""Products report — list-style export of the products table joined
to the ``product_valuation`` view (single source of truth for
on_hand_quantity + inventory_value).

Used by the export pipeline (F.6) when ``report == "products"``.

Filters:
* ``period`` alias: today / this_week / this_month / this_year / all
  (``all`` is the default — no created_at predicate is applied).
* ``from_iso`` / ``to_iso``: explicit ISO range; applied to ``created_at``.
* ``is_active``: True/False/None
* ``category_id``: int
* ``unit_id``: int

``created_at`` is the only date dimension the products table exposes
(per the locked schema). Deactivation / edits do not change
``created_at`` — those products still appear in the export as of
their original creation date.

The output is a list of dicts shaped for ``ExportService._normalize_rows``
(flat keys + scalar values). The renderer turns them into PDF/XLSX
without further work.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import UnitOfWork

__all__ = ["fetch_products_report"]


_BASE_SELECT = """
SELECT
    p.id,
    p.code,
    p.name,
    p.category_id,
    p.unit_id,
    p.purchase_price,
    p.selling_price,
    p.low_stock_threshold,
    p.is_sellable,
    p.is_purchasable,
    p.is_producible,
    p.is_active,
    p.created_at,
    p.updated_at,
    COALESCE(pv.on_hand_quantity, 0) AS on_hand_quantity,
    COALESCE(pv.inventory_value, 0) AS inventory_value
FROM products p
LEFT JOIN product_valuation pv ON pv.product_id = p.id
"""


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string into a ``datetime`` (UTC fallback)."""
    if value is None:
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return datetime.fromisoformat(v)


async def fetch_products_report(
    uow: UnitOfWork,
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return rows for a products export.

    See module docstring for accepted filters.
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}

    is_active = filters.get("is_active")
    if is_active is not None:
        clauses.append("p.is_active = :is_active")
        params["is_active"] = bool(is_active)

    category_id = filters.get("category_id")
    if category_id is not None:
        clauses.append("p.category_id = :category_id")
        params["category_id"] = int(category_id)

    unit_id = filters.get("unit_id")
    if unit_id is not None:
        clauses.append("p.unit_id = :unit_id")
        params["unit_id"] = int(unit_id)

    # Date range — applied to ``created_at``. Period aliases are
    # resolved by the caller (ExportService) into from_iso / to_iso.
    from_iso = filters.get("from_iso")
    to_iso = filters.get("to_iso")
    if from_iso is not None:
        clauses.append("p.created_at >= :from_iso")
        params["from_iso"] = _parse_dt(from_iso)
    if to_iso is not None:
        clauses.append("p.created_at <= :to_iso")
        params["to_iso"] = _parse_dt(to_iso)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = (
        f"{_BASE_SELECT} {where} "  # noqa: S608 — closed whitelist literals only
        "ORDER BY p.id ASC"
    )
    rows = await uow.fetch_all(sql, params)

    # Flatten the rows for the renderer. We keep numeric fields as
    # their native types (renderer stringifies via str()).
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "id": row.get("id"),
                "code": row.get("code") or "",
                "name": row.get("name") or "",
                "category_id": row.get("category_id"),
                "unit_id": row.get("unit_id"),
                "purchase_price": row.get("purchase_price"),
                "selling_price": row.get("selling_price"),
                "low_stock_threshold": row.get("low_stock_threshold"),
                "is_sellable": bool(row.get("is_sellable")),
                "is_purchasable": bool(row.get("is_purchasable")),
                "is_producible": bool(row.get("is_producible")),
                "is_active": bool(row.get("is_active")),
                "on_hand_quantity": row.get("on_hand_quantity"),
                "inventory_value": row.get("inventory_value"),
                "created_at": _isoformat(row.get("created_at")),
                "updated_at": _isoformat(row.get("updated_at")),
            }
        )
    return result


def _isoformat(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
