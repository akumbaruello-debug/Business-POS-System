"""AuthService — composes the auth primitives into login/refresh/logout/me.

The service layer is the **only** place that:
* composes ``PasswordAuthenticator`` + ``SessionStore``,
* knows about the Owner / Staff default capability sets,
* returns the ``LoginResponse`` / ``SessionUser`` shapes.

This keeps business code away from transaction plumbing and makes the
flows testable against an in-memory fake data source.

**Transaction model:** Every public method accepts an optional
``uow`` parameter. When provided (the API layer's preference), the
method uses it and **does not** commit — the caller (route handler)
owns the commit. When omitted, the method creates and commits its own
UoW (convenience / script path).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.auth.password import PasswordAuthenticator
from app.auth.principal import Principal
from app.auth.session_store import SessionStore
from app.auth.tokens import TokenPair
from app.authz.resolver import resolve_principal_for_session
from app.config import get_settings
from app.core.clock import get_clock
from app.db import UnitOfWork
from app.errors import (
    SessionExpired,
    SessionRevoked,
    Unauthenticated,
)
from app.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class LoginResult:
    """Successful login payload (matches OpenAPI ``LoginResponse``)."""

    __slots__ = (
        "access_token",
        "expires_in",
        "principal",
        "refresh_expires_in",
        "refresh_token",
    )

    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_in: int,
        refresh_expires_in: int,
        principal: Principal,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_in = expires_in
        self.refresh_expires_in = refresh_expires_in
        self.principal = principal

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_in": self.expires_in,
            "refresh_expires_in": self.refresh_expires_in,
            "user": self.principal.to_session_user(),
        }


# ---------------------------------------------------------------------------
# AuthService
# ---------------------------------------------------------------------------


class AuthService:
    """Compose login / refresh / logout / me flows."""

    def __init__(
        self,
        *,
        authenticator: PasswordAuthenticator | None = None,
    ) -> None:
        self._settings = get_settings()
        self._authenticator = authenticator or PasswordAuthenticator()

    # ---- helpers ----

    def _access_exp(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self._settings.access_token_ttl_seconds)

    def _refresh_exp(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self._settings.refresh_token_ttl_seconds)

    # ---- fetch user for authenticator ----

    async def fetch_user(
        self, username: str, *, uow: UnitOfWork | None = None
    ) -> dict[str, Any] | None:
        sql = """
            SELECT
                u.id           AS user_id,
                u.username     AS username,
                u.full_name    AS full_name,
                u.email        AS email,
                u.is_active    AS is_active,
                u.role_id      AS role_id,
                u.password_hash AS password_hash,
                u.failed_login_count AS failed_login_count,
                u.locked_until AS locked_until,
                u.last_login_at AS last_login_at
            FROM users u
            WHERE u.username = :username
        """
        if uow is None:
            async with UnitOfWork() as owned:
                row = await owned.first_row(sql, {"username": username})
                await owned.commit()
                return row
        assert uow is not None  # narrowed: not-None after early return
        return await uow.first_row(sql, {"username": username})

    async def record_login_success(
        self,
        user_id: int,
        *,
        at: datetime,
        uow: UnitOfWork | None = None,
    ) -> None:
        sql = """
            UPDATE users
            SET failed_login_count = 0,
                locked_until       = NULL,
                last_login_at      = :at
            WHERE id = :id
        """
        if uow is None:
            async with UnitOfWork() as owned:
                await owned.execute(sql, {"id": user_id, "at": at})
                await owned.commit()
                return
        assert uow is not None  # narrowed: not-None after early return
        await uow.execute(sql, {"id": user_id, "at": at})

    async def record_login_failure(
        self,
        user_id: int,
        *,
        at: datetime,
        lockout_until: datetime | None,
        uow: UnitOfWork | None = None,
    ) -> int:
        sql = """
            UPDATE users
            SET failed_login_count = failed_login_count + 1,
                locked_until       = COALESCE(:lockout_until, locked_until)
            WHERE id = :id
            RETURNING failed_login_count
        """
        if uow is None:
            async with UnitOfWork() as owned:
                row = await owned.first_row(sql, {"id": user_id, "lockout_until": lockout_until})
                await owned.commit()
                return int(row["failed_login_count"]) if row else 0
        assert uow is not None
        row = await uow.first_row(sql, {"id": user_id, "lockout_until": lockout_until})
        return int(row["failed_login_count"]) if row else 0

    # ---- public flows ----

    async def login(
        self,
        *,
        username: str,
        password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        uow: UnitOfWork | None = None,
    ) -> LoginResult:
        """Authenticate and create a new session.

        When ``uow`` is provided (API layer), the caller owns the
        transaction boundary and commits (or rolls back).
        """
        now = get_clock().now()
        owned = uow is None
        if owned:
            uow = UnitOfWork()
        assert uow is not None  # for type-checkers

        try:
            user = await self._authenticator.authenticate(
                username=username,
                password=password,
                source=self,
            )
        except Exception:
            if owned:
                await uow.rollback()
            raise

        user_id = int(user["user_id"])
        access_exp = self._access_exp(now)
        refresh_exp = self._refresh_exp(now)
        tokens = TokenPair.fresh()

        store = SessionStore(uow)
        session_id = await store.create(
            user_id=user_id,
            tokens=tokens,
            access_expires_at=access_exp,
            refresh_expires_at=refresh_exp,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self.record_login_success(user_id, at=now, uow=uow)

        principal = await resolve_principal_for_session(
            uow,
            user_id=user_id,
            session_id=session_id,
            access_exp=access_exp,
            refresh_exp=refresh_exp,
        )

        result = LoginResult(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_in=self._settings.access_token_ttl_seconds,
            refresh_expires_in=self._settings.refresh_token_ttl_seconds,
            principal=principal,
        )
        if owned:
            await uow.commit()
        return result

    async def refresh(
        self,
        *,
        refresh_token: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        uow: UnitOfWork | None = None,
    ) -> LoginResult:
        """Rotate the (access, refresh) tokens atomically.

        The old refresh token is invalidated.
        """
        now = get_clock().now()
        owned = uow is None
        if owned:
            uow = UnitOfWork()
        assert uow is not None

        store = SessionStore(uow)
        row = await store.get_by_refresh_token(refresh_token)
        if row is None:
            raise Unauthenticated()
        if row.get("revoked_at") is not None:
            raise SessionRevoked()
        refresh_exp_old = row.get("refresh_expires_at")
        if refresh_exp_old is not None and refresh_exp_old < now:
            raise SessionExpired()

        user_id = int(row["user_id"])
        access_exp = self._access_exp(now)
        refresh_exp = self._refresh_exp(now)
        new_tokens = TokenPair.fresh()
        await store.rotate(
            session_id=str(row["id"]),
            new_tokens=new_tokens,
            new_access_expires_at=access_exp,
            new_refresh_expires_at=refresh_exp,
        )

        principal = await resolve_principal_for_session(
            uow,
            user_id=user_id,
            session_id=str(row["id"]),
            access_exp=access_exp,
            refresh_exp=refresh_exp,
        )
        if owned:
            await uow.commit()
        return LoginResult(
            access_token=new_tokens.access_token,
            refresh_token=new_tokens.refresh_token,
            expires_in=self._settings.access_token_ttl_seconds,
            refresh_expires_in=self._settings.refresh_token_ttl_seconds,
            principal=principal,
        )

    async def logout_current(self, *, session_id: str, uow: UnitOfWork | None = None) -> None:
        if uow is None:
            async with UnitOfWork() as owned:
                store = SessionStore(owned)
                await store.revoke(session_id=session_id, reason="logout")
                await owned.commit()
            return
        store = SessionStore(uow)
        await store.revoke(session_id=session_id, reason="logout")

    async def logout_all(
        self,
        *,
        user_id: int,
        except_session_id: str | None = None,
        uow: UnitOfWork | None = None,
    ) -> int:
        if uow is None:
            async with UnitOfWork() as owned:
                store = SessionStore(owned)
                count = await store.revoke_all_for_user(
                    user_id=user_id,
                    except_session_id=except_session_id,
                    reason="logout_all",
                )
                await owned.commit()
            return count
        store = SessionStore(uow)
        count = await store.revoke_all_for_user(
            user_id=user_id,
            except_session_id=except_session_id,
            reason="logout_all",
        )
        return count


__all__ = ["AuthService", "LoginResult"]
