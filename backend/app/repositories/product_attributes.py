"""Data-access layer for the ``product_attributes`` table.

V3 Phase 2 — managed master values for the three product dimensions
(Jenis / Size / Kualitas), discriminated by ``kind``.

* Columns: ``id SERIAL PK``, ``kind VARCHAR(20) NOT NULL`` (CHECK IN
  ('jenis','size','kualitas')), ``name VARCHAR(100) NOT NULL``,
  ``is_active BOOLEAN NOT NULL DEFAULT TRUE``, ``created_at`` /
  ``updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()``.
* Indexes: ``ux_product_attributes_kind_name UNIQUE (kind, name)``,
  ``ix_product_attributes_kind (kind)``, ``ix_product_attributes_active (is_active)``.
* No seeds — the Owner reviews V3-PLAN templates and enters values through
  the UI; nothing is invented for the merchant.
* ``products.jenis_id / size_id / kualitas_id`` FK ON DELETE RESTRICT sit in
  the products table, NOT here — a referenced attribute cannot be hard-deleted
  (surfaces 23503 → service translates to 409 referenced_by_history).
* No ``version`` column (derived from ``updated_at`` server-side, the
  standard M2 master-data pattern).
"""

from __future__ import annotations

import builtins
from typing import Any

from app.db import UnitOfWork


class ProductAttributeRepository:
    """Raw-SQL repository for ``product_attributes``. All UoW-scoped."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list(
        self,
        *,
        page: int,
        per_page: int,
        kind: str | None = None,
        q: str | None = None,
        active_only: bool | None = None,
        sort: str = "id",
    ) -> tuple[list[dict[str, Any]], int]:
        """List attributes with pagination, kind filter, search, active filter.

        Dynamic fragments are closed-whitelist literals — never user input.
        """
        clauses: list[str] = []
        params: dict[str, Any] = {
            "limit": per_page,
            "offset": (page - 1) * per_page,
        }

        if kind is not None:
            clauses.append("kind = :kind")
            params["kind"] = kind
        if active_only is True:
            clauses.append("is_active = TRUE")
        elif active_only is False:
            clauses.append("is_active = FALSE")
        if q:
            clauses.append("(name ILIKE :q)")
            params["q"] = f"%{q}%"

        # Allowlist for sort to never interpolate raw user input into ORDER BY.
        sort_map = {"name": "name", "kind": "kind", "id": "id", "created_at": "created_at"}
        order = sort_map.get(sort, "id")

        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        total_row = await self._uow.first_scalar(
            f"SELECT COUNT(*) FROM product_attributes {where}",  # noqa: S608 — closed whitelist literals only, user data parameterized
            params,
        )
        total = int(total_row or 0)

        rows = await self._uow.fetch_all(
            f"""
            SELECT id, kind, name, is_active, created_at, updated_at
            FROM product_attributes
            {where}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
            """,  # noqa: S608 — closed whitelist: `where` from fixed literals, `order` from sort_map allowlist
            params,
        )
        return rows, total

    async def get(self, id: int) -> dict[str, Any] | None:
        """Get a single attribute by id."""
        row = await self._uow.first_row(
            "SELECT id, kind, name, is_active, created_at, updated_at "
            "FROM product_attributes WHERE id = :id",
            {"id": id},
        )
        return row

    async def create(
        self,
        *,
        kind: str,
        name: str,
        is_active: bool,
    ) -> dict[str, Any] | None:
        """Insert a new attribute. Returns the new row, or None on constraint violation."""
        row = await self._uow.first_row(
            """
            INSERT INTO product_attributes (kind, name, is_active)
            VALUES (:kind, :name, :is_active)
            RETURNING id, kind, name, is_active, created_at, updated_at
            """,
            {"kind": kind, "name": name, "is_active": is_active},
        )
        return row

    async def update(
        self,
        id: int,
        *,
        name: str | None = None,
        is_active: bool | None = None,
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

        if not fields:
            return await self.get(id)

        fields.append("updated_at = NOW()")

        row = await self._uow.first_row(
            f"""
            UPDATE product_attributes
            SET {" , ".join(fields)}
            WHERE id = :id
            RETURNING id, kind, name, is_active, created_at, updated_at
            """,  # noqa: S608 — SET fields are hardcoded whitelist literals, user data parameterized
            params,
        )
        return row

    async def deactivate(self, id: int) -> dict[str, Any] | None:
        """Set is_active=FALSE (soft-delete). Always succeeds."""
        row = await self._uow.first_row(
            """
            UPDATE product_attributes
            SET is_active = FALSE
            WHERE id = :id
            RETURNING id, kind, name, is_active, created_at, updated_at
            """,
            {"id": id},
        )
        return row

    async def hard_delete(self, id: int) -> bool:
        """Hard-delete an attribute by id. Returns True if a row was deleted."""
        result = await self._uow.execute(
            "DELETE FROM product_attributes WHERE id = :id",
            {"id": id},
        )
        return getattr(result, "rowcount", 0) > 0

    async def count_products_referencing(self, id: int) -> int:
        """Count products whose jenis_id / size_id / kualitas_id reference id."""
        row = await self._uow.first_scalar(
            """
            SELECT COUNT(*) FROM products
            WHERE jenis_id = :id OR size_id = :id OR kualitas_id = :id
            """,
            {"id": id},
        )
        return int(row or 0)


__all__ = ["ProductAttributeRepository"]