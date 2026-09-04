"""Request/response schemas for Units (M2).

Mirrors ``openapi.yaml`` §15.6.

Known schema/DB contradiction (documented, NOT silently resolved):

* The OpenAPI ``Unit`` schema includes a required ``version`` field.
  The locked ``units`` DB table has NO ``version`` column. We return
  ``version=1`` as a stable placeholder so the contract is honored
  until the schema is amended in a future migration.
* The OpenAPI ``Unit`` schema also has ``created_at``/``updated_at``
  as optional. The DB has no such columns, so they are omitted from
  responses (not required by OpenAPI).
* The OpenAPI ``UnitPatch`` does NOT include ``code`` (despite the
  prose in API-Architecture §15.6 saying it does). The locked schema
  enforces ``code`` is mutable in SQL but the public API contract
  pins code as immutable. Honored: PATCH does not accept ``code``.
* The unique index on ``code`` is full (not partial) per the locked
  baseline — ``ux_units_code UNIQUE (code)`` (not active-only). So
  ``code`` is unique across ALL rows, active or deactivated. The
  M2 plan originally assumed partial-active; corrected to match the
  locked schema.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.validation.pagination import MetaEnvelope


class UnitBase(BaseModel):
    """Common fields for unit create/patch."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        ...,
        min_length=1,
        max_length=20,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Unit code (1-20 chars, alphanumeric + underscore + hyphen).",
    )
    name: str = Field(..., min_length=1, max_length=50)
    is_active: bool = True


class UnitRequest(UnitBase):
    """Body for ``POST /units``."""


class UnitPatch(BaseModel):
    """Body for ``PATCH /units/{id}``.

    Per OpenAPI §15.6 schema ``UnitPatch``: ``code`` is NOT exposed
    (code is immutable through the public API, even though the DB
    column itself is mutable).
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=50)
    is_active: bool | None = None


class UnitResponse(BaseModel):
    """Response for unit endpoints (matches ``Unit`` in openapi.yaml).

    The OpenAPI-required ``version`` is always ``1`` (the DB has no
    version column; the field is a placeholder for a future migration).
    ``created_at``/``updated_at`` are omitted because the DB does not
    store them and OpenAPI does not require them for ``Unit``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    is_active: bool
    version: int = 1


class DeactivateRequest(BaseModel):
    """Body for ``POST /units/{id}/deactivate``."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


__all__ = [
    "DeactivateRequest",
    "MetaEnvelope",
    "UnitBase",
    "UnitPatch",
    "UnitRequest",
    "UnitResponse",
]
