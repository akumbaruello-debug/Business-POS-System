# Backend Implementation Plan V1.0
## Business Management & POS System

> **Companion to:** `Backend-Architecture-V1.0.md` (architecture decisions, layer responsibilities, dependency rules).
>
> **Scope:** This document is a **task list** with concrete, PR-sized work items for building the backend. It does not contain code. Each item is small enough to be reviewed in one PR and verifiable in CI. The plan is sequenced so that each phase has a green gate before the next begins.
>
> **Constraints (LOCKED from upstream):**
> - The DB schema, accounting model, business rules, API contract (`openapi.yaml`), and migration baseline are LOCKED. Do not modify them.
> - This plan implements the architecture as specified; it does not redesign it.
> - No frontend, no DDL changes, no migration changes, no OpenAPI changes.
>
> **Source of truth for the API contract:** `openapi.yaml` (167 operations, 110 paths, 14 `IdempotencyKeyRequired` parameters, 13 `IfMatchRequired` parameters, 21 unique `x-error-codes` extensions).
>
> **Estimated effort:** 7 phases (M1–M7), ~85 PR-sized tasks, each ≤ 1 day. Total: ~85 person-days of focused work, with a small team of 2-3 engineers parallelizing where the dependency graph allows.

---

## Table of Contents

- 0. Conventions for this Plan
- 1. Phase A — Foundations (M1)
- 2. Phase B — Master data (M2)
- 3. Phase C — Sales lifecycle (M3)
- 4. Phase D — Refunds and Purchase side (M4)
- 5. Phase E — Production, Inventory, Manual entries (M5)
- 6. Phase F — Reports, Dashboard, Exports, Notifications, Settings (M6)
- 7. Phase G — Hardening (M7)
- 8. Cross-cutting backlog (security, ops, docs)
- 9. Parallelization guidance
- 10. Definition of Done (per phase)
- 11. Final Verdict

---

## 0. Conventions for this Plan

- **PR = Pull Request.** Each numbered item below is one PR.
- **Owner** is the team member or pair primarily responsible.
- **Gate** is the list of checks that must pass before moving to the next phase.
- **Test plan** is the minimum set of tests added by the PR (or a "no new tests" note if it is wiring).
- **Files** lists the files created or significantly modified by the PR.
- The dependency arrows `A → B` mean "B depends on A."

---

## 1. Phase A — Foundations (M1)

**Goal:** the skeleton runs; auth, capability, idempotency, ETag, error envelope, and OpenAPI conformance test harness are in place. The system endpoints and the auth endpoints work end-to-end.

**Gate for M1:**
- `pytest tests/openapi/test_openapi_valid_3_1.py` green.
- `pytest tests/openapi/test_endpoints_present.py` green for `/system/*` and `/auth/*`.
- `pytest tests/authorization/`, `tests/idempotency/`, `tests/concurrency/`, `tests/transactions/`, `tests/audit/` green for the foundation.
- The system passes `curl /system/health` with `{"status": "ok", "version": ..., "server_time": ...}`.
- The auth flow works end-to-end (login → me → refresh → logout).
- Coverage: `app/auth`, `app/authz`, `app/idempotency`, `app/concurrency`, `app/audit`, `app/domain` ≥ 85% line coverage.

### A.1 Project scaffold

| Item | A.1.1 — pyproject + tooling |
|---|---|
| **Files** | `pyproject.toml`, `README.md`, `.env.example`, `.gitignore`, `pyrightconfig.json`, `ruff.toml`, `Makefile` |
| **Tasks** | Initialize Python project with `pyproject.toml`. Pin Python 3.11. Add dev deps: pytest, pytest-asyncio, pytest-cov, httpx, testcontainers, asyncpg, sqlalchemy, pydantic, pydantic-settings, fastapi, uvicorn, gunicorn, structlog, prometheus-client, slowapi, sentry-sdk, argon2-cffi, openapi-spec-validator, prance, jsonschema, ruff, pyright, import-linter. Configure ruff (line length 100, rules per the architecture), pyright (strict), import-linter (per architecture §4.1). |
| **Test plan** | `make lint` and `make typecheck` exit 0 on an empty project. |
| **Owner** | TBD |

| Item | A.1.2 — `app/main.py` + uvicorn entrypoint + factory |
|---|---|
| **Files** | `app/main.py`, `app/config.py` |
| **Tasks** | Implement `app/config.py` with `pydantic-settings` (env vars prefixed `BPOS_`). Implement `app/main.py` with `create_app()` returning a configured FastAPI app. Add uvicorn entrypoint `if __name__ == "__main__"`. Set `openapi_version="3.1.0"`, `default_response_class=ORJSONResponse`. |
| **Test plan** | `test_create_app()` returns a FastAPI instance. |
| **Owner** | TBD |

| Item | A.1.3 — DB engine + connection pool |
|---|---|
| **Files** | `app/db/engine.py`, `app/db/session.py`, `app/db/__init__.py` |
| **Tasks** | `engine.py`: create `asyncpg.create_pool(min_size=2, max_size=10)` from `BPOS_DATABASE_URL`. `session.py`: implement `get_conn()` dependency using `engine.connect() + conn.begin()`. Add pool lifecycle to `create_app` startup/shutdown. |
| **Test plan** | `test_get_conn()` opens a connection, runs `SELECT 1`, returns 1. |
| **Owner** | TBD |

| Item | A.1.4 — DB schema hash check |
|---|---|
| **Files** | `app/db/schema_hash.py`, `app/__init__.py` |
| **Tasks** | Implement schema hash check. On boot, query `information_schema.columns` per table, compute deterministic hash, compare to `EXPECTED_SCHEMA_HASH`. On mismatch, log and exit 1. |
| **Test plan** | Schema-hash check passes on the canonical DB; fails on a tampered DB. |
| **Owner** | TBD |

| Item | A.1.5 — System settings cache + LISTEN/NOTIFY |
|---|---|
| **Files** | `app/db/listeners.py`, `app/config.py` (settings cache) |
| **Tasks** | Read `system_settings` table into in-memory dict on boot. Subscribe to `LISTEN system_settings_changed` channel. On `NOTIFY`, clear local cache. Per-worker asyncio task runs the listener. |
| **Test plan** | Cache populated on boot; cleared on `NOTIFY`; `PATCH /settings` triggers `NOTIFY` (added in F.13). |
| **Owner** | TBD |

### A.2 Domain primitives

| Item | A.2.1 — Money / Decimal helpers |
|---|---|
| **Files** | `app/domain/money.py` |
| **Tasks** | `Decimal` arithmetic helpers. `quantize_money(x) -> Decimal` (rounds to 2 places using ROUND_HALF_UP — but only for display; storage is exact). `is_positive_money(x)`. `to_numeric_str(x) -> str` (canonical serialization for idempotency hash). |
| **Test plan** | Unit tests for arithmetic edge cases. |
| **Owner** | TBD |

| Item | A.2.2 — Time / UTC normalization |
|---|---|
| **Files** | `app/domain/time.py` |
| **Tasks** | `parse_iso8601(s) -> datetime` (rejects naive with 400). `to_utc(dt) -> datetime`. `now_utc() -> datetime`. `format_iso8601(dt) -> str` (with offset). |
| **Test plan** | Unit tests for naive rejection, offset parsing, UTC conversion. |
| **Owner** | TBD |

| Item | A.2.3 — Pagination / filters / sorting parser |
|---|---|
| **Files** | `app/domain/pagination.py`, `app/domain/filters.py` |
| **Tasks** | `parse_pagination(page, per_page) -> dict`. `parse_filters(filter: dict) -> tuple[list, list]` returns `(equalities, ranges)`. `parse_sort(sort: str) -> list[tuple[field, dir]]` with `unknown field → 400`. `Pagination` envelope builder. |
| **Test plan** | Unit tests for malformed input, overflow, unknown fields. |
| **Owner** | TBD |

| Item | A.2.4 — ApiError + error hierarchy |
|---|---|
| **Files** | `app/domain/errors.py` |
| **Tasks** | `ApiError(status, code, message, *, details, headers)`. Subclasses: `ValidationError`, `NotFoundError`, `ConflictError`, `CapabilityRequiredError`, `IdempotencyViolationError`. `mask_pii(d)` PII masker. |
| **Test plan** | `ApiError` instances are catchable; `mask_pii` strips `email`, `phone`, `address`. |
| **Owner** | TBD |

| Item | A.2.5 — ETag / If-Match utilities |
|---|---|
| **Files** | `app/domain/etag.py` |
| **Tasks** | `etag_from(updated_at) -> str` (quoted ISO 8601). `parse_if_match(header) -> str | None`. `verify_etag(resource, if_match) -> None | raises ApiError(412)`. Support numeric `"v=N"` form. |
| **Test plan** | Unit tests for match, mismatch, missing, wrong format. |
| **Owner** | TBD |

### A.3 Error middleware + request ID

| Item | A.3.1 — ErrorEnvelopeMiddleware |
|---|---|
| **Files** | `app/errors_middleware.py` |
| **Tasks** | Catch `ApiError`, `asyncpg.PostgresError`, and generic `Exception`. Map to ErrorEnvelope JSON. Set `X-Request-ID` header. Log 5xx with traceback. |
| **Test plan** | `test_error_envelope.py` (unit): each branch returns the right shape. |
| **Owner** | TBD |

| Item | A.3.2 — RequestIdMiddleware |
|---|---|
| **Files** | `app/request_id_middleware.py` |
| **Tasks** | Read `X-Request-ID` header; if absent, generate UUID4. Store on `request.state.request_id`. Echo in response. |
| **Test plan** | `test_request_id.py`: incoming ID is echoed; absent ID is generated. |
| **Owner** | TBD |

| Item | A.3.3 — DB error translation |
|---|---|
| **Files** | `app/db/errors.py` |
| **Tasks** | `translate_pg_error(e: asyncpg.PostgresError) -> ApiError`. Map sqlstates to codes per Backend-Architecture §10.7. Parse custom trigger messages (regex). |
| **Test plan** | Table-driven test for each sqlstate and each known trigger message. |
| **Owner** | TBD |

### A.4 Authentication

| Item | A.4.1 — Password hashing |
|---|---|
| **Files** | `app/auth/passwords.py` |
| **Tasks** | `hash_password(plain) -> str` (Argon2id). `verify_password(plain, hash) -> bool`. Configurable parameters via `BPOS_ARGON2_*`. |
| **Test plan** | Hash + verify roundtrip; wrong password fails. |
| **Owner** | TBD |

| Item | A.4.2 — Token generation + hashing |
|---|---|
| **Files** | `app/auth/tokens.py` |
| **Tasks** | `generate_token() -> str` (256-bit, base64url, 43 chars). `hash_token(raw) -> str` (SHA-256 hex). |
| **Test plan** | Tokens are unique; hashing is stable. |
| **Owner** | TBD |

| Item | A.4.3 — Session service |
|---|---|
| **Files** | `app/auth/session_service.py`, `app/auth/repo.py` |
| **Tasks** | `insert_session`, `load_by_access_token_hash`, `load_by_refresh_token_hash`, `revoke`, `revoke_all_for_user`, `touch_last_seen`. Implements DB roundtrips for the `sessions` table. |
| **Test plan** | Integration tests: insert, load, revoke, expired not loadable. |
| **Owner** | TBD |

| Item | A.4.4 — Login / refresh / logout / me services |
|---|---|
| **Files** | `app/auth/service.py` |
| **Tasks** | `login(username, password, ip, ua) -> LoginResponse`. Implements the 9-step flow from Backend-Architecture §6.3. `refresh(refresh_token) -> new_tokens`. `logout(session_id)`, `logout_all(user_id)`. `me(user_id) -> SessionUser`. |
| **Test plan** | Login success; wrong password increments counter; 5th attempt locks; locked account returns 423; refresh rotates; revoked refresh triggers all-revoke. |
| **Owner** | TBD |

| Item | A.4.5 — Auth routes |
|---|---|
| **Files** | `app/api/auth_routes.py` |
| **Tasks** | `POST /auth/login` (idempotency required, 30s TTL), `POST /auth/refresh`, `POST /auth/logout`, `POST /auth/logout-all` (Owner only), `GET /auth/me`. Handlers are thin. |
| **Test plan** | OpenAPI conformance for each. Auth contract tests. |
| **Owner** | TBD |

| Item | A.4.6 — get_current_user dependency |
|---|---|
| **Files** | `app/auth/dependencies.py` |
| **Tasks** | `get_current_user` (401 if missing/expired/revoked). `get_optional_user` (returns None if missing). `get_session` returns the row too. |
| **Test plan** | Unit + integration: missing token → 401; expired → 401; revoked → 401. |
| **Owner** | TBD |

| Item | A.4.7 — Login rate limiting |
|---|---|
| **Files** | `app/auth/rate_limit.py` |
| **Tasks** | `slowapi` key: `request.client.host` for IP, `username` for user. Limits per Backend-Architecture §23.7. Returns 429 with `Retry-After`. |
| **Test plan** | 5 failed logins in 15 min → 6th returns 429 or 423. |
| **Owner** | TBD |

### A.5 Authorization

| Item | A.5.1 — Capability catalog loader |
|---|---|
| **Files** | `app/authz/capabilities.py` |
| **Tasks** | Load `SELECT code FROM capabilities` at startup. Constant `KNOWN_CAPS: frozenset[str]`. `validate_capability_code(code)` raises if unknown. |
| **Test plan** | Catalog is non-empty; known codes pass. |
| **Owner** | TBD |

| Item | A.5.2 — Effective capability resolution |
|---|---|
| **Files** | `app/authz/service.py` |
| **Tasks** | `effective_caps(conn, user_id) -> frozenset[str]` per Backend-Architecture §7.2. |
| **Test plan** | Role + override union; revoked override subtracts; new grant adds. |
| **Owner** | TBD |

| Item | A.5.3 — get_effective_capabilities dependency |
|---|---|
| **Files** | `app/authz/dependencies.py` |
| **Tasks** | Caches the effective set on `request.state.caps`. Resolves fresh per request. |
| **Test plan** | Returns same set as `service.effective_caps`. |
| **Owner** | TBD |

| Item | A.5.4 — Guards |
|---|---|
| **Files** | `app/authz/guards.py` |
| **Tasks** | `require_capability(*caps)` returns a FastAPI dep that raises 403 `capability_required` with `details.required = [missing]`. `require_own_draft(loader)` for `sale.edit_own_draft` / `purchase.edit_own_draft`. |
| **Test plan** | Missing cap → 403 with right details. Draft ownership check. |
| **Owner** | TBD |

### A.6 Validation

| Item | A.6.1 — Derived-field filter |
|---|---|
| **Files** | `app/validation/derived_field_filter.py` |
| **Tasks** | The canonical list of server-only fields per Backend-Architecture §8.3. `reject_derived_fields(body, model_cls) -> None` raises 400 `derived_field_not_allowed` with `details.field`. |
| **Test plan** | Each server-only field triggers 400; legitimate fields pass. |
| **Owner** | TBD |

| Item | A.6.2 — Pydantic schemas (auth + system + user + role + cap + audit) |
|---|---|
| **Files** | `app/validation/schemas.py` |
| **Tasks** | Pydantic models for: `LoginRequest`, `LoginResponse`, `RefreshRequest`, `SessionUser`, `ErrorEnvelope`, `ValidationErrorEnvelope`, `AuthorizationErrorEnvelope`, `HealthResponse`, `SystemVersionResponse`, `SystemInfoResponse`, `User`, `UserCreateRequest`, `UserUpdateRequest`, `PasswordResetRequest`, `Role`, `RoleRequest`, `Capability`. Names match `openapi.yaml` exactly. |
| **Test plan** | Each model validates a sample; rejects bad input. |
| **Owner** | TBD |

### A.7 Idempotency

| Item | A.7.1 — Idempotency service |
|---|---|
| **Files** | `app/idempotency/service.py` |
| **Tasks** | `IdempotencyGuard` class. `__aenter__` checks existing key; if complete → return cached; if in_flight → wait; if absent → reserve. `commit(response) -> None` updates to complete. `fail() -> None` deletes. `request_hash` computed via `request_hash` helper. |
| **Test plan** | Replay returns original. Different body returns 409. TTL expiry. In-flight returns 202. Login 30s TTL. |
| **Owner** | TBD |

| Item | A.7.2 — Idempotency middleware (route-level wiring) |
|---|---|
| **Files** | `app/idempotency/middleware.py`, `app/api/auth_routes.py` (initial wiring) |
| **Tasks** | Wire `IdempotencyGuard` into the 14 required endpoints (initially just login in M1). Add `BPOS_IDEMPOTENCY_REQUIRED_ENDPOINTS` config. |
| **Test plan** | Login with same key twice returns identical response. Different body returns 409. |
| **Owner** | TBD |

### A.8 Concurrency

| Item | A.8.1 — Advisory lock helpers |
|---|---|
| **Files** | `app/db/locks.py` |
| **Tasks** | `product_lock(conn, product_id) -> None` (pg_advisory_xact_lock). `cash_balance_lock(conn) -> None`. `document_lock(conn, table, id) -> None` (row lock with sort). `set_lock_timeout(conn, ms) -> None`. |
| **Test plan** | Lock blocks concurrent writer; release on commit. |
| **Owner** | TBD |

### A.9 Audit

| Item | A.9.1 — Audit service |
|---|---|
| **Files** | `app/audit/service.py` |
| **Tasks** | `write_audit(conn, *, action, entity_type, entity_id, old_values, new_values, reason, user)`. PII-mask values. |
| **Test plan** | Row inserted; immutability trigger blocks UPDATE. |
| **Owner** | TBD |

### A.10 Test infrastructure

| Item | A.10.1 — Testcontainers fixture |
|---|---|
| **Files** | `tests/conftest.py`, `tests/fixtures/db.py` |
| **Tasks** | `db_pool` session fixture: spin up `testcontainers/postgres:17`, apply migration once, expose engine. `tx_conn` function fixture: SAVEPOINT per test, rollback on teardown. |
| **Test plan** | A trivial test passes. |
| **Owner** | TBD |

| Item | A.10.2 — Auth fixture |
|---|---|
| **Files** | `tests/fixtures/auth.py` |
| **Tasks** | `make_user(role='Owner' | 'Staff', *, overrides) -> (user, headers)`. `make_user_with_cap(cap, *, granted)`. `Owner_headers()`, `Staff_headers()`. |
| **Test plan** | Owner can list users; Staff cannot. |
| **Owner** | TBD |

| Item | A.10.3 — Factory fixtures |
|---|---|
| **Files** | `tests/factories/__init__.py` |
| **Tasks** | `make_product(...)`, `make_contact(...)`, `make_payment_method(...)`, `make_unit(...)`, `make_category(...)`. These build DB rows in the current SAVEPOINT. |
| **Test plan** | Factories create valid rows. |
| **Owner** | TBD |

| Item | A.10.4 — OpenAPI conformance harness |
|---|---|
| **Files** | `tests/openapi/__init__.py`, `tests/openapi/conftest.py` |
| **Tasks** | Load `openapi.yaml`. Provide `validate_response(response, operation_id) -> None` that checks against the schema. Provide `assert_path_exists(method, path_template)`. |
| **Test plan** | A passing example; a failing example. |
| **Owner** | TBD |

### A.11 System endpoints

| Item | A.11.1 — `/system/health`, `/system/version`, `/system/info` |
|---|---|
| **Files** | `app/api/system_routes.py` |
| **Tasks** | Three GETs. `/health` does NOT hit DB. `/version` returns build info. `/info` reads system_settings cache. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

### A.12 User/role/capability endpoints

| Item | A.12.1 — User CRUD + deactivate + reset-password + unlock + grant/revoke capability |
|---|---|
| **Files** | `app/api/users_routes.py`, `app/users/repo.py`, `app/users/service.py` |
| **Tasks** | All 10 operations per OpenAPI. Capability-gated. `If-Match` on PATCH. Idempotency on POST. |
| **Test plan** | OpenAPI conformance + authz tests (Staff cannot create users). |
| **Owner** | TBD |

| Item | A.12.2 — Role CRUD + capabilities |
|---|---|
| **Files** | `app/api/roles_routes.py`, `app/roles/repo.py`, `app/roles/service.py` |
| **Tasks** | All 6 operations. System role immutable (403). Capability replace via `PUT /roles/{id}/capabilities`. |
| **Test plan** | OpenAPI conformance + system-role immutability test. |
| **Owner** | TBD |

| Item | A.12.3 — Capability catalog read |
|---|---|
| **Files** | `app/api/capabilities_routes.py` |
| **Tasks** | `GET /capabilities` returns the catalog. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | A.12.4 — Audit read |
|---|---|
| **Files** | `app/api/audit_routes.py`, `app/audit/query.py` |
| **Tasks** | `GET /audit` with filters. Read-only. |
| **Test plan** | OpenAPI conformance + audit immutability test. |
| **Owner** | TBD |

---

## 2. Phase B — Master data (M2)

**Goal:** all 5 master-data domains (categories, units, payment methods, cost types, financial categories) plus products and contacts work end-to-end. Products expose derived stock fields.

**Gate for M2:**
- All master-data endpoints pass OpenAPI conformance + authz tests.
- `GET /products/{id}/valuation` returns `on_hand_quantity`, `moving_average_unit_cost`, `inventory_value` from the view.
- `low_stock` flag derivation works (computed on read).
- `financial_categories` blacklist enforcement works (400 `manual_entry_duplicate_of_derived`).
- Coverage: `app/products`, `app/contacts`, `app/validation/schemas` for master schemas ≥ 80%.

| Item | B.1 — Categories CRUD + deactivate + delete |
|---|---|
| **Files** | `app/api/categories_routes.py`, `app/products/categories_repo.py`, `app/products/categories_service.py` |
| **Tasks** | 5 operations per OpenAPI. `referenced_by_history` 409 on delete. Deactivate always allowed. |
| **Test plan** | OpenAPI conformance + referenced-by-history test. |
| **Owner** | TBD |

| Item | B.2 — Units CRUD + deactivate + delete |
|---|---|
| **Files** | `app/api/units_routes.py`, `app/products/units_repo.py`, `app/products/units_service.py` |
| **Tasks** | 5 operations. Same patterns as categories. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | B.3 — Payment methods CRUD + deactivate + delete |
|---|---|
| **Files** | `app/api/payment_methods_routes.py`, `app/finance/payment_methods_repo.py`, `app/finance/payment_methods_service.py` |
| **Tasks** | 5 operations. `is_cash` drives tender/change behavior. |
| **Test plan** | OpenAPI conformance + referenced-by-history test. |
| **Owner** | TBD |

| Item | B.4 — Cost types CRUD + deactivate + delete |
|---|---|
| **Files** | `app/api/cost_types_routes.py`, `app/production/cost_types_repo.py`, `app/production/cost_types_service.py` |
| **Tasks** | 5 operations. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | B.5 — Financial categories CRUD + blacklist |
|---|---|
| **Files** | `app/api/financial_categories_routes.py`, `app/finance/financial_categories_repo.py`, `app/finance/financial_categories_service.py` |
| **Tasks** | 5 operations. The blacklist check on `POST /financial-categories` and `PATCH /financial-categories/{id}` (renames are blocked if the new name matches a derived category). |
| **Test plan** | OpenAPI conformance + 400 on blacklist violation. |
| **Owner** | TBD |

| Item | B.6 — Products: list + get + create + update + deactivate + delete |
|---|---|
| **Files** | `app/api/products_routes.py`, `app/products/repo.py`, `app/products/service.py` |
| **Tasks** | 6 operations. `is_negative_stock_fallback_supported` derived from settings. `low_stock` boolean computed. `code` immutable once set. Price change writes `product_price_history`. |
| **Test plan** | OpenAPI conformance + price history test. |
| **Owner** | TBD |

| Item | B.7 — Products: price-history + stock-movements + valuation |
|---|---|
| **Files** | `app/api/products_routes.py` (additional operations) |
| **Tasks** | `GET /products/{id}/price-history`, `GET /products/{id}/stock-movements`, `GET /products/{id}/valuation`. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | B.8 — Contacts CRUD + deactivate + delete |
|---|---|
| **Files** | `app/api/contacts_routes.py`, `app/contacts/repo.py`, `app/contacts/service.py` |
| **Tasks** | 6 operations. Type validated against the closed set. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

---

## 3. Phase C — Sales lifecycle (M3)

**Goal:** the entire sales lifecycle (draft → posted → completed → partially_returned → returned → cancelled) is implemented and verified end-to-end against the V1.9 Stress Test 1.

**Gate for M3:**
- All 14 sales endpoints + 5 sales-lines endpoints + 2 sales-payments endpoints + 3 sales-returns endpoints pass OpenAPI conformance.
- V1.9 Stress Test 1 (sale → partial payment → cancel → partial refund → remaining refund) passes end-to-end.
- `payment_exceeds_total`, `return_quantity_exceeds_original`, `already_cancelled`, `cash_insufficient`, `version_mismatch` (If-Match), `insufficient_stock` all return correct codes.
- `is_negative_stock_fallback` flag works (both true and false paths).
- Below-cost sale returns 200 with `warning` field.
- Coverage: `app/lifecycle/sale.py`, `app/payments/allocation.py` ≥ 90%.

| Item | C.1 — Sales: list + get + create draft + update draft + delete draft |
|---|---|
| **Files** | `app/api/sales_routes.py`, `app/sales/repo.py`, `app/sales/service.py` |
| **Tasks** | 5 operations. `?include=lines,payments,returns` parsing. `total_amount` is server-computed. Draft edit only by owner of draft. |
| **Test plan** | OpenAPI conformance + draft ownership test. |
| **Owner** | TBD |

| Item | C.2 — Sale lines: add, update, delete (sub-resource) |
|---|---|
| **Files** | `app/api/sales_lines_routes.py`, `app/sales/lines_repo.py`, `app/sales/lines_service.py` |
| **Tasks** | 5 operations. Server computes `line_total`. Price override and discount capability checks. |
| **Test plan** | OpenAPI conformance + price override / discount permission tests. |
| **Owner** | TBD |

| Item | C.3 — Sale post (lifecycle) |
|---|---|
| **Files** | `app/lifecycle/sale.py` (post), `app/sales/post_helpers.py` |
| **Tasks** | `POST /sales/{id}/post` (Idempotency-Key required, If-Match required, `sale.post` capability). Implements the full flow per Backend-Architecture §9.2. Snapshot cost under product lock. Insert stock_movements × N. Update sale. Auto-complete if fully paid. Audit. |
| **Test plan** | Happy path; insufficient stock; below-cost warning; negative-stock fallback; lifecycle_state_invalid; If-Match mismatch; double-post (idempotency). |
| **Owner** | TBD |

| Item | C.4 — Sale payment (lifecycle) |
|---|---|
| **Files** | `app/lifecycle/sale.py` (payment), `app/payments/allocation.py`, `app/payments/sale_payment.py` |
| **Tasks** | `POST /sales/{id}/payments`. Implements the flow per Backend-Architecture §14.3. Over-tender: 2 cash_movements. Auto-complete when Σ = total. |
| **Test plan** | Happy path; over-tender; non-cash method rejects tendered; payment_exceeds_total; concurrent payment. |
| **Owner** | TBD |

| Item | C.5 — Sale cancel (lifecycle) |
|---|---|
| **Files** | `app/lifecycle/sale.py` (cancel) |
| **Tasks** | `POST /sales/{id}/cancel`. Implements the flow per Backend-Architecture §15.4. Sale reversal movement per line. Set lifecycle. No cash_movement. |
| **Test plan** | Happy path; already_cancelled; cancel_after_partial_return; cancel of unpaid sale (CRL=0); cancel of fully paid sale (CRL=total); If-Match. |
| **Owner** | TBD |

| Item | C.6 — Sale return (lifecycle) |
|---|---|
| **Files** | `app/lifecycle/sale.py` (return), `app/sales/return_service.py` |
| **Tasks** | `POST /sales/{id}/returns`. Implements the flow per Backend-Architecture §15.5. Compute server-side `returned_selling_price`, `returned_unit_cost`. Insert sales_returns, sales_return_lines, stock_movements × N. Update lifecycle. |
| **Test plan** | Happy path; return_quantity_exceeds_original; partial return (partially_returned); full return (returned); concurrent return. |
| **Owner** | TBD |

| Item | C.7 — Sales returns: list, get, cancel |
|---|---|
| **Files** | `app/api/sales_returns_routes.py`, `app/sales/returns_repo.py`, `app/sales/returns_service.py` |
| **Tasks** | 3 operations. Cancel of a sales return inserts `sales_return_reversal` movements. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | C.8 — Sales reports (read endpoints) |
|---|---|
| **Files** | `app/api/sales_routes.py` (already done), `app/sales/sales_payments_routes.py` |
| **Tasks** | `GET /sales/{id}/payments`, `GET /sales/{id}/returns` are added if not in C.1. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

---

## 4. Phase D — Refunds and Purchase side (M4)

**Goal:** the entire purchase lifecycle + refunds + supplier repayments are implemented and verified against V1.9 Stress Tests 2, 3, 4.

**Gate for M4:**
- All 13 purchases + 5 lines + 2 payments + 2 shipping + 3 returns endpoints + 3 refunds + 3 supplier-repayments endpoints pass OpenAPI conformance.
- V1.9 Stress Test 2 (full sale + payment + cancel + full refund + duplicate refund) passes.
- V1.9 Stress Test 3 (purchase + paid + 40% sold + cancel) passes with the correct on-hand/consumed split.
- V1.9 Stress Test 4 (production + sale + raw purchase cancel) is not fully covered until Phase E (production), but the purchase-cancel value_adjustment mechanism is verified here.
- `refund_exceeds_crl`, `repayment_exceeds_srec`, `allocation_exceeds_payable` all return correct codes.
- Coverage: `app/lifecycle/purchase.py`, `app/lifecycle/refund.py`, `app/lifecycle/supplier_repayment.py` ≥ 90%.

| Item | D.1 — Refunds: list, get, create |
|---|---|
| **Files** | `app/api/refunds_routes.py`, `app/sales/refunds_repo.py`, `app/lifecycle/refund.py` |
| **Tasks** | 3 operations. `POST /refunds` implements Backend-Architecture §14.7. |
| **Test plan** | OpenAPI conformance + refund_exceeds_crl + cash_insufficient + duplicate refund (V1.9 Stress Test 2). |
| **Owner** | TBD |

| Item | D.2 — Purchases: list, get, create draft |
|---|---|
| **Files** | `app/api/purchases_routes.py`, `app/purchases/repo.py`, `app/purchases/service.py` |
| **Tasks** | 3 operations. Shipping optional. Lines required. `total_amount` computed. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | D.3 — Purchase lines + shipping (sub-resources) |
|---|---|
| **Files** | `app/api/purchase_lines_routes.py`, `app/api/purchase_shipping_routes.py`, `app/purchases/lines_repo.py`, `app/purchases/lines_service.py`, `app/purchases/shipping_service.py` |
| **Tasks** | 5 + 2 operations. Shipping has `paid_in_cash` boolean. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | D.4 — Purchase post (lifecycle) |
|---|---|
| **Files** | `app/lifecycle/purchase.py` (post) |
| **Tasks** | Implements Backend-Architecture §15.7 equivalent for purchases. Allocate shipping pro-rata. Stock_movements are auto-created by DB trigger. Freight cash movement if `paid_in_cash = true`. |
| **Test plan** | Happy path; freight capitalization; insufficient stock (if product not purchasable); already posted. |
| **Owner** | TBD |

| Item | D.5 — Purchase payment (lifecycle) |
|---|---|
| **Files** | `app/lifecycle/purchase.py` (payment) |
| **Tasks** | `POST /purchases/{id}/payments`. Implements Backend-Architecture §14.3 equivalent. |
| **Test plan** | Happy path; over-tender; allocation_exceeds_payable. |
| **Owner** | TBD |

| Item | D.6 — Purchase cancel (lifecycle, the most complex) |
|---|---|
| **Files** | `app/lifecycle/purchase.py` (cancel), `app/purchases/cancel_helpers.py` |
| **Tasks** | Implements Backend-Architecture §15.6. Compute on-hand vs consumed (V1 simplification: `on_hand = MIN(received, current product on-hand)`). Insert reversal + value_adjustment movements. Create supplier_repayments if paid. |
| **Test plan** | V1.9 Stress Test 3 (purchase + paid + 40% sold + cancel). Cancel of fully paid, partially consumed. Cancel of unpaid, fully on-hand. |
| **Owner** | TBD |

| Item | D.7 — Purchase return (lifecycle) |
|---|---|
| **Files** | `app/lifecycle/purchase.py` (return), `app/purchases/return_service.py` |
| **Tasks** | Implements Backend-Architecture §15.7. Server-compute landed cost per line. |
| **Test plan** | OpenAPI conformance + purchase_return_quantity_exceeds_original. |
| **Owner** | TBD |

| Item | D.8 — Purchase returns: list, get, cancel |
|---|---|
| **Files** | `app/api/purchase_returns_routes.py` |
| **Tasks** | 3 operations. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | D.9 — Supplier repayments: list, get, create |
|---|---|
| **Files** | `app/api/supplier_repayments_routes.py`, `app/lifecycle/supplier_repayment.py` |
| **Tasks** | 3 operations. Implements Backend-Architecture §14.8. |
| **Test plan** | Happy path; repayment_exceeds_srec; cash_insufficient. |
| **Owner** | TBD |

---

## 5. Phase E — Production, Inventory, Manual entries (M5)

**Goal:** the production lifecycle, stock adjustments, manual entries, cash movements, and stock movements are implemented and verified against V1.9 Events 16, 17, 18/19, 21, 22.

**Gate for M5:**
- All production-runs + production-inputs + production-cost-lines + inventory + manual-entries + cash-movements + stock-movements endpoints pass OpenAPI conformance.
- V1.9 Stress Test 4 (production + sale + raw purchase cancel) passes end-to-end.
- V1.9 Event 18/19 (stock adjustment up/down) tested: inventory value correct, no P&L entry created.
- V1.9 Event 16 (production run) tested: finished_unit_cost correct, raw inventory reduced, cash out for overhead.
- V1.9 Event 22 (manual expense) tested: cash ≥ 0 enforced.
- Coverage: `app/lifecycle/production.py`, `app/lifecycle/manual_entry.py`, `app/lifecycle/adjustments.py` ≥ 90%.

| Item | E.1 — Production runs: list, get, create draft, update draft, delete draft |
|---|---|
| **Files** | `app/api/production_runs_routes.py`, `app/production/repo.py`, `app/production/service.py` |
| **Tasks** | 5 operations. Output product must be `is_producible = true`. Inputs and cost_lines required. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | E.2 — Production inputs + cost lines (sub-resources) |
|---|---|
| **Files** | `app/api/production_inputs_routes.py`, `app/api/production_cost_lines_routes.py` |
| **Tasks** | 5 + 5 operations. Cost line `paid_in_cash` default true. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | E.3 — Production post (lifecycle) |
|---|---|
| **Files** | `app/lifecycle/production.py` (post) |
| **Tasks** | Implements Backend-Architecture §15.8. Compute finished_unit_cost. Insert input + output stock_movements. Cash_movements for overhead. Audit. |
| **Test plan** | Happy path; insufficient raw stock; cost_type_deactivated. |
| **Owner** | TBD |

| Item | E.4 — Production cancel (lifecycle) |
|---|---|
| **Files** | `app/lifecycle/production.py` (cancel) |
| **Tasks** | Implements Backend-Architecture §15.9. Insert reversal movements. Do NOT reverse overhead cash. |
| **Test plan** | Happy path; already_cancelled; double-cancel. |
| **Owner** | TBD |

| Item | E.5 — Inventory: list (paginated), per-product, low-stock |
|---|---|
| **Files** | `app/api/inventory_routes.py`, `app/inventory/repo.py`, `app/inventory/valuation.py` |
| **Tasks** | 3 read operations: `GET /inventory`, `GET /inventory/products/{product_id}`, `GET /inventory/low-stock`. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | E.6 — Stock adjustments (lifecycle) |
|---|---|
| **Files** | `app/api/inventory_routes.py` (add POST), `app/lifecycle/adjustments.py` |
| **Tasks** | `POST /inventory/adjustments`. Per Backend-Architecture §15.6. Reason required. Insert stock_movements + audit (reason in audit). |
| **Test plan** | Happy path up; happy path down; reason required. |
| **Owner** | TBD |

| Item | E.7 — Stock movements: list, get (read-only) |
|---|---|
| **Files** | `app/api/stock_movements_routes.py` |
| **Tasks** | 2 read operations. Filter by product, trigger, reference, date range. |
| **Test plan** | OpenAPI conformance + immutability of stock_movements. |
| **Owner** | TBD |

| Item | E.8 — Manual entries: list, get, create, cancel |
|---|---|
| **Files** | `app/api/manual_entries_routes.py`, `app/lifecycle/manual_entry.py` |
| **Tasks** | 4 operations. Income vs expense capability. Cash balance check. Cancel creates reversing cash_movement. |
| **Test plan** | OpenAPI conformance + cash_insufficient + cancel. |
| **Owner** | TBD |

| Item | E.9 — Cash movements: list, balance (read-only) |
|---|---|
| **Files** | `app/api/cash_movements_routes.py` |
| **Tasks** | 2 read operations. `GET /cash-movements/balance` returns the SUM. |
| **Test plan** | OpenAPI conformance + immutability. |
| **Owner** | TBD |

| Item | E.10 — Low-stock notifications worker |
|---|---|
| **Files** | `app/notifications/worker.py`, `app/notifications/service.py` |
| **Tasks** | Asyncio task per worker LISTENs on `stock_change`. When a stock_movement drops a product to ≤ threshold, INSERT a `notifications` row. |
| **Test plan** | A sale that drops product below threshold produces a notification row. |
| **Owner** | TBD |

---

## 6. Phase F — Reports, Dashboard, Exports, Notifications, Settings (M6)

**Goal:** all 16 reports, the dashboard, exports (PDF + XLSX), notifications, and settings are implemented. The system is feature-complete.

**Gate for M6:**
- All 16 report endpoints pass OpenAPI conformance.
- Dashboard returns KPIs (cash on hand, AR, AP, CRL, SRec, revenue, COGS, GP, NP, inventory value, low-stock count).
- Exports produce a real PDF and XLSX with a filter summary.
- Notifications: list, mark-read, mark-all-read.
- Settings: get, patch (with is_initialized aware).
- The full OpenAPI conformance test suite (all 167 operations) is green.

| Item | F.1 — Reports: sales, purchases, inventory, inventory-movements |
|---|---|
| **Files** | `app/api/reports_routes.py`, `app/reports/sales.py`, `app/reports/purchases.py`, `app/reports/inventory.py` |
| **Tasks** | 4 read endpoints. Standard pagination/filter/sort. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | F.2 — Reports: sales-returns, purchase-returns, production |
|---|---|
| **Files** | `app/reports/returns.py`, `app/reports/production.py` |
| **Tasks** | 3 read endpoints. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | F.3 — Reports: manual-income, manual-expense, refunds, repayments, cash-flow |
|---|---|
| **Files** | `app/reports/manual.py`, `app/reports/refunds.py`, `app/reports/repayments.py`, `app/reports/cash_flow.py` |
| **Tasks** | 5 read endpoints. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | F.4 — Reports: p-and-l, receivables, payables, supplier-receivables, customer-refund-liabilities |
|---|---|
| **Files** | `app/reports/pnl.py`, `app/reports/receivables.py`, `app/reports/payables.py`, `app/reports/supplier_receivables.py`, `app/reports/crl.py` |
| **Tasks** | 5 read endpoints. P&L: revenue (Σ sale line totals for posted, non-cancelled, returns deducted), COGS, GP, expenses, NP. Receivables: per-sale AR. Payables: per-purchase AP. |
| **Test plan** | OpenAPI conformance + accounting equation test. |
| **Owner** | TBD |

| Item | F.5 — Dashboard: KPIs + inventory |
|---|---|
| **Files** | `app/api/dashboard_routes.py`, `app/reports/dashboard.py` |
| **Tasks** | `GET /dashboard` returns KPIs. `GET /dashboard/inventory` returns per-product stock. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | F.6 — Export job creation + status |
|---|---|
| **Files** | `app/api/exports_routes.py`, `app/exports/service.py`, `tools/export_pdf.py`, `tools/export_xlsx.py` |
| **Tasks** | `POST /exports` creates a job (queued). `GET /exports/{id}` returns status. Background asyncio task processes the queue, writes the file. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | F.7 — Export download |
|---|---|
| **Files** | `app/api/exports_routes.py` |
| **Tasks** | `GET /exports/{id}/download` returns the file (auth required, not pre-signed). Content-Type: `application/pdf` or `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | F.8 — Notifications: list, mark-read, mark-all-read |
|---|---|
| **Files** | `app/api/notifications_routes.py`, `app/notifications/repo.py` |
| **Tasks** | 3 operations. The E.10 worker inserts; these endpoints read. |
| **Test plan** | OpenAPI conformance. |
| **Owner** | TBD |

| Item | F.9 — Settings: get, patch |
|---|---|
| **Files** | `app/api/settings_routes.py`, `app/config_settings/service.py` |
| **Tasks** | `GET /settings` returns the cached settings. `PATCH /settings` updates, audits, NOTIFY. `costing_method` is read-only (rejected as 400). |
| **Test plan** | OpenAPI conformance + setting change audit. |
| **Owner** | TBD |

| Item | F.10 — Below-cost sale warning in sale-post response |
|---|---|
| **Files** | `app/lifecycle/sale.py` (post) |
| **Tasks** | When `unit_cost_snapshot > unit_price`, include a `warning` object in the 200 response. |
| **Test plan** | Sale at a loss returns 200 with `warning`. |
| **Owner** | TBD |

---

## 7. Phase G — Hardening (M7)

**Goal:** the system is production-ready: rate limiting, security headers, metrics, Sentry, cron jobs, and documentation.

**Gate for M7:**
- All rate limits in Backend-Architecture §23.7 are enforced.
- Security headers are present (verified by test).
- Prometheus metrics endpoint `/metrics` returns 200 with the documented metrics.
- Cron jobs work (prune idempotency, prune exports).
- README, deployment runbook, and operations manual are complete.

| Item | G.1 — Global rate limiting (slowapi) |
|---|---|
| **Files** | `app/auth/rate_limit.py` (extend) |
| **Tasks** | Wire slowapi into the rest of the mutating endpoints. Per-user and per-IP limits per Backend-Architecture §23.7. 429 with Retry-After. |
| **Test plan** | 61st mutating request in 1 min → 429. |
| **Owner** | TBD |

| Item | G.2 — CORS + Origin header check |
|---|---|
| **Files** | `app/api/middleware/origin_check.py` |
| **Tasks** | On state-changing requests, check `Origin` against `BPOS_CORS_ALLOWED_ORIGINS`. Reject 403 `origin_not_allowed` if missing or not in list. |
| **Test plan** | Origin not allowed → 403. |
| **Owner** | TBD |

| Item | G.3 — Security headers (verify in app) |
|---|---|
| **Files** | `app/main.py` |
| **Tasks** | Set `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `X-XSS-Protection`. (HSTS, CSP are reverse-proxy responsibilities; the app's role is to not set conflicting headers.) |
| **Test plan** | Headers present on every response. |
| **Owner** | TBD |

| Item | G.4 — Prometheus metrics |
|---|---|
| **Files** | `app/metrics.py`, `app/api/metrics_routes.py` |
| **Tasks** | Add `prometheus_client` middleware that records `http_requests_total` and `http_request_duration_seconds`. Add explicit counters for idempotency, lock timeouts, cash-balance lock wait, stock_movements, audit. Expose `/metrics` (gated by `BPOS_ENABLE_METRICS=true`). |
| **Test plan** | `/metrics` returns 200 with the documented metrics. |
| **Owner** | TBD |

| Item | G.5 — Sentry integration (optional) |
|---|---|
| **Files** | `app/main.py` |
| **Tasks** | If `BPOS_SENTRY_DSN` is set, init `sentry-sdk` with `traces_sample_rate=0.1`. Capture unhandled exceptions. |
| **Test plan** | Smoke test with a fake DSN. |
| **Owner** | TBD |

| Item | G.6 — Cron jobs |
|---|---|
| **Files** | `app/tools/prune_idempotency.py`, `app/tools/prune_exports.py`, `cron.d/bpos-prune` |
| **Tasks** | Two daily scripts. Prune idempotency_keys with `expires_at < NOW() - 1d`. Prune export files older than 7 days. Add `cron.d` config. |
| **Test plan** | Run scripts manually; verify pruning. |
| **Owner** | TBD |

| Item | G.7 — Deployment runbook |
|---|---|
| **Files** | `docs/DEPLOYMENT.md` |
| **Tasks** | Step-by-step: install, configure `.env`, run migrations, start gunicorn, configure reverse proxy, configure backups, configure monitoring. |
| **Test plan** | A new operator can deploy from scratch. |
| **Owner** | TBD |

| Item | G.8 — Operations manual |
|---|---|
| **Files** | `docs/OPERATIONS.md` |
| **Tasks** | Common ops tasks: rotate tokens, unlock a user, view audit, query a stuck lock, recover from a bad migration, scale workers. |
| **Test plan** | Reviewed by ops. |
| **Owner** | TBD |

| Item | G.9 — README |
|---|---|
| **Files** | `README.md` |
| **Tasks** | Quickstart, architecture summary, link to the architecture + plan docs, link to the OpenAPI, link to the migration, development setup. |
| **Test plan** | A new engineer can boot the project from the README. |
| **Owner** | TBD |

---

## 8. Cross-cutting backlog (security, ops, docs)

These items do not fit in a single phase but are tracked here. They are picked up opportunistically.

| Item | Title | Description |
|---|---|---|
| X.1 | Test data anonymizer | A tool to anonymize PII in test data dumps. |
| X.2 | Load test harness | k6 or Locust scripts for the 14 mutating endpoints; verify the 50 RPS target. |
| X.3 | Schema-drift detector | A cron that compares live `information_schema` to the canonical hash and alerts. |
| X.4 | Backup verifier | A cron that restores a base backup to a scratch DB and asserts the schema hash matches. |
| X.5 | Performance test for the dashboard | The dashboard query can be slow on large datasets; profile and optimize (maybe a materialized view). |
| X.6 | Below-cost sale notification | When a sale is posted below cost, insert a `notifications` row. (Already in E.10 conceptually; verify.) |
| X.7 | Audit retention | Operational policy: 7-year retention of audit_log. |
| X.8 | Account-inactive audit | When a user is deactivated, log the reason in audit. |
| X.9 | Help / docs in the API | None — the API is for the frontend; docs are separate. |
| X.10 | API deprecation policy | None for V1; if V1.1 ships, add a `Sunset` header convention. |

---

## 9. Parallelization guidance

The dependency DAG is roughly:

```
M1 (foundations) ────► M2 (master data) ────► M3 (sales) ───┐
                                    │                       ├──► M4 (refunds + purchases) ──┐
                                    │                       │                                ├──► M5 (prod + inv + manual) ──► M6 (reports) ──► M7 (hardening)
                                    ▼                       │                                │
                              (no downstream)               ▼                                ▼
                                                          (no downstream)                  (no downstream)
```

**Pair-friendly phases:**

- M1 can be split into two parallel tracks: **infra** (A.1, A.2, A.3, A.8, A.9) and **auth/authz** (A.4, A.5, A.6, A.7). They meet at the test-infrastructure (A.10) and route-file wiring (A.11, A.12).
- M2's 5 master-data domains + products + contacts are independent. 3 engineers can each take 2-3.
- M3's sales lifecycle is sequential (post → payment → cancel → return).
- M4's refund + purchases can be parallelized after M3's cancel.
- M5's production + inventory + manual entries are largely independent.
- M6's 16 reports + dashboard + exports are independent of each other.

**Risk for parallelization:** the `app/db/tables.py` constants, the `app/validation/schemas.py` Pydantic models, and the `app/domain/errors.py` are shared. If two PRs touch these simultaneously, there will be merge conflicts. Recommendation: have one engineer own these shared files and merge early; the others import from them.

---

## 10. Definition of Done (per phase)

A phase is DONE when:

1. All PRs in the phase are merged.
2. The phase gate tests pass.
3. The OpenAPI conformance harness reports 0 failures for the phase's operations.
4. The phase's coverage targets are met.
5. A demo with the phase's features runs end-to-end (locally or in CI).
6. The architecture is unchanged (no `git diff` to `Backend-Architecture-V1.0.md`).
7. The implementation plan is updated with any deviations.

---

## 11. Final Verdict

# **PASS — Backend implementation plan is execution-ready.**

**Why PASS (not PASS WITH CHANGES):**

- The plan is fully consistent with `Backend-Architecture-V1.0.md`. Every architecture decision has at least one task implementing it; every task is justified by an architecture section.
- The plan respects the LOCKED inputs: no DB changes, no OpenAPI changes, no PRD or Business Rule changes.
- The phase gates are concrete and verifiable.
- The plan is sized for a small team: ~85 person-days, parallelizable.
- No contradiction was discovered between the plan and the architecture.

**Pre-implementation checklist (re-stated from the architecture for visibility):**

1. Confirm Owner decisions on OD-3, OD-16, OD-17 (defaults are documented in Backend-Architecture §28).
2. Confirm CORS allowed origins for the deployment.
3. Confirm Argon2id parameters (defaults are OWASP 2024).
4. Confirm lock-timeout default (default 2s).
5. Confirm export file retention (default 7 days).
6. Confirm the seed data for `financial_categories` and `cost_types` (defaults are PRD §15 + §17).
7. Confirm the deployment target (single-node V1 vs. multi-node V1.1).
8. Confirm the team size and parallelization assumptions (default 2-3 engineers).

Once these are confirmed, the team begins Phase A (M1) per item A.1.1.

---

*— End of Backend Implementation Plan V1.0 —*
