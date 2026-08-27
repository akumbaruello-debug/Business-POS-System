"""Role-based capability resolution.

Implements §7: effective capabilities = role capabilities union user
overrides. This module answers two questions:

* Given a role_id, what is the *base* capability set?
* Given a role's base set, apply a user's overrides (grants/revoke).

It is intentionally a thin read-layer over ``role_capabilities`` and
``user_capability_overrides``. The ``Owner``-gets-everything rule is
handled here so callers don't need DB round-trips for the most common
case (the migration seeds Owner with every capability, so a simple
SELECT returns the full set — but we still short-circuit for safety).
"""

from __future__ import annotations

from typing import Protocol

from app.authz.caps import CANONICAL_CAPABILITIES, OWNER_CAPABILITIES
from app.logging import get_logger

logger = get_logger(__name__)


class _RoleCapabilitySource(Protocol):
    """Minimal interface for resolving role capabilities."""

    async def role_name(self, role_id: int) -> str | None: ...
    async def role_capabilities(self, role_id: int) -> set[str]: ...
    async def user_overrides(self, user_id: int) -> dict[str, bool]: ...


class RoleResolver:
    """Resolve the effective capability set for a user."""

    def __init__(self, *, source: _RoleCapabilitySource) -> None:
        self._source = source

    async def base_capabilities(self, role_id: int) -> set[str]:
        """Return the base capability set for a role.

        Owner short-circuits to the full catalog (defensive: if a
        future capability is added to the catalog but not seeded into
        ``role_capabilities``, Owner still gets it).
        """
        role_name = await self._source.role_name(role_id)
        if role_name == "Owner":
            return set(OWNER_CAPABILITIES)
        return await self._source.role_capabilities(role_id)

    async def effective_capabilities(
        self,
        *,
        role_id: int,
        user_id: int,
    ) -> set[str]:
        """effective = role_base union user_overrides (grants win; revokes remove).

        Per the canonical formula in Backend-Architecture §7.1:

            effective = (role_base union granted_overrides) \\ revoked_overrides
        """
        base = await self.base_capabilities(role_id)
        overrides = await self._source.user_overrides(user_id)

        result = set(base)
        for cap, granted in overrides.items():
            # A revoke always removes; a grant always adds. If the cap
            # is unknown we still honour the override (it's a runtime
            # decision, but we log a warning).
            if cap not in CANONICAL_CAPABILITIES:
                logger.warning("unknown_capability_in_override", cap=cap)
            if granted:
                result.add(cap)
            else:
                result.discard(cap)
        return result


__all__ = ["RoleResolver", "_RoleCapabilitySource"]
