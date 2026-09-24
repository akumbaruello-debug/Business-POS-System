"""Data-access layer for the ``users`` table.

Thin SQL repository exposing only what the Users service needs.
"""
from __future__ import annotations

from typing import Any

from app.db import UnitOfWork


class UserRepository:
    """Raw-SQL repository for ``users``."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list(
        self,
        *,
        page: int,
        per_page: int,
        q: str | None = None,
        active_only: bool | None = None,
        role_id: int | None = None,
        sort: str = "id",
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": per_page, "offset": (page - 1) * per_page}

        if active_only is True:
            clauses.append("u.is_active = TRUE")
        elif active_only is False:
            clauses.append("u.is_active = FALSE")
        if role_id is not None:
            clauses.append("u.role_id = :role_id")
            params["role_id"] = role_id
        if q:
            clauses.append(
                "(u.username ILIKE :q OR u.full_name ILIKE :q OR u.email ILIKE :q)"
            )
            params["q"] = f"%{q}%"

        sort_map = {"id": "u.id", "username": "u.username", "created_at": "u.created_at"}
        order = sort_map.get(sort, "u.id")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        total_row = await self._uow.first_scalar(
            f"SELECT COUNT(*) FROM users u {where}",  # noqa: S608
            params,
        )
        total = int(total_row or 0)

        rows = await self._uow.fetch_all(
            f"""
            SELECT
                u.id, u.username, u.full_name, u.email, u.is_active,
                u.role_id, r.name AS role_name,
                u.failed_login_count, u.locked_until, u.last_login_at,
                u.created_at, u.updated_at, u.created_by, u.updated_by
            FROM users u
            JOIN roles r ON r.id = u.role_id
            {where}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
            """,  # noqa: S608
            params,
        )
        return rows, total

    async def get(self, id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            """
            SELECT
                u.id, u.username, u.full_name, u.email, u.is_active,
                u.role_id, r.name AS role_name,
                u.failed_login_count, u.locked_until, u.last_login_at,
                u.created_at, u.updated_at, u.created_by, u.updated_by
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE u.id = :id
            """,
            {"id": id},
        )

    async def get_by_username(self, username: str) -> dict[str, Any] | None:
        return await self._uow.first_row(
            """
            SELECT
                u.id, u.username, u.full_name, u.email, u.is_active,
                u.role_id, r.name AS role_name,
                u.failed_login_count, u.locked_until, u.last_login_at,
                u.created_at, u.updated_at, u.created_by, u.updated_by
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE u.username = :username
            """,
            {"username": username},
        )

    async def get_role(self, role_id: int) -> dict[str, Any] | None:
        return await self._uow.first_row(
            "SELECT id, name, is_system_role FROM roles WHERE id = :id",
            {"id": role_id},
        )

    async def create(
        self,
        *,
        username: str,
        full_name: str,
        email: str | None,
        password_hash: str,
        role_id: int,
        created_by: int | None = None,
    ) -> dict[str, Any] | None:
        row = await self._uow.first_row(
            """
            INSERT INTO users (username, full_name, email, password_hash, role_id, created_by)
            VALUES (:username, :full_name, :email, :password_hash, :role_id, :created_by)
            RETURNING
                id, username, full_name, email, is_active, role_id,
                failed_login_count, locked_until, last_login_at,
                created_at, updated_at, created_by, updated_by
            """,
            {
                "username": username,
                "full_name": full_name,
                "email": email,
                "password_hash": password_hash,
                "role_id": role_id,
                "created_by": created_by,
            },
        )
        if row is None:
            return None
        row["role_name"] = await self._role_name(role_id)
        return row

    async def update(
        self,
        id: int,
        *,
        full_name: str | None = None,
        email: Any = Ellipsis,
        is_active: bool | None = None,
        role_id: int | None = None,
        password_hash: str | None = None,
        updated_by: int | None = None,
    ) -> dict[str, Any] | None:
        fields: list[str] = []
        params: dict[str, Any] = {"id": id}

        if full_name is not None:
            fields.append("full_name = :full_name")
            params["full_name"] = full_name
        if email is not Ellipsis:
            fields.append("email = :email")
            params["email"] = email
        if is_active is not None:
            fields.append("is_active = :is_active")
            params["is_active"] = is_active
        if role_id is not None:
            fields.append("role_id = :role_id")
            params["role_id"] = role_id
        if password_hash is not None:
            fields.append("password_hash = :password_hash")
            params["password_hash"] = password_hash
        if updated_by is not None:
            fields.append("updated_by = :updated_by")
            params["updated_by"] = updated_by

        if not fields:
            return await self.get(id)

        fields.append("updated_at = NOW()")

        row = await self._uow.first_row(
            f"""
            UPDATE users
            SET {", ".join(fields)}
            WHERE id = :id
            RETURNING
                id, username, full_name, email, is_active, role_id,
                failed_login_count, locked_until, last_login_at,
                created_at, updated_at, created_by, updated_by
            """,  # noqa: S608
            params,
        )
        if row is None:
            return None
        row["role_name"] = await self._role_name(row["role_id"])
        return row

    async def deactivate(self, id: int, *, updated_by: int | None = None) -> dict[str, Any] | None:
        return await self.update(id, is_active=False, updated_by=updated_by)

    async def unlock(self, id: int, *, updated_by: int | None = None) -> dict[str, Any] | None:
        """Clear lockout state (failed_login_count=0, locked_until=NULL)."""
        params: dict[str, Any] = {"id": id, "updated_by": updated_by}
        row = await self._uow.first_row(
            """
            UPDATE users
            SET failed_login_count = 0,
                locked_until = NULL,
                updated_by = COALESCE(:updated_by, updated_by)
            WHERE id = :id
            RETURNING
                id, username, full_name, email, is_active, role_id,
                failed_login_count, locked_until, last_login_at,
                created_at, updated_at, created_by, updated_by
            """,
            params,
        )
        if row is None:
            return None
        row["role_name"] = await self._role_name(row["role_id"])
        return row

    async def count_active_owners(self) -> int:
        row = await self._uow.first_row(
            """
            SELECT COUNT(*)::int AS cnt
            FROM users u
            JOIN roles r ON r.id = u.role_id
            WHERE r.name = 'Owner' AND u.is_active = TRUE
            """
        )
        return int(row["cnt"]) if row else 0

    async def _role_name(self, role_id: int) -> str | None:
        row = await self._uow.first_row(
            "SELECT name FROM roles WHERE id = :id", {"id": role_id}
        )
        return row["name"] if row else None

    async def list_capability_overrides(self, user_id: int) -> list[dict[str, Any]]:
        return await self._uow.fetch_all(
            """
            SELECT
                c.code AS capability_code,
                uco.is_granted,
                uco.granted_at,
                uco.granted_by
            FROM user_capability_overrides uco
            JOIN capabilities c ON c.id = uco.capability_id
            WHERE uco.user_id = :user_id
            ORDER BY c.code
            """,
            {"user_id": user_id},
        )

    async def get_capability_id(self, code: str) -> int | None:
        row = await self._uow.first_row(
            "SELECT id FROM capabilities WHERE code = :code",
            {"code": code},
        )
        return int(row["id"]) if row else None

    async def upsert_capability_override(
        self,
        *,
        user_id: int,
        capability_id: int,
        is_granted: bool,
        granted_by: int,
    ) -> None:
        """Insert or update a user capability override."""
        await self._uow.execute(
            """
            INSERT INTO user_capability_overrides
                (user_id, capability_id, is_granted, granted_by)
            VALUES (:user_id, :capability_id, :is_granted, :granted_by)
            ON CONFLICT (user_id, capability_id)
            DO UPDATE SET
                is_granted = EXCLUDED.is_granted,
                granted_by = EXCLUDED.granted_by,
                granted_at = NOW()
            """,
            {
                "user_id": user_id,
                "capability_id": capability_id,
                "is_granted": is_granted,
                "granted_by": granted_by,
            },
        )

    async def delete_capability_override(self, *, user_id: int, capability_id: int) -> bool:
        result = await self._uow.execute(
            "DELETE FROM user_capability_overrides WHERE user_id = :uid AND capability_id = :cid",
            {"uid": user_id, "cid": capability_id},
        )
        return getattr(result, "rowcount", 0) > 0


__all__ = ["UserRepository"]
