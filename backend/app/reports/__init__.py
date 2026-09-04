"""Phase F — Reports package.

Phase F milestones implemented so far:

* ``F.1`` (CLOSED) — ``GET /reports/{sales,purchases,inventory,inventory-movements}``
* ``F.2`` (CLOSED) — ``GET /reports/{sales-returns,purchase-returns,production}``
* ``F.3`` (this milestone) — ``GET /reports/{manual-income,manual-expense,refunds,supplier-repayments,cash-flow}``

All endpoints are read-only. They aggregate or list over the existing
ledgers. No writes, no migrations, no new tables.

Capability: ``report.view`` (Owner only — see ``app.authz.caps``).
"""
