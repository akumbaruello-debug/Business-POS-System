"""Data-access layer for the ``contacts`` table.

Per DB spec §2.5.3 (Database-Design-V1.0.md) ``contacts`` is a M2
master-data table (customers + suppliers as a single table, discriminated
by the ``type`` column: ``customer`` / ``supplier`` / ``both``).

Columns: ``id SERIAL PK``, ``type VARCHAR(20) NOT NULL`` (CHECK),
``name VARCHAR(200) NOT NULL``, ``phone VARCHAR(50)``, ``email VARCHAR(150)``
(CHECK lower-case per spec), ``address TEXT``, ``notes TEXT``,
``is_active BOOLEAN NOT NULL DEFAULT TRUE``,
``created_at``/``updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()``,
``created_by INT NULL FK -> users(id) ON DELETE SET NULL``.

Indexes: ``ux_contacts_name`` UNIQUE (name, type); ``ix_contacts_type`` (type);
``ix_contacts_active`` (is_active); ``ix_contacts_type_active`` (type, is_active).

Trigger: ``trg_contacts_touch_updated`` (BEFORE UPDATE updates ``updated_at``).

No ``version`` column (derived from ``updated_at`` server-side).
"""

from __future__ import annotations

import builtins
from typing import Any

from app.db import UnitOfWork


class ContactRepository:
    """Raw-SQL repository for ``contacts``. All UoW-scoped."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    #: Closed whitelist of columns selectable into the response. Never
    #: interpolates user data — only this fixed literal list.
    _SELECT = (
        "id, type, name, phone, email, address, notes, is_active, "
        "created_at, updated_at, created_by"
    )

    async def list(
        self,
        *,
        page: int,
        per_page: int,
        q: str | None = None,
        contact_type: str | None = None,
        active_only: bool | None = None,
        sort: str = "id",
    ) -> builtins.tuple[list[dict[str, Any]], int]:
        """List contacts with pagination, search, type/active filter, sort.

        ``q`` matches across name/phone/email. ``contact_type`` filters by
        the ``type`` discriminator. Dynamic fragments are closed-whitelist
        literals; user values are parameterized.
        """
        clauses: list[str] = []
        params: dict[str, Any] = {
            "limit": per_page,
            "offset": (page - 1) * per_page,
        }

        if contact_type:
            clauses.append("type = :contact_type")
            params["contact_type"] = contact_type
        if active_only is True:
            clauses.append("is_active = TRUE")
        if active_only is False:
            clauses.append("is_active = FALSE")
        if q:
            clauses.append(
                "(name ILIKE :q OR phone ILIKE :q OR email ILIKE :q)"
            )
            params["q"] = f"%{q}%"

        # Allowlist for sort to never interpolate raw user input.
        sort_map = {
            "id": "id",
            "name": "name",
            "created_at": "created_at",
            "updated_at": "updated_at",
        }
        order = sort_map.get(sort, "id")

        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        total_row = await self._uow.first_scalar(
            f"SELECT COUNT(*) FROM contacts {where}",
            params,
        )
        total = int(total_row or 0)

        rows = await self._uow.fetch_all(
            f"""
            SELECT {self._SELECT}
            FROM contacts
            {where}
            ORDER BY {order}
            LIMIT :limit OFFSET :offset
            """,
            params,
        )
        return rows, total

    async def get(self, id: int) -> dict[str, Any] | None:
        """Get a single contact by id."""
        return await self._uow.first_row(
            f"SELECT {self._SELECT} FROM contacts WHERE id = :id",
            {"id": id},
        )

    async def get_by_name_and_type(
        self, name: str, contact_type: str
    ) -> dict[str, Any] | None:
        """Look up by name + type for the uniqueness check (DB: ux_contacts_name)."""
        return await self._uow.first_row(
            f"SELECT {self._SELECT} FROM contacts WHERE name = :name AND type = :type",
            {"name": name, "type": contact_type},
        )

    async def create(
        self,
        *,
        type: str,
        name: str,
        phone: str | None,
        email: str | None,
        address: str | None,
        notes: str | None,
        is_active: bool,
        created_by: int | None,
    ) -> dict[str, Any] | None:
        """Insert a new contact. Returns the new row, or None on constraint violation."""
        return await self._uow.first_row(
            f"""
            INSERT INTO contacts (type, name, phone, email, address, notes, is_active, created_by)
            VALUES (:type, :name, :phone, :email, :address, :notes, :is_active, :created_by)
            RETURNING {self._SELECT}
            """,
            {
                "type": type,
                "name": name,
                "phone": phone,
                "email": email,
                "address": address,
                "notes": notes,
                "is_active": is_active,
                "created_by": created_by,
            },
        )

    async def update(
        self,
        id: int,
        *,
        type: str | None = None,
        name: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        notes: str | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update mutable fields. SET clause uses hardcoded column literals only."""
        fields: list[str] = []
        params: dict[str, Any] = {"id": id}

        if type is not None:
            fields.append("type = :type")
            params["type"] = type
        if name is not None:
            fields.append("name = :name")
            params["name"] = name
        if phone is not None:
            fields.append("phone = :phone")
            params["phone"] = phone
        if email is not None:
            fields.append("email = :email")
            params["email"] = email
        if address is not None:
            fields.append("address = :address")
            params["address"] = address
        if notes is not None:
            fields.append("notes = :notes")
            params["notes"] = notes
        if is_active is not None:
            fields.append("is_active = :is_active")
            params["is_active"] = is_active

        if not fields:
            return await self.get(id)

        return await self._uow.first_row(
            f"""
            UPDATE contacts
            SET {", ".join(fields)}
            WHERE id = :id
            RETURNING {self._SELECT}
            """,
            params,
        )

    async def deactivate(self, id: int) -> dict[str, Any] | None:
        """Set is_active=FALSE (soft-delete). Always succeeds."""
        return await self._uow.first_row(
            f"""
            UPDATE contacts
            SET is_active = FALSE
            WHERE id = :id
            RETURNING {self._SELECT}
            """,
            {"id": id},
        )

    async def hard_delete(self, id: int) -> bool:
        """Hard-delete a contact by id. Returns True if a row was deleted."""
        result = await self._uow.execute(
            "DELETE FROM contacts WHERE id = :id",
            {"id": id},
        )
        return getattr(result, "rowcount", 0) > 0


__all__ = ["ContactRepository"]
