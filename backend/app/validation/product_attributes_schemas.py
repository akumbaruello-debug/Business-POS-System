"""Request/response schemas for Product Attributes (V3 Phase 2).

Mirrors the existing M2 master-data envelope (categories/units pattern):
``id``, ``kind`` (closed Literal: jenis | size | kualitas), ``name``,
``is_active``, derived ``version`` + ``created_at``/``updated_at``.

DB note:

* The ``product_attributes`` table has NO ``version`` column; the version
  is derived from ``updated_at`` via
  :func:`app.concurrency.master_etag.etag_and_version_from_updated_at`
  (standard M2 master-data pattern, see Backend-Architecture-V1.0.md §12).
* ``kind`` is a closed CHECK set on the DB AND a closed Literal here —
  the three product dimensions stay semantically distinct. No other
  kind values exist.
* No starter/default values are ever seeded; the Owner enters values
  through the settings UI (V3-PLAN §5.1 — do NOT infer business rules
  or invent default values).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.validation.pagination import MetaEnvelope

ProductAttributeKind = Literal["jenis", "size", "kualitas"]


class ProductAttributeBase(BaseModel):
    """Common fields for attribute create/patch."""

    model_config = ConfigDict(extra="forbid")

    kind: ProductAttributeKind = Field(..., description="jenis | size | kualitas.")
    name: str = Field(..., min_length=1, max_length=100)


class ProductAttributeRequest(ProductAttributeBase):
    """Body for ``POST /product-attributes``.

    Per the master-data convention: ``name`` + ``kind`` required;
    ``is_active`` default true.
    """

    is_active: bool = True


class ProductAttributePatch(BaseModel):
    """Body for ``PATCH /product-attributes/{id}``.

    ``kind`` is immutable through the public API (it discriminates the
    dimension; changing it would silently re-purpose an attribute that
    may already be referenced by products). Renaming within the same
    kind is allowed; the unique index ``(kind, name)`` catches dupes →
    409.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


class ProductAttributeResponse(BaseModel):
    """Response shape for attribute endpoints (mirrors Category response)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: ProductAttributeKind
    name: str
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class DeactivateRequest(BaseModel):
    """Body for ``POST /product-attributes/{id}/deactivate``."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


__all__ = [
    "DeactivateRequest",
    "MetaEnvelope",
    "ProductAttributeBase",
    "ProductAttributeKind",
    "ProductAttributePatch",
    "ProductAttributeRequest",
    "ProductAttributeResponse",
]