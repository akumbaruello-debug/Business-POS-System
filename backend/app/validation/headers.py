"""HTTP header helpers for validated request headers.

Provides parsing + validation of ``Idempotency-Key`` and
``If-Match`` headers into typed values, returning ``AppError``
subclasses (not raw ``ValueError``) so the error handler converts
them to the right envelope.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.errors import InvalidHeader, MissingHeader
from app.util import parse_etag


@dataclass(frozen=True)
class IdempotencyKeyParsed:
    value: uuid.UUID
    raw: str


def parse_idempotency_key(header_value: str | None) -> IdempotencyKeyParsed:
    """Parse and validate an ``Idempotency-Key`` header.

    Per OpenAPI ``IdempotencyKey``:
      * type: string
      * format: uuid
      * maxLength: 100

    Returns the parsed UUID. Raises:
      * MissingHeader — if the header is absent.
      * InvalidHeader — if the value is not a valid UUID or exceeds 100 chars.
    """
    if not header_value:
        raise MissingHeader("Idempotency-Key header is required.")
    raw = header_value.strip()
    if len(raw) > 100:
        raise InvalidHeader("Idempotency-Key exceeds 100 characters.")
    try:
        value = uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidHeader(
            "Idempotency-Key must be a valid UUID.",
            details={"field": "Idempotency-Key", "value": "[REDACTED]"},
        ) from exc
    return IdempotencyKeyParsed(value=value, raw=raw)


def parse_idempotency_key_optional(header_value: str | None) -> IdempotencyKeyParsed | None:
    """Like ``parse_idempotency_key`` but returns None when absent."""
    if not header_value:
        return None
    return parse_idempotency_key(header_value)


@dataclass(frozen=True)
class IfMatchParsed:
    etag: str
    is_weak: bool = False


def parse_if_match(header_value: str | None) -> IfMatchParsed | None:
    """Parse the ``If-Match`` header.

    Per OpenAPI ``If-Match`` / ETag Architecture §12:
      * ETag = quoted ISO-8601 ``updated_at`` (e.g. ``"2024-01-01T00:00:00Z"``).
      * If the header is absent, concurrency checking is skipped
        (caller may enforce requirement per endpoint).
      * If present but malformed → InvalidHeader.

    Returns None when absent; otherwise the parsed ETag (inner value).
    """
    if not header_value:
        return None
    stripped = header_value.strip()
    if stripped == "*":
        return IfMatchParsed(etag="*")
    etag = parse_etag(stripped)
    if etag is None:
        raise InvalidHeader(
            "If-Match header must be a quoted ETag or '*'.",
            details={"field": "If-Match"},
        )
    return IfMatchParsed(etag=etag)


__all__ = [
    "IdempotencyKeyParsed",
    "IfMatchParsed",
    "parse_idempotency_key",
    "parse_idempotency_key_optional",
    "parse_if_match",
]
