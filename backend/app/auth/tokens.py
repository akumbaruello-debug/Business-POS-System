"""Opaque token generation + hashing.

Per the OpenAPI ``bearerAuth`` description (line 5871 of openapi.yaml):
   * 256-bit random
   * base64url-encoded
   * stored hashed in the ``sessions`` table
   * client does NOT parse them
   * send ``Authorization: Bearer <token>``
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

#: Number of random bytes; 32 bytes = 256 bits (OWASP 2024 minimum for
#: bearer tokens).
_TOKEN_BYTES = 32


def generate_token() -> str:
    """Generate a 256-bit random token, base64url-encoded.

    Returns a 43-character ASCII string (no padding).
    """
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_opaque(token: str) -> str:
    """Hash an opaque token for storage in ``sessions.access_token_hash`` /
    ``sessions.refresh_token_hash``.

    The migration's CHECK / INDEX requirements just say the hash is a
    VARCHAR(255). We use SHA-256 here (constant-time, fixed length,
    collision-resistant for 256-bit inputs). This is **not** a
    password-hash — there is no salt, because we are not protecting
    against brute-forcing a stored hash; we are providing O(1) lookup
    and constant-time comparison.

    See ``Backend-Architecture-V1.0.md`` §6 Authentication.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Backwards-compat aliases (some callers prefer shorter names)
# ---------------------------------------------------------------------------


def generate_refresh_token() -> str:
    """Alias of :func:`generate_token` — kept for clarity at call sites."""
    return generate_token()


# ---------------------------------------------------------------------------
# TokenPair
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenPair:
    """A (access, refresh) token pair, with the hashes used for storage."""

    access_token: str
    refresh_token: str
    access_token_hash: str
    refresh_token_hash: str

    @classmethod
    def fresh(cls) -> TokenPair:
        access = generate_token()
        refresh = generate_token()
        return cls(
            access_token=access,
            refresh_token=refresh,
            access_token_hash=hash_opaque(access),
            refresh_token_hash=hash_opaque(refresh),
        )


__all__ = [
    "TokenPair",
    "generate_refresh_token",
    "generate_token",
    "hash_opaque",
]
