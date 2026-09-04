"""Request/response schemas for Cost Types (M2).

Mirrors ``openapi.yaml`` §15.6.

Known schema/DB contradictions (documented, NOT silently resolved):

* The OpenAPI ``CostType`` schema includes a required ``version`` field.
  The locked ``cost_types`` DB table has NO ``version`` column (nor an
  ``updated_at`` column or a touch trigger — only a ``created_at``).
  We return ``version=1`` as a stable placeholder so the contract is
  honored until a future migration adds a real version/ETag column.
  This mirrors the documented treatment of ``units``.
* The OpenAPI ``CostTypePatch`` does NOT expose ``code`` (immutable once
  set, per §15.6 prose). The DB column is mutable in SQL but the public
  API contract pins code as immutable.
* ``code`` pattern is restricted to ``^[a-z0-9_]+$`` per OpenAPI (lower
  case + digits + underscore only), unlike ``units`` which permits
  ``A-Z`` and ``-``.
* ``created_at`` / ``updated_at`` are NOT present on ``cost_types`` in
  the locked schema (only ``created_at`` exists). The OpenAPI
  ``CostType`` response does not require them, so they are omitted from
  responses.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.validation.pagination import MetaEnvelope


class CostTypeBase(BaseModel):
    """Common fields for cost-type create/patch."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9_]+$",
        description="Cost-type code (lowercase alphanumeric + underscore).",
    )
    name: str = Field(..., min_length=1, max_length=100)
    is_active: bool = True


class CostTypeRequest(CostTypeBase):
    """Body for ``POST /cost-types``."""


class CostTypePatch(BaseModel):
    """Body for ``PATCH /cost-types/{id}``.

    Per OpenAPI §15.6 ``CostTypePatch``: ``code`` is NOT exposed (immutable
    through the public API, even though the DB column itself is mutable).
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


class CostTypeResponse(BaseModel):
    """Response for cost-type endpoints (matches ``CostType`` in openapi.yaml).

    The OpenAPI-required ``version`` is always ``1`` (the DB has no version
    column; placeholder for a future migration). ``created_at`` is omitted
    because OpenAPI does not require it for ``CostType``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    is_active: bool
    version: int = 1


# Re-export the envelope used by list endpoints.
__all__ = [
    "CostTypeBase",
    "CostTypePatch",
    "CostTypeRequest",
    "CostTypeResponse",
    "MetaEnvelope",
]
