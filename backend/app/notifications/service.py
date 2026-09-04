"""E.10 — Low-stock notification service.

Threshold-crossing detection and ``notifications`` INSERT logic.

Business rules (Backend-Architecture §21.2 + §13.6):

* **low_stock**: fires when a product's on-hand crosses from
  ``> threshold`` to ``<= threshold``.  ``severity = 'info'``.
* **insufficient_stock** (DB representation of the architecture's
  "out_of_stock" concept): fires when on-hand crosses from ``> 0``
  to ``<= 0``.  ``severity = 'warning'``.

  NOTE: The DB CHECK constraint on ``notifications.category`` does
  NOT allow the string ``'out_of_stock'`` — only
  ``'insufficient_stock'``.  The OpenAPI ``Notification.type`` enum
  uses ``'out_of_stock'``; that mapping is F.8's concern (the API
  read layer).

Duplicate prevention follows *crossing* semantics: if on-hand is
already at or below threshold, a further drop does NOT generate a
duplicate.  If stock recovers above threshold and then drops again,
a new notification IS generated.

Threshold resolution:
  1. ``products.low_stock_threshold`` (per-product override)
  2. ``system_settings`` row where ``key = 'default_low_stock_threshold'``
  3. fallback: ``0`` (only out-of-stock fires)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.db import UnitOfWork
from app.logging import get_logger

logger = get_logger(__name__)

__all__ = ["evaluate_stock_notification"]


async def _get_default_threshold(uow: UnitOfWork) -> Decimal:
    """Read ``default_low_stock_threshold`` from ``system_settings``."""
    row = await uow.first_row(
        "SELECT value FROM system_settings "
        "WHERE key = 'default_low_stock_threshold'",
    )
    if row is None:
        return Decimal("0")
    try:
        return Decimal(str(row["value"]))
    except (TypeError, ValueError, ArithmeticError):
        return Decimal("0")


async def _get_on_hand(uow: UnitOfWork, product_id: int) -> Decimal:
    """Current on-hand from ``product_valuation`` view."""
    row = await uow.first_row(
        "SELECT on_hand_quantity FROM product_valuation "
        "WHERE product_id = :pid",
        {"pid": product_id},
    )
    if row is None:
        return Decimal("0")
    val = row.get("on_hand_quantity")
    if val is None:
        return Decimal("0")
    return Decimal(str(val))


async def _get_product_threshold(
    uow: UnitOfWork, product_id: int
) -> tuple[Decimal, bool]:
    """Return (threshold, is_active) for a product.

    If the product has no per-product threshold, falls back to the
    system default.
    """
    row = await uow.first_row(
        "SELECT low_stock_threshold, is_active FROM products WHERE id = :pid",
        {"pid": product_id},
    )
    if row is None:
        return Decimal("0"), False

    is_active = bool(row.get("is_active"))
    threshold_raw = row.get("low_stock_threshold")
    if threshold_raw is not None:
        try:
            return Decimal(str(threshold_raw)), is_active
        except (TypeError, ValueError, ArithmeticError):
            pass
    # Fall back to system default
    default = await _get_default_threshold(uow)
    return default, is_active


async def _get_on_hand_before_movement(
    uow: UnitOfWork, product_id: int, movement_id: int
) -> Decimal:
    """Compute what on-hand was BEFORE the given movement.

    on_hand_before = current_on_hand - quantity_of_this_movement

    This works because ``product_valuation`` is a live SUM view.
    """
    current = await _get_on_hand(uow, product_id)
    row = await uow.first_row(
        "SELECT quantity FROM stock_movements WHERE id = :mid",
        {"mid": movement_id},
    )
    if row is None:
        return current
    try:
        qty = Decimal(str(row["quantity"]))
    except (TypeError, ValueError, ArithmeticError):
        return current
    return current - qty


async def _insert_notification(
    uow: UnitOfWork,
    *,
    category: str,
    severity: str,
    title: str,
    body: str | None,
    reference_type: str | None,
    reference_id: int | None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """INSERT a row into ``notifications`` and return it."""
    row = await uow.first_row(
        """
        INSERT INTO notifications
            (user_id, category, severity, title, body,
             reference_type, reference_id, is_read, read_at)
        VALUES
            (:user_id, :category, :severity, :title, :body,
             :ref_type, :ref_id, FALSE, NULL)
        RETURNING id, category, severity, title, body,
                  reference_type, reference_id, is_read, created_at
        """,
        {
            "user_id": user_id,
            "category": category,
            "severity": severity,
            "title": title,
            "body": body,
            "ref_type": reference_type,
            "ref_id": reference_id,
        },
    )
    return row or {}


async def evaluate_stock_notification(
    uow: UnitOfWork,
    *,
    product_id: int,
    movement_id: int,
) -> list[dict[str, Any]]:
    """Evaluate whether a stock movement caused a threshold crossing.

    Called by the E.10 worker after receiving a ``stock_change``
    notification.  Runs inside its own UoW transaction.

    Returns a list of notification rows created (0, 1, or 2).
    """
    threshold, is_active = await _get_product_threshold(uow, product_id)

    # Inactive products don't generate stock notifications.
    if not is_active:
        return []

    on_hand_after = await _get_on_hand(uow, product_id)
    on_hand_before = await _get_on_hand_before_movement(
        uow, product_id, movement_id
    )

    created: list[dict[str, Any]] = []

    # --- low_stock ---
    # Crossing: was above threshold, now at or below.
    if on_hand_before > threshold and on_hand_after <= threshold:
        product_name = await _get_product_name(uow, product_id)
        row = await _insert_notification(
            uow,
            category="low_stock",
            severity="info",
            title=f"Low stock: {product_name}",
            body=(
                f"Product '{product_name}' (ID {product_id}) dropped to "
                f"{on_hand_after} units, at or below the threshold of "
                f"{threshold}."
            ),
            reference_type="product",
            reference_id=product_id,
        )
        created.append(row)
        logger.info(
            "low_stock_notification_created",
            product_id=product_id,
            on_hand_before=str(on_hand_before),
            on_hand_after=str(on_hand_after),
            threshold=str(threshold),
        )

    # --- insufficient_stock (out-of-stock) ---
    # Crossing: was above zero, now at or below zero.
    # This is separate from low_stock — both can fire for the same
    # movement when threshold > 0 and stock drops to 0.
    if on_hand_before > 0 and on_hand_after <= 0:
        product_name = await _get_product_name(uow, product_id)
        row = await _insert_notification(
            uow,
            category="insufficient_stock",
            severity="warning",
            title=f"Out of stock: {product_name}",
            body=(
                f"Product '{product_name}' (ID {product_id}) is now "
                f"out of stock (on-hand: {on_hand_after})."
            ),
            reference_type="product",
            reference_id=product_id,
        )
        created.append(row)
        logger.info(
            "out_of_stock_notification_created",
            product_id=product_id,
            on_hand_before=str(on_hand_before),
            on_hand_after=str(on_hand_after),
        )

    return created


async def _get_product_name(uow: UnitOfWork, product_id: int) -> str:
    """Fetch product name for notification messages."""
    row = await uow.first_row(
        "SELECT name FROM products WHERE id = :pid",
        {"pid": product_id},
    )
    if row is None:
        return f"Product #{product_id}"
    return str(row.get("name", f"Product #{product_id}"))
