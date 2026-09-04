"""Manual finance entry repository (E.8).

Backs :mod:`app.manual_entry.service` with SQL helpers for the four
``/api/v1/manual-entries/*`` operations:

* ``list_manual_entries``  — paginated list with filters.
* ``get_manual_entry``     — single row by id.
* ``insert_manual_entry``  — create (posted) row.
* ``cancel_manual_entry``  — lifecycle flip + reversal cash_movement.

The ``manual_finance_entries`` table is the authoritative record of
manual income/expense entries (schema.sql §12.1). Each posted entry is
paired with a ``cash_movements`` row in the same transaction; cancel
creates a reversing cash_movement. The DB trigger
``fn_cash_movements_balance_check`` enforces INV-03 (Cash >= 0) on
every cash_movement insert.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.db import UnitOfWork

# ruff: noqa: S608  — every interpolated SQL fragment is a hard-coded column
# name or a value bound via params (:name); no user string is spliced in.

__all__ = [
    "count_manual_entries",
    "get_manual_entry",
    "insert_cash_movement_for_manual",
    "insert_manual_entry",
    "list_manual_entry_rows",
    "update_manual_entry_cancel",
]


# ---------------------------------------------------------------------------
# List / count helpers
# ---------------------------------------------------------------------------

_SORT_KEYS: dict[str, str] = {
    "id": "mfe.id ASC",
    "-id": "mfe.id DESC",
    "entry_date": "mfe.entry_date ASC, mfe.id ASC",
    "-entry_date": "mfe.entry_date DESC, mfe.id ASC",
    "amount": "mfe.amount ASC, mfe.id ASC",
    "-amount": "mfe.amount DESC, mfe.id ASC",
    "created_at": "mfe.created_at ASC, mfe.id ASC",
    "-created_at": "mfe.created_at DESC, mfe.id ASC",
}


def _order_by(sort: str) -> str:
    return _SORT_KEYS.get((sort or "id").strip(), "mfe.id ASC")


def _where(
    *,
    entry_type: str | None,
    category_id: int | None,
    lifecycle_status: str | None,
    from_iso: str | None,
    to_iso: str | None,
    q: str | None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if entry_type is not None:
        clauses.append("fc.entry_type = :entry_type")
        params["entry_type"] = entry_type
    if category_id is not None:
        clauses.append("mfe.category_id = :category_id")
        params["category_id"] = int(category_id)
    if lifecycle_status is not None:
        clauses.append("mfe.lifecycle_status = :lifecycle_status")
        params["lifecycle_status"] = lifecycle_status
    if from_iso is not None:
        clauses.append("mfe.entry_date >= :from_iso")
        params["from_iso"] = from_iso
    if to_iso is not None:
        clauses.append("mfe.entry_date <= :to_iso")
        params["to_iso"] = to_iso
    if q:
        # Search on notes or category name.
        clauses.append("(mfe.notes ILIKE :q OR fc.name ILIKE :q)")
        params["q"] = f"%{q}%"
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


_LIST_COLS = (
    "mfe.id, mfe.category_id, fc.name AS category_name, fc.entry_type, "
    "mfe.amount, mfe.payment_method_id, mfe.entry_date, mfe.notes, "
    "mfe.lifecycle_status, mfe.cancellation_date, mfe.cancellation_reason, "
    "mfe.cancelled_by, mfe.created_at, mfe.created_by, mfe.version"
)


async def get_manual_entry(
    uow: UnitOfWork, *, entry_id: int
) -> dict[str, Any] | None:
    sql = (
        f"SELECT {_LIST_COLS} FROM manual_finance_entries mfe "
        "JOIN financial_categories fc ON fc.id = mfe.category_id "
        "WHERE mfe.id = :id"
    )
    return await uow.first_row(sql, {"id": int(entry_id)})


async def count_manual_entries(
    uow: UnitOfWork,
    *,
    entry_type: str | None,
    category_id: int | None,
    lifecycle_status: str | None,
    from_iso: str | None,
    to_iso: str | None,
    q: str | None,
) -> int:
    where, params = _where(
        entry_type=entry_type,
        category_id=category_id,
        lifecycle_status=lifecycle_status,
        from_iso=from_iso,
        to_iso=to_iso,
        q=q,
    )
    sql = (
        f"SELECT COUNT(*) FROM manual_finance_entries mfe "
        f"JOIN financial_categories fc ON fc.id = mfe.category_id {where}"
    )
    total = await uow.scalar(sql, params)
    return int(total or 0)


async def list_manual_entry_rows(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    sort: str,
    entry_type: str | None,
    category_id: int | None,
    lifecycle_status: str | None,
    from_iso: str | None,
    to_iso: str | None,
    q: str | None,
) -> list[dict[str, Any]]:
    where, params = _where(
        entry_type=entry_type,
        category_id=category_id,
        lifecycle_status=lifecycle_status,
        from_iso=from_iso,
        to_iso=to_iso,
        q=q,
    )
    order = _order_by(sort)
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"SELECT {_LIST_COLS} FROM manual_finance_entries mfe "
        f"JOIN financial_categories fc ON fc.id = mfe.category_id {where} "
        f"ORDER BY {order} LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


# ---------------------------------------------------------------------------
# Insert (create)
# ---------------------------------------------------------------------------


async def insert_manual_entry(
    uow: UnitOfWork,
    *,
    category_id: int,
    amount: Decimal,
    payment_method_id: int,
    entry_date: Any,
    notes: str | None,
    created_by: int,
) -> dict[str, Any] | None:
    """Insert one ``manual_finance_entries`` row (lifecycle=posted)."""
    result = await uow.first_row(
        """
        INSERT INTO manual_finance_entries (
            category_id, amount, payment_method_id,
            entry_date, notes, created_by
        ) VALUES (
            :category_id, :amount, :payment_method_id,
            :entry_date, :notes, :created_by
        )
        RETURNING id
        """,
        {
            "category_id": int(category_id),
            "amount": amount,
            "payment_method_id": int(payment_method_id),
            "entry_date": entry_date,
            "notes": notes,
            "created_by": int(created_by),
        },
    )
    if result is None:
        return None
    return await get_manual_entry(uow, entry_id=int(result["id"]))


# ---------------------------------------------------------------------------
# Cash movement helper
# ---------------------------------------------------------------------------


async def insert_cash_movement_for_manual(
    uow: UnitOfWork,
    *,
    amount: Decimal,
    direction: str,
    trigger: str,
    payment_method_id: int,
    reference_id: int,
    created_by: int,
) -> dict[str, Any] | None:
    """Insert a ``cash_movements`` row for a manual entry."""
    return await uow.first_row(
        """
        INSERT INTO cash_movements (
            amount, direction, trigger,
            payment_method_id, reference_type, reference_id, created_by
        ) VALUES (
            :amount, :direction, :trigger,
            :payment_method_id, 'manual_entry', :reference_id, :created_by
        )
        RETURNING id
        """,
        {
            "amount": amount,
            "direction": direction,
            "trigger": trigger,
            "payment_method_id": int(payment_method_id),
            "reference_id": int(reference_id),
            "created_by": int(created_by),
        },
    )


# ---------------------------------------------------------------------------
# Cancel (lifecycle flip)
# ---------------------------------------------------------------------------


async def update_manual_entry_cancel(
    uow: UnitOfWork,
    *,
    entry_id: int,
    cancellation_date: Any,
    cancellation_reason: str,
    cancelled_by: int,
) -> dict[str, Any] | None:
    """Flip lifecycle_status to 'cancelled' + set cancellation fields."""
    result = await uow.first_row(
        """
        UPDATE manual_finance_entries SET
            lifecycle_status    = 'cancelled',
            cancellation_date   = :cancellation_date,
            cancellation_reason = :cancellation_reason,
            cancelled_by        = :cancelled_by
        WHERE id = :id
        RETURNING id
        """,
        {
            "id": int(entry_id),
            "cancellation_date": cancellation_date,
            "cancellation_reason": cancellation_reason,
            "cancelled_by": int(cancelled_by),
        },
    )
    if result is None:
        return None
    return await get_manual_entry(uow, entry_id=int(result["id"]))
