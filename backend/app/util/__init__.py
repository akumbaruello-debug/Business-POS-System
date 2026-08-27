"""Utility helpers for HTTP-level parsing (ETags, client IP, etc.)."""

from __future__ import annotations

import uuid

from app.util.http import _parse_etag_value


def parse_etag(raw: str) -> str | None:
    """Extract the ETag value from a raw header string.

    Accepts:
      * ``"2024-01-01T00:00:00Z"``  (strong)
      * ``W/"2024-01-01T00:00:00Z"`` (weak)

    Returns the inner value (without quotes / weak prefix), or None if
    the input does not match the expected shape.

    The ETag value is the resource's ``updated_at`` serialised as ISO 8601
    (per Backend-Architecture-V1.0.md §12).
    """
    return _parse_etag_value(raw)


def format_etag(from_value: str) -> str:
    """Wrap a raw ISO-8601 value as a strong ETag: ``"value"``."""
    return f'"{from_value}"'


def parse_request_id_or_none(raw: str | None) -> uuid.UUID | None:
    """Parse an ``X-Request-ID`` header value as a UUID, or None."""
    if not raw:
        return None
    try:
        return uuid.UUID(raw.strip())
    except (ValueError, AttributeError, TypeError):
        return None


def client_ip(scope_headers: list[tuple[bytes, bytes]]) -> str | None:
    """Return the client IP from an ASGI scope's headers.

    Honors ``X-Forwarded-For`` only when configured (we intentionally do
    NOT trust it by default — see §23 Security Architecture). This helper
    just reads the raw bytes; trust policy is applied by the
    ``XForwardedForMiddleware`` in the security layer.
    """
    for name, value in scope_headers:
        if name == b"x-forwarded-for":
            parts = value.decode("latin-1").split(",")
            return parts[0].strip() if parts else None
    # Fall back to the raw peer (scope["client"]).
    return None


__all__ = ["client_ip", "format_etag", "parse_etag", "parse_request_id_or_none"]
