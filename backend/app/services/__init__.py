"""Services package — business operations (sales, purchases, etc.).

In M1 the package only contains :mod:`app.services.idempotency`
(foundation). M2+ adds sales, purchases, refunds, payments, inventory,
production, etc.
"""

from __future__ import annotations

from app.services.idempotency import (
    IdempotencyRecord,
    IdempotencyStore,
    IdempotencyViolationConflict,
    compute_request_fingerprint,
)

__all__ = [
    "IdempotencyRecord",
    "IdempotencyStore",
    "IdempotencyViolationConflict",
    "compute_request_fingerprint",
]
