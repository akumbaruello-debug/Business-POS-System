"""Data-access layer for the ``categories`` table.

Per DB spec §5.1 (Database-Design-V1.0.md):

* Columns: ``id SERIAL PK``, ``name VARCHAR(100) NOT NULL``,
  ``parent_id INT NULL FK -> categories(id) ON DELETE RESTRICT``,
  ``is_active BOOLEAN NOT NULL DEFAULT TRUE``,
  ``created_at``/``updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()``.
* Indexes: ``ux_categories_name UNIQUE (name)``,
  ``ix_categories_parent (parent_id)``, ``ix_categories_active (is_active)``.
* Trigger: ``trg_categories_touch_updated`` (BEFORE UPDATE on categories).
* Parent FK is ``ON DELETE RESTRICT`` : a category with children cannot
  be hard-deleted. Deleting a category referenced by products is also
  blocked by ``products.category_id ON DELETE RESTRICT``.
* No seeds — starts empty. No ``version`` column (derived from
  ``updated_at`` server-side).
"""

from __future__ import annotations

import builtins
from typing import Any

from app.db import UnitOfWork


class CategoryRepository:
    """Raw-SQL repository for ``categories``. All UoW-scoped."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list(
        self,
        *,
        page: int,
        per_page: int,
        q: str | None = None,
        active_only: bool | None = None,
        sort: str = "id",
    ) -> tuple[list[dict[str, Any]], int]:
        """List categories with pagination, search, and optional active-filter.

        ``parent_id`` is excluded from search (no column alias).
        Dynamic fragments are closed-whitelist literals — never user identifiers.
        """
        clauses: list[str] = []
        params: dict[str, Any] = {
            "limit": per_page,
            "offset": (page - 1) * per_page,
        }

        if active_only is True:
            clauses.append("is_active = TRUE")
        if q:
            clauses.append("(name ILIKE :q)")
            params["q"] = f"%{q}%"

        # Allowlist for sort to never interpolate raw user input into ORDER BY.
        sort_map = {"name": "name", "id": "id", "created_at": "created_at"}
        order = sort_map.get(sort, "id")

        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        total_row = await self._uow.first_scalar(
            f"SELECT COUNT(*) FROM categories {where}",  # noqa: S608 — closed whitelist literals only, user data parameterized
            params,
        )
        total = int(total_row or 0)

        rows = await self._uow.fetch_all(
            f"""
            SELECT id, name, parent_id, is_active, created_at, updated_at
            FROM categories
            {where}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
            """,  # noqa: S608 — closed whitelist: `where` from fixed literals, `order` from sort_map allowlist
            params,
        )
        return rows, total

    async def get(self, id: int) -> dict[str, Any] | None:
        """Get a single category by id."""
        row = await self._uow.first_row(
            "SELECT id, name, parent_id, is_active, created_at, updated_at "
            "FROM categories WHERE id = :id",
            {"id": id},
        )
        return row

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        """Look up by exact name (for uniqueness checks)."""
        return await self._uow.first_row(
            "SELECT id, name, parent_id, is_active, created_at, updated_at "
            "FROM categories WHERE name = :name",
            {"name": name},
        )

    async def get_by_parent(self, parent_id: int) -> builtins.list[dict[str, Any]]:
        """Return direct children of a parent (for child-restriction check)."""
        return await self._uow.fetch_all(
            "SELECT id, name, parent_id, is_active, created_at, updated_at "
            "FROM categories WHERE parent_id = :pid",
            {"pid": parent_id},
        )

    async def count_children(self, id: int) -> int:
        """Count categories whose parent_id = id (prevents orphaning children)."""
        row = await self._uow.first_scalar(
            "SELECT COUNT(*) FROM categories WHERE parent_id = :id",
            {"id": id},
        )
        return int(row or 0)

    async def create(
        self,
        *,
        name: str,
        parent_id: int | None,
        is_active: bool,
    ) -> dict[str, Any] | None:
        """Insert a new category. Returns the new row, or None on constraint violation."""
        row = await self._uow.first_row(
            """
            INSERT INTO categories (name, parent_id, is_active)
            VALUES (:name, :parent_id, :is_active)
            RETURNING id, name, parent_id, is_active, created_at, updated_at
            """,
            {"name": name, "parent_id": parent_id, "is_active": is_active},
        )
        return row

    async def update(
        self,
        id: int,
        *,
        name: str | None = None,
        is_active: bool | None = None,
        parent_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Update mutable fields. SET clause uses hardcoded column literals only."""
        fields: list[str] = []
        params: dict[str, Any] = {"id": id}

        if name is not None:
            fields.append("name = :name")
            params["name"] = name
        if is_active is not None:
            fields.append("is_active = :is_active")
            params["is_active"] = is_active
        if parent_id is not None:
            fields.append("parent_id = :parent_id")
            params["parent_id"] = parent_id

        if not fields:
            return await self.get(id)

        row = await self._uow.first_row(
            f"""
            UPDATE categories
            SET {", ".join(fields)}
            WHERE id = :id
            RETURNING id, name, parent_id, is_active, created_at, updated_at
            """,  # noqa: S608 — SET fields are hardcoded whitelist literals, user data parameterized
            params,
        )
        return row

    async def hard_delete(self, id: int) -> bool:
        """Hard-delete a category by id. Returns True if a row was deleted."""
        result = await self._uow.execute(
            "DELETE FROM categories WHERE id = :id",
            {"id": id},
        )
        # asyncpg returns rowcount via .rowcount on the Result
        return getattr(result, "rowcount", 0) > 0

    async def deactivate(self, id: int) -> dict[str, Any] | None:
        """Set is_active=FALSE (soft-delete). Always succeeds."""
        row = await self._uow.first_row(
            """
            UPDATE categories
            SET is_active = FALSE
            WHERE id = :id
            RETURNING id, name, parent_id, is_active, created_at, updated_at
            """,
            {"id": id},
        )
        return row

    async def reactivate(self, id: int) -> dict[str, Any] | None:
        """Set is_active=TRUE (undo a deactivation)."""
        row = await self._uow.first_row(
            """
            UPDATE categories
            SET is_active = TRUE
            WHERE id = :id
            RETURNING id, name, parent_id, is_active, created_at, updated_at
            """,
            {"id": id},
        )
        return row


__all__ = ["CategoryRepository"]
