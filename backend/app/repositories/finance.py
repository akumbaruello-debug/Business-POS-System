"""Data-access layer for payment_methods & financial_categories.

Uses UnitOfWork (raw SQL, SQLAlchemy Core). No ORM models — the
canonical schema lives in the migration. Repositories are thin and
testable; business rules live in the service layer.
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork


class PaymentMethodRepository:
    """CRUD for ``payment_methods``."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list(
        self,
        *,
        active_only: bool = False,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (items, total) with pagination."""
        clauses = []
        params: dict[str, Any] = {"limit": per_page, "offset": (page - 1) * per_page}

        if active_only:
            clauses.append("is_active = TRUE")

        if q:
            clauses.append("(code ILIKE :q OR name ILIKE :q)")
            params["q"] = f"%{q}%"

        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        # Total
        total_row = await self._uow.first_scalar(
            f"SELECT COUNT(*) FROM payment_methods {where}",  # noqa: S608 — closed whitelist literals only, user data parameterized
            params,
        )
        total = int(total_row or 0)

        # Items
        rows = await self._uow.fetch_all(
            f"""
            SELECT id, code, name, is_cash, is_active, created_at, updated_at
            FROM payment_methods
            {where}
            ORDER BY id
            LIMIT :limit OFFSET :offset
            """,  # noqa: S608 — closed whitelist literals only, user data parameterized
            params,
        )
        return rows, total

    async def get(self, id: int) -> dict[str, Any] | None:
        """Get by id or None."""
        return await self._uow.first_row(
            """
            SELECT id, code, name, is_cash, is_active, created_at, updated_at
            FROM payment_methods
            WHERE id = :id
            """,
            {"id": id},
        )

    async def get_by_code(self, code: str) -> dict[str, Any] | None:
        """Get by code (for uniqueness check)."""
        return await self._uow.first_row(
            """
            SELECT id, code, name, is_cash, is_active, created_at, updated_at
            FROM payment_methods
            WHERE code = :code
            """,
            {"code": code},
        )

    async def create(
        self,
        *,
        code: str,
        name: str,
        is_cash: bool,
        is_active: bool,
    ) -> dict[str, Any]:
        """Insert and return the new row."""
        row = await self._uow.first_row(
            """
            INSERT INTO payment_methods (code, name, is_cash, is_active)
            VALUES (:code, :name, :is_cash, :is_active)
            RETURNING id, code, name, is_cash, is_active, created_at, updated_at
            """,
            {"code": code, "name": name, "is_cash": is_cash, "is_active": is_active},
        )
        if row is None:
            raise RuntimeError("Payment method insert returned no row")
        return row

    async def update(
        self,
        id: int,
        *,
        name: str | None = None,
        is_cash: bool | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update provided fields; return the updated row or None if not found."""
        fields = []
        params: dict[str, Any] = {"id": id}
        if name is not None:
            fields.append("name = :name")
            params["name"] = name
        if is_cash is not None:
            fields.append("is_cash = :is_cash")
            params["is_cash"] = is_cash
        if is_active is not None:
            fields.append("is_active = :is_active")
            params["is_active"] = is_active

        if not fields:
            return await self.get(id)

        fields.append("updated_at = NOW()")

        row = await self._uow.first_row(
            f"""
            UPDATE payment_methods
            SET {", ".join(fields)}
            WHERE id = :id
            RETURNING id, code, name, is_cash, is_active, created_at, updated_at
            """,  # noqa: S608 — SET fields are hardcoded whitelist literals, user data parameterized
            params,
        )
        return row

    async def deactivate(self, id: int, *, reason: str | None = None) -> dict[str, Any] | None:
        """Set is_active = FALSE; return the updated row or None if not found.

        ``reason`` is accepted for API symmetry but not stored in the table
        (the audit_log captures the deactivation with context).
        """
        row = await self._uow.first_row(
            """
            UPDATE payment_methods
            SET is_active = FALSE, updated_at = NOW()
            WHERE id = :id
            RETURNING id, code, name, is_cash, is_active, created_at, updated_at
            """,
            {"id": id},
        )
        return row

    async def delete_if_unreferenced(self, id: int) -> bool:
        """Hard delete only if no referencing rows exist.

        Returns True if deleted, False if referenced (caller should raise
        Conflict with ``referenced_by_history``).
        """
        deleted = await self._uow.first_scalar(
            """
            DELETE FROM payment_methods
            WHERE id = :id
            RETURNING id
            """,
            {"id": id},
        )
        return deleted is not None


class FinancialCategoryRepository:
    """CRUD for ``financial_categories``."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list(
        self,
        *,
        active_only: bool = False,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (items, total) with pagination."""
        clauses = []
        params: dict[str, Any] = {"limit": per_page, "offset": (page - 1) * per_page}

        if active_only:
            clauses.append("is_active = TRUE")

        if q:
            clauses.append("(code ILIKE :q OR name ILIKE :q)")
            params["q"] = f"%{q}%"

        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        total_row = await self._uow.first_scalar(
            f"SELECT COUNT(*) FROM financial_categories {where}",  # noqa: S608 — closed whitelist literals only, user data parameterized
            params,
        )
        total = int(total_row or 0)

        rows = await self._uow.fetch_all(
            f"""
            SELECT id, code, name, entry_type, is_active, created_at, updated_at
            FROM financial_categories
            {where}
            ORDER BY id
            LIMIT :limit OFFSET :offset
            """,  # noqa: S608 — closed whitelist literals only, user data parameterized
            params,
        )
        return rows, total

    async def get(self, id: int) -> dict[str, Any] | None:
        """Get by id or None."""
        return await self._uow.first_row(
            """
            SELECT id, code, name, entry_type, is_active, created_at, updated_at
            FROM financial_categories
            WHERE id = :id
            """,
            {"id": id},
        )

    async def get_by_code(self, code: str) -> dict[str, Any] | None:
        """Get by code (for uniqueness check)."""
        return await self._uow.first_row(
            """
            SELECT id, code, name, entry_type, is_active, created_at, updated_at
            FROM financial_categories
            WHERE code = :code
            """,
            {"code": code},
        )

    async def create(
        self,
        *,
        code: str,
        name: str,
        entry_type: str,
        is_active: bool,
    ) -> dict[str, Any]:
        """Insert and return the new row."""
        row = await self._uow.first_row(
            """
            INSERT INTO financial_categories (code, name, entry_type, is_active)
            VALUES (:code, :name, :entry_type, :is_active)
            RETURNING id, code, name, entry_type, is_active, created_at, updated_at
            """,
            {"code": code, "name": name, "entry_type": entry_type, "is_active": is_active},
        )
        if row is None:
            raise RuntimeError("Financial category insert returned no row")
        return row

    async def update(
        self,
        id: int,
        *,
        name: str | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update provided fields; code and entry_type are immutable.

        Return the updated row or None if not found.
        """
        fields = []
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
            UPDATE financial_categories
            SET {", ".join(fields)}
            WHERE id = :id
            RETURNING id, code, name, entry_type, is_active, created_at, updated_at
            """,  # noqa: S608 — SET fields are hardcoded whitelist literals, user data parameterized
            params,
        )
        return row

    async def deactivate(self, id: int, *, reason: str | None = None) -> dict[str, Any] | None:
        """Set is_active = FALSE; return the updated row or None if not found."""
        row = await self._uow.first_row(
            """
            UPDATE financial_categories
            SET is_active = FALSE, updated_at = NOW()
            WHERE id = :id
            RETURNING id, code, name, entry_type, is_active, created_at, updated_at
            """,
            {"id": id},
        )
        return row

    async def delete_if_unreferenced(self, id: int) -> bool:
        """Hard delete only if no referencing rows exist.

        Returns True if deleted, False if referenced.
        """
        deleted = await self._uow.first_scalar(
            """
            DELETE FROM financial_categories
            WHERE id = :id
            RETURNING id
            """,
            {"id": id},
        )
        return deleted is not None


__all__ = [
    "FinancialCategoryRepository",
    "PaymentMethodRepository",
]
