"""Data-access layer for the ``cost_types`` table.

Per ``Database-Design-V1.0.md`` §2.7.1 + locked ``schema.sql``:

* Columns: ``(id SERIAL PK, code VARCHAR(50) NOT NULL, name VARCHAR(100)
  NOT NULL, is_active BOOLEAN NOT NULL DEFAULT TRUE, created_at
  TIMESTAMPTZ NOT NULL DEFAULT NOW())``.
* No ``updated_at`` column and no touch trigger.
* ``ux_cost_types_code UNIQUE (code)`` — full unique constraint (not
  partial/active-only). Code is unique across ALL rows, active or not.
* ``ix_cost_types_active`` on ``is_active``.

FK protection:
  ``production_cost_lines.cost_type_id`` REFERENCES ``cost_types.id`` ON
  DELETE RESTRICT — hard-delete is blocked by the DB when any
  ``production_cost_line`` references the type. The service layer
  translates the FK violation to 409 ``referenced_by_history``.

This repo is thin (raw SQL via UnitOfWork); business rules live in the
service layer.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork


class CostTypeRepository:
    """CRUD for ``cost_types``."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # ------------------------------------------------------------------
    # List / get
    # ------------------------------------------------------------------

    async def list(
        self,
        *,
        active_only: bool = False,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (items, total) with pagination.

        Matches the openapi ``listCostTypes`` parameter contract:
        ``page``, ``per_page``, ``filter[is_active]``, ``q``.
        """
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": per_page, "offset": (page - 1) * per_page}

        if active_only:
            clauses.append("is_active = TRUE")

        if q:
            clauses.append("(code ILIKE :q OR name ILIKE :q)")
            params["q"] = f"%{q}%"

        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        total_row = await self._uow.first_scalar(
            f"SELECT COUNT(*) FROM cost_types {where}",  # noqa: S608
            params,
        )
        total = int(total_row or 0)

        rows = await self._uow.fetch_all(
            f"""
            SELECT id, code, name, is_active, created_at
            FROM cost_types
            {where}
            ORDER BY id
            LIMIT :limit OFFSET :offset
            """,  # noqa: S608 — closed whitelist literals only, user data parameterized
            params,
        )
        return rows, total

    async def get(self, id: int) -> dict[str, Any] | None:
        """Get by id (any state)."""
        return await self._uow.first_row(
            """
            SELECT id, code, name, is_active, created_at
            FROM cost_types
            WHERE id = :id
            """,
            {"id": id},
        )

    async def get_by_code(self, code: str) -> dict[str, Any] | None:
        """Get by code (any state) — for uniqueness checks."""
        return await self._uow.first_row(
            """
            SELECT id, code, name, is_active, created_at
            FROM cost_types
            WHERE code = :code
            """,
            {"code": code},
        )

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        code: str,
        name: str,
        is_active: bool,
    ) -> dict[str, Any]:
        """Insert and return the new row."""
        row = await self._uow.first_row(
            """
            INSERT INTO cost_types (code, name, is_active)
            VALUES (:code, :name, :is_active)
            RETURNING id, code, name, is_active, created_at
            """,
            {"code": code, "name": name, "is_active": is_active},
        )
        if row is None:
            raise RuntimeError("Cost type insert returned no row")
        return row

    async def update(
        self,
        id: int,
        *,
        name: str | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update provided fields. ``code`` is immutable via the API.

        The locked schema has no ``updated_at`` column or touch trigger,
        so only the supplied fields are written.
        """
        fields: list[str] = []
        params: dict[str, Any] = {"id": id}
        if name is not None:
            fields.append("name = :name")
            params["name"] = name
        if is_active is not None:
            fields.append("is_active = :is_active")
            params["is_active"] = is_active

        if not fields:
            return await self.get(id)

        row = await self._uow.first_row(
            f"""
            UPDATE cost_types
            SET {", ".join(fields)}
            WHERE id = :id
            RETURNING id, code, name, is_active, created_at
            """,  # noqa: S608 — SET fields are hardcoded whitelist literals, user data parameterized
            params,
        )
        return row

    async def deactivate(self, id: int) -> dict[str, Any] | None:
        """Set is_active = FALSE. Always allowed (reversible)."""
        row = await self._uow.first_row(
            """
            UPDATE cost_types
            SET is_active = FALSE
            WHERE id = :id
            RETURNING id, code, name, is_active, created_at
            """,
            {"id": id},
        )
        return row

    async def delete_if_unreferenced(self, id: int) -> bool:
        """Hard delete only if no referencing rows exist.

        The DB FK (``production_cost_lines.cost_type_id ON DELETE RESTRICT``)
        is the authoritative guard. We attempt the DELETE and treat a
        returned row as "deleted" / absent row as "referenced-by-history or
        not found". The service layer raises the appropriate error.
        """
        deleted = await self._uow.first_scalar(
            """
            DELETE FROM cost_types
            WHERE id = :id
            RETURNING id
            """,
            {"id": id},
        )
        return deleted is not None


__all__ = [
    "CostTypeRepository",
]
