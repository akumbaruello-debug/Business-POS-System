"""Concurrency infrastructure foundation (If-Match / ETag).

Implements ``Backend-Architecture-V1.0.md`` §12 / §17:

* The resource ETag is the **quoted ISO 8601 ``updated_at``**.
* Clients send ``If-Match: "<ISO 8601>"`` on mutating endpoints.
* The server compares the provided ETag against the resource's
  current ``updated_at``; mismatch → 412 ``version_mismatch``.
* The DB also has a `version` column on the 6 mutable entities that
  increments on every UPDATE via ``trg_*_bump_version``. That column
  is the **server-side defense-in-depth** — the ETag is the
  client-facing contract.

M1 provides the helpers; M2+ uses them inside PUT/PATCH/POST flows.
"""

from __future__ import annotations

from datetime import datetime

from app.errors import VersionMismatch
from app.logging import get_logger

logger = get_logger(__name__)


def make_etag(updated_at: datetime) -> str:
    """Render the canonical strong ETag for a resource.

    The ETag value is the resource's ``updated_at`` serialised in
    ISO 8601 with microseconds + UTC offset, surrounded by double
    quotes (per RFC 7232 strong-validator syntax).
    """
    return f'"{updated_at.isoformat()}"'


def check_if_match(
    *,
    provided: str | None,
    current_etag: str,
) -> None:
    """Compare an ``If-Match`` header value to the current ETag.

    Behaviour:

    * ``provided is None`` → no concurrency check (caller decides
      whether it's required).
    * Provided value matches current → pass.
    * Mismatch → raise :class:`VersionMismatch` (412).
    """
    if provided is None:
        return
    # Both should be the same quoted ISO 8601 string. Direct string
    # comparison is correct — the format is deterministic.
    if provided != current_etag:
        logger.info("version_mismatch", provided=provided, current=current_etag)
        raise VersionMismatch(
            "Resource has been updated since you last read it.",
        )


def strip_quotes(etag: str) -> str:
    """Remove the surrounding quotes from an ETag value."""
    if etag.startswith('"') and etag.endswith('"') and len(etag) >= 2:
        return etag[1:-1]
    return etag


__all__ = ["check_if_match", "make_etag", "strip_quotes"]
