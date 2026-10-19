"""Data-access layer for the ``units`` table.

Per DB spec §2.2.4 + §5.2 (locked baseline schema.sql):
* Columns: ``(id SERIAL PK, code VARCHAR(20) NOT NULL, name VARCHAR(50)
  NOT NULL, is_active BOOLEAN NOT NULL DEFAULT TRUE)``.
* No ``created_at``/``updated_at`` columns — timestamps are NOT stored.
* ``ux_units_code UNIQUE (code)`` — full unique constraint, not
  active-only. Two rows (active or not) cannot share a code.
* ``products.unit_id`` FK with ``ON DELETE RESTRICT`` — hard-delete is
  blocked by the DB itself when products reference the unit.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork


class UnitRepository:
    """CRUD for ``units``."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # ------------------------------------------------------------------
    # List / get
    # --------------------------------------------------

    async def list(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        active_only: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """List units (paginated)."""
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": per_page, "offset": (page - 1) * per_page}

        if active_only:
            clauses.append("is_active = TRUE")

        if q:
            clauses.append("(code ILIKE :q OR name ILIKE :q)")
            params["q"] = f"%{q}%"

        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        total_row = await self._uow.first_scalar(
            f"SELECT COUNT(*) FROM units {where}",  # noqa: S608
            params,
        )
        total = int(total_row or 0)

        rows = await self._uow.fetch_all(
            f"""
            SELECT id, code, name, is_active,
                   dimension, base_unit_id, factor_to_base
            FROM units
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
            "SELECT id, code, name, is_active, dimension, base_unit_id, factor_to_base "
            "FROM units WHERE id = :id",
            {"id": id},
        )

    async def get_by_code(self, code: str) -> dict[str, Any] | None:
        """Get by code (any state)."""
        return await self._uow.first_row(
            "SELECT id, code, name, is_active, dimension, base_unit_id, factor_to_base "
            "FROM units WHERE code = :code",
            {"code": code},
        )

    # ------------------------------------------------------------------
    # Mutations
    # -----------------------------------------------

    async def create(
        self,
        *,
        code: str,
        name: str,
        is_active: bool,
        dimension: str | None = None,
        base_unit_id: int | None = None,
        factor_to_base: float | None = None,
    ) -> dict[str, Any]:
        """Insert and return the new row."""
        row = await self._uow.first_row(
            "INSERT INTO units (code, name, is_active, dimension, base_unit_id, factor_to_base) "
            "VALUES (:code, :name, :is_active, :dimension, :base_unit_id, :factor_to_base) "
            "RETURNING id, code, name, is_active, dimension, base_unit_id, factor_to_base",
            {
                "code": code,
                "name": name,
                "is_active": is_active,
                "dimension": dimension,
                "base_unit_id": base_unit_id,
                "factor_to_base": factor_to_base,
            },
        )
        if row is None:
            raise RuntimeError("Unit insert returned no row")
        return row

    async def update(
        self,
        id: int,
        *,
        name: str | None = None,
        is_active: bool | None = None,
        dimension: str | None = None,
        base_unit_id: int | None = None,
        factor_to_base: float | None = None,
    ) -> dict[str, Any] | None:
        """Update name/is_active/dimension/conversion. Returns the updated row or None.

        ``code`` is mutable in SQL but NOT exposed via the public API
        (UnitPatch has no code field) — see service layer.
        """
        fields: list[str] = []
        params: dict[str, Any] = {"id": id}
        if name is not None:
            fields.append("name = :name")
            params["name"] = name
        if is_active is not None:
            fields.append("is_active = :is_active")
            params["is_active"] = is_active
        if dimension is not None:
            fields.append("dimension = :dimension")
            params["dimension"] = dimension
        if base_unit_id is not None:
            fields.append("base_unit_id = :base_unit_id")
            params["base_unit_id"] = base_unit_id
        if factor_to_base is not None:
            fields.append("factor_to_base = :factor_to_base")
            params["factor_to_base"] = factor_to_base

        if not fields:
            return await self.get(id)

        row = await self._uow.first_row(
            f"""
            UPDATE units
            SET {", ".join(fields)}
            WHERE id = :id
            RETURNING id, code, name, is_active, dimension, base_unit_id, factor_to_base
            """,  # noqa: S608 — SET fields are hardcoded whitelist literals, user data is parameterized
            params,
        )
        return row

    async def update_code(self, id: int, code: str) -> dict[str, Any] | None:
        """Update ``code`` (used by the service for rename-to-existing-code
        conflict handling — the full unique constraint blocks duplicates)."""
        row = await self._uow.first_row(
            "UPDATE units SET code = :code WHERE id = :id "
            "RETURNING id, code, name, is_active, dimension, base_unit_id, factor_to_base",
            {"id": id, "code": code},
        )
        return row

    async def deactivate(self, id: int) -> dict[str, Any] | None:
        """Set is_active = FALSE (always allowed — reversible)."""
        row = await self._uow.first_row(
            "UPDATE units SET is_active = FALSE WHERE id = :id "
            "RETURNING id, code, name, is_active, dimension, base_unit_id, factor_to_base",
            {"id": id},
        )
        return row

    async def delete(self, id: int) -> bool:
        """Hard delete.

        The ``products.unit_id`` FK with ``ON DELETE RESTRICT`` blocks
        this at the DB level when any product references the unit. The
        service layer translates the FK violation to 409.
        """
        row = await self._uow.first_scalar(
            "DELETE FROM units WHERE id = :id RETURNING id",
            {"id": id},
        )
        return row is not None


__all__ = ["UnitRepository"]
