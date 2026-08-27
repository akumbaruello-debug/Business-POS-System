"""Authorization package — capability catalog + resolver + deps."""

from __future__ import annotations

from app.authz.caps import CANONICAL_CAPABILITIES, CapabilityCategory
from app.authz.deps import current_principal, require_capability
from app.authz.resolver import resolve_capabilities, resolve_principal
from app.authz.roles import RoleResolver

__all__ = [
    "CANONICAL_CAPABILITIES",
    "CapabilityCategory",
    "RoleResolver",
    "current_principal",
    "require_capability",
    "resolve_capabilities",
    "resolve_principal",
]
