"""Pydantic request/response models shared across the API.

Defines:
* ``Pagination`` — matches ``openapi.yaml`` ``Pagination`` schema.
* ``MetaEnvelope`` — a minimal paginated-list wrapper.
* Common scalar models (IdResponse, etc.).
"""

from __future__ import annotations

import math
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Pagination(BaseModel):
    """Matches ``openapi.yaml`` ``Pagination`` exactly — page 1-based,
    per_page capped at 500, total/total_pages derived from the query.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    page: int = Field(..., ge=1)
    per_page: int = Field(..., ge=1, le=500)
    total: int = Field(..., ge=0)
    total_pages: int = Field(..., ge=0)


class MetaEnvelope(BaseModel, Generic[T]):
    """Generic wrapper: ``data`` + ``pagination``.

    Used for every paginated collection endpoint so the contract is
    uniform. We deliberately avoid a per-endpoint pagination model.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    data: list[T]
    pagination: Pagination


def make_pagination(page: int, per_page: int, total: int) -> Pagination:
    """Build a ``Pagination`` object, clamping inputs safely."""
    per_page = max(1, min(per_page, 500))
    page = max(1, page)
    total_pages = math.ceil(total / per_page) if total else 0
    return Pagination(page=page, per_page=per_page, total=total, total_pages=total_pages)


__all__ = [
    "MetaEnvelope",
    "Pagination",
    "make_pagination",
]
