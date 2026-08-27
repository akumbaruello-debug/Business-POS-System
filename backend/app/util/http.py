"""HTTP-level utility helpers.

This module contains low-level parsing helpers. Higher-level helpers
(``parse_etag``, ``format_etag``, ``client_ip``, ``parse_request_id``)
are re-exported from :mod:`app.util`.
"""

from __future__ import annotations

import re

#: Regex for a strong ETag: ``"value"`` — the value may contain
#: any printable ASCII except a double-quote.
_STRONG_ETAG = re.compile(r'^"([^"]*)"$')
#: Regex for a weak ETag: ``W/"value"``.
_WEAK_ETAG = re.compile(r'^W/"([^"]*)"$')


def _parse_etag_value(raw: str) -> str | None:
    """Extract the inner value of an ETag header.

    Returns None on malformed input. The caller decides whether
    "absent" vs "malformed" is an error.
    """
    if not raw:
        return None
    m = _WEAK_ETAG.match(raw)
    if m is not None:
        return m.group(1)
    m = _STRONG_ETAG.match(raw)
    if m is not None:
        return m.group(1)
    return None


__all__: list[str] = []
