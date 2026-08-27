"""FastAPI dependencies for authentication and authorization.

Provides the M1 authn/authz entry points used by the API layer:

* :func:`get_uow` — yields a UnitOfWork for the request's transaction.
* :func:`current_principal` — extract + validate the Bearer token,
  resolve the session + effective capabilities, and return a
  ``Principal``. Raises ``Unauthenticated`` on any session failure.
* :func:`require_capability` — a dependency factory that requires a
  specific capability (or set). Raises ``PermissionDenied`` otherwise.

Reference: Backend-Architecture-V1.0.md §5 (API layer), §6 (auth),
§7 (authz).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request

from app.auth.principal import Principal
from app.auth.session_store import SessionStore
from app.authz.resolver import resolve_principal
from app.core.clock import get_clock
from app.db import UnitOfWork, get_uow
from app.errors import (
    PermissionDenied,
    SessionExpired,
    SessionRevoked,
    Unauthenticated,
)
from app.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Bearer-token extraction
# ---------------------------------------------------------------------------


def extract_bearer_token(auth_header: str | None) -> str | None:
    """Return the token from ``Authorization: Bearer <token>`` or None."""
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


# ---------------------------------------------------------------------------
# Principal resolution
# ---------------------------------------------------------------------------


async def _resolve_session_row(token: str, uow: UnitOfWork) -> dict[str, Any] | None:
    store = SessionStore(uow)
    return await store.get_by_access_token(token)


async def current_principal(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> Principal:
    """Resolve the Bearer token to a ``Principal``.

    Reads the `Authorization: Bearer <token>` header, validates the
    session, resolves the effective capabilities (role union user
    overrides), and returns a populated ``Principal``.

    Raises:
        Unauthenticated (401): missing token, no matching session,
            inactive user.
        SessionRevoked (401): session explicitly revoked.
        SessionExpired (401): access token past its expiry.
    """
    auth_header = request.headers.get("authorization")
    token = extract_bearer_token(auth_header)
    if not token:
        raise Unauthenticated()

    row = await _resolve_session_row(token, uow)
    if row is None:
        # Don't leak whether the token was valid; just say unauthenticated.
        logger.debug("auth_session_not_found")
        raise Unauthenticated()

    now = get_clock().now()

    if row.get("revoked_at") is not None:
        logger.info("auth_session_revoked", session_id=str(row["id"]))
        raise SessionRevoked()

    access_expires = row.get("access_expires_at")
    if access_expires is not None and access_expires < now:
        raise SessionExpired()

    if not row.get("is_active"):
        raise Unauthenticated()

    # Build the Principal (resolves capabilities inside this UoW).
    principal = await resolve_principal(row, uow=uow)

    # Touch last_seen_at (last thing before commit).
    store = SessionStore(uow)
    await store.touch_last_seen(str(row["id"]))

    # Commit so the last_seen_at write persists.
    await uow.commit()
    return principal


# ---------------------------------------------------------------------------
# Capability gate
# ---------------------------------------------------------------------------


def require_capability(*capabilities: str) -> Any:
    """Return a FastAPI dependency that requires one or more capabilities.

    Usage::

        @router.get(
            "/admin",
            dependencies=[Depends(require_capability("user.manage"))],
        )

    The dependency is applied *after* ``current_principal`` (which raises
    401). If the principal lacks the capability, raises
    ``PermissionDenied`` (403).

    Semantics:
      * No arguments → always passes (i.e. authenticated-only).
      * One or more → the principal must hold **all** of them.

    Each call generates a fresh inner function so FastAPI's per-route
    dependency cache keys it correctly.
    """
    caps = frozenset(capabilities)

    async def _checker(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Principal:
        if caps and not principal.has_all(*caps):
            missing = caps - set(principal.capabilities)
            logger.warning(
                "authorization_denied",
                user_id=principal.user_id,
                required=sorted(caps),
                missing=sorted(missing),
            )
            raise PermissionDenied(
                "User lacks required capability: " + ", ".join(sorted(missing)),
                details={"required": sorted(caps), "missing": sorted(missing)},
            )
        return principal

    _checker.__name__ = "RequireCapability_" + "_".join(sorted(caps)) if caps else "RequireAuthOnly"
    return _checker


# Convenience aliases.
require_authenticated = require_capability()

__all__ = [
    "current_principal",
    "extract_bearer_token",
    "require_authenticated",
    "require_capability",
]
