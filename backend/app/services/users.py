"""Service layer for Users.

Business rules per DB spec §4 + API §Users:

* ``username`` is unique → 409 ``username_exists``.
* ``email`` is unique when non-NULL → 409 ``email_exists``.
* Passwords are hashed with the canonical ``PasswordHasher`` (Argon2id).
* Deactivation of the last active Owner is forbidden.
* Deactivation / reset-password revoke all active sessions for the user.
* Unlock clears the lockout state.
* Capability overrides take effect immediately and are audited.
* Owner's effective capabilities always equal the full catalog.
* Mutations write audit_log.
"""
from __future__ import annotations

from typing import Any

from app.audit.service import ENTITY_USERS, AuditContext, write_audit
from app.auth.session_store import SessionStore
from app.authz.caps import is_capability
from app.authz.roles import RoleResolver
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.core.security import PasswordHasher
from app.db import UnitOfWork
from app.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.repositories.users import UserRepository
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
    return "violates foreign key constraint" in str(exc).lower()


def _version_from_row(row: dict[str, Any]) -> tuple[str, int]:
    if row is None:
        raise NotFound("User not found.")
    return etag_and_version_from_updated_at(row["updated_at"])


def _to_audit_values(row: dict[str, Any]) -> dict[str, Any]:
    # Strip internal/sensitive columns before audit snapshot.
    return {k: v for k, v in row.items() if k not in {"password_hash"}}


class _CapabilitySource:
    """DB-backed source for RoleResolver."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = UserRepository(uow)

    async def role_name(self, role_id: int) -> str | None:
        row = await self._repo.get_role(role_id)
        return row["name"] if row else None

    async def role_capabilities(self, role_id: int) -> set[str]:
        rows = await self._uow.scalars(
            """
            SELECT c.code
            FROM role_capabilities rc
            JOIN capabilities c ON c.id = rc.capability_id
            WHERE rc.role_id = :rid
            """,
            {"rid": role_id},
        )
        return set(rows.all())

    async def user_overrides(self, user_id: int) -> dict[str, bool]:
        rows = await self._repo.list_capability_overrides(user_id)
        return {r["capability_code"]: bool(r["is_granted"]) for r in rows}


class UserService:
    """Business logic for users."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = UserRepository(uow)
        self._hasher = PasswordHasher()

    # ------------------------------------------------------------------
    # List / Get
    # ------------------------------------------------------------------

    async def list(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        active_only: bool | None = None,
        role_id: int | None = None,
        sort: str = "id",
    ) -> tuple[list[dict[str, Any]], int, list[tuple[str, int]]]:
        rows, total = await self._repo.list(
            page=page,
            per_page=per_page,
            q=q,
            active_only=active_only,
            role_id=role_id,
            sort=sort,
        )
        versions = [_version_from_row(r) for r in rows]
        return rows, total, versions

    async def get(self, id: int) -> dict[str, Any]:
        row = await self._repo.get(id)
        if row is None:
            raise NotFound(f"User {id} not found.")
        return row

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def _validate_role(self, role_id: int) -> None:
        role = await self._repo.get_role(role_id)
        if role is None:
            raise ValidationFailed(
                "Invalid role.",
                errors=[
                    {
                        "code": "enum_value_invalid",
                        "field": "role_id",
                        "message": f"Role {role_id} does not exist.",
                    }
                ],
            )

    async def create(
        self,
        *,
        username: str,
        full_name: str,
        email: str | None,
        password: str,
        role_id: int,
        created_by: int | None = None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        await self._validate_role(role_id)

        if len(password) < 8:
            raise ValidationFailed(
                "Password must be at least 8 characters.",
                errors=[
                    {
                        "code": "validation_failed",
                        "field": "password",
                        "message": "Password must be at least 8 characters.",
                    }
                ],
            )

        password_hash = self._hasher.hash(password)

        try:
            row = await self._repo.create(
                username=username,
                full_name=full_name,
                email=email,
                password_hash=password_hash,
                role_id=role_id,
                created_by=created_by,
            )
        except Exception as exc:
            if _is_unique_violation(exc):
                msg = str(exc).lower()
                if "ux_users_username" in msg or "username" in msg:
                    raise Conflict(
                        "A user with this username already exists.",
                        details={"field": "username", "code": "username_exists"},
                    ) from exc
                if "ux_users_email" in msg or "email" in msg:
                    raise Conflict(
                        "A user with this email already exists.",
                        details={"field": "email", "code": "email_exists"},
                    ) from exc
                raise Conflict("User already exists.") from exc
            if _is_fk_violation(exc):
                raise ValidationFailed(
                    "Invalid role.",
                    errors=[
                        {
                            "code": "enum_value_invalid",
                            "field": "role_id",
                            "message": f"Role {role_id} does not exist.",
                        }
                    ],
                ) from exc
            raise

        if row is None:
            raise Conflict("User could not be created.")

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_USERS,
                entity_id=int(row["id"]),
                new_values=_to_audit_values(row),
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
        full_name: str | None = None,
        email: Any = Ellipsis,
        is_active: bool | None = None,
        role_id: int | None = None,
        if_match: str | None = None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"User {id} not found.")

        etag, _ = _version_from_row(existing)
        check_if_match(provided=if_match, current_etag=etag)

        if role_id is not None:
            await self._validate_role(role_id)

        try:
            result = await self._repo.update(
                id,
                full_name=full_name,
                email=email,
                is_active=is_active,
                role_id=role_id,
                updated_by=ctx.user_id if ctx else None,
            )
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "A user with this email already exists.",
                    details={"field": "email", "code": "email_exists"},
                ) from exc
            raise

        if result is None:
            raise NotFound(f"User {id} not found after update.")

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_USERS,
                entity_id=id,
                old_values=_to_audit_values(existing),
                new_values=_to_audit_values(result),
                ctx=ctx,
            )

        await self._uow.commit()
        return result

    # ------------------------------------------------------------------
    # Deactivate
    # ------------------------------------------------------------------

    async def deactivate(
        self,
        id: int,
        *,
        reason: str | None = None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"User {id} not found.")

        role = await self._repo.get_role(existing["role_id"])
        is_owner = bool(role and role["name"] == "Owner")
        if is_owner and existing.get("is_active", True):
            count = await self._repo.count_active_owners()
            if count <= 1:
                raise PermissionDenied(
                    "Cannot deactivate the last active Owner.",
                    details={"code": "last_active_owner_protected"},
                )

        row = await self._repo.deactivate(id, updated_by=ctx.user_id if ctx else None)
        assert row is not None

        # Revoke all active sessions for the user.
        await SessionStore(self._uow).revoke_all_for_user(
            user_id=id,
            reason="deactivate",
        )

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.DEACTIVATE,
                entity_type=ENTITY_USERS,
                entity_id=id,
                old_values=_to_audit_values(existing),
                new_values=_to_audit_values(row),
                reason=reason,
                ctx=ctx,
            )

        await self._uow.commit()
        return row

    # ------------------------------------------------------------------
    # Reset password
    # ------------------------------------------------------------------

    async def reset_password(
        self,
        id: int,
        *,
        new_password: str,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"User {id} not found.")

        if len(new_password) < 8:
            raise ValidationFailed(
                "Password must be at least 8 characters.",
                errors=[
                    {
                        "code": "validation_failed",
                        "field": "new_password",
                        "message": "Password must be at least 8 characters.",
                    }
                ],
            )

        password_hash = self._hasher.hash(new_password)
        row = await self._repo.update(
            id,
            password_hash=password_hash,
            updated_by=ctx.user_id if ctx else None,
        )
        assert row is not None

        # Revoke all active sessions.
        await SessionStore(self._uow).revoke_all_for_user(
            user_id=id,
            reason="password_reset",
        )

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_USERS,
                entity_id=id,
                old_values=_to_audit_values(existing),
                new_values=_to_audit_values(row),
                ctx=ctx,
            )

        await self._uow.commit()
        return row

    # ------------------------------------------------------------------
    # Unlock
    # ------------------------------------------------------------------

    async def unlock(
        self,
        id: int,
        *,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"User {id} not found.")

        row = await self._repo.unlock(id, updated_by=ctx.user_id if ctx else None)
        assert row is not None

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_USERS,
                entity_id=id,
                old_values=_to_audit_values(existing),
                new_values=_to_audit_values(row),
                ctx=ctx,
            )

        await self._uow.commit()
        return row

    # ------------------------------------------------------------------
    # Effective capabilities
    # ------------------------------------------------------------------

    async def effective_capabilities(self, id: int) -> dict[str, Any]:
        row = await self._repo.get(id)
        if row is None:
            raise NotFound(f"User {id} not found.")

        role_id = int(row["role_id"])
        role_name = str(row["role_name"])

        source = _CapabilitySource(self._uow)
        resolver = RoleResolver(source=source)

        base = await resolver.base_capabilities(role_id)
        overrides_rows = await self._repo.list_capability_overrides(id)
        effective = await resolver.effective_capabilities(role_id=role_id, user_id=id)

        return {
            "role": role_name,
            "role_capabilities": sorted(base),
            "overrides": overrides_rows,
            "effective": sorted(effective),
        }

    # ------------------------------------------------------------------
    # Capability grant
    # ------------------------------------------------------------------

    async def grant_capability(
        self,
        id: int,
        *,
        capability_code: str,
        is_granted: bool,
        if_match: str | None,
        granted_by: int,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        existing_user = await self._repo.get(id)
        if existing_user is None:
            raise NotFound(f"User {id} not found.")

        etag, _ = _version_from_row(existing_user)
        check_if_match(provided=if_match, current_etag=etag)

        if not is_capability(capability_code):
            raise ValidationFailed(
                "Unknown capability code.",
                errors=[
                    {
                        "code": "enum_value_invalid",
                        "field": "capability_code",
                        "message": f"{capability_code!r} is not a valid capability code.",
                    }
                ],
            )

        capability_id = await self._repo.get_capability_id(capability_code)
        if capability_id is None:
            raise ValidationFailed(
                "Unknown capability code.",
                errors=[
                    {
                        "code": "enum_value_invalid",
                        "field": "capability_code",
                        "message": f"{capability_code!r} is not a valid capability code.",
                    }
                ],
            )

        await self._repo.upsert_capability_override(
            user_id=id,
            capability_id=capability_id,
            is_granted=is_granted,
            granted_by=granted_by,
        )

        action = AuditAction.PERMISSION_GRANT if is_granted else AuditAction.PERMISSION_REVOKE
        if ctx is not None:
            await write_audit(
                self._uow,
                action=action,
                entity_type=ENTITY_USERS,
                entity_id=id,
                new_values={
                    "capability_code": capability_code,
                    "is_granted": is_granted,
                    "granted_by": granted_by,
                },
                ctx=ctx,
            )

        await self._uow.commit()
        return await self.effective_capabilities(id)

    # ------------------------------------------------------------------
    # Capability revoke (delete override)
    # ------------------------------------------------------------------

    async def revoke_capability(
        self,
        id: int,
        capability_code: str,
        *,
        revoked_by: int,
        ctx: AuditContext | None = None,
    ) -> None:
        existing_user = await self._repo.get(id)
        if existing_user is None:
            raise NotFound(f"User {id} not found.")

        if not is_capability(capability_code):
            raise ValidationFailed(
                "Unknown capability code.",
                errors=[
                    {
                        "code": "enum_value_invalid",
                        "field": "capability_code",
                        "message": f"{capability_code!r} is not a valid capability code.",
                    }
                ],
            )

        capability_id = await self._repo.get_capability_id(capability_code)
        if capability_id is None:
            raise ValidationFailed(
                "Unknown capability code.",
                errors=[
                    {
                        "code": "enum_value_invalid",
                        "field": "capability_code",
                        "message": f"{capability_code!r} is not a valid capability code.",
                    }
                ],
            )

        deleted = await self._repo.delete_capability_override(
            user_id=id, capability_id=capability_id
        )
        if not deleted:
            raise NotFound(
                f"Capability override {capability_code!r} not found for user {id}."
            )

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.PERMISSION_REVOKE,
                entity_type=ENTITY_USERS,
                entity_id=id,
                new_values={"capability_code": capability_code, "revoked_by": revoked_by},
                ctx=ctx,
            )

        await self._uow.commit()


__all__ = ["UserService"]
