"""Concurrency helpers — If-Match / ETag / version derivation.

Implements ``Backend-Architecture-V1.0.md`` §12 (Concurrency):

- The resource **ETag** is the **quoted ISO 8601 ``updated_at``** (strong
  validator). Clients send ``If-Match: "<ISO 8601>"`` on mutating
  endpoints.
- The **version** integer exposed in OpenAPI response schemas is
  derived server-side. The locked schema only has a ``version`` column
  on the 6 transactional document tables (``sales``, ``purchases``, ...);
  the M2 master-data tables do NOT. For master-data resources we derive
  a stable, monotonically-increasing ``version`` integer from
  ``updated_at`` at microsecond precision — this guarantees uniqueness
  across distinct writes and satisfies the OpenAPI response contract
  without altering the locked schema.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import TypeAdapter

from app.errors import VersionMismatch
from app.util import format_etag, parse_etag

_DATETIME_ADAPTER = TypeAdapter(datetime)

__all__ = [
    "check_if_match",
    "derive_version",
    "make_etag_from_updated_at",
    "parse_if_match_optional",
    "parse_if_match_required",
]


def derive_version(updated_at: datetime) -> int:
    """Derive a monotonically-increasing integer version from ``updated_at``.

    For master-data resources the DB has no ``version`` column, so we
    synthesize one. Using microsecond-precision epoch nanoseconds yields
    distinct integers for writes that occur at different microsecond
    timestamps, which is the resolution PostgreSQL's ``NOW()`` /
    ``clock_timestamp()`` provides for the ``BEFORE UPDATE`` trigger.

    Precision ceiling: microsecond-level writes to the same row within
    the same microsecond collide. That is only possible under parallel
    writers to the same row, which the single-row ``FOR UPDATE`` lock in
    the service layer prevents. Upgrade path: add a real ``version``
    column + trigger (a schema migration) when concurrent writers to
    master-data rows become a real scenario.
    """
    ts = updated_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    # Microsecond-resolution epoch as an integer.
    epoch_micro = int(ts.timestamp() * 1_000_000)
    return epoch_micro


def make_etag_from_updated_at(updated_at: datetime) -> str:
    """Render the canonical strong ETag for a resource.

    The ETag value is the resource's ``updated_at`` serialised in the
    canonical ISO 8601 form (``Z`` for UTC, matching the response
    serialization) surrounded by double quotes (RFC 7232 strong-validator).
    """
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return format_etag(_DATETIME_ADAPTER.dump_python(updated_at, mode="json"))


def check_if_match(*, provided: str | None, current_etag: str) -> None:
    """Compare an ``If-Match`` header value to the current ETag.

    Behaviour:
      * ``provided is None`` -> no concurrency check (caller decides
        whether it's required).
      * RFC 7232 ``provided == "*"`` -> always pass (matches any
        existing representation).
      * Provided value matches current -> pass.
      * Mismatch -> raise :class:`VersionMismatch` (412).

    Both sides are normalised through :func:`parse_etag` so callers can
    pass either the raw quoted ``If-Match`` header value or the already
    unquoted inner value; the comparison is performed on the inner ETag
    body only.
    """
    if provided is None:
        return
    # RFC 7232: If-Match: "*" matches any existing resource.
    if provided == "*":
        return
    provided_inner = parse_etag(provided) or provided
    current_inner = parse_etag(current_etag) or current_etag
    if provided_inner != current_inner:
        raise VersionMismatch("Resource has been updated since you last read it.")


def parse_if_match_optional(value: str | None) -> str | None:
    """Parse an ``If-Match`` header; return the inner value or None.

    Returns None when the header is absent.
    Raises ``InvalidHeader``-compatible: for M2 we raise
    :class:`app.errors.InvalidHeader` when the header is present but
    malformed.
    """
    if not value:
        return None
    inner = parse_etag(value.strip())
    if inner is None:
        from app.errors import InvalidHeader

        raise InvalidHeader(
            "If-Match header must be a quoted ETag.",
            details={"field": "If-Match"},
        )
    # Return the quoted form for direct comparison with make_etag.
    return format_etag(inner)


def parse_if_match_required(value: str | None) -> str:
    """Parse an ``If-Match`` header that MUST be present.

    Used by PATCH endpoints where If-Match is declared ``required: true``
    in OpenAPI (``IfMatchRequired`` parameter).
    """
    if not value:
        from app.errors import MissingHeader

        raise MissingHeader("If-Match header is required.")
    return parse_if_match_optional(value)  # type: ignore[return-value]
