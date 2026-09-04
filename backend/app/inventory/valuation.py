"""Inventory valuation helpers.

Implements the server-side derivation of stock metrics required by the
OpenAPI contract:

* ``on_hand_quantity``  — sum of signed ``stock_movements.quantity`` per product.
* ``inventory_value``   — sum of signed ``stock_movements.total_cost`` per product.
* ``moving_average_unit_cost`` — inventory_value / on_hand_quantity, when stock > 0.

The locked ``product_valuation`` view exposes only ``on_hand_quantity``
and ``inventory_value`` (no ``moving_average_unit_cost``). Per the
approved BLOCKER-1 resolution, this module computes the moving average
in app code from the two view columns, so the endpoint contract
(``GET /products/{id}/valuation`` returns
``{ product_id, on_hand_quantity, moving_average_unit_cost,
inventory_value, as_of }``) is satisfied without altering the locked
schema.

The DB is the source of truth for all stock metrics. The view is the
canonical read path; this module adds the missing derived column.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import UnitOfWork
from app.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "compute_moving_average_unit_cost",
    "get_product_valuation",
]


def compute_moving_average_unit_cost(
    *,
    on_hand_quantity: Any,
    inventory_value: Any,
) -> float | None:
    """Derive ``moving_average_unit_cost`` from view columns.

    Returns ``None`` when stock is zero (the OpenAPI schema declares the
    field nullable; clients must handle the null case as "no cost basis
    available yet").
    """
    qty_raw = on_hand_quantity
    val_raw = inventory_value
    if qty_raw is None or val_raw is None:
        return None
    try:
        qty = float(qty_raw)
        val = float(val_raw)
    except (TypeError, ValueError):
        return None
    if qty == 0:
        return None
    return val / qty


async def get_product_valuation(
    uow: UnitOfWork,
    *,
    product_id: int,
    as_of: datetime,
) -> dict[str, Any] | None:
    """Read the per-product valuation snapshot for the OpenAPI contract.

    Returns ``None`` if the product does not exist; otherwise returns
    the contract-shaped dict including the derived
    ``moving_average_unit_cost``.

    The view is::

        SELECT product_id, code, name, on_hand_quantity, inventory_value
        FROM product_valuation
        WHERE product_id = :id
    """
    row = await uow.first_row(
        """
        SELECT product_id, code, name, on_hand_quantity, inventory_value
        FROM product_valuation
        WHERE product_id = :id
        """,
        {"id": product_id},
    )
    if row is None:
        return None
    on_hand = row.get("on_hand_quantity")
    inv_value = row.get("inventory_value")
    mauc = compute_moving_average_unit_cost(
        on_hand_quantity=on_hand,
        inventory_value=inv_value,
    )
    return {
        "product_id": int(row["product_id"]),
        "on_hand_quantity": on_hand,
        "moving_average_unit_cost": mauc,
        "inventory_value": inv_value,
        "as_of": as_of,
    }


async def get_settings_number(
    uow: UnitOfWork,
    key: str,
    *,
    default: float | None = None,
) -> float | None:
    """Read a numeric ``system_settings`` row.

    Returns the parsed number on success, the supplied ``default`` on
    missing key, or raises a defensive :class:`ValueError` on a stored
    value that cannot be parsed.
    """
    row = await uow.first_row(
        "SELECT value, value_type FROM system_settings WHERE key = :k",
        {"k": key},
    )
    if row is None:
        return default
    raw = row.get("value")
    vtype = row.get("value_type")
    if raw is None:
        return default
    if vtype == "number":
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"system_settings.{key} value {raw!r} is not numeric") from exc
    # Tolerate stringified numbers for resilience.
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"system_settings.{key} value {raw!r} is not numeric (value_type={vtype})"
        ) from exc


async def get_settings_bool(
    uow: UnitOfWork,
    key: str,
    *,
    default: bool | None = None,
) -> bool | None:
    """Read a boolean ``system_settings`` row."""
    row = await uow.first_row(
        "SELECT value, value_type FROM system_settings WHERE key = :k",
        {"k": key},
    )
    if row is None:
        return default
    raw = row.get("value")
    vtype = row.get("value_type")
    if raw is None:
        return default
    if vtype == "boolean":
        return str(raw).strip().lower() in ("true", "1", "t", "yes", "on")
    return str(raw).strip().lower() in ("true", "1", "t", "yes", "on")
