"""Service layer for cost_types.

Business rules enforced here, per ``Database-Design-V1.0.md`` §2.7.1 and
``Business-Rules.md`` (production costing cost-type amounts):

* ``code`` is unique across ALL rows (locked ``ux_cost_types_code UNIQUE`` —
  not active-only). Translates a race unique violation to 409 ``conflict``.
* Hard-delete is blocked by the DB FK (``production_cost_lines.cost_type_id
  ON DELETE RESTRICT``). The service translates the FK violation to 409
  ``referenced_by_history``.
* Deactivate sets ``is_active = FALSE`` and always succeeds (reversible per
  DB spec §2.7.1 — historical ``production_cost_lines`` keep their
  ``cost_type_id``).
* ``code`` is immutable via the API (``CostTypePatch`` has no ``code``).
* All writes run inside the request's ``UnitOfWork`` transaction so the
  ``audit_log`` row + business mutation commit / roll back atomically.

Documented contradiction (same family as ``units``):
The OpenAPI ``CostType`` schema requires a ``version`` integer, but the
locked ``cost_types`` table has no ``version`` (nor ``updated_at``) column.
The response layer injects ``version = 1`` as a stable placeholder; no ETag
source exists, so ``If-Match`` on PATCH is not enforced (mirrors the
committed ``units`` / ``payment_methods`` / ``financial_categories``
behavior, which also do not enforce If-Match despite the openapi param).
"""

from __future__ import annotations

from typing import Any

from app.audit.service import (
    ENTITY_COST_TYPES,
    AuditContext,
    write_audit,
)
from app.db import UnitOfWork
from app.errors import Conflict, NotFound, ReferencedByHistory
from app.repositories.cost_types import CostTypeRepository
from app.validation.enums import AuditAction

# PostgreSQL error codes.
_PG_UNIQUE_VIOLATION = "23505"
_PG_FK_VIOLATION = "23503"


def _is_unique_violation(exc: Exception) -> bool:
    """True if the underlying asyncpg/psycopg exception is a unique violation."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None:
        return str(sqlstate) == _PG_UNIQUE_VIOLATION
    return "duplicate key value" in str(exc).lower()


def _is_fk_violation(exc: Exception) -> bool:
    """True if the underlying exception is a foreign-key violation."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None:
        return str(sqlstate) == _PG_FK_VIOLATION
    msg = str(exc).lower()
    return "violates foreign key constraint" in msg or "foreign key" in msg


class CostTypeService:
    """Business logic for cost_types."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = CostTypeRepository(uow)

    async def list(
        self,
        *,
        page: int,
        per_page: int,
        q: str | None = None,
        active_only: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self._repo.list(page=page, per_page=per_page, q=q, active_only=active_only)

    async def get(self, id: int) -> dict[str, Any]:
        row = await self._repo.get(id)
        if row is None:
            raise NotFound("Cost type not found.", details={"resource": "cost_type", "id": id})
        return row

    async def create(
        self,
        *,
        code: str,
        name: str,
        is_active: bool,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Create a cost type.

        ``code`` uniqueness is full (all rows). Translates any race unique
        violation to 409 ``conflict``.
        """
        try:
            row = await self._repo.create(code=code, name=name, is_active=is_active)
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    f"Cost type code '{code}' already exists.",
                    details={
                        "resource": "cost_type",
                        "field": "code",
                        "value": code,
                    },
                ) from exc
            raise

        new_values: dict[str, Any] = {
            "id": row["id"],
            "code": row["code"],
            "name": row["name"],
            "is_active": row["is_active"],
            "version": 1,
        }
        await write_audit(
            self._uow,
            action=AuditAction.CREATE,
            entity_type=ENTITY_COST_TYPES,
            entity_id=int(row["id"]),
            old_values=None,
            new_values=new_values,
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
        """Update a cost type's name and/or is_active.

        ``code`` is NOT changed here — it is immutable through the public
        API (the DB column is mutable, the API contract is not).
        """
        old = await self.get(id)
        if name is None and is_active is None:
            # Nothing to change; return existing row (no-op, no audit write).
            return old
        try:
            row = await self._repo.update(id, name=name, is_active=is_active)
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "Cost type update conflicts with an existing row.",
                    details={"resource": "cost_type", "id": id},
                ) from exc
            raise

        if row is None:
            raise NotFound("Cost type not found.", details={"resource": "cost_type", "id": id})

        await write_audit(
            self._uow,
            action=AuditAction.UPDATE,
            entity_type=ENTITY_COST_TYPES,
            entity_id=int(row["id"]),
            old_values={
                "id": old["id"],
                "code": old["code"],
                "name": old["name"],
                "is_active": old["is_active"],
            },
            new_values={
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
                "is_active": row["is_active"],
                "version": 1,
            },
            ctx=ctx,
        )
        return row

    async def deactivate(
        self, id: int, *, reason: str | None = None, ctx: AuditContext | None = None
    ) -> dict[str, Any]:
        """Set is_active = FALSE. Always allowed (reversible)."""
        old = await self.get(id)
        row = await self._repo.deactivate(id)
        if row is None:
            raise NotFound("Cost type not found.", details={"resource": "cost_type", "id": id})

        await write_audit(
            self._uow,
            action=AuditAction.DEACTIVATE,
            entity_type=ENTITY_COST_TYPES,
            entity_id=int(row["id"]),
            old_values={"is_active": old["is_active"]},
            new_values={"is_active": row["is_active"]},
            reason=reason,
            ctx=ctx,
        )
        return row

    async def delete(self, id: int, *, ctx: AuditContext | None = None) -> None:
        """Hard-delete only if no production_cost_lines reference the type.

        The DB FK (``production_cost_lines.cost_type_id ON DELETE RESTRICT``)
        is the authoritative guard. We translate the FK violation to 409
        ``referenced_by_history``.
        """
        old = await self.get(id)
        try:
            deleted = await self._repo.delete_if_unreferenced(id)
        except Exception as exc:
            if _is_fk_violation(exc):
                raise ReferencedByHistory(
                    "Cost type is referenced by production cost lines.",
                    details={"resource": "cost_type", "id": id},
                ) from exc
            raise

        if not deleted:
            raise NotFound("Cost type not found.", details={"resource": "cost_type", "id": id})

        await write_audit(
            self._uow,
            action=AuditAction.UPDATE,
            entity_type=ENTITY_COST_TYPES,
            entity_id=int(old["id"]),
            old_values={
                "id": old["id"],
                "code": old["code"],
                "name": old["name"],
                "is_active": old["is_active"],
            },
            new_values=None,
            reason="hard_delete",
            ctx=ctx,
        )


__all__ = [
    "CostTypeService",
]
