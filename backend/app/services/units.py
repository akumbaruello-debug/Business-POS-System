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
from app.errors import Conflict, NotFound, ReferencedByHistory, ValidationFailed
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


def _is_check_violation(exc: Exception) -> bool:
    """PostgreSQL check_violation (23514), e.g. ck_units_base_not_self."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None:
        return str(sqlstate) == "23514"
    return "violates check constraint" in str(exc).lower()


def _base_reference_error() -> ValidationFailed:
    return ValidationFailed(
        "Referenced base unit is invalid.",
        errors=[{"loc": ["base_unit_id"], "msg": "invalid base unit reference"}],
    )


def convert_quantity(
    qty: float,
    from_factor: float | None,
    to_factor: float | None,
) -> float:
    """Convert a quantity between two units of the SAME dimension.

    Both factors are relative to the dimension base (kg=1, kuintal=100,
    ton=1000 in mass; buah=1 in count). The dimension base unit itself
    does not need a base_unit_id and is conventionally stored with
    factor_to_base=1.

    Incomplete conversion data is rejected explicitly: an unknown factor
    (None) is never silently treated as factor=1. Each unit must declare
    its factor, or conversion must go to the base with factor 1.
    """
    if from_factor is None or to_factor is None:
        raise ValidationFailed("Both units must define conversion factors.")
    if from_factor <= 0 or to_factor <= 0:
        raise ValidationFailed("Conversion factors must be positive.")
    # qty(unit A) * factorA gives base; then / factorB gives unit B.
    return round(qty * from_factor / to_factor, 6)


def _require_same_dimension(
    *,
    base: dict[str, Any],
    unit_dimension: str | None,
    base_unit_id: int | None,
) -> None:
    """A referenced base unit must belong to the same dimension as the unit."""
    if base_unit_id is None:
        return
    base_dim = base.get("dimension")
    if base_dim is not None and unit_dimension is not None and base_dim != unit_dimension:
        raise ValidationFailed(
            "Cross-dimension conversion is not allowed. ",
            errors=[{"loc": ["base_unit_id"], "msg": "base unit dimension differs from unit dimension"}],
        )
    # A base_unit_id pointing at a unit with dimension NULL is tolerated
    # (legacy rows — no explicit rule broken).



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
        dimension: str | None = None,
        base_unit_id: int | None = None,
        factor_to_base: float | None = None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Create a unit.

        ``code`` uniqueness is full (all rows). Translates any race
        unique violation to 409 ``conflict``.
        """
        if base_unit_id is not None:
            base = await self.get(base_unit_id)  # NotFound → clean 404, never 500
            _require_same_dimension(
                base=base,
                unit_dimension=dimension,
                base_unit_id=base_unit_id,
            )
        try:
            row = await self._repo.create(
                code=code,
                name=name,
                is_active=is_active,
                dimension=dimension,
                base_unit_id=base_unit_id,
                factor_to_base=factor_to_base,
            )
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    f"Unit code '{code}' already exists.",
                    details={"resource": "unit", "field": "code", "value": code},
                ) from exc
            if _is_fk_violation(exc):
                # Race only — base_unit_id pre-validated above, but concurrent
                # delete of the base between check and insert hits this.
                raise _base_reference_error() from exc
            if _is_check_violation(exc):
                # base_unit_id = id (self-reference) violates the DB CHECK.
                raise _base_reference_error() from exc
            raise

        _new_values: dict[str, Any] = {
            "id": row["id"],
            "code": row["code"],
            "name": row["name"],
            "is_active": row["is_active"],
            "dimension": row.get("dimension"),
            "base_unit_id": row.get("base_unit_id"),
            "factor_to_base": row.get("factor_to_base"),
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
        dimension: str | None = None,
        base_unit_id: int | None = None,
        factor_to_base: float | None = None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Update a unit's name and/or is_active and/or conversion metadata.

        ``code`` is NOT changed here — it is immutable through the public
        API per OpenAPI.
        """
        old = await self.get(id)
        if base_unit_id is not None:
            base = await self.get(base_unit_id)  # NotFound → clean 404, never 500
            _require_same_dimension(
                base=base,
                unit_dimension=dimension if dimension is not None else old["dimension"],
                base_unit_id=base_unit_id,
            )
        try:
            row = await self._repo.update(
                id,
                name=name,
                is_active=is_active,
                dimension=dimension,
                base_unit_id=base_unit_id,
                factor_to_base=factor_to_base,
            )
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "Unit update conflicts with an existing row.",
                    details={"resource": "unit", "id": id},
                ) from exc
            if _is_fk_violation(exc):
                raise _base_reference_error() from exc
            if _is_check_violation(exc):
                raise _base_reference_error() from exc
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
                "dimension": old.get("dimension"),
                "base_unit_id": old.get("base_unit_id"),
                "factor_to_base": old.get("factor_to_base"),
            },
            new_values={
                "id": row["id"],
                "code": row["code"],
                "name": row["name"],
                "is_active": row["is_active"],
                "dimension": row.get("dimension"),
                "base_unit_id": row.get("base_unit_id"),
                "factor_to_base": row.get("factor_to_base"),
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


__all__ = ["UnitService", "convert_quantity"]
