"""Request/response schemas for Users (Phase 1.4).

Mirrors ``openapi.yaml`` §Users exactly.

DB vs OpenAPI note:
* ``version`` is derived from ``updated_at`` (no DB column).
* ``role_name`` is derived by joining the ``roles`` table.
"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.validation.pagination import MetaEnvelope

_USERNAME_RE = re.compile(r"^[a-z0-9._-]+$")


class UserCreateRequest(BaseModel):
    """Body for ``POST /users``."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9._-]+$")
    full_name: str = Field(..., min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=150)
    password: str = Field(..., min_length=8, max_length=256)
    role_id: int = Field(..., ge=1)


class UserUpdateRequest(BaseModel):
    """Body for ``PATCH /users/{id}``. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=150)
    email: str | None = Field(default=None, max_length=150)
    is_active: bool | None = None
    role_id: int | None = Field(default=None, ge=1)


class UserResponse(BaseModel):
    """Response shape for user endpoints (matches ``User`` in openapi.yaml)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    email: str | None = None
    is_active: bool
    role_id: int
    role_name: str
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    version: int


class PasswordResetRequest(BaseModel):
    """Body for ``POST /users/{id}/reset-password``."""

    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(..., min_length=8, max_length=256)


class UserCapabilityOverrideRequest(BaseModel):
    """Body for ``POST /users/{id}/capabilities``."""

    model_config = ConfigDict(extra="forbid")

    capability_code: str
    is_granted: bool


class CapabilityOverrideEntry(BaseModel):
    """Single override entry in the effective-capabilities response."""

    model_config = ConfigDict(from_attributes=True)

    capability_code: str
    is_granted: bool
    granted_at: datetime
    granted_by: int


class UserEffectiveCapabilities(BaseModel):
    """Response for ``GET /users/{id}/capabilities``."""

    model_config = ConfigDict(from_attributes=True)

    role: str
    role_capabilities: list[str]
    overrides: list[CapabilityOverrideEntry]
    effective: list[str]


__all__ = [
    "CapabilityOverrideEntry",
    "MetaEnvelope",
    "PasswordResetRequest",
    "UserCapabilityOverrideRequest",
    "UserCreateRequest",
    "UserEffectiveCapabilities",
    "UserResponse",
    "UserUpdateRequest",
]
