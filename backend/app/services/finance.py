"""Service layer for payment_methods & financial_categories.

Business rules enforced here:

* Payment methods:
  - ``code`` unique (DB-level; we translate the unique-violation to 409).
  - No positive ``is_cash`` duplicates beyond the existing seed semantics
    (caller-controlled; cash flag drives over-tender logic).
  - DELETE only if unreferenced by history; otherwise 409
    ``referenced_by_history``.
  - Soft-deactivate sets ``is_active=FALSE`` while preserving history.

* Financial categories:
  - ``code`` unique.
  - ``entry_type`` is ``income`` or ``expense`` (DB CHECK enforced).
  - The :func:`app.domain.blacklist.assert_not_blacklisted` rule is
    invoked on every name mutation (create + rename). The function is
    the single source of truth — no route bypasses it.
  - Same hard-delete-unreferenced rule as payment methods.

All writes run inside the request's ``UnitOfWork`` transaction so the
``audit_log`` row + business mutation commit / roll back atomically.
"""

from __future__ import annotations

from typing import Any

from app.audit.service import (
    ENTITY_FINANCIAL_CATEGORIES,
    ENTITY_PAYMENT_METHODS,
    AuditContext,
    write_audit,
)
from app.db import UnitOfWork
from app.domain.blacklist import assert_not_blacklisted
from app.errors import Conflict, NotFound, ValidationFailed
from app.repositories.finance import (
    FinancialCategoryRepository,
    PaymentMethodRepository,
)
from app.validation.enums import AuditAction

# PostgreSQL unique_violation error code.
_PG_UNIQUE_VIOLATION = "23505"
# PostgreSQL foreign_key_violation (raised when the row is referenced).
_PG_FK_VIOLATION = "23503"


def _is_unique_violation(exc: Exception) -> bool:
    """True if the underlying asyncpg/psycopg exception is a unique violation."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None:
        return str(sqlstate) == _PG_UNIQUE_VIOLATION
    return "duplicate key value" in str(exc).lower()


def _is_fk_violation(exc: Exception) -> bool:
    """True if the underlying exception is a FK violation."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None:
        return str(sqlstate) == _PG_FK_VIOLATION
    msg = str(exc).lower()
    return "violates foreign key constraint" in msg


class PaymentMethodService:
    """Business logic for payment_methods."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = PaymentMethodRepository(uow)

    async def list(
        self,
        *,
        page: int,
        per_page: int,
        q: str | None = None,
        active_only: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._repo.list(active_only=active_only, page=page, per_page=per_page, q=q)

    async def get(self, id: int) -> dict[str, Any]:
        row = await self._repo.get(id)
        if row is None:
            raise NotFound(
                "Payment method not found.", details={"resource": "payment_method", "id": id}
            )
        return row

    async def create(
        self,
        *,
        code: str,
        name: str,
        is_cash: bool,
        is_active: bool,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        try:
            row = await self._repo.create(
                code=code, name=name, is_cash=is_cash, is_active=is_active
            )
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    f"Payment method code '{code}' already exists.",
                    details={"resource": "payment_method", "field": "code", "value": code},
                ) from exc
            raise

        await write_audit(
            self._uow,
            action=AuditAction.CREATE,
            entity_type=ENTITY_PAYMENT_METHODS,
            entity_id=int(row["id"]),
            old_values=None,
            new_values={
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
                "is_cash": row["is_cash"],
                "is_active": row["is_active"],
            },
            ctx=ctx,
        )
        return row

    async def update(
        self,
        id: int,
        *,
        name: str | None,
        is_cash: bool | None,
        is_active: bool | None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        old = await self.get(id)
        try:
            row = await self._repo.update(id, name=name, is_cash=is_cash, is_active=is_active)
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "Payment method update conflicts with an existing row.",
                    details={"resource": "payment_method", "id": id},
                ) from exc
            raise

        if row is None:
            raise NotFound(
                "Payment method not found.", details={"resource": "payment_method", "id": id}
            )

        await write_audit(
            self._uow,
            action=AuditAction.UPDATE,
            entity_type=ENTITY_PAYMENT_METHODS,
            entity_id=int(row["id"]),
            old_values={
                "id": old["id"],
                "code": old["code"],
                "name": old["name"],
                "is_cash": old["is_cash"],
                "is_active": old["is_active"],
            },
            new_values={
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
                "is_cash": row["is_cash"],
                "is_active": row["is_active"],
            },
            ctx=ctx,
        )
        return row

    async def deactivate(
        self, id: int, *, reason: str | None = None, ctx: AuditContext | None = None
    ) -> dict[str, Any]:
        old = await self.get(id)
        row = await self._repo.deactivate(id, reason=reason)
        if row is None:
            raise NotFound(
                "Payment method not found.", details={"resource": "payment_method", "id": id}
            )

        await write_audit(
            self._uow,
            action=AuditAction.DEACTIVATE,
            entity_type=ENTITY_PAYMENT_METHODS,
            entity_id=int(row["id"]),
            old_values={"is_active": old["is_active"]},
            new_values={"is_active": row["is_active"]},
            reason=reason,
            ctx=ctx,
        )
        return row

    async def delete(self, id: int, *, ctx: AuditContext | None = None) -> None:
        """Hard delete only if no referencing rows exist.

        The DB has no FK from sale_payments to payment_methods at the M1
        baseline (sale_payments references payments via a string code in
        the app layer), so the canonical "referenced by history" rule is
        enforced *here* by attempting the DELETE inside the transaction
        and translating any FK violation to 409 ``referenced_by_history``.
        """
        old = await self.get(id)
        try:
            deleted = await self._repo.delete_if_unreferenced(id)
        except Exception as exc:
            if _is_fk_violation(exc):
                raise Conflict(
                    "Payment method is referenced by history.",
                    details={
                        "resource": "payment_method",
                        "id": id,
                        "code": "referenced_by_history",
                    },
                ) from exc
            raise

        if not deleted:
            raise NotFound(
                "Payment method not found.", details={"resource": "payment_method", "id": id}
            )

        await write_audit(
            self._uow,
            action=AuditAction.UPDATE,
            entity_type=ENTITY_PAYMENT_METHODS,
            entity_id=int(old["id"]),
            old_values={
                "id": old["id"],
                "code": old["code"],
                "name": old["name"],
                "is_cash": old["is_cash"],
                "is_active": old["is_active"],
            },
            new_values=None,
            reason="hard_delete",
            ctx=ctx,
        )


class FinancialCategoryService:
    """Business logic for financial_categories.

    The blacklist rule is enforced through a single helper,
    :func:`app.domain.blacklist.assert_not_blacklisted`, called on every
    name mutation (create + update). Both the HTTP routes and any future
    service entry-point (import script, bulk seed) **must** call the
    service methods below — they are the only path that touches the
    table, so the rule cannot be bypassed by a different endpoint.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = FinancialCategoryRepository(uow)

    async def list(
        self,
        *,
        page: int,
        per_page: int,
        q: str | None = None,
        active_only: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._repo.list(active_only=active_only, page=page, per_page=per_page, q=q)

    async def get(self, id: int) -> dict[str, Any]:
        row = await self._repo.get(id)
        if row is None:
            raise NotFound(
                "Financial category not found.",
                details={"resource": "financial_category", "id": id},
            )
        return row

    async def create(
        self,
        *,
        code: str,
        name: str,
        entry_type: str,
        is_active: bool,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        # Blacklist rule is enforced here — both create and update go
        # through the service so this single call covers every HTTP
        # path. (See ``assert_not_blacklisted`` docstring.)
        assert_not_blacklisted(name, field="name")

        try:
            row = await self._repo.create(
                code=code, name=name, entry_type=entry_type, is_active=is_active
            )
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    f"Financial category code '{code}' already exists.",
                    details={"resource": "financial_category", "field": "code", "value": code},
                ) from exc
            raise

        await write_audit(
            self._uow,
            action=AuditAction.CREATE,
            entity_type=ENTITY_FINANCIAL_CATEGORIES,
            entity_id=int(row["id"]),
            old_values=None,
            new_values={
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
                "entry_type": row["entry_type"],
                "is_active": row["is_active"],
            },
            ctx=ctx,
        )
        return row

    async def update(
        self,
        id: int,
        *,
        name: str | None = None,
        is_active: bool | None = None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        old = await self.get(id)

        # Blacklist rule fires on rename as well — the spec requires this
        # so a category can never be renamed *into* a derived name (and
        # therefore appear in the manual-entry list as a derived).
        if name is not None:
            assert_not_blacklisted(name, field="name")

        try:
            row = await self._repo.update(id, name=name, is_active=is_active)
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "Financial category update conflicts with an existing row.",
                    details={"resource": "financial_category", "id": id},
                ) from exc
            raise

        if row is None:
            raise NotFound(
                "Financial category not found.",
                details={"resource": "financial_category", "id": id},
            )

        await write_audit(
            self._uow,
            action=AuditAction.UPDATE,
            entity_type=ENTITY_FINANCIAL_CATEGORIES,
            entity_id=int(row["id"]),
            old_values={
                "id": old["id"],
                "code": old["code"],
                "name": old["name"],
                "entry_type": old["entry_type"],
                "is_active": old["is_active"],
            },
            new_values={
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
                "entry_type": row["entry_type"],
                "is_active": row["is_active"],
            },
            ctx=ctx,
        )
        return row

    async def deactivate(
        self, id: int, *, reason: str | None = None, ctx: AuditContext | None = None
    ) -> dict[str, Any]:
        old = await self.get(id)
        row = await self._repo.deactivate(id, reason=reason)
        if row is None:
            raise NotFound(
                "Financial category not found.",
                details={"resource": "financial_category", "id": id},
            )

        await write_audit(
            self._uow,
            action=AuditAction.DEACTIVATE,
            entity_type=ENTITY_FINANCIAL_CATEGORIES,
            entity_id=int(row["id"]),
            old_values={"is_active": old["is_active"]},
            new_values={"is_active": row["is_active"]},
            reason=reason,
            ctx=ctx,
        )
        return row

    async def delete(self, id: int, *, ctx: AuditContext | None = None) -> None:
        """Hard delete only if no referencing rows exist."""
        old = await self.get(id)
        try:
            deleted = await self._repo.delete_if_unreferenced(id)
        except Exception as exc:
            if _is_fk_violation(exc):
                raise Conflict(
                    "Financial category is referenced by history.",
                    details={
                        "resource": "financial_category",
                        "id": id,
                        "code": "referenced_by_history",
                    },
                ) from exc
            raise

        if not deleted:
            raise NotFound(
                "Financial category not found.",
                details={"resource": "financial_category", "id": id},
            )

        await write_audit(
            self._uow,
            action=AuditAction.UPDATE,
            entity_type=ENTITY_FINANCIAL_CATEGORIES,
            entity_id=int(old["id"]),
            old_values={
                "id": old["id"],
                "code": old["code"],
                "name": old["name"],
                "entry_type": old["entry_type"],
                "is_active": old["is_active"],
            },
            new_values=None,
            reason="hard_delete",
            ctx=ctx,
        )


# Re-export so callers can import from one place.
__all__ = [
    "FinancialCategoryService",
    "PaymentMethodService",
    "ValidationFailed",
]
