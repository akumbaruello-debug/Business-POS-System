"""Concurrency infrastructure foundation (If-Match / ETag).

Implements ``Backend-Architecture-V1.0.md`` §12 / §17:

* The resource ETag is the **quoted ISO 8601 ``updated_at``**.
* Clients send ``If-Match: "<ISO 8601>"`` on mutating endpoints.
* The server compares the provided ETag against the resource's
  current ``updated_at``; mismatch -> 412 ``version_mismatch``.

For the 6 transactional document tables (sales, purchases, ...) the DB
also carries a ``version`` column bumped by ``trg_*_bump_version`` as a
defense-in-depth counter.

For M2 master-data tables without a ``version`` column, see
:mod:`app.concurrency.etag` for the derived version integer.

This package keeps the M1 ``make_etag`` / ``check_if_match`` shims for
backwards compatibility and re-exports the M2 helpers from
:mod:`app.concurrency.etag`.
"""

from __future__ import annotations

from datetime import datetime

from app.concurrency.etag import (
    check_if_match,
    derive_version,
    make_etag_from_updated_at,
    parse_if_match_optional,
    parse_if_match_required,
)
from app.logging import get_logger

logger = get_logger(__name__)


def make_etag(updated_at: datetime) -> str:
    """Backwards-compatible alias to ``make_etag_from_updated_at``.

    Retained because the prior version of this module exposed
    ``make_etag``; existing callers must continue to work. The M1
    implementation omitted tzinfo normalization; we still accept naive
    datetimes and treat them as UTC for ISO-format serialization.
    """
    return make_etag_from_updated_at(updated_at)


def strip_quotes(etag: str) -> str:
    """Remove the surrounding quotes from an ETag value."""
    if etag.startswith('"') and etag.endswith('"') and len(etag) >= 2:
        return etag[1:-1]
    return etag


__all__ = [
    "check_if_match",
    "derive_version",
    "make_etag",
    "make_etag_from_updated_at",
    "parse_if_match_optional",
    "parse_if_match_required",
    "strip_quotes",
]
