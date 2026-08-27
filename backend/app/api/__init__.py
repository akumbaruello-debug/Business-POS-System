"""API package.

M1 only exposes:
  * ``/auth/*`` — the 5 OpenAPI auth endpoints.
  * ``/healthz``, ``/readyz``, ``/livez`` — operational probes.

M2+ adds the business modules (sales, purchases, refunds, ...).
"""

from __future__ import annotations
