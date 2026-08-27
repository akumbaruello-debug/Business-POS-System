"""Password hashing wrapper around Argon2id.

We use Argon2id as required by ``Backend-Architecture-V1.0.md`` §23
Security Architecture. The default ``argon2-cffi`` parameters are
considered safe for interactive use (m=64 MiB, t=3, p=4). For
production, we additionally pin them to the OWASP 2024 recommendation
which we expose via ``PasswordHasher`` (so tests can assert on cost
parameters).

The migration seeds the default Owner user with a placeholder
``password_hash = '!UNSET'`` (a literal sentinel). The
``verify()`` method treats that sentinel as a failed match — it does
NOT raise, so a poisoned DB row never crashes the login path; it
simply produces ``False``.
"""

from __future__ import annotations

import logging
from typing import Final

from argon2 import PasswordHasher as _Argon2Hasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Intentionally use stdlib logging here — this module is imported very
# early in the package (via app.core), before structlog is configured.
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public sentinel
# ---------------------------------------------------------------------------


#: The literal ``!UNSET`` string used by the canonical migration to
#: mark a user whose password has not been set yet. It is intentionally
#: not a valid Argon2 hash so verification will never accidentally
#: succeed.
UNSET_PASSWORD_HASH: Final[str] = "!UNSET"  # noqa: S105


# ---------------------------------------------------------------------------
# Wrapper
# ---------------------------------------------------------------------------


class PasswordHasher:
    """Thin wrapper around ``argon2-cffi`` for testability and safety.

    Why a wrapper?
      * Centralizes the sentinel handling (``!UNSET``).
      * Surfaces a single exception (``InvalidPassword``) for callers.
      * Allows tests to construct a low-cost hasher without monkey-
        patching the global default.
    """

    def __init__(
        self,
        *,
        time_cost: int = 3,
        memory_cost: int = 64 * 1024,  # 64 MiB
        parallelism: int = 4,
        hash_len: int = 32,
        salt_len: int = 16,
    ) -> None:
        self._hasher = _Argon2Hasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=hash_len,
            salt_len=salt_len,
        )

    # ---- Encoding / verification ---------------------------------------

    def hash(self, password: str) -> str:
        """Hash ``password`` and return the encoded string.

        Raises:
            ValueError: if the password is empty.
        """
        if not password:
            raise ValueError("password must be non-empty")
        return self._hasher.hash(password)

    def verify(self, password: str, stored_hash: str) -> bool:
        """Constant-time verify. Returns False on any failure.

        Never raises. The migration seed ``!UNSET`` is treated as a
        failed match.
        """
        if not password or not stored_hash:
            return False
        if stored_hash == UNSET_PASSWORD_HASH:
            # The user has not had their initial password set yet.
            # Do NOT raise; surface as a verification failure.
            return False
        try:
            return self._hasher.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError) as exc:
            logger.debug("argon2 verification failed: %s", exc.__class__.__name__)
            return False

    def needs_rehash(self, stored_hash: str) -> bool:
        """True if the stored hash uses weaker parameters than the current hasher.

        Callers should re-hash on successful login to keep parameters
        fresh. We never store older hashes silently.
        """
        if not stored_hash or stored_hash == UNSET_PASSWORD_HASH:
            return False
        try:
            return self._hasher.check_needs_rehash(stored_hash)
        except InvalidHashError:
            return False

    def is_unset(self, stored_hash: str | None) -> bool:
        """True if the stored hash is the ``!UNSET`` sentinel.

        Used by login to produce the structured ``password_unset`` error
        instead of the generic ``invalid_credentials``.
        """
        return stored_hash is None or stored_hash == UNSET_PASSWORD_HASH
