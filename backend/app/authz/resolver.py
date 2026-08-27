"""DB-backed capability resolution.

Implements the ``_RoleCapabilitySource`` protocol against the real
``roles``, ``role_capabilities``, ``capabilities``, ``users``, and
``user_capability_overrides`` tables using SQLAlchemy Core over a
UnitOfWork.

Exposed functions:
  * :func:`resolve_capabilities` — given (role_id, user_id), return the
    effective capability set.
  * :func:`resolve_principal` — given a session row dict (from
    SessionStore), build a fully-resolved ``Principal``.

Both are used by :mod:`app.auth.deps` (the Bearer auth dependency).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from app.auth.principal import Principal, SessionContext
from app.authz.roles import RoleResolver
from app.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Source implementation
# ---------------------------------------------------------------------------


class CapabilitySource:
    """DB-backed ``_RoleCapabilitySource``.

    Each method takes a UoW (which exposes ``first_row`` / ``execute``).
    """

    def __init__(self, uow: Any) -> None:
        self._uow = uow

    async def role_name(self, role_id: int) -> str | None:
        row = await self._uow.first_row(
            "SELECT name FROM roles WHERE id = :rid",
            {"rid": role_id},
        )
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
        rows = await self._uow.fetch_all(
            """
            SELECT c.code AS code, uco.is_granted AS granted
            FROM user_capability_overrides uco
            JOIN capabilities c ON c.id = uco.capability_id
            WHERE uco.user_id = :uid
            """,
            {"uid": user_id},
        )
        return {r["code"]: bool(r["granted"]) for r in rows}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve_capabilities(
    uow: Any,
    *,
    role_id: int,
    user_id: int,
) -> set[str]:
    """Resolve the effective capability set for (role_id, user_id).

    Uses RoleResolver with a DB-backed source.
    """
    source = CapabilitySource(uow)
    resolver = RoleResolver(source=source)
    return await resolver.effective_capabilities(role_id=role_id, user_id=user_id)


async def resolve_principal(
    session_row: dict[str, Any],
    *,
    uow: Any,
) -> Principal:
    """Build a fully-resolved ``Principal`` from a session row.

    The session row must include the joined ``users`` + ``roles``
    columns (as produced by ``SessionStore.get_by_access_token``).
    """
    user_id = int(session_row["user_id"])
    role_id = int(session_row["role_id"])

    # Resolve effective capabilities (role union overrides) inside UoW.
    caps = await resolve_capabilities(uow, role_id=role_id, user_id=user_id)

    session = SessionContext(
        id=str(session_row["id"]),
        user_id=user_id,
        access_expires_at=_to_dt(session_row["access_expires_at"]),
        refresh_expires_at=_to_dt(session_row["refresh_expires_at"]),
        revoked=session_row["revoked_at"] is not None,
        revoked_reason=session_row.get("revoked_reason"),
    )

    return Principal(
        user_id=user_id,
        username=str(session_row["username"]),
        full_name=str(session_row["full_name"]),
        email=session_row.get("email"),
        is_active=bool(session_row["is_active"]),
        role_id=role_id,
        role_name=str(session_row["role_name"]),
        role_is_system=bool(session_row.get("role_is_system", False)),
        capabilities=frozenset(caps),
        session=session,
    )


# ---------------------------------------------------------------------------


def _to_dt(value: Any) -> datetime:
    """Coerce a DBAPI datetime to a Python datetime (naive handling)."""
    if value is None:
        # Should not happen for expires, but guard anyway.
        from app.core.clock import get_clock

        return get_clock().now()
    if isinstance(value, datetime):
        return value
    # SQLAlchemy / asyncpg usually return datetime already; this is
    # the only path that could return something other than datetime.
    # Cast through datetime since DB columns are typed as TIMESTAMP.
    return cast("datetime", value)


__all__ = [
    "CapabilitySource",
    "resolve_capabilities",
    "resolve_principal",
    "resolve_principal_for_session",
]


async def resolve_principal_for_session(
    uow: Any,
    *,
    user_id: int,
    session_id: str,
    access_exp: datetime,
    refresh_exp: datetime,
) -> Principal:
    """Build a ``Principal`` from (user_id, session_id) without a session row.

    Used by the auth flows (login, refresh) where we create the session
    and know the expiry times but want to avoid a second read.
    """
    user_row = await uow.first_row(
        """
        SELECT
            u.id            AS user_id,
            u.username      AS username,
            u.full_name     AS full_name,
            u.email         AS email,
            u.is_active     AS is_active,
            u.role_id       AS role_id,
            r.name          AS role_name,
            r.is_system_role AS role_is_system
        FROM users u JOIN roles r ON r.id = u.role_id
        WHERE u.id = :uid
        """,
        {"uid": user_id},
    )
    if user_row is None:
        from app.errors import Unauthenticated

        raise Unauthenticated()
    role_id = int(user_row["role_id"])
    caps = await resolve_capabilities(uow, role_id=role_id, user_id=user_id)
    session = SessionContext(
        id=session_id,
        user_id=user_id,
        access_expires_at=access_exp,
        refresh_expires_at=refresh_exp,
        revoked=False,
        revoked_reason=None,
    )
    return Principal(
        user_id=user_id,
        username=str(user_row["username"]),
        full_name=str(user_row["full_name"]),
        email=user_row.get("email"),
        is_active=bool(user_row["is_active"]),
        role_id=role_id,
        role_name=str(user_row["role_name"]),
        role_is_system=bool(user_row.get("role_is_system", False)),
        capabilities=frozenset(caps),
        session=session,
    )
