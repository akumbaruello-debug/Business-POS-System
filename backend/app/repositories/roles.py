"""Data-access layer for the ``roles`` table and ``role_capabilities``.

Per DB spec §4.1 + §4.3:

* ``roles`` has ``id SMALLSERIAL PK``, ``name VARCHAR(50) NOT NULL``,
  ``description TEXT NULL``, ``is_system_role BOOLEAN NOT NULL DEFAULT FALSE``,
  ``created_at``/``updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()``.
* Unique index ``ux_roles_name``.
* ``role_capabilities`` PK is (role_id, capability_id), FK to roles ON DELETE CASCADE.
* No ``version`` or ``is_active`` column on roles.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork


class RoleRepository:
    """Raw-SQL repository for ``roles`` + ``role_capabilities``."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list_roles(
        self,
        *,
        page: int,
        per_page: int,
        q: str | None = None,
        sort: str = "id",
    ) -> tuple[list[dict[str, Any]], int]:
        """List roles with pagination and optional search/sort."""
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": per_page, "offset": (page - 1) * per_page}

        if q:
            clauses.append("(name ILIKE :q OR description ILIKE :q)")
            params["q"] = f"%{q}%"

        sort_map = {"id": "id", "name": "name", "created_at": "created_at"}
        order = sort_map.get(sort, "id")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        total_row = await self._uow.first_scalar(
            f"SELECT COUNT(*) FROM roles {where}",  # noqa: S608
            params,
        )
        total = int(total_row or 0)

        rows = await self._uow.fetch_all(
            f"""
            SELECT id, name, description, is_system_role, created_at, updated_at
            FROM roles
            {where}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
            """,  # noqa: S608
            params,
        )
        return rows, total

    async def get(self, id: int) -> dict[str, Any] | None:
        """Get a single role by id."""
        return await self._uow.first_row(
            "SELECT id, name, description, is_system_role, created_at, updated_at "
            "FROM roles WHERE id = :id",
            {"id": id},
        )

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        """Look up by exact name (for uniqueness checks)."""
        return await self._uow.first_row(
            "SELECT id, name, description, is_system_role, created_at, updated_at "
            "FROM roles WHERE name = :name",
            {"name": name},
        )

    async def create(
        self,
        *,
        name: str,
        description: str | None,
    ) -> dict[str, Any] | None:
        """Insert a new custom role. Returns the new row."""
        row = await self._uow.first_row(
            """
            INSERT INTO roles (name, description)
            VALUES (:name, :description)
            RETURNING id, name, description, is_system_role, created_at, updated_at
            """,
            {"name": name, "description": description},
        )
        return row

    async def update(
        self,
        id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any] | None:
        """Update mutable fields. SET clause uses hardcoded column literals only."""
        fields: list[str] = []
        params: dict[str, Any] = {"id": id}

        if name is not None:
            fields.append("name = :name")
            params["name"] = name
        if description is not None:
            fields.append("description = :description")
            params["description"] = description

        if not fields:
            return await self.get(id)

        row = await self._uow.first_row(
            f"""
            UPDATE roles
            SET {", ".join(fields)}
            WHERE id = :id
            RETURNING id, name, description, is_system_role, created_at, updated_at
            """,  # noqa: S608
            params,
        )
        return row

    async def hard_delete(self, id: int) -> bool:
        """Hard-delete a role by id. Returns True if a row was deleted."""
        result = await self._uow.execute(
            "DELETE FROM roles WHERE id = :id",
            {"id": id},
        )
        return getattr(result, "rowcount", 0) > 0

    async def count_users(self, id: int) -> int:
        """Count users assigned to this role."""
        row = await self._uow.first_row(
            "SELECT COUNT(*)::int AS cnt FROM users WHERE role_id = :id",
            {"id": id},
        )
        return int(row["cnt"]) if row else 0

    async def list_capability_codes(self, role_id: int) -> list[str]:
        """Return the capability codes assigned to this role, sorted."""
        rows = await self._uow.fetch_all(
            """
            SELECT c.code
            FROM role_capabilities rc
            JOIN capabilities c ON c.id = rc.capability_id
            WHERE rc.role_id = :rid
            ORDER BY c.code
            """,
            {"rid": role_id},
        )
        return [r["code"] for r in rows]

    async def set_capabilities(self, role_id: int, capability_ids: list[int]) -> None:
        """Replace the role's capability set with ``capability_ids``.

        Deletes existing assignments not in the list and inserts missing ones.
        """
        # Delete all existing assignments first, then re-insert. This avoids
        # SQL parameter-binding pitfalls with IN lists on asyncpg.
        await self._uow.execute(
            "DELETE FROM role_capabilities WHERE role_id = :rid",
            {"rid": role_id},
        )
        for cid in capability_ids:
            await self._uow.execute(
                """
                INSERT INTO role_capabilities (role_id, capability_id)
                VALUES (:rid, :cid)
                ON CONFLICT (role_id, capability_id) DO NOTHING
                """,
                {"rid": role_id, "cid": cid},
            )

    async def get_capability_id(self, code: str) -> int | None:
        """Return the capability id for a canonical code, or None."""
        row = await self._uow.first_row(
            "SELECT id FROM capabilities WHERE code = :code",
            {"code": code},
        )
        return int(row["id"]) if row else None


__all__ = ["RoleRepository"]
