"""Cash-movements repository (E.9 — read-only SQL helpers).

Backs :mod:`app.cash_movements.service` with the SQL primitives that
power the two ``/api/v1/cash-movements`` operations:

* ``count_cash_movements``      — COUNT(*) over the ledger with filters.
* ``list_cash_movement_rows``   — paginated SELECT with filters + sort.
* ``sum_cash_balance``          — ``SUM(amount)`` aggregate.

The ``cash_movements`` table is append-only
(``schema.sql`` §10.1 + the immutability trigger §18.4). The DB
``COALESCE(SUM(amount), 0)`` aggregate gives the current cash balance;
this is the same expression the ``fn_cash_movements_balance_check``
pre-insert trigger uses (schema.sql §18.5).

ruff: S608 ignored — every interpolated SQL fragment is either a
hard-coded column/alias name or a value pulled from the whitelisted
``_SORT_KEYS`` map. All user-supplied values are bound parameters.

Q/From/To semantics
-------------------
The generated OpenAPI contract (``build_openapi.py`` ``collection_get_op``)
auto-includes ``Q`` / ``From`` / ``To`` on every collection endpoint.
For ``cash_movements``:

* ``From`` / ``To`` filter on ``movement_date`` (the authoritative
  cash-journal date; there is no ``updated_at`` on the append-only
  table).
* ``Q`` does a case-insensitive substring match against the two
  text columns available on the ledger: ``trigger`` and
  ``reference_type`` (both free-form string values drawn from the
  OpenAPI enum / reference-type contract). There is no
  notes/reason/description column to search.

These choices are documented in the module docstring so future readers
do not need to re-derive them.
"""

# ruff: noqa: S608

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.db import UnitOfWork

__all__ = [
    "count_cash_movements",
    "list_cash_movement_rows",
    "sum_cash_balance",
]


# ----------------------------------------------------------------------------
# Sort whitelist
# ----------------------------------------------------------------------------
# Single-value sort expression per the established E.7/E.8 convention
# (the OpenAPI ``Sort`` parameter description is generic "comma-separated"
# but every collection implementation in this repo consumes a single
# value; multi-column sort is not a contract requirement).
_SORT_KEYS: dict[str, str] = {
    "id": "cm.id ASC, cm.movement_date ASC, cm.id ASC",
    "-id": "cm.id DESC",
    "movement_date": "cm.movement_date ASC, cm.id ASC",
    "-movement_date": "cm.movement_date DESC, cm.id ASC",
}


def _order_by(sort: str) -> str:
    return _SORT_KEYS.get((sort or "id").strip(), "cm.id ASC, cm.movement_date ASC, cm.id ASC")


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string into a tz-aware ``datetime``.

    asyncpg binds ``TIMESTAMPTZ`` parameters strictly: a string value
    raises ``DataError`` ("expected a datetime.date or datetime.datetime
    instance, got 'str'"). The route layer passes the OpenAPI ISO
    string through unchanged, so we parse here before binding.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# ----------------------------------------------------------------------------
# WHERE clause builder
# ----------------------------------------------------------------------------


def _where(
    *,
    trigger: str | None,
    payment_method_id: int | None,
    reference_type: str | None,
    reference_id: int | None,
    from_iso: str | None,
    to_iso: str | None,
    q: str | None,
) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if trigger is not None:
        clauses.append("cm.trigger = :trigger")
        params["trigger"] = str(trigger)
    if payment_method_id is not None:
        clauses.append("cm.payment_method_id = :payment_method_id")
        params["payment_method_id"] = int(payment_method_id)
    if reference_type is not None:
        clauses.append("cm.reference_type = :reference_type")
        params["reference_type"] = str(reference_type)
    if reference_id is not None:
        # Pair reference_type + reference_id when both are supplied so a
        # bare reference_id never crosses reference kinds by accident.
        # reference_id alone is still accepted (ops debugging across
        # tables) per the E.7 convention.
        if reference_type is not None:
            clauses.append("cm.reference_id = :reference_id")
        else:
            clauses.append("cm.reference_id = :reference_id")
        params["reference_id"] = int(reference_id)
    if from_iso is not None:
        clauses.append("cm.movement_date >= :from_iso")
        params["from_iso"] = _parse_iso(from_iso)
    if to_iso is not None:
        clauses.append("cm.movement_date <= :to_iso")
        params["to_iso"] = _parse_iso(to_iso)
    if q:
        # Search the two text columns on the ledger. ILIKE on both
        # trigger + reference_type (the only free-form text columns).
        clauses.append("(cm.trigger ILIKE :q OR cm.reference_type ILIKE :q)")
        params["q"] = f"%{q}%"
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# ----------------------------------------------------------------------------
# Column list
# ----------------------------------------------------------------------------

_COLUMNS = (
    "cm.id, cm.movement_date, cm.amount, cm.direction, cm.trigger, "
    "cm.payment_method_id, cm.reference_type, cm.reference_id, "
    "cm.created_at, cm.created_by"
)


# ----------------------------------------------------------------------------
# Count + list
# ----------------------------------------------------------------------------


async def count_cash_movements(
    uow: UnitOfWork,
    *,
    trigger: str | None,
    payment_method_id: int | None,
    reference_type: str | None,
    reference_id: int | None,
    from_iso: str | None,
    to_iso: str | None,
    q: str | None,
) -> int:
    """Count rows matching the ``listCashMovements`` filters."""
    where, params = _where(
        trigger=trigger,
        payment_method_id=payment_method_id,
        reference_type=reference_type,
        reference_id=reference_id,
        from_iso=from_iso,
        to_iso=to_iso,
        q=q,
    )
    sql = f"SELECT COUNT(*) FROM cash_movements cm {where}"
    total = await uow.scalar(sql, params)
    return int(total or 0)


async def list_cash_movement_rows(
    uow: UnitOfWork,
    *,
    page: int,
    per_page: int,
    sort: str,
    trigger: str | None,
    payment_method_id: int | None,
    reference_type: str | None,
    reference_id: int | None,
    from_iso: str | None,
    to_iso: str | None,
    q: str | None,
) -> list[dict[str, Any]]:
    """Paginated rows for ``listCashMovements``."""
    where, params = _where(
        trigger=trigger,
        payment_method_id=payment_method_id,
        reference_type=reference_type,
        reference_id=reference_id,
        from_iso=from_iso,
        to_iso=to_iso,
        q=q,
    )
    order = _order_by(sort)
    offset = (page - 1) * per_page
    params.update({"limit": per_page, "offset": offset})
    sql = (
        f"SELECT {_COLUMNS} FROM cash_movements cm {where} "
        f"ORDER BY {order} LIMIT :limit OFFSET :offset"
    )
    return await uow.fetch_all(sql, params)


# ----------------------------------------------------------------------------
# Balance aggregate
# ----------------------------------------------------------------------------


async def sum_cash_balance(uow: UnitOfWork) -> tuple[float, datetime]:
    """Return ``(SUM(amount) COALESCE 0, NOW())`` over ``cash_movements``.

    The ``as_of`` timestamp is captured via the DB clock (``NOW()``)
    inside the same SELECT so the balance + as_of are derived from a
    single statement. ``NOW()`` returns the transaction start time
    which is the authoritative "as of" for the read.
    """
    sql = (
        "SELECT COALESCE(SUM(amount), 0) AS balance, NOW() AS as_of "
        "FROM cash_movements"
    )
    result = await uow.first_row(sql)
    if result is None:
        return 0.0, datetime.now(tz=UTC)
    bal = result.get("balance")
    as_of = result.get("as_of")
    if as_of is None:
        as_of = datetime.now(tz=UTC)
    elif as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    return float(bal or 0), as_of
