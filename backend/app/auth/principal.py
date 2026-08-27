"""The Principal (actor) model.

A ``Principal`` represents the authenticated caller for the duration of
a request. It carries the user's identity, the active session, and
the effective capabilities (§7 Authorization).

The Principal is exposed to route handlers via the ``current_principal``
FastAPI dependency (see :mod:`app.api.deps`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SessionContext:
    """Snapshot of the active session."""

    id: str  # UUID
    user_id: int
    access_expires_at: datetime
    refresh_expires_at: datetime
    revoked: bool
    revoked_reason: str | None = None


@dataclass(frozen=True)
class Principal:
    """The authenticated caller for the current request."""

    user_id: int
    username: str
    full_name: str
    email: str | None
    is_active: bool
    role_id: int
    role_name: str
    role_is_system: bool
    # Effective capability codes: role union user overrides
    capabilities: frozenset[str] = field(default_factory=frozenset)
    # The session backing this principal (None for system actor).
    session: SessionContext | None = None

    def has(self, capability: str) -> bool:
        """True if this principal holds the given capability."""
        return capability in self.capabilities

    def has_any(self, *capabilities: str) -> bool:
        if not capabilities:
            return True
        return any(cap in self.capabilities for cap in capabilities)

    def has_all(self, *capabilities: str) -> bool:
        if not capabilities:
            return True
        return all(cap in self.capabilities for cap in capabilities)

    def to_session_user(self) -> dict[str, Any]:
        """Render as the OpenAPI ``SessionUser`` shape."""
        return {
            "id": self.user_id,
            "username": self.username,
            "full_name": self.full_name,
            "email": self.email,
            "is_active": self.is_active,
            "role_id": self.role_id,
            "role_name": self.role_name,
            "capabilities": sorted(self.capabilities),
        }


__all__ = ["Principal", "SessionContext"]
