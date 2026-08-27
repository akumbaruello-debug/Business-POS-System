# Migration Plan — Business-POS-System V1.0

## 1. Strategy: Clean Baseline

The Business-POS-System is a **new project with no existing production data**.
The validated `schema.sql` is the canonical V1.0 target. Per project policy
("prefer a clean baseline migration strategy rather than inventing unnecessary
historical migrations"), the migration strategy is:

> A **single baseline migration** (`m0001`) contains the complete, validated
> canonical schema — DDL + triggers + functions + seed data.
> Future V1.1+ changes will land as additional `m0002__*.sql`, `m0003__*.sql`,
> etc., applied incrementally on top of the baseline.

### Why a single baseline?

| Consideration | Decision |
|---|---|
| No existing production data | ✅ Fresh DB only |
| No prior migration history | ✅ No migrations to reconstruct |
| `schema.sql` is already dependency-ordered | ✅ Can be wrapped as-is |
| 3 schema corrections made during validation (C-1–C-3) | ✅ Corrections are IN the baseline — no separate migration needed |
| Future incremental changes | ✅ Each gets numbered m0002, m0003, etc. |

## 2. Migration Files

| Order | File | Description |
|---|---|---|
| m0001 | `m0001__initial_schema_baseline.sql` | Complete V1.0 schema: extensions, 38 tables, 2 views, 20 trigger functions, 40 triggers, all indexes/constraints, and seed data for roles/capabilities/payment_methods/financial_categories/system_settings/users. |

## 3. Dependency / Ordering Strategy

The baseline migration (`m0001`) is already internally dependency-ordered
(the `schema.sql` was validated that way). The ordering rules are:

### For m0001 (baseline — already complete):
1. **Extensions** (pgcrypto, btree_gist) — always first
2. **Reference tables** — `payment_methods`, `cost_types`, `financial_categories` (no FK dependencies, no business FK references)
3. **Identity tables** — `roles` → `capabilities` → `users` (users has FK → roles; roles referenced by users)
4. **Master data** — `categories`, `units`, `products`, `contacts`, `product_price_history`, `product_supplier` (products FKs to categories, units, users)
5. **Transactional tables** — `sales` → `sale_lines`, `purchases` → `purchase_lines`, `stock_movements` + `stock_movements_reversals`, `cash_movements` → `manual_finance_entries`, `production_runs` + lines
6. **Derived/ledger tables** — `sale_payments`, `purchase_payments`, `refunds`, `supplier_repayments`, `inventory_snapshots`, `sales_returns` + lines, `purchase_returns` + lines
7. **Sessions/idempotency** — `sessions`, `idempotency_keys`, `notifications`, `system_settings`, `audit_log`
8. **Views** — `product_stock`, `product_valuation` (after all tables and functions)
9. **Functions** — 20 trigger functions defined before triggers that reference them
10. **Triggers** — 40 custom triggers
11. **Forward-referenced FKs** — The FK from `sale_lines.stock_movement_id → stock_movements.id` is added via `ALTER TABLE` *after* `stock_movements` is created (this is the only forward reference; it was corrected during schema validation)

### For future migrations (m0002+):
Follow the same rules: referenced object before referencing object, functions
before triggers, tables before dependent views. Each migration runs in its
own transaction and is recorded in `schema_migrations` with a SHA-256 checksum.

## 4. Rollback Strategy

### Baseline (m0001): no ROLLBACK
The baseline is **irreversible**: dropping it would destroy all schema.
The rollback approach for a baseline-only database is:

> **Recreate the database**: drop and recreate the database, then re-apply
> migrations from m0001 (or restore from a pre-migration backup).

### For future migrations (m0002+):
- Each migration should be paired with a `down` section (or a separate
  `rollback_*.sql` file) when structural changes are reversible.
- Irreversible operations (DROP COLUMN, DROP TABLE) must be documented.
- The `schema_migrations` table prevents re-application and supports
  checksum verification of already-applied migrations.

## 5. Seed Data Strategy

Seed data is included within `m0001` inside a single `BEGIN; ... COMMIT;`
block. This keeps the bootstrap atomic — if any seed INSERT fails, the entire
migration rolls back.

| Table | Rows | Purpose |
|---|---|---|
| `roles` | 2 | Owner (system), Staff (system) |
| `capabilities` | 65 | Canonical catalog (API §3.1) |
| `role_capabilities` | 82 | Owner 65 + Staff 17 default-on |
| `payment_methods` | 4 | cash, bank_transfer, e_wallet, other |
| `cost_types` | 5 | labor, electricity, gas, packaging, other |
| `financial_categories` | 11 | income + expense manual categories |
| `system_settings` | 10 | 1 row + 9 key/value rows (costing_method, is_initialized=false, etc.) |
| `users` | 1 | Default Owner user (password !UNSET — must be changed on first login) |

**Note on `system_settings.is_initialized`**: seeded as `FALSE` per
reconciliation C-13. The application is responsible for setting it to `TRUE`
during first-run onboarding. This is a design-level workflow, not a DB
constraint.

## 6. Migration Runner

The runner is `db/migrate.sh`:

- Creates `schema_migrations(name, sha256, applied_at)` table if not present
- Lists `m*.sql` files in `db/migrations/` in lexical order
- Applies each not-yet-applied migration with `psql -v ON_ERROR_STOP=1 -f`
- Records name + SHA-256 checksum + timestamp
- Skips already-applied migrations (idempotent)
- **Rejects** re-running a migration if the file's checksum changed
  (defends against silent in-place edits)

Usage:
```bash
bash db/migrate.sh [DB_NAME] [PGHOST] [PGPORT] [PGUSER]
# defaults: bpos_validation, localhost, 5433, postgres
```

## 7. Future Migration Workflow

1. Author changes as `db/migrations/m0002__descriptive_name.sql`
2. Include both `up` (default) and `down` (rollback) sections if reversible
3. Run: `bash db/migrate.sh`
4. The runner applies it, records the checksum, and reports success/failure
5. For rollback of a specific migration: `psql -d $DB -f db/migrations/m0002__descriptive_name.down.sql`

---

*End of Migration Plan V1.0*
