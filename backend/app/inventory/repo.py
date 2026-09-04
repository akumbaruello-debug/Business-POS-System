"""Inventory repository — read-only queries for the E.5 endpoints.

Backs :mod:`app.inventory.service` with the SQL helpers that drive the
three ``/api/v1/inventory/*`` operations:

* ``list_inventory``       — paginated per-product summary list.
* ``get_product_stock``    — single product snapshot.
* ``list_low_stock``       — paginated list filtered to the low-stock predicate.

The ``product_valuation`` view is the canonical stock read path (it
already exposes ``on_hand_quantity`` and ``inventory_value``). The
moving-average unit cost is derived app-side in
:func:`app.inventory.valuation.compute_moving_average_unit_cost`; we
do NOT compute it in SQL because the view columns are the source of
truth.

The low-stock predicate matches the partial index
``ix_products_low_stock`` (active + non-null threshold) so
``list_low_stock`` can be served from an index-only scan.

ruff: S608 ignored — every interpolated fragment is either a hard-coded
identifier (column/table aliases, sort direction) or a value pulled
from a hard-coded whitelist (``_order_by``). Bind parameters are used
for every user-supplied value.
"""

# ruff: noqa: S608

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.db import UnitOfWork

__all__ = [
    "count_inventory",
    "count_low_stock",
    "count_stock_movements",
    "get_product_for_update",
    "get_product_stock_row",
    "get_setting_bool",
    "get_stock_movement_by_id",
    "insert_stock_movement",
    "list_inventory_rows",
    "list_low_stock_rows",
    "list_stock_movement_rows",
    "lock_product_for_update",
]


# -----------------------------------------------------------------------------
# E.7 — stock_movements read helpers (listStockMovements, getStockMovement)
# -----------------------------------------------------------------------------
#
# ``stock_movements`` is the authoritative inventory ledger (append-only
# per Backend-Architecture §12 + the immutability triggers in schema.sql
# §18.2). E.7 exposes the top-level read path for the ledger; the
# per-product sub-resource ``GET /products/{id}/stock-movements`` lives
# in ``app.services.products`` and reuses the same SQL building blocks
# (the B.7 implementation was the prototype for this one).

# Whitelisted sort keys for ``listStockMovements``. ``-`` prefix = DESC.
# Anything else falls back to a stable primary-key order so we never
# expose arbitrary SQL injection through the ``sort`` query parameter.
_STOCK_MOVEMENT_SORT: dict[str, str] = {
    "id": "sm.id ASC",
    "-id": "sm.id DESC",
    "movement_date": "sm.movement_date ASC, sm.id ASC",
    "-movement_date": "sm.movement_date DESC, sm.id ASC",
    "product_id": "sm.product_id ASC, sm.id ASC",
    "-product_id": "sm.product_id DESC, sm.id ASC",
    "created_at": "sm.created_at ASC, sm.id ASC",
    "-created_at": "sm.created_at DESC, sm.id ASC",
}


def _stock_movement_order_by(sort: str) -> str:
    return _STOCK_MOVEMENT_SORT.get((sort or "id").strip(), "sm.id ASC")


def _stock_movement_where(
    *,
    product_id: int | None,
    trigger: str | None,
    reference_type: str | None,
    reference_id: int | None,
    from_iso: str | None = None,
    to_iso: str | None = None,
    q: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Build the WHERE clause + bind params for the stock_movements list.

    All four filters are optional and may be combined. ``trigger`` and
    ``reference_type`` are validated against the DB ``ck_sm_trigger``
    enum implicitly by passing the bind value — invalid strings will
    surface as a ``CheckViolationError`` from the DB, which the error
    envelope translates to a 4xx (we do not pre-validate here so the
    DB remains the source of truth for the enum set).

    ``from_iso`` / ``to_iso`` (F.1 — added for the inventory-movements
    report) are inclusive bounds on ``sm.movement_date``. Both default
    to ``None`` so the E.7 / B.7 callers continue to work unchanged;
    the F.1 report path is the only consumer of these kwargs.

    ``q`` (F.1) is a case-insensitive substring search against the two
    text columns on the ledger: ``trigger`` and ``reference_type``.
    Defaults to ``None`` for E.7 / B.7 backward compatibility.
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if product_id is not None:
        clauses.append("sm.product_id = :product_id")
        params["product_id"] = int(product_id)
    if trigger is not None:
        clauses.append("sm.trigger = :trigger")
        params["trigger"] = str(trigger)
    if reference_type is not None:
        clauses.append("sm.reference_type = :reference_type")
        params["reference_type"] = str(reference_type)
    if reference_id is not None:
        # Pair the reference_type+id when both are present so a bare
        # reference_id without a type does not match a different
        # reference kind by accident. reference_id alone is still
        # allowed (e.g. ops debugging across tables).
        if reference_type is not None:
            clauses.append("sm.reference_id = :reference_id")
        else:
            clauses.append("sm.reference_id = :reference_id")
        params["reference_id"] = int(reference_id)
    # F.1 — period filter for the inventory-movements report.
    if from_iso is not None:
        clauses.append("sm.movement_date >= :from_iso")
        params["from_iso"] = from_iso
    if to_iso is not None:
        clauses.append("sm.movement_date <= :to_iso")
        params["to_iso"] = to_iso
    # F.1 — q search across the two text columns on the ledger.
    if q:
        clauses.append(
            "(sm.trigger ILIKE :q OR sm.reference_type ILIKE :q)"
        )
        params["q"] = f"%{q}%"
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


_STOCK_MOVEMENT_COLUMNS = (
    "sm.id, sm.product_id, sm.movement_date, sm.trigger, sm.quantity,"
    " sm.unit_cost_at_movement, sm.total_cost, sm.reference_type,"
    " sm.reference_id, sm.reference_line_id, sm.reversal_of_movement_id,"
    " sm.reversed_by_movement_id, sm.reason, sm.created_at, sm.created_by"
)


async def count_stock_movements(
    uow: UnitOfWork,
    *,
    product_id: int | None,
    trigger: str | None,
    reference_type: str | None,
    reference_id: int | None,
    from_iso: str | None = None,
    to_iso: str | None = None,
    q: str | None = None,
) -> int:
    """Count rows that match the ``listStockMovements`` filters.

    ``from_iso``/``to_iso``/``q`` were added in F.1 (inventory-movements
    report) and default to ``None`` so the E.7 / B.7 callers are
    unaffected.
    """
    where, params = _stock_movement_where(
        product_id=product_id,
        trigger=trigger,
        reference_type=reference_type,
        reference_id=reference_id,
        from_iso=from_iso,
        to_iso=to_iso,
        q=q,
    )
    sql = f"SELECT COUNT(*) FROM stock_movements sm {where}"
    total = await uow.scalar(sql, params)
    return int(total or 0)


async def list_stock_movement_rows(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    sort: str,
    product_id: int | None,
    trigger: str | None,
    reference_type: str | None,
    reference_id: int | None,
    from_iso: str | None = None,
    to_iso: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    """Paginated rows for ``listStockMovements``.

    Mirrors the B.7 sub-resource shape so the route layer can pass the
    rows through the same ``StockMovement`` Pydantic model.

    ``from_iso``/``to_iso``/``q`` were added in F.1 (inventory-movements
    report) and default to ``None`` so the E.7 / B.7 callers are
    unaffected.
    """
    where, params = _stock_movement_where(
        product_id=product_id,
        trigger=trigger,
        reference_type=reference_type,
        reference_id=reference_id,
        from_iso=from_iso,
        to_iso=to_iso,
        q=q,
    )
    order = _stock_movement_order_by(sort)
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"SELECT {_STOCK_MOVEMENT_COLUMNS} FROM stock_movements sm {where} "
        f"ORDER BY {order} LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


# SQL fragment used in COUNT() and paginated SELECT. Keeps the
# ``product_valuation`` view as the single source of truth for stock
# numbers while joining the master row for the metadata we need for
# filtering and for the contract response (low_stock threshold).
#
# ``on_hand_quantity`` and ``inventory_value`` come straight from the
# view; the rest is pulled from ``products``.
_BASE_SELECT = """
SELECT
    pv.product_id,
    pv.code,
    pv.name,
    pv.on_hand_quantity,
    pv.inventory_value,
    p.category_id,
    p.low_stock_threshold,
    p.is_active,
    p.updated_at
FROM product_valuation pv
JOIN products p ON p.id = pv.product_id
"""

# Same shape but excluding columns the response doesn't need.
_BASE_LIST_SELECT = """
SELECT
    pv.product_id,
    pv.code,
    pv.name,
    pv.on_hand_quantity,
    pv.inventory_value,
    p.low_stock_threshold,
    p.is_active,
    p.updated_at
FROM product_valuation pv
JOIN products p ON p.id = pv.product_id
"""


def _where(
    *,
    q: str | None,
    category_id: int | None,
    is_active: bool | None,
    from_iso: str | None,
    to_iso: str | None,
    low_stock_only: bool,
) -> tuple[str, dict[str, Any]]:
    """Build the WHERE clause + bind params shared by COUNT and SELECT.

    ``low_stock_only`` is computed at the SQL boundary so the filter
    lines up with the partial index. The predicate mirrors the OpenAPI
    contract: an active product with a non-null threshold that has
    dropped at or below the threshold.
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if q:
        # Search on code/name; both fields are present in the base SELECT.
        clauses.append("(pv.name ILIKE :q OR pv.code ILIKE :q)")
        params["q"] = f"%{q}%"
    if category_id is not None:
        clauses.append("p.category_id = :category_id")
        params["category_id"] = int(category_id)
    if is_active is not None:
        clauses.append("p.is_active = :is_active")
        params["is_active"] = bool(is_active)
    if from_iso is not None:
        clauses.append("p.updated_at >= :from_iso")
        params["from_iso"] = from_iso
    if to_iso is not None:
        clauses.append("p.updated_at <= :to_iso")
        params["to_iso"] = to_iso
    if low_stock_only:
        # Mirrors the partial index predicate in schema.sql §5.3 so the
        # planner can use ``ix_products_low_stock``.
        clauses.append(
            "p.is_active = TRUE "
            "AND p.low_stock_threshold IS NOT NULL "
            "AND pv.on_hand_quantity <= p.low_stock_threshold"
        )
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _order_by(sort: str) -> str:
    """Resolve a small whitelisted set of sort keys.

    The set is intentionally narrow — anything else falls back to a
    stable primary-key order so we never expose arbitrary SQL injection
    vectors through the sort query parameter.
    """
    sort = (sort or "id").strip()
    # Whitelist. ``-`` prefix = descending.
    allowed = {
        "id": "pv.product_id ASC",
        "-id": "pv.product_id DESC",
        "on_hand_quantity": "pv.on_hand_quantity ASC, pv.product_id ASC",
        "-on_hand_quantity": "pv.on_hand_quantity DESC, pv.product_id ASC",
        "inventory_value": "pv.inventory_value ASC, pv.product_id ASC",
        "-inventory_value": "pv.inventory_value DESC, pv.product_id ASC",
        "updated_at": "p.updated_at ASC, pv.product_id ASC",
        "-updated_at": "p.updated_at DESC, pv.product_id ASC",
    }
    return allowed.get(sort, "pv.product_id ASC")


async def count_inventory(
    uow: UnitOfWork,
    *,
    q: str | None,
    category_id: int | None,
    is_active: bool | None,
    from_iso: str | None,
    to_iso: str | None,
) -> int:
    """Count rows that satisfy the listInventory filters (no low-stock filter)."""
    where, params = _where(
        q=q,
        category_id=category_id,
        is_active=is_active,
        from_iso=from_iso,
        to_iso=to_iso,
        low_stock_only=False,
    )
    sql = f"SELECT COUNT(*) FROM product_valuation pv JOIN products p ON p.id = pv.product_id {where}"
    total = await uow.scalar(sql, params)
    return int(total or 0)


async def list_inventory_rows(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    sort: str,
    q: str | None,
    category_id: int | None,
    is_active: bool | None,
    from_iso: str | None,
    to_iso: str | None,
) -> list[dict[str, Any]]:
    """Paginated rows for ``listInventory``."""
    where, params = _where(
        q=q,
        category_id=category_id,
        is_active=is_active,
        from_iso=from_iso,
        to_iso=to_iso,
        low_stock_only=False,
    )
    order = _order_by(sort)
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"{_BASE_LIST_SELECT} {where} ORDER BY {order} "
        "LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


async def count_low_stock(
    uow: UnitOfWork,
    *,
    q: str | None,
    category_id: int | None,
    from_iso: str | None,
    to_iso: str | None,
) -> int:
    """Count products at or below their low-stock threshold."""
    where, params = _where(
        q=q,
        category_id=category_id,
        is_active=None,  # low_stock_only forces is_active=TRUE
        from_iso=from_iso,
        to_iso=to_iso,
        low_stock_only=True,
    )
    sql = f"SELECT COUNT(*) FROM product_valuation pv JOIN products p ON p.id = pv.product_id {where}"
    total = await uow.scalar(sql, params)
    return int(total or 0)


async def list_low_stock_rows(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    sort: str,
    q: str | None,
    category_id: int | None,
    from_iso: str | None,
    to_iso: str | None,
) -> list[dict[str, Any]]:
    """Paginated rows for ``listLowStock``."""
    where, params = _where(
        q=q,
        category_id=category_id,
        is_active=None,
        from_iso=from_iso,
        to_iso=to_iso,
        low_stock_only=True,
    )
    order = _order_by(sort)
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"{_BASE_LIST_SELECT} {where} ORDER BY {order} "
        "LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


async def get_product_stock_row(
    uow: UnitOfWork,
    *,
    product_id: int,
) -> dict[str, Any] | None:
    """Single-product stock snapshot, or ``None`` if the product is missing."""
    sql = f"{_BASE_SELECT} WHERE pv.product_id = :pid"
    rows = await uow.fetch_all(sql, {"pid": int(product_id)})
    return rows[0] if rows else None


# -----------------------------------------------------------------------------
# E.6 — Stock-adjustment helpers
# -----------------------------------------------------------------------------
#
# The stock-adjustment endpoint (`POST /inventory/adjustments`) is the only
# write path that mutates stock through the inventory module. It needs:
#   * a row-level FOR UPDATE lock on the product (to serialise against
#     concurrent sales/production posts consuming the same product);
#   * the product's allow_negative_stock override (NULL → fall back to
#     the system_settings.default_negative_stock_allowed global default);
#   * the current on-hand snapshot (computed app-side from the
#     product_valuation view; sign-aware math happens in the service);
#   * a stock_movements INSERT with trigger='stock_adjustment' and a
#     caller-supplied reason (V1: no P&L effect — Backend-Architecture
#     §12.5 / Reconciliation Report C-07).
#
# We deliberately re-use the canonical SQL fragments from the E.5 helpers
# rather than redefining them here — single source of truth for the
# per-product stock snapshot.

_PRODUCT_LOCK_COLS = (
    "id, is_active, allow_negative_stock, low_stock_threshold, "
    "purchase_price, updated_at"
)


async def get_product_for_update(
    uow: UnitOfWork,
    *,
    product_id: int,
) -> dict[str, Any] | None:
    """``SELECT ... FOR UPDATE`` on the product row.

    Per Backend-Architecture §15.6 + §15.8 step 1 ("Lock products in ASC
    order"): a row-level lock inside the same transaction serialises
    concurrent stock-affecting endpoints (sale post, production post,
    stock adjustment) against the same product. The caller is
    responsible for any multi-product ASC ordering discipline; this
    helper only acquires the lock for one product.
    """
    return await uow.first_row(
        f"SELECT {_PRODUCT_LOCK_COLS} FROM products "
        "WHERE id = :pid FOR UPDATE",
        {"pid": int(product_id)},
    )


# Alias kept for naming parity with ``app.production.repo.lock_product_for_update``.
lock_product_for_update = get_product_for_update


async def get_setting_bool(
    uow: UnitOfWork,
    *,
    key: str,
    default: bool | None = None,
) -> bool | None:
    """Read a boolean ``system_settings`` row.

    Thin convenience over ``app.inventory.valuation.get_settings_bool`` —
    we re-export here so the inventory service does not have to reach
    into the valuation module for this single key.
    """
    from app.inventory.valuation import get_settings_bool as _get

    return await _get(uow, key, default=default)


async def insert_stock_movement(
    uow: UnitOfWork,
    *,
    product_id: int,
    trigger: str,
    quantity: Decimal,
    unit_cost_at_movement: Decimal | None,
    total_cost: Decimal | None,
    reference_type: str | None = None,
    reference_id: int | None = None,
    reference_line_id: int | None = None,
    reason: str | None = None,
    reversal_of_movement_id: int | None = None,
    reversed_by_movement_id: int | None = None,
    created_by: int,
) -> dict[str, Any] | None:
    """Insert one ``stock_movements`` row and return the persisted record.

    Mirrors ``app.production.repo.insert_stock_movement`` — same
    contract, same column set. The DB enforces:

    * ``ck_sm_trigger``  — ``trigger`` must be one of the 15 valid values
      (including ``stock_adjustment``).
    * ``ck_sm_quantity`` — quantity ≠ 0 except for ``value_adjustment``
      (which stock_adjustment is not).
    * ``ck_sm_total_cost_nonzero`` — total_cost carries the sign of
          quantity and is non-zero. Stock adjustments use the current MAUC
          snapshot times the signed qty (see E.6 service for the derivation).
    * Append-only — UPDATE/DELETE on this table is rejected by trigger.

    Returns the new row (id, product_id, trigger, quantity, …) so the
    service can hand it back to the client as the StockMovement
    response payload.
    """
    return await uow.first_row(
        """
        INSERT INTO stock_movements (
            product_id, trigger, quantity,
            unit_cost_at_movement, total_cost,
            reference_type, reference_id, reference_line_id,
            reversal_of_movement_id, reversed_by_movement_id,
            reason, created_by
        ) VALUES (
            :product_id, :trigger, :quantity,
            :unit_cost_at_movement, :total_cost,
            :reference_type, :reference_id, :reference_line_id,
            :reversal_of_movement_id, :reversed_by_movement_id,
            :reason, :created_by
        )
        RETURNING
            id, product_id, movement_date, trigger, quantity,
            unit_cost_at_movement, total_cost,
            reference_type, reference_id, reference_line_id,
            reversal_of_movement_id, reversed_by_movement_id,
            reason, created_at, created_by
        """,
        {
            "product_id": int(product_id),
            "trigger": str(trigger),
            "quantity": quantity,
            "unit_cost_at_movement": unit_cost_at_movement,
            "total_cost": total_cost,
            "reference_type": reference_type,
            "reference_id": reference_id,
            "reference_line_id": reference_line_id,
            "reversal_of_movement_id": reversal_of_movement_id,
            "reversed_by_movement_id": reversed_by_movement_id,
            "reason": reason,
            "created_by": int(created_by),
        },
    )


async def get_stock_movement_by_id(
    uow: UnitOfWork,
    *,
    movement_id: int,
) -> dict[str, Any] | None:
    """Read a single stock_movements row by primary key.

    Used by the idempotency replay path so the cached response can be
    re-shaped against the contract (``StockMovement``) without re-running
    the whole transaction.
    """
    return await uow.first_row(
        """
        SELECT
            id, product_id, movement_date, trigger, quantity,
            unit_cost_at_movement, total_cost,
            reference_type, reference_id, reference_line_id,
            reversal_of_movement_id, reversed_by_movement_id,
            reason, created_at, created_by
        FROM stock_movements
        WHERE id = :id
        """,
        {"id": int(movement_id)},
    )
