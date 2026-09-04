# B.7 Verification Report

## B.7 Status

**COMPLETE + FROZEN**

All gates pass. No unresolved blockers. B.7 implementation NOT modified during verification (verification was read-only).

---

## Endpoints

Three read-only GET endpoints added (require PostgreSQL on `127.0.0.1:5433`):

1. `GET /products/{id}/price-history` — capability `product.view` (route: `get_product_price_history`)
2. `GET /products/{id}/stock-movements` — capability `inventory.view` (route: `get_product_stock_movements`)
3. `GET /products/{id}/valuation` — capability `inventory.view` (route: `get_product_valuation`)

---

## Implementation

Route/service/schema changes, all under `backend/`:

| File | Change |
|------|--------|
| `app/api/v1/products.py` | +3 route handlers; added `RequireInventoryView = require_capability("inventory.view")`; added `_compose_response` valuation enricher (on_hand/inventory_value/moving_avg) on `GET /products/{id}`; `StockMovement.quantity` typed `int`→`float`; `svc.list`→`svc.list_products` (avoids `list` builtin shadow for mypy). |
| `app/services/products.py` | +`list_price_history`, `list_stock_movements`, `get_valuation_snapshot`, `get_valuation`, `get_moving_average_unit_cost`; +`_parse_dt` (ISO 8601 → datetime for TIMESTAMPTZ). |
| `app/validation/products_schemas.py` | `StockMovement.quantity`: `int`→`float` per OpenAPI NUMERIC(15,4). |
| `tests/api/test_products_b7.py` | 24 tests (created). |

---

## Business Rules

B.7 rules actually enforced (all in focused tests + full suite):

- **Read-only contract**: price-history / stock-movements / valuation endpoints perform NO writes (no audit_log / stock_movements side effects). Verified by `TestB7ReadOnlyContract` (2 tests).
- **Authz**: all 3 endpoints require a session token; `price-history` requires `product.view`; `stock-movements`/`valuation` require `inventory.view`. Verified by `TestB7Authorization` (6 tests).
- **Filtering**: `from`/`to` ISO-8601 timestamp filters on price-history & stock-movements. (4 tests)
- **Pagination**: cursor-style `limit`/`offset` returns bounded windows + total. (4 tests)
- **404**: nonexistent product ID → single 404 error envelope, no 500. (3 tests)
- **Ordering**: price-history and stock-movements return newest-first. (2 tests)
- **Empty state**: empty history/movements → `[]`, total 0. (2 tests)
- **Valuation semantics**: zero-stock → `on_hand_quantity=0`, `inventory_value=0`, `moving_average_unit_cost=None`. (1 test)
- **Moving-average derivation**: `moving_average_unit_cost = Σ(total_cost)/Σ(quantity)` over stock movements, computed app-side, not from a DB column.

---

## Contradictions

### C-1 — Trigger enum mismatch (RESOLVED, not invented)

- OpenAPI `openapi.yaml:8332` — `StockMovement.trigger` enum value `production_output_reversal`.
- DB constraint `ck_sm_trigger` in `schema.sql:941,970,974` — value `production_reversal`.
- Implementation: `StockMovement.trigger` response model uses plain `str` (NOT a constrained `Enum`/`Literal`). `products_schemas.py:142` documents the mismatch and the `str` rationale.
- No database value is invented; response serializes the actual persisted DB value verbatim. OpenAPI enum is a frozen spec gap, left untouched.
- Verification: `grep 'production_output_reversal'` → ONLY `openapi.yaml`; `production_reversal` → ONLY `schema.sql`. Implementation emits neither as a literal — forwards the DB string.

### C-2 — Missing `moving_average_unit_cost` in `product_valuation` view (RESOLVED, not modified)

- `schema.sql:1524` — `CREATE OR REPLACE VIEW product_valuation AS`.
- `grep 'moving_average_unit_cost' schema.sql` → **empty**. Frozen view has NO such column.
- Implementation does NOT add the column. Uses the required helper:
  `app.inventory.valuation.compute_moving_average_unit_cost`
  - `products.py:48` imports it: `from app.inventory.valuation import compute_moving_average_unit_cost, get_settings_bool`.
  - `inventory/valuation.py:39` defines it (`Σ(total_cost)/Σ(quantity)`); `inventory/__init__.py:10` re-exports.
- Frozen DB/view definition untouched.

---

## Files Created

1. `backend/tests/api/test_products_b7.py` — 24-test B.7 contract suite.

### Files Modified (B.7 scope only)

2. `backend/app/api/v1/products.py` — +3 routes, valuation enrich, type fixes, renaming.
3. `backend/app/services/products.py` — +3 service methods + helpers.
4. `backend/app/validation/products_schemas.py` — `StockMovement.quantity` int→float + C-1 `str` resolution.

### Out-of-scope modified files (B.6 / M2-foundation, NOT B.7, NOT touched during verification)

- `backend/app/api/v1/router.py`, `backend/app/concurrency/__init__.py`, `backend/app/errors/{__init__.py,codes.py}`, `backend/app/util/__init__.py`, `backend/tests/conftest.py`, `validate_oas31.py`, `validate_openapi.py` — all pre-existing B.6/M2-foundation work; left untouched by B.7 verification.

---

## Tests

### B.7 focused (run in isolation)

```
pytest tests/api/test_products_b7.py
```
**24 passed** in 43.62s — mypy clean, ruff clean, ruff-format clean.

### Full suite (scoped to `backend/`, configured rootdir)

```
cd backend && python -m pytest -q --tb=line
```
**399 passed, 0 failed, 0 skipped** in 509.45s (8m29s).

### Isolation / classification of the "B.6 ETag flaky test" rumor

status.md:12 mentions "1 pre-existing flaky ETag timing test in B.6". **Verification found NO failing test** — full run was **399 passed, 0 failed**. ETag logic is exercised by `test_products.py` (B.6), `test_middleware.py`, `test_authz.py` — all green, deterministic.

The 21 collection ERRORS from the initial unscoped root run:
- `pg-tmp/pg17/pgsql/pgAdmin 4/.../sqlalchemy/testing/suite/*` — 9 import-mismatch errors from pytest walking the gitignored `pg-tmp/` scratch PG install (bundled SQLAlchemy test suite shadowing the venv package).
- `_wip_m2_baseline/tests/api/test_categories.py` — 1 stale import in an untracked WIP-baseline dir.
- Classification: **shared-infrastructure / collection-scope**, not B.7 regressions. Mitigation already in `pyproject.toml` (`testpaths=["tests"]`): run from `backend/`. No code/test weakening performed.

---

## Quality Gates

| Gate        | Result                                              |
| ----------- | --------------------------------------------------- |
| Ruff        | ✅ `All checks passed!` (exit 0)                    |
| Ruff format | ✅ `107 files already formatted` (exit 0)           |
| Mypy        | ✅ `Success: no issues found in 106 source files` (exit 0) |
| B.7 pytest  | ✅ `24 passed` in 43.62s                            |
| Full pytest | ✅ `399 passed, 0 failed, 0 skipped` in 509.45s     |

---

## Locked Files

Every locked artifact verified **clean vs HEAD** (`git diff HEAD` = 0 lines; `git status --porcelain` empty). `pyproject.toml` is at `backend/pyproject.toml`:

| Locked artifact | `git diff HEAD` lines | Status |
|-----------------|-----------------------|--------|
| `schema.sql` | 0 | UNCHANGED ✅ |
| `openapi.yaml` | 0 | UNCHANGED ✅ |
| `Business-Rules.md` | 0 | UNCHANGED ✅ |
| `Database-Design-V1.0.md` | 0 | UNCHANGED ✅ |
| `Backend-Architecture-V1.0.md` | 0 | UNCHANGED ✅ |
| `Backend-Implementation-Plan-V1.0.md` | 0 | UNCHANGED ✅ |
| `API-Architecture-V1.0.md` | 0 | UNCHANGED ✅ |
| `backend/pyproject.toml` | 0 | UNCHANGED ✅ |
| `db/migrations/m0001__initial_schema_baseline.sql` | 0 | UNCHANGED ✅ |

No accidental formatting/whitespace changes found; nothing required restoring.

---

## Frozen Milestones

- **B.1–B.6 implementation modules**: UNCHANGED. Verified via content comparison of `backend/app/api/v1/products.py` (8 routes = 5 B.6 CRUD + 3 B.7 read-only; B.6 route bodies/types intact) against `_wip_m2_baseline/app/api/v1/products_routes.py`. Only working-set additions are B.7 handlers + `RequireInventoryView` + `_compose_response` + `StockMovement.quantity` int→float. No B.6 endpoint logic rewritten.
- **B.7**: COMPLETE + FROZEN.

---

## Known Issues

1. **Scoping hygiene**: running `pytest` from the repo root (not `backend/`) collects `pg-tmp/` (bundled SQLAlchemy/Alembic suites) and `_wip_m2_baseline/`, producing 21 collection ERRORs + 6 `integration` mark warnings. Not a code defect. `pyproject.toml` config (`testpaths=["tests"]`) already excludes them when run from `backend/`. Recommendation: add a root-level `pytest.ini`/CI alias so root invocation fails loud instead of silently collecting junk.

---

## B.8

**NOT STARTED.** Verification scope-limited to B.7 (§B.7 instruction). No B.8 work performed. Stopping here.
