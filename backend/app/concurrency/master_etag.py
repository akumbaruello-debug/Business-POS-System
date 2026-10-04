"""Shared helpers for ETag / version derivation across M2 master-data resources.

The locked schema has two categories of M2 tables:

1. **Tables with ``updated_at``** (categories, payment_methods,
   financial_categories, products, contacts): ETag = quoted ISO-8601
   ``updated_at`` per ``Backend-Architecture-V1.0.md`` §12. ``version``
   is derived from microsecond epoch.

2. **Tables WITHOUT ``updated_at``** (units: no timestamp columns at all;
   cost_types: ``created_at`` only): the timestamp-ETag contract is
   mathematically impossible. For these, we derive a **deterministic
   content-hash ETag** from the immutable-ish identity columns. The hash
   changes whenever the row's content changes, so If-Match semantics
   (detect stale clients) are still satisfied — but the ETag is
   content-derived, not time-derived.

This is a documented deviation from §12 (see §28 Risks / Ambiguities)
forced by the locked schema. No schema columns are added or altered.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.util import format_etag, iso_utc

__all__ = [
    "etag_and_version_from_content",
    "etag_and_version_from_updated_at",
]

def _epoch_micro(updated_at: datetime) -> int:
    ts = updated_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return int(ts.timestamp() * 1_000_000)


def etag_and_version_from_updated_at(updated_at: datetime) -> tuple[str, int]:
    """Canonical ETag + version derived from a ``updated_at`` timestamp.

    Returns ``(etag, version)`` where ``etag`` is the quoted ISO-8601
    string (canonical ``+00:00`` form for UTC, matching the response
    serialization) and ``version`` is a microsecond-epoch integer.
    """
    ts = updated_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return format_etag(iso_utc(ts)), _epoch_micro(ts)


def etag_and_version_from_content(*parts: object) -> tuple[str, int]:
    """Content-derived ETag + version for tables lacking ``updated_at``.

    ``parts`` are the row columns whose identity defines the resource
    state (e.g. ``id, code, name, is_active``). The ETag is a hash of
    their canonical string form; ``version`` is the first 8 hex digits
    of the SHA-256 of the same, as an integer.

    Both change monotonically-ish when content changes (not timestamp
    monotonic), satisfying If-Match stale-client detection.
    """
    raw = "|".join(str(p) for p in parts if p is not None)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    # 16-hex-chars = 64-bit integer; ample headroom, monotonically
    # distinct per distinct content.
    version = int(digest[:16], 16)
    return format_etag(digest), version
