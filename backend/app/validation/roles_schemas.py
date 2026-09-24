"""Request/response schemas for Roles (Phase 1.5).

Mirrors ``openapi.yaml`` §15.4 exactly.

DB vs OpenAPI note:
* ``version`` is derived from ``updated_at`` (no DB column).
* ``is_active`` is required by the OpenAPI ``Role`` schema, but the
  ``roles`` table has no ``is_active`` column. We return ``True`` for
  every role — this preserves the V1 response contract without adding
  a migration. Custom roles can only be deleted (when unreferenced);
  there is no deactivation endpoint.
* ``capability_codes`` are resolved from ``role_capabilities``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.validation.pagination import MetaEnvelope


class RoleCreateRequest(BaseModel):
    """Body for ``POST /roles``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_ \-]+$")
    description: str | None = Field(default=None, max_length=500)


class RoleUpdateRequest(BaseModel):
    """Body for ``PATCH /roles/{id}``."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=500)


class RoleCapabilitiesRequest(BaseModel):
    """Body for ``PUT /roles/{id}/capabilities``."""

    model_config = ConfigDict(extra="forbid")

    capability_codes: list[str]


class RoleResponse(BaseModel):
    """Response shape for role endpoints (matches ``Role`` in openapi.yaml)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    is_system_role: bool
    is_active: bool
    capability_codes: list[str]
    created_at: datetime
    updated_at: datetime
    version: int


__all__ = [
    "MetaEnvelope",
    "RoleCapabilitiesRequest",
    "RoleCreateRequest",
    "RoleResponse",
    "RoleUpdateRequest",
]
