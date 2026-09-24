"""Pydantic response models for the Capabilities API.

Matches ``openapi.yaml`` ``Capability`` schema exactly.
The ``domain``, ``default_owner``, and ``default_staff`` fields are
derived from the frozen capability catalog in ``app.authz.caps`` and
the role→capability seed mapping in ``schema.sql`` §19.3 — they are
not stored in the ``capabilities`` table.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CapabilityResponse(BaseModel):
    """Response for ``GET /capabilities/{id}``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    domain: str
    description: str | None
    default_owner: bool
    default_staff: bool


__all__ = ["CapabilityResponse"]
