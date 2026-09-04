"""Request/response schemas for Categories (M2).

Mirrors ``openapi.yaml`` §15.2 (Categories) exactly.

DB vs OpenAPI note:

* The DB ``categories`` table has ``id SERIAL`` (Python ``int``) and
  ``created_at``/``updated_at`` columns. The OpenAPI ``Category`` schema
  requires those plus a ``version`` integer (the ETag-derived version).
  The DB does NOT have a ``version`` column; the version is derived
  server-side from ``updated_at`` via
  :func:`app.concurrency.master_etag.etag_and_version_from_updated_at`
  (this is the standard pattern for M2 master-data tables per
  ``Backend-Architecture-V1.0.md`` §12).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.validation.pagination import MetaEnvelope


class CategoryBase(BaseModel):
    """Common fields for category create/patch."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    parent_id: int | None = Field(default=None, ge=1)


class CategoryRequest(CategoryBase):
    """Body for ``POST /categories``.

    Per OpenAPI: ``name`` required; ``is_active`` default true; ``parent_id`` optional.
    """

    is_active: bool = True


class CategoryPatch(BaseModel):
    """Body for ``PATCH /categories/{id}``.

    Per OpenAPI: all fields optional.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None
    parent_id: int | None = Field(default=None, ge=1)


class CategoryResponse(BaseModel):
    """Response shape for category endpoints (matches ``Category`` in openapi.yaml).

    All required OpenAPI fields are exposed; ``version`` is derived from
    ``updated_at`` by the service layer (no DB column).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_active: bool
    version: int
    parent_id: int | None = None
    created_at: datetime
    updated_at: datetime


class DeactivateRequest(BaseModel):
    """Body for ``POST /categories/{id}/deactivate``."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


__all__ = [
    "CategoryBase",
    "CategoryPatch",
    "CategoryRequest",
    "CategoryResponse",
    "DeactivateRequest",
    "MetaEnvelope",
]
