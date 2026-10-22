# Business-POS-System — STATUS
**Last updated:** 2026-10-22
**HEAD:** `f0d9871` (`release/candidate-1`). V3 work committed through Phase C; F1 fixed by migration commit. F2 fix pending uncommitted.

## Current objective
Ship the in-flight V3 implementation as **10 coherent commits** on `main`. The implementation modifies 43 files + adds 33 new files (product attributes, units/conversions, purchase-cost policy, sorting/mixed-receiving). Phase C is not yet started; this file describes Phase A/B progress and the Phase C plan.

## Phase A — test harness (complete)
| Step | Outcome |
|---|---|
| A1 `tests/api/test_middleware.py` | CORS override tests now save/restore `POS_CORS_ALLOWED_ORIGINS` (via `_set_origins`/`_restore_origins` + `_CORS_ENV_KEY`) instead of `del os.environ[...]`. Fixed the order-dependent cascade that turned the suite from 661 failures → green. |
| A2 `tests/conftest.py` | New `_TEST_DATABASE_URL`/`_TEST_CORS_ORIGINS` constants; `POS_DATABASE_URL` + cache-clear re-asserted in `_ensure_schema`, `db`, and `app` fixtures. This also prevents a leaked env mutation from letting `pydantic-settings` fall back to `backend/.env` and pointing `DROP SCHEMA` at `pos_dev` / production. |
| A3 `app/api/v1/auth.py` | `json.loads(existing.response_body)` guarded with dict-or-parse logic → fixes HTTP 500 on idempotent-login replay. |
| A4 full suite re-run | **First run: 9 failed / 1281 passed** (was 661 failed). Targeted A1–A3 re-run: 54 passed / 0 failed. |
| A5 migration parity (two fresh disposable scratch DBs) | Baseline+16 migrations apply clean; `schema.sql` applies clean. **0 column/type/nullability/default diffs** (after stripping ordinal). 239 functions both sides. |

## Phase A gate result
A4 was the **hard stop** gate. After A1–A3 plus the approved narrow settings-test fix (see below), a full re-run returned **8 failed / 1282 passed** (`A4_full_suite3.txt`).

The 8 remaining failures are **all baseline / pre-existing**, none are new V3 regressions:

| Failing test | Why it's pre-existing |
|---|---|
| `test_production_cancel`, `test_production_cost_lines`, `test_production_inputs`, `test_purchases`, `test_refunds`, `test_sales_b8`, `test_supplier_repayments` (7) | Login (`_login`) sets session cookies on the shared ASGI `app` client, so the "unauthenticated" follow-up request is actually authenticated → 200 (sales variant hits the Idempotency-Key check first → 400). **Reproduced identically at pristine HEAD `9b43f86`**. |
| `test_manual_entries.py::test_cancel_ok` | `audit_log` is append-only (a trigger blocks TRUNCATE), and `manual_finance_entries` uses `RESTART IDENTITY`, so an entity ID is reused and a stale `cancel` audit row collides → `assert 2 == 1`. **Passes in isolation and as a full file; only fails in the full-suite ordering context.** |

- 1 V3 failure found at A4 → **fixed** (approved Option 1): `test_settings_f9` now expects the new `default_purchase_cost_allocation` key (seeded in `schema.sql` as `'line_value'` / string). Passes.
- **Decision:** gate permits proceeding to Phase B/C. The 7 cookie-auth failures and `test_cancel_ok` are documented baseline issues **out of scope** for this V3 pass (there is even an unmerged `fix/manual-entry-audit-test-isolation` branch for the latter, deliberately not merged here).

## Phase A fixes (in working tree, not committed)
- `backend/tests/api/test_middleware.py` — CORS env restore helpers.
- `backend/tests/conftest.py` — `_TEST_DATABASE_URL` / `_TEST_CORS_ORIGINS`, env re-assert in fixtures, + **Phase B TRUNCATE additions** (the 8 V3 tables: `purchase_cost_categories`, `purchase_costs`, `purchase_cost_allocations`, `pending_receipt_batches`, `pending_inventory_movements`, `sorting_events`, `sorting_event_outputs`, `sorting_event_reversals`; `product_attributes` was already listed).
- `backend/app/api/v1/auth.py` — idempotent-login replay guard.
- `backend/tests/api/test_settings_f9.py` — expect `default_purchase_cost_allocation`.

## Phase B — hygiene (complete)
- `Business-Rules.md`: renamed the V3 duplicate `BR-STOCK-012` ("Pending Mixed Receipts and Sorting") to `BR-STOCK-013`; the pre-existing V1 `BR-STOCK-012` ("Stock Movement Audit", Source §26) is preserved.
- `.gitignore`: added `/nul` (root-level; `backend/nul` kept).
- `memory/status.md`: this rewrite.
- `docs/perf/*`: left untouched and untracked.

## Phase C — 10-commit sequence (executed)
The approved sequence at `.opencode/plan/business-pos-v3-stabilize-and-commit.md` was executed commit by commit, each with its required checks (touched pytest files, `pnpm typecheck`/`pnpm build` for frontend commits, `validate_openapi.py` against the staged spec for every openapi touch). See `git log 9b43f86..HEAD` for the authoritative list and SHAs.

1. `docs(v3): add V3 plan package and starter templates` — `V3-PLAN/Business-POS-V3-Plan-Package/V3-PLAN.md` + `templates/{README.md,units.csv,product_attribute_values.csv,production_cost_types.csv,purchase_landed_cost_types.csv}`. `V3-PLAN/MUST-READ.txt` deliberately excluded.
2. `feat(db): V3 master data, pricing, purchase cost and sorting schema`
3. `feat(units): add unit dimension and conversion factor`
4. `feat(products): add Jenis/Size/Kualitas product attributes`
5. `feat(pricing): per-SKU base kg prices and unit overrides`
6. `feat(purchases): purchase cost policy, allocation and landed-cost posting`
7. `feat(inventory): mixed receiving, pending receipts and sorting events`
8. `fix(purchases): sales-returns trigger and purchase-line quantity bound`
9. `docs: align business rules, database design and accounting event matrix with V3 phases 3–4`
10. `fix(tests): isolate CORS env, fix idempotent login replay, close V3 hygiene gaps` — Phase A1–A3 + Phase B1–B4 (test_middleware.py, conftest.py, auth.py, test_settings_f9.py, Business-Rules.md numbering, memory/status.md, .gitignore) — i.e. **all Phase A/B edits land here**.

A separate `chore(seeds)` commit carries `backend/scripts/seed_demo_extended.py`, which is genuinely cross-phase (it drives sorting, production, sales, purchase, refund, supplier-repayment, product-attribute and unit services) and therefore could not be attributed to any single feature commit.

**Shared-file attribution rule (approved):** split `router.py`/`caps.py`/`sidebar.tsx`/`settings page.tsx`/`i18n/{en,id,context}.ts`/`conftest.py`/`schema.sql` **by hunk** when cleanly separable; otherwise keep the whole file in one commit and state the rationale in the commit body. `openapi.yaml` is split by API-path section across the feature commits, validated after each.

**Per-commit verification (no commit may skip a red check):** backend `pytest` of touched tests; `pnpm typecheck`+`pnpm build` for `frontend/` commits; `validate_openapi.py` after any `openapi.yaml`/contract touch.

## A5 migration-parity findings (F1/F2 resolved; F3–F6 accepted drift — do not fix)
Two fresh scratch DBs were created on `:5433` (`mig_v3_parity_chain_20261021`, `mig_v3_parity_schema_20261021`); **not dropped**. `pos_dev`/`production` were only ever SELECTed.

- **F1 (real, resolved):** index `ix_sorting_outputs_product` on `sorting_event_outputs(product_id, id)` is in `schema.sql` but was **absent from every migration**. **`pos_dev` confirmed missing it** (read-only). Test DBs (schema-built) have it; migrated DBs didn't. Fixed by migration commit `f0d9871` (`20261022000001_add_ix_sorting_outputs_product.sql`).
- **F2 (real, resolved + dynamically verified):** trigger `trg_purchase_cost_categories_touch_updated` (BEFORE UPDATE → `fn_touch_updated_at()`) is in the migration chain and `pos_dev` but was **absent from `schema.sql`** → the test DB never bumped `updated_at` for that table. Fix (uncommitted): 4 lines added to `schema.sql` after `trg_system_settings_touch_updated`, matching `supabase/migrations/20261019000004_purchase_cost_policy.sql:89-91` exactly.
  - **Dynamic verification (isolated cluster):** data directory `%LOCALAPPDATA%\Temp\opencode\f2cluster_20261022\data`, port `55441` bound to `127.0.0.1` only, fresh `initdb`, cluster stopped and **retained** for review.
  - Databases: `f2v2_schema` (`schema.sql` applied) and `f2v2_chain` (all 18 migrations, name order).
  - Results: 50 triggers each — **0 differences**; 461 columns each — **0 differences**; 253 functions each; target trigger definition byte-identical on both sides; `updated_at` bump proven in both DBs inside a rolled-back transaction (`bumped=true`), 0 rows remaining after rollback.
  - Retained artifacts in `f2cluster_20261022\`: `server.log`, `schema_apply.log`, `chain_apply.log`, `trg_schema.txt`, `trg_chain.txt`, `col_schema.txt`, `col_chain.txt`, `con_schema.txt`, `con_chain.txt`.
  - **Invalid evidence (superseded):** the earlier F2 dynamic run used the project cluster at `pg-tmp\pg17\data` (port 5433), which also hosts `pos_dev` and `pos_test`, and created `f2_schema_20261022` / `f2_chain_20261022` inside it. That cluster was started/stopped during that session and was **not** isolated. Its results must not be cited. No explicit connection to `pos_dev`/`pos_test` was evidenced, but absence of impact cannot be certified from the available evidence. The two `f2_*` databases and the cluster were left untouched.
- **F3 (cosmetic, accepted):** **8 constraint-name-only pairs** with identical definitions — `products_purchase_price_per_kg_check`/`ck_products_purchase_price_per_kg`, `products_selling_price_per_kg_check`/`ck_products_selling_price_per_kg`, `product_price_history_purchase_price_per_kg_check`/`ck_pph_purchase_price_per_kg`, `product_price_history_selling_price_per_kg_check`/`ck_pph_selling_price_per_kg`, `fk_products_jenis`/`products_jenis_id_fkey`, `fk_products_kualitas`/`products_kualitas_id_fkey`, `fk_products_size`/`products_size_id_fkey`, `fk_purchase_lines_unit`/`purchase_lines_unit_id_fkey`.
- **F4 (cosmetic, accepted):** **2 same-name constraints** (`ck_sm_reversal_trigger`, `ck_sm_nonreversal_trigger`) whose ARRAY element order differs; same 7-element set (`sorting_reversal` last vs 4th), semantically equal.
- **Constraint-drift total: 10 divergent constraints** (8 name-only pairs + 2 ARRAY-order pairs) — *not* 10 name-only pairs. Confirmed unchanged by the isolated re-run (311 constraints both sides, same 20 diff lines, no new drift).
- **F5 (accepted textual/diagnostic drift — no fix planned):** no application or test dependency was demonstrated for any of the three differences.
  - `fn_purchase_cost_allocation_immutable` — `schema.sql:1020-1037` vs `20261019000004_purchase_cost_policy.sql:65-78`: `IF/ELSE` vs `CASE` for `v_cost_id`; same message, same `ERRCODE='23514'`, same return semantics. Cosmetic.
  - `fn_sorting_posted_immutable` — `schema.sql:1287-1309` vs `20261020000001_v3_phase4_mixed_receiving_sorting.sql:126-143`: three messages differ by a trailing `.` only (`schema.sql:1292,1298,1305` vs migration `130,134,140`); control flow, NULL guards, and the `P0001` (immutability) / `23514` (reconciliation) split are identical. Cosmetic.
  - `fn_pending_inventory_balance_guard` — `schema.sql:914-926` vs `20261020000001:54-62`: message `'Pending receipt batch % has insufficient available quantity'` (with batch id) vs `'Insufficient pending receipt balance'`; same decision logic, same `FOR UPDATE` lock, same `COALESCE` sums, same `ERRCODE='check_violation'`. Behaviorally different in text only.
  - No test asserts any of these strings. `test_purchase_costs.py:130` matches `"Posted purchase costs are immutable"`, which belongs to `fn_purchase_cost_snapshot_immutable` (`schema.sql:1006-1013`, migration `20261019000004:51-57`) — identical on both sides. `sorting_service.py:197` raises an app-level 409 before the trigger fires. Raw trigger exceptions are untranslated and reach `unhandled_handler` (`app/errors/handlers.py:263-283`) as a generic 500, so DB message text is never API-visible.
  - If parity is ever required, the only correct route is a **new forward migration** `CREATE OR REPLACE`ing the three functions to match `schema.sql` (canonical target) — never an edit to an applied migration. That is a production-visible message change and **requires owner approval**. Not justified now.
- **F6 (cosmetic, accepted):** ~55 columns differ only in ordinal position (column order within `CREATE TABLE`); 239 functions in both, bodies identical for the 6th function.
- Error-message text still diverges between `schema.sql`-built and migrated DBs for the pending-inventory and sorting guards (F5 above) — **no test asserts that text**, so the earlier "tests fail in migrated envs" concern is not demonstrated.

## Disclosures
- **`build_openapi.py` is stale.** The generator contains **no** V3 endpoint knowledge; running it would silently regenerate `openapi.yaml` without the 831 V3 lines. Not run; `openapi.yaml` preserved as-is.
- **`validate_oas31.py` is unavailable** — `openapi_spec_validator` is not installed. The approved check remains `python validate_openapi.py` (0 errors / 0 warnings, 189 ops).
- **`validate_openapi.py` is the approved spec check.** Do **not** run `build_openapi.py` as a regeneration step.

## Live dev state (unchanged)
- Postgres PG17 at `pg-tmp/pg17/...` on **port 5433** (not 5432), DB `pos_dev`/`pos_test`, user `postgres`.
- Backend run via `.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider` from `backend/`.
- Frontend: `pnpm typecheck` / `pnpm build` (build emits 30 routes incl. `/inventory/sorting`, `/settings/product-attributes`, `/settings/purchase-costs`, `/products`).
