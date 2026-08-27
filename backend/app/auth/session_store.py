"""Session persistence (sessions table CRUD) and related helpers.

Reference:
  * ``Backend-Architecture-V1.0.md`` §6 Authentication
  * ``Database-Design-V1.0.md`` §2.13.1 ``sessions`` table
  * OpenAPI ``bearerAuth`` description (line 5871)

The session table holds **hashed** access and refresh tokens. We never
store the raw tokens. Lookups by access token hash are O(1) via the
unique index ``ux_sessions_access_token_hash``.

All session-mutating operations run inside a UnitOfWork so they share
the caller's transaction boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.auth.tokens import (
    TokenPair,
    generate_token,
    hash_opaque,
)
from app.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionRecord:
    """Plain-data view of a row in the ``sessions`` table."""

    id: str  # UUID, serialised
    user_id: int
    access_expires_at: datetime
    refresh_expires_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Token helpers (re-exported from app.auth.tokens for convenience)
# ---------------------------------------------------------------------------

hash_token = hash_opaque
new_token = generate_token
# verify_token: re-hash and compare. Since SHA-256 is deterministic
# we just hash again and compare; the DB lookup already does the
# security-sensitive part (constant-time via Postgres).
verify_token = hash_opaque


# ---------------------------------------------------------------------------
# Protocol (for tests / DI)
# ---------------------------------------------------------------------------


class _SessionConnection(Protocol):
    """Anything that can run ``execute``/``first_row`` — i.e. a UnitOfWork."""

    async def first_row(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...
    async def execute(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> Any: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class SessionStore:
    """CRUD for the ``sessions`` table.

    Stateless: each call takes a UoW. Designed so the service layer
    composes ``SessionStore + UnitOfWork`` to control the transaction
    boundary.
    """

    def __init__(self, uow: _SessionConnection) -> None:
        self._uow = uow

    # ---- create ----

    async def create(
        self,
        *,
        user_id: int,
        tokens: TokenPair,
        access_expires_at: datetime,
        refresh_expires_at: datetime,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        """Insert a new session row and return its UUID (as a string)."""
        row = await self._uow.first_row(
            """
            INSERT INTO sessions (
                user_id,
                access_token_hash,
                refresh_token_hash,
                access_expires_at,
                refresh_expires_at,
                ip_address,
                user_agent
            )
            VALUES (:user_id, :access_hash, :refresh_hash,
                    :access_exp, :refresh_exp, :ip, :ua)
            RETURNING id::text
            """,
            {
                "user_id": user_id,
                "access_hash": tokens.access_token_hash,
                "refresh_hash": tokens.refresh_token_hash,
                "access_exp": access_expires_at,
                "refresh_exp": refresh_expires_at,
                "ip": ip_address,
                "ua": user_agent,
            },
        )
        if row is None:
            raise RuntimeError("INSERT ... RETURNING produced no row")
        return str(row["id"])

    # ---- read ----

    async def get_by_access_token(
        self,
        access_token: str,
    ) -> dict[str, Any] | None:
        """Return the session row for a given access token, or None.

        The row includes joined user data so the auth layer doesn't
        need a second round-trip.
        """
        access_hash = hash_opaque(access_token)
        return await self._uow.first_row(
            """
            SELECT
                s.id::text              AS id,
                s.user_id               AS user_id,
                s.access_expires_at     AS access_expires_at,
                s.refresh_expires_at    AS refresh_expires_at,
                s.revoked_at            AS revoked_at,
                s.revoked_reason        AS revoked_reason,
                s.created_at            AS created_at,
                u.username              AS username,
                u.full_name             AS full_name,
                u.email                 AS email,
                u.is_active             AS is_active,
                u.role_id               AS role_id,
                r.name                  AS role_name,
                r.is_system_role        AS role_is_system,
                u.locked_until          AS locked_until,
                u.failed_login_count    AS failed_login_count
            FROM sessions s
            JOIN users u   ON u.id = s.user_id
            JOIN roles r   ON r.id = u.role_id
            WHERE s.access_token_hash = :hash
            """,
            {"hash": access_hash},
        )

    async def get_by_refresh_token(
        self,
        refresh_token: str,
    ) -> dict[str, Any] | None:
        refresh_hash = hash_opaque(refresh_token)
        return await self._uow.first_row(
            """
            SELECT
                s.id::text              AS id,
                s.user_id               AS user_id,
                s.access_expires_at     AS access_expires_at,
                s.refresh_expires_at    AS refresh_expires_at,
                s.revoked_at            AS revoked_at,
                s.revoked_reason        AS revoked_reason
            FROM sessions s
            WHERE s.refresh_token_hash = :hash
            """,
            {"hash": refresh_hash},
        )

    # ---- write ----

    async def touch_last_seen(self, session_id: str) -> None:
        """Bump ``last_seen_at`` (rate-limited effect; not critical)."""
        await self._uow.execute(
            "UPDATE sessions SET last_seen_at = NOW() WHERE id = :id",
            {"id": session_id},
        )

    async def rotate(
        self,
        *,
        session_id: str,
        new_tokens: TokenPair,
        new_access_expires_at: datetime,
        new_refresh_expires_at: datetime,
    ) -> None:
        """Rotate the access+refresh tokens atomically."""
        await self._uow.execute(
            """
            UPDATE sessions
            SET access_token_hash  = :access_hash,
                refresh_token_hash = :refresh_hash,
                access_expires_at  = :access_exp,
                refresh_expires_at = :refresh_exp,
                last_seen_at       = NOW()
            WHERE id = :id
            """,
            {
                "access_hash": new_tokens.access_token_hash,
                "refresh_hash": new_tokens.refresh_token_hash,
                "access_exp": new_access_expires_at,
                "refresh_exp": new_refresh_expires_at,
                "id": session_id,
            },
        )

    async def revoke(
        self,
        *,
        session_id: str,
        reason: str = "logout",
    ) -> None:
        """Revoke a single session. Idempotent."""
        await self._uow.execute(
            """
            UPDATE sessions
            SET revoked_at = COALESCE(revoked_at, NOW()),
                revoked_reason = COALESCE(revoked_reason, :reason)
            WHERE id = :id
            """,
            {"id": session_id, "reason": reason},
        )

    async def revoke_all_for_user(
        self,
        *,
        user_id: int,
        except_session_id: str | None = None,
        reason: str = "logout_all",
    ) -> int:
        """Revoke all non-revoked sessions for ``user_id``.

        If ``except_session_id`` is provided, that session is left
        alone (used so a user can keep their current session while
        revoking all others). Returns the count revoked.
        """
        if except_session_id:
            row = await self._uow.first_row(
                """
                WITH rev AS (
                    UPDATE sessions
                    SET revoked_at     = COALESCE(revoked_at, NOW()),
                        revoked_reason = COALESCE(revoked_reason, :reason)
                    WHERE user_id = :user_id
                      AND revoked_at IS NULL
                      AND id <> :sid
                    RETURNING id
                )
                SELECT COUNT(*)::int AS cnt FROM rev
                """,
                {"user_id": user_id, "reason": reason, "sid": except_session_id},
            )
        else:
            row = await self._uow.first_row(
                """
                WITH rev AS (
                    UPDATE sessions
                    SET revoked_at     = COALESCE(revoked_at, NOW()),
                        revoked_reason = COALESCE(revoked_reason, :reason)
                    WHERE user_id = :user_id
                      AND revoked_at IS NULL
                    RETURNING id
                )
                SELECT COUNT(*)::int AS cnt FROM rev
                """,
                {"user_id": user_id, "reason": reason},
            )
        return int(row["cnt"]) if row else 0

    async def purge_expired(self) -> int:
        """Delete sessions past their access expiry. Operational job."""
        row = await self._uow.first_row(
            """
            WITH del AS (
                DELETE FROM sessions
                WHERE access_expires_at < NOW()
                RETURNING id
            )
            SELECT COUNT(*)::int AS cnt FROM del
            """,
        )
        return int(row["cnt"]) if row else 0


__all__ = [
    "SessionRecord",
    "SessionStore",
    "TokenPair",
    "hash_token",
    "new_token",
    "verify_token",
]
