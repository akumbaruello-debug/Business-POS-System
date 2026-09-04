"""Service layer for the ``units`` table.

Enforces business rules per DB spec §2.2.4 + §5.2 + API §15.6:

* ``code`` is unique across ALL rows (full unique index ``ux_units_code``
  in the locked baseline — NOT active-only).
* PATCH does not accept ``code`` changes (OpenAPI ``UnitPatch`` has no
  ``code`` field). This is the canonical API contract even though the
  DB column itself is mutable.
* Hard-delete is blocked by the DB FK (``products.unit_id ON DELETE
  RESTRICT``). The service translates the FK violation to 409
  ``referenced_by_history``.
* Deactivate is always allowed (reversible per DB spec §2.2.4).
* Audit logging on every mutation.

Schema contradiction acknowledged:
The OpenAPI ``Unit`` schema has a required ``version`` field, but the
locked ``units`` table has no ``version`` column. The service returns
``version=1`` as a stable placeholder.
"""

from __future__ import annotations

from typing import Any

from app.audit.service import (
    ENTITY_UNITS,
    AuditContext,
    write_audit,
)
from app.db import UnitOfWork
from app.errors import Conflict, NotFound, ReferencedByHistory
from app.repositories.units import UnitRepository
from app.validation.enums import AuditAction

# PostgreSQL unique_violation error code.
_PG_UNIQUE_VIOLATION = "23505"
# PostgreSQL foreign_key_violation (raised when products reference the unit).
_PG_FK_VIOLATION = "23503"


def _is_unique_violation(exc: Exception) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None:
        return str(sqlstate) == _PG_UNIQUE_VIOLATION
    return "duplicate key value" in str(exc).lower()


def _is_fk_violation(exc: Exception) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None:
        return str(sqlstate) == _PG_FK_VIOLATION
    msg = str(exc).lower()
    return "violates foreign key constraint" in msg


class UnitService:
    """Business logic for units."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = UnitRepository(uow)

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
            raise NotFound("Unit not found.", details={"resource": "unit", "id": id})
        return row

    async def create(
        self,
        *,
        code: str,
        name: str,
        is_active: bool,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Create a unit.

        ``code`` uniqueness is full (all rows). Translates any race
        unique violation to 409 ``conflict``.
        """
        try:
            row = await self._repo.create(code=code, name=name, is_active=is_active)
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    f"Unit code '{code}' already exists.",
                    details={"resource": "unit", "field": "code", "value": code},
                ) from exc
            raise

        _new_values: dict[str, Any] = {
            "id": row["id"],
            "code": row["code"],
            "name": row["name"],
            "is_active": row["is_active"],
            "version": 1,
        }
        await write_audit(
            self._uow,
            action=AuditAction.CREATE,
            entity_type=ENTITY_UNITS,
            entity_id=int(row["id"]),
            old_values=None,
            new_values=_new_values,
            ctx=ctx,
        )
        return row

    async def update(
        self,
        id: int,
        *,
        name: str | None,
        is_active: bool | None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Update a unit's name and/or is_active.

        ``code`` is NOT changed here — it is immutable through the public
        API per OpenAPI ``UnitPatch``.
        """
        old = await self.get(id)
        try:
            row = await self._repo.update(id, name=name, is_active=is_active)
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "Unit update conflicts with an existing row.",
                    details={"resource": "unit", "id": id},
                ) from exc
            raise

        if row is None:
            raise NotFound("Unit not found.", details={"resource": "unit", "id": id})

        await write_audit(
            self._uow,
            action=AuditAction.UPDATE,
            entity_type=ENTITY_UNITS,
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
            raise NotFound("Unit not found.", details={"resource": "unit", "id": id})

        await write_audit(
            self._uow,
            action=AuditAction.DEACTIVATE,
            entity_type=ENTITY_UNITS,
            entity_id=int(row["id"]),
            old_values={"is_active": old["is_active"]},
            new_values={"is_active": row["is_active"]},
            reason=reason,
            ctx=ctx,
        )
        return row

    async def delete(self, id: int, *, ctx: AuditContext | None = None) -> None:
        """Hard-delete only if no product references the unit.

        The DB FK (``products.unit_id ON DELETE RESTRICT``) is the
        authoritative guard. We translate the FK violation to 409
        ``referenced_by_history`` for a uniform error envelope.
        """
        old = await self.get(id)
        try:
            deleted = await self._repo.delete(id)
        except Exception as exc:
            if _is_fk_violation(exc):
                raise ReferencedByHistory(
                    "Unit is referenced by products.",
                    details={
                        "resource": "unit",
                        "id": id,
                    },
                ) from exc
            raise

        if not deleted:
            raise NotFound("Unit not found.", details={"resource": "unit", "id": id})

        await write_audit(
            self._uow,
            action=AuditAction.UPDATE,
            entity_type=ENTITY_UNITS,
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


__all__ = ["UnitService"]
