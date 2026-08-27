"""Password authentication: lookup + verify + lockout state machine.

Implements BR-AUTH-001 (rate-limited / lockout) and BR-AUTH-002
(password hashing). The actual login flow composition lives in
:mod:`app.auth.service`; this module is the primitive.

The account-locking logic writes back to ``users.failed_login_count``
and ``users.locked_until`` and is a single ``UPDATE`` so the state
machine is atomic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.core.clock import get_clock
from app.core.security import UNSET_PASSWORD_HASH, PasswordHasher
from app.errors import AccountLocked, InvalidCredentials, PasswordUnset
from app.logging import get_logger

logger = get_logger(__name__)


#: Default rate-limit policy. Mirrors ``OpenAPI`` x-rate-limit
#: ``5 per 15 min per IP; 10 per hour per user``. We use a simpler
#: account-level model (the per-IP throttling lives in the
#: slowapi middleware — see :mod:`app.middleware.rate_limit`).
DEFAULT_MAX_FAILED = 5
DEFAULT_WINDOW = timedelta(minutes=15)
DEFAULT_LOCKOUT = timedelta(minutes=15)


@dataclass(frozen=True)
class PasswordPolicy:
    """Account lockout policy."""

    max_failed: int = DEFAULT_MAX_FAILED
    window: timedelta = DEFAULT_WINDOW
    lockout_duration: timedelta = DEFAULT_LOCKOUT


class RecordLockedError(Exception):
    """Internal — raised when a record is locked. Translated to AccountLocked."""


class PasswordUnsetError(Exception):
    """Internal — raised when the record has no password set yet."""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class _UserRowSource(Protocol):
    """Anything that can fetch a user row + update lock state.

    In production this is ``app.auth.service.AuthService`` (which has a
    UoW); in tests we inject a small in-memory implementation.
    """

    async def fetch_user(self, username: str) -> dict[str, Any] | None: ...
    async def record_login_success(self, user_id: int, *, at: datetime) -> None: ...
    async def record_login_failure(
        self,
        user_id: int,
        *,
        at: datetime,
        lockout_until: datetime | None,
    ) -> int: ...


# ---------------------------------------------------------------------------
# Authenticator
# ---------------------------------------------------------------------------


class PasswordAuthenticator:
    """Verifies a (username, password) pair and enforces lockout.

    Stateless: each call to :meth:`authenticate` consults the data
    source. The :class:`PasswordAuthenticator` itself owns the hashing
    policy; the data source owns the persistence.
    """

    def __init__(
        self,
        *,
        hasher: PasswordHasher | None = None,
        policy: PasswordPolicy | None = None,
    ) -> None:
        self._hasher = hasher or PasswordHasher()
        self._policy = policy or PasswordPolicy()

    @property
    def hasher(self) -> PasswordHasher:
        return self._hasher

    @property
    def policy(self) -> PasswordPolicy:
        return self._policy

    # ---- single-credential authentication ----

    async def authenticate(
        self,
        *,
        username: str,
        password: str,
        source: _UserRowSource,
    ) -> dict[str, Any]:
        """Verify (username, password) against ``source``.

        Returns the user row on success. Raises:

        * :class:`PasswordUnset` (→ 401 password_unset) if the stored
          hash is the ``!UNSET`` sentinel.
        * :class:`AccountLocked` (→ 423 account_locked) if the user
          is currently locked out.
        * :class:`InvalidCredentials` (→ 401 invalid_credentials) on
          any other failure (unknown user, wrong password, inactive).

        The lockout state machine is updated **inside** this call —
        a successful authentication resets ``failed_login_count``;
        a failed attempt increments it and, if it crosses the
        threshold, sets ``locked_until``.
        """
        if not username or not password:
            raise InvalidCredentials()

        user = await source.fetch_user(username)
        if user is None:
            # Constant-time-ish: we still run argon2 over a dummy hash
            # so a timing side-channel can't tell whether the user
            # exists. This is a defence-in-depth measure.
            self._hasher.verify(password, UNSET_PASSWORD_HASH)
            raise InvalidCredentials()

        now = get_clock().now()

        # 1. Already locked?
        if user.get("locked_until") is not None and user["locked_until"] > now:
            raise AccountLocked(
                f"Account is locked until {user['locked_until'].isoformat()}.",
                details={
                    "locked_until": user["locked_until"].isoformat(),
                },
            )

        # 2. Inactive?
        if not user.get("is_active", True):
            raise InvalidCredentials()

        stored_hash = user.get("password_hash") or ""
        if self._hasher.is_unset(stored_hash):
            # Special error so the API can return `password_unset` (a
            # distinct code from generic invalid_credentials).
            raise PasswordUnset(
                "Owner password has not been set.",
            )

        # 3. Verify.
        ok = self._hasher.verify(password, stored_hash)
        if not ok:
            # Increment failure counter, possibly set lock.
            next_count = await source.record_login_failure(
                user_id=int(user["user_id"]),
                at=now,
                lockout_until=(
                    now + self._policy.lockout_duration
                    if (int(user.get("failed_login_count") or 0) + 1) >= self._policy.max_failed
                    else None
                ),
            )
            # If we just hit the threshold and were NOT already locked
            # by a prior check, raise AccountLocked so the response is
            # 423 rather than 401.
            if next_count >= self._policy.max_failed:
                raise AccountLocked(
                    f"Account locked after {self._policy.max_failed} consecutive failed logins.",
                )
            raise InvalidCredentials()

        # 4. Success.
        await source.record_login_success(int(user["user_id"]), at=now)
        return user


__all__ = [
    "DEFAULT_LOCKOUT",
    "DEFAULT_MAX_FAILED",
    "DEFAULT_WINDOW",
    "PasswordAuthenticator",
    "PasswordPolicy",
    "PasswordUnsetError",
    "RecordLockedError",
]
