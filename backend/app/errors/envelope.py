"""Canonical error envelope implementation.

This module is the **single source of truth** for the shape of every
4xx/5xx response body in the application. It matches the
``ErrorEnvelope`` and ``ValidationErrorEnvelope`` schemas in
``openapi.yaml`` exactly.

Contract reference (``openapi.yaml`` ``components.schemas``):

    ErrorEnvelope:
      type: object
      required: [error]
      properties:
        error:
          type: object
          required: [code, message, request_id]
          properties:
            code: string             # stable snake_case machine code
            message: string          # human-readable English
            details:                 # optional code-specific
              oneOf: [object, array, null]
            request_id: string uuid

    ValidationErrorEnvelope:
      allOf:
        - $ref: ErrorEnvelope
        - type: object
          properties:
            error:
              properties:
                code: { const: validation_failed }
                details:
                  type: array
                  items:
                    type: object
                    required: [field, code, message]
                    properties:
                      field: string
                      code: string
                      message: string
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

# Re-export a small model layer. The actual exception class and the
# builder live in ``app.errors.codes`` and ``app.errors.handlers``.

__all__ = [
    "REQUEST_ID_KEY",
    "ErrorBody",
    "ErrorEnvelopeModel",
    "ValidationErrorItem",
]


#: ContextVar key for the per-request UUID used in error envelopes.
REQUEST_ID_KEY: Final[str] = "request_id"


class ErrorBody(BaseModel):
    """The inner ``error`` object of an envelope.

    Extra keys are forbidden by the contract — clients are guaranteed
    a stable shape across error codes.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        ...,
        description="Stable, machine-readable snake_case code.",
    )
    message: str = Field(
        ...,
        description="Human-readable English.",
    )
    details: dict[str, Any] | list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional, code-specific structure (object/array/null).",
    )
    request_id: uuid.UUID = Field(
        ...,
        description="Echoes the X-Request-ID header.",
    )


class ErrorEnvelopeModel(BaseModel):
    """The full ``ErrorEnvelope`` response body."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorBody


class ValidationErrorItem(BaseModel):
    """One field-level validation error inside ``ValidationErrorEnvelope``."""

    model_config = ConfigDict(extra="forbid")

    field: str
    code: str
    message: str
