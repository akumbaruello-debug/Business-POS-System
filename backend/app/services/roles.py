"""Service layer for Roles.

Business rules per DB spec §4 + API §15.4:

* ``name`` is unique (index ``ux_roles_name``) → 409 Conflict.
* Custom roles start with zero capabilities.
* System roles (``is_system_role = TRUE``) cannot be PATCHed, DELETEd,
  or have capabilities replaced via PUT.
* Hard-delete of a role referenced by users is blocked → 409 referenced_by_history.
* ``version``/ETag derived from ``updated_at``.
* PATCH ``If-Match`` is required per OpenAPI → 400 missing header; stale → 412.
* Idempotency-Key required on POST / DELETE / PUT capabilities.
* Unknown capability codes → 400 enum_value_invalid.
* All mutations write audit_log.
"""

from __future__ import annotations

from typing import Any

from app.audit.service import ENTITY_ROLES, AuditContext, write_audit
from app.authz.caps import is_capability
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.db import UnitOfWork
from app.errors import (
    Conflict,
    NotFound,
    ReferencedByHistory,
    SystemRoleImmutable,
    ValidationFailed,
)
from app.repositories.roles import RoleRepository
from app.validation.enums import AuditAction

_PG_UNIQUE_VIOLATION = "23505"
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


def _version_from_row(row: dict[str, Any]) -> tuple[str, int]:
    if row is None:
        raise NotFound("Role not found.")
    return etag_and_version_from_updated_at(row["updated_at"])


class RoleService:
    """Business logic for roles."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = RoleRepository(uow)

    # ------------------------------------------------------------------
    # List / Get
    # ------------------------------------------------------------------

    async def list_roles(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        sort: str = "id",
    ) -> tuple[list[dict[str, Any]], int]:
        """List roles with pagination."""
        rows, total = await self._repo.list_roles(
            page=page, per_page=per_page, q=q, sort=sort
        )
        return rows, total

    async def get(self, id: int) -> dict[str, Any]:
        """Get a single role, or raise NotFound."""
        row = await self._repo.get(id)
        if row is None:
            raise NotFound(f"Role {id} not found.")
        return row

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        description: str | None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Create a new custom role with zero capabilities."""
        try:
            row = await self._repo.create(name=name, description=description)
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "A role with this name already exists.",
                    details={"field": "name", "code": "role_name_exists"},
                ) from exc
            raise

        if row is None:
            raise Conflict(
                "Role could not be created.",
                details={"field": "name", "code": "role_name_exists"},
            )

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_ROLES,
                entity_id=int(row["id"]),
                new_values={k: v for k, v in row.items()},
                ctx=ctx,
            )

        await self._uow.commit()
        return row

    # ------------------------------------------------------------------
    # Update (PATCH)
    # ------------------------------------------------------------------

    async def update(
        self,
        id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        if_match: str | None = None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Update a custom role. System roles are immutable."""
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Role {id} not found.")

        if existing["is_system_role"]:
            raise SystemRoleImmutable("System roles cannot be modified.")

        etag, _ = _version_from_row(existing)
        check_if_match(provided=if_match, current_etag=etag)

        try:
            result = await self._repo.update(id, name=name, description=description)
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "A role with this name already exists.",
                    details={"field": "name", "code": "role_name_exists"},
                ) from exc
            raise

        if result is None:
            raise NotFound(f"Role {id} not found after update.")

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_ROLES,
                entity_id=id,
                old_values=dict(existing),
                new_values={k: v for k, v in result.items()},
                ctx=ctx,
            )

        await self._uow.commit()
        return result

    # ------------------------------------------------------------------
    # Hard Delete
    # ------------------------------------------------------------------

    async def delete(
        self,
        id: int,
        *,
        ctx: AuditContext | None = None,
    ) -> None:
        """Hard-delete a custom role.

        Blocked by:
        * System role → 403 system_role_immutable.
        * Users assigned → 409 referenced_by_history.
        * Not found → 404.
        """
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Role {id} not found.")

        if existing["is_system_role"]:
            raise SystemRoleImmutable("System roles cannot be deleted.")

        user_count = await self._repo.count_users(id)
        if user_count > 0:
            raise ReferencedByHistory(
                "Role is assigned to users.",
                details={"resource": "role", "id": id, "user_count": user_count},
            )

        try:
            deleted = await self._repo.hard_delete(id)
        except Exception as exc:
            if _is_fk_violation(exc):
                raise ReferencedByHistory(
                    "Role is referenced by users.",
                    details={"resource": "role", "id": id},
                ) from exc
            raise

        if not deleted:
            raise NotFound(f"Role {id} not found after delete.")

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_ROLES,
                entity_id=id,
                old_values=dict(existing),
                new_values=None,
                ctx=ctx,
            )

        await self._uow.commit()

    # ------------------------------------------------------------------
    # Set capabilities
    # ------------------------------------------------------------------

    async def set_capabilities(
        self,
        id: int,
        *,
        capability_codes: list[str],
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Replace the capability set of a role.

        System roles are immutable. Unknown codes → 400 enum_value_invalid.
        """
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Role {id} not found.")

        if existing["is_system_role"]:
            raise SystemRoleImmutable("System roles cannot be modified.")

        # Validate every code against the canonical catalog.
        invalid = [code for code in capability_codes if not is_capability(code)]
        if invalid:
            raise ValidationFailed(
                "Unknown capability code(s).",
                errors=[
                    {
                        "code": "enum_value_invalid",
                        "field": "capability_codes",
                        "message": f"{code!r} is not a valid capability code.",
                    }
                    for code in invalid
                ],
            )

        capability_ids: list[int] = []
        for code in capability_codes:
            cap_id = await self._repo.get_capability_id(code)
            if cap_id is None:
                # Defensive: catalog says it is valid, but DB row missing.
                raise ValidationFailed(
                    "Unknown capability code.",
                    errors=[
                        {
                            "code": "enum_value_invalid",
                            "field": "capability_codes",
                            "message": f"{code!r} is not a valid capability code.",
                        }
                    ],
                )
            capability_ids.append(cap_id)

        await self._repo.set_capabilities(id, capability_ids)

        # Refresh the role row so updated_at is current after the trigger.
        result = await self._repo.get(id)
        assert result is not None

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.PERMISSION_GRANT,
                entity_type=ENTITY_ROLES,
                entity_id=id,
                new_values={"capability_codes": sorted(capability_codes)},
                ctx=ctx,
            )

        await self._uow.commit()
        return result


__all__ = ["RoleService"]
