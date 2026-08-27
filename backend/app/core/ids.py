"""ID generation helpers.

* ``new_uuid()`` — RFC 4122 v4 random UUID.
* ``new_request_id()`` — request-scoped UUID used to correlate logs
  and to populate the ``X-Request-ID`` response header and the
  ``error.request_id`` field of the canonical ``ErrorEnvelope``.
"""

from __future__ import annotations

import uuid


def new_uuid() -> uuid.UUID:
    """Generate a fresh RFC 4122 v4 UUID."""
    return uuid.uuid4()


def new_request_id() -> uuid.UUID:
    """Alias of ``new_uuid`` — kept separate so we can change strategy later."""
    return uuid.uuid4()
