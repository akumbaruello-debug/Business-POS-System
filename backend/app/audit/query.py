"""Audit-log read helpers.

Per ``Backend-Architecture-V1.0.md`` §18 — audit queries are read-only and
paginate through the ``audit_log`` table (append-only; no deletes).
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork

__all__ = ["count_audit_log", "query_audit_log"]


async def query_audit_log(
    uow: UnitOfWork,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
    action: str | None = None,
    user_id: int | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    where: list[str] = []
    params: dict[str, Any] = {"off": (page - 1) * per_page, "lim": per_page}
    if entity_type:
        where.append("entity_type = :et")
        params["et"] = entity_type
    if entity_id is not None:
        where.append("entity_id = :eid")
        params["eid"] = entity_id
    if action:
        where.append("action = :act")
        params["act"] = action
    if user_id is not None:
        where.append("user_id = :uid")
        params["uid"] = user_id
    # ``where_sql`` is built only from literal column names below; every
    # user-supplied value is passed through named bind parameters
    # (``:et``/``:eid``/``:act``/``:uid``/``:lim``/``:off``), so there is
    # no injection surface. Ruff's S608 fires on any SQL string
    # interpolation, hence the targeted suppression.
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    row = await uow.first_row(
        f"SELECT COUNT(*) AS n FROM audit_log {where_sql}",  # noqa: S608
        params,
    )
    total = int(row["n"]) if row else 0
    rows = await uow.fetch_all(
        f"SELECT id, event_time, user_id, action, entity_type, entity_id, old_values, new_values, reason "  # noqa: S608
        f"FROM audit_log {where_sql} ORDER BY event_time DESC, id DESC LIMIT :lim OFFSET :off",
        params,
    )
    return rows, total


async def count_audit_log(uow: UnitOfWork, *, entity_type: str | None = None) -> int:
    params: dict[str, Any] = {}
    where = ""
    if entity_type:
        where = "WHERE entity_type = :et"
        params["et"] = entity_type
    row = await uow.first_row(
        f"SELECT COUNT(*) AS n FROM audit_log {where}",  # noqa: S608
        params,
    )
    return int(row["n"]) if row else 0
