"""Cash-movements read-side module (E.9).

Backs the two ``/api/v1/cash-movements`` operations:

* ``listCashMovements`` — paginated list with filters + sort + date range.
* ``getCashBalance``    — current SUM(amount) over the cash_movements ledger.

The ``cash_movements`` table is the authoritative cash ledger
(append-only per ``schema.sql`` §10.1 + §18.4; INV-03 non-negative
balance enforced by the pre-insert trigger §18.5). E.9 is read-only;
writes happen inside other milestones (E.3-E.8) via dedicated helpers.
"""

from __future__ import annotations

__all__: list[str] = []
