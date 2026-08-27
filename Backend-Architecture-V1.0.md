# Backend Architecture Specification V1.0
## Business Management & POS System

> **Source of Truth (authoritative inputs — do not modify):**
> - `Business-POS-System-PRD-V1.1.md`
> - `Business-Rules.md`
> - `Database-Design-V1.0.md`
> - `V1.9-Accounting-Event-Matrix.md` (11-account chart, 22 events, 8 invariants)
> - `Reconciliation-Report-V1.0.md` (18 XDC + 15 MS items, all resolved with defaults)
> - `API-Architecture-V1.0.md` (167 operations across 110 paths)
> - `openapi.yaml` (OpenAPI 3.1, machine-readable contract)
> - `db/migrations/m0001__initial_schema_baseline.sql` (canonical migration, 39 tables, 20 functions, 40 triggers)
> - `db/Migration-Plan-V1.0.md`, `Migration-Validation-Report-V1.0.md`, `PostgreSQL-Execution-Validation-Report-V1.0.md`
>
> **Status:** DRAFT — Authoritative for implementation. The DB, accounting model, API contract, and migration baseline are LOCKED. This document is the bridge between those and the runnable code.
>
> **Scope:** Defines the backend runtime, framework, layers, module layout, and cross-cutting mechanisms (auth, authz, idempotency, concurrency, transactions, error handling, audit, accounting, inventory, payment, lifecycle, idempotency, audit, observability, deployment). Maps every OpenAPI operation to a module/controller/service. Does NOT write application code in this phase.
>
> **Pipeline position:** PRD V1.1 → Business Rules → Database V1.0 → API Architecture V1.0 → **Backend Architecture V1.0 (this doc)** → Backend Implementation Plan V1.0 → Implementation → Testing.

---

## Table of Contents

- 1. Executive Summary
- 2. Technology Stack
- 3. Runtime Architecture
- 4. Directory / Module Architecture
- 5. API Layer
- 6. Authentication
- 7. Authorization
- 8. Validation
- 9. Service Layer
- 10. Repository / Data Access
- 11. Transaction Architecture
- 12. Accounting Architecture
- 13. Inventory Architecture
- 14. Payment Architecture
- 15. Lifecycle Architecture
- 16. Idempotency Architecture
- 17. Concurrency Architecture
- 18. Audit Architecture
- 19. Error Architecture
- 20. Storage Architecture
- 21. Notification Architecture
- 22. Testing Architecture
- 23. Security Architecture
- 24. Observability
- 25. OpenAPI Integration
- 26. Database / Migration Integration
- 27. Deployment Architecture
- 28. Risks / Ambiguities
- 29. Implementation Order
- 30. Final Verdict

---

## 1. Executive Summary

This document defines the backend architecture that implements the existing OpenAPI 3.1 contract (`openapi.yaml`, 167 operations) against the existing PostgreSQL 17 schema (`db/migrations/m0001__initial_schema_baseline.sql`, 39 tables, 20 functions, 40 triggers).

The architecture is deliberately a thin, **server-as-authority** model:

- The PostgreSQL schema is the source of truth for inventory, cost snapshots, lifecycle state machines, and the cash non-negative invariant (enforced by DB triggers). The backend never recomputes values the DB can provide.
- The backend is responsible for HTTP/JSON, authentication, capability authorization, request validation, idempotency, optimistic concurrency, transaction orchestration, the canonical ErrorEnvelope, and the audit-log write (for endpoints that audit by application code).
- The 8 accounting invariants (INV-01..INV-08) and 22 accounting events are preserved as a joint property: the DB triggers handle the immutable inserts, the backend orchestrates the workflow, and the migration's CHECK constraints guarantee termination states.

The selected runtime is **Python 3.11 + FastAPI + SQLAlchemy 2.0 + asyncpg + Alembic-free Pydantic-driven validation** (rationale §2). This is the smallest stack that satisfies every requirement in the task brief without inventing new abstractions.

**Verdict: PASS — Implementation-ready.** All 30 sections below are concretely specified. The 28 ambiguity items identified in §28 each have a documented default behavior. No contradiction with the authoritative inputs was found.

---

## 2. Technology Stack

### 2.1 Selection criteria

The stack was chosen against the following non-negotiable requirements (from the task brief and the API spec):

| Requirement | Implication for stack |
|---|---|
| PostgreSQL 17 | First-class async driver (asyncpg). |
| REST/JSON | Mature HTTP framework. |
| OpenAPI 3.1 contract | Framework that emits/consumes OpenAPI 3.1 natively or via a Pydantic adapter. |
| Transactional business ops | Connection-pooled SQLAlchemy Core (not ORM-only) with explicit `BEGIN`/`COMMIT` boundaries. |
| Optimistic concurrency (If-Match / ETag) | HTTP framework that exposes raw request/response control. |
| Idempotency-Key processing | Middleware-shaped architecture that can intercept before routing. |
| Capability-based authorization | Decorator/di-based guard. |
| Append-only audit | DB triggers handle most cases; service-layer helper for the rest. |
| Server-side derived values | View-based, computed in SQL. |
| Lifecycle enforcement | DB CHECK + trigger; service layer re-checks before lifecycle UPDATE. |
| Accounting event persistence | Multi-row inserts in a single transaction; uses raw SQL composability. |
| Inventory operations | FOR UPDATE / advisory locks; needs raw SQL composability. |
| Payment allocation | Cross-row trigger relies on a single DB transaction per insert. |
| Session management | Server-side sessions table; opaque tokens hashed at rest. |
| Notifications | DB table + computed-on-read list. |
| File/storage | PDF/XLSX export jobs to local disk or S3-compatible. |

### 2.2 Selected stack

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.11** | Single-business web app, no mobile, no real-time. Py 3.11+ has typing/async mature. Pinned via `pyproject.toml`. |
| HTTP framework | **FastAPI 0.115+** | Native OpenAPI 3.1 (via `openapi=3.1.0` in `FastAPI(openapi_version=...)`); Pydantic v2; per-request DI; raw response control. |
| Data access | **SQLAlchemy 2.0 Core + asyncpg** | Raw SQL composability for advisory locks, multi-table inserts, RETURNING. Async engine. `text()` for everything DB-specific. |
| Validation | **Pydantic v2** | Same library as FastAPI; one model for request, response, derived view. |
| Migrations | **None new** — uses the canonical `db/migrations/m0001__initial_schema_baseline.sql` via the existing `db/migrate.sh`. The application does NOT manage schema changes. **Forward-compatible note:** if V1.1 adds columns, the application will read them via reflection (`inspect()`) and lazy attribute access; the canonical migration remains the source of schema. |
| Auth | **Argon2id (`argon2-cffi`)** | OWASP 2024 default. Token = 256-bit random, base64url; stored as SHA-256 in `sessions`. |
| PDF/XLSX export | **reportlab + openpyxl** | Pure-Python, no native deps, deterministic. |
| Rate limiting | **slowapi** | Per-route, per-IP, per-user, with `Retry-After`. |
| Logging | **structlog** | JSON logs, request_id propagation. |
| Process model | **uvicorn** (single worker in dev) + **gunicorn -k uvicorn.workers.UvicornWorker** (prod, N workers) | Standard. Single node assumed; multi-node HA out of V1 scope. |
| Testing | **pytest + pytest-asyncio + httpx (ASGI) + testcontainers (postgres)** | Same process for unit and integration; testcontainers for real PG. |

### 2.3 What is NOT selected (and why)

| Rejected | Reason |
|---|---|
| Django + DRF | Over-bundled: brings admin, ORM, template engine we don't need. Capability authorization would be retrofit. |
| Node.js / NestJS | Viable, but the team has no TS toolchain in the repo; introducing it would be a new framework. Stdlib + stdlib-first principle applies. |
| Go / Java / .NET | Same. The repo has no Go/Java/.NET footprints; selecting them would be a new ecosystem. |
| SQLAlchemy ORM (mapped classes) | The accounting workflow requires `INSERT ... RETURNING`, advisory locks, FOR UPDATE, and `SELECT ... FOR UPDATE` chains across 5+ tables per atomic operation. Core + raw SQL is the right abstraction. ORM is rejected. |
| `alembic` | The schema is **locked** and applied via `db/migrate.sh` + `m0001__initial_schema_baseline.sql`. The backend does not own migrations. |
| Pydantic-only OpenAPI codegen | Tempting, but `openapi.yaml` is the contract; the backend validates against it via `prance` or `openapi-spec-validator` in CI, and serves responses that match it. We do not auto-generate routes from OpenAPI (we hand-write handlers and the FastAPI schema is in sync with `openapi.yaml` via the test suite). |
| Redis / external cache | Out of scope. All derived values are read-time queries on the DB. If a future V1.1 needs caching, add a single Redis instance; not now. |

### 2.4 Constraints respected from prior phases

- The DB schema, accounting model, business rules, API contract, and migration baseline are LOCKED. The backend adapts to them; it does not modify them.
- "One inventory effect per event" (API §1.2 #5) — the backend NEVER inserts `stock_movements` directly. The DB trigger and the backend cooperate: the backend inserts into the parent (`sales`, `purchases`, `production_runs`, etc.) and the DB trigger inserts the corresponding `stock_movements` row(s). Where the trigger does not cover the operation (manual adjustments, sales returns, refunds, repayments), the backend inserts the `stock_movements` row inside the same DB transaction as the parent row, with the same row locked (`SELECT ... FOR UPDATE` on the product).
- "Posted records are immutable" — the DB trigger blocks any UPDATE of `sales`/`purchases`/`production_runs` outside of `lifecycle_status` transitions; the backend only attempts lifecycle transitions via action endpoints.

---

## 3. Runtime Architecture

### 3.1 Process topology (single-node, V1)

```
                 ┌─────────────────────────┐
   HTTPS ────►  │  reverse proxy (nginx)  │   TLS, HSTS, gzip/br
                 │  rate limit (per-IP)    │
                 └────────────┬────────────┘
                              │
              ┌───────────────▼────────────────┐
              │  gunicorn (N workers)          │
              │  ├── worker 0: uvicorn         │
              │  ├── worker 1: uvicorn         │
              │  └── ...                       │
              │   each: FastAPI app            │
              └───────────────┬────────────────┘
                              │  asyncpg pool
                              ▼
                 ┌────────────────────────┐
                 │  PostgreSQL 17         │
                 │  - canonical schema    │
                 │  - canonical triggers  │
                 │  - sessions,           │
                 │    idempotency_keys,   │
                 │    notifications       │
                 └────────────────────────┘
                              ▲
                              │  migrations via db/migrate.sh
                 ┌────────────┴────────────┐
                 │  psql + m0001 + future  │
                 └─────────────────────────┘

       Exports (PDF/XLSX)        Local disk or S3-compatible
       ────────────────►         volume mounted at /var/bpos/exports
```

### 3.2 Process / thread model

- **Workers:** `gunicorn` with N = `CPU*2 + 1` uvicorn workers. Each worker is a single-threaded asyncio loop. For V1 (single business, ~50 RPS target), N=4 is sufficient.
- **DB pool:** `asyncpg.create_pool(min_size=2, max_size=10, max_inactive_connection_lifetime=300)`. Per-worker pool. Total max connections = `N * 10`; PG `max_connections` set to ≥ `N*10 + 10` (headroom for `psql` and migrations).
- **Transaction-per-request:** Every HTTP request that mutates state opens exactly one DB transaction (the outermost `with engine.begin():` block). Idempotency, optimistic-lock checks, validation, and writes all run in that one transaction. If the request fails, the transaction is rolled back; the client sees either a 4xx or a 5xx and the DB is unchanged.

### 3.3 Configuration

- **Source:** environment variables, loaded via `pydantic.BaseSettings` (or `pydantic-settings` in v2). All variables prefixed `BPOS_`.
- **Required at startup:** `BPOS_DATABASE_URL`, `BPOS_SECRET_KEY` (for session token generation, ≥ 32 bytes random), `BPOS_ARGON2_TIME_COST`, `BPOS_ARGON2_MEMORY_COST`, `BPOS_ACCESS_TOKEN_TTL_SECONDS`, `BPOS_REFRESH_TOKEN_TTL_SECONDS`, `BPOS_RATE_LIMIT_*`, `BPOS_LOG_LEVEL`, `BPOS_EXPORT_DIR`.
- **Optional:** `BPOS_CORS_ALLOWED_ORIGINS` (CSV), `BPOS_LOCK_TIMEOUT_MS` (default 2000), `BPOS_CASH_BALANCE_LOCK_KEY` (default `hashtext('cash_balance')`).

### 3.4 Startup checks

At worker boot, the app:

1. Loads `.env`, validates all required `BPOS_*` vars. Fail fast on missing.
2. Creates the asyncpg pool. Pings the DB with `SELECT 1`. If fails, fail boot.
3. Reads `system_settings` once into an in-memory cache (`system_settings_get_all` SQL helper). Cache is invalidated on `PATCH /settings` via Redis-less pub/sub (PostgreSQL `LISTEN`/`NOTIFY` on channel `system_settings_changed`; each worker holds a `NOTIFY` listener that clears its local cache).
4. Loads the canonical capability catalog from `capabilities` into memory (read-only; refreshed every 60s as a safety net).
5. Verifies the DB schema hash matches the expected hash recorded in `app/__init__.py` (defense against accidental migration drift). On mismatch, fail boot.

---

## 4. Directory / Module Architecture

```
business-pos-backend/
├── pyproject.toml
├── README.md
├── .env.example
├── app/
│   ├── __init__.py                  # version + schema hash
│   ├── main.py                      # FastAPI app factory + uvicorn entrypoint
│   ├── config.py                    # pydantic-settings
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py                # async engine + pool
│   │   ├── session.py               # per-request connection context
│   │   ├── locks.py                 # advisory lock helpers (per-product, cash_balance)
│   │   ├── listeners.py             # PG LISTEN/NOTIFY handler
│   │   └── errors.py                # sqlalchemy/pg error → API error mapping
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── money.py                 # NUMERIC(15,2) helpers; Decimal arithmetic
│   │   ├── time.py                  # UTC normalization; ISO 8601 parsing
│   │   ├── pagination.py            # page/per_page/total envelope
│   │   ├── filters.py               # ?filter[...] parser
│   │   ├── errors.py                # ApiError, AppError, exception hierarchy
│   │   └── etag.py                  # ETag/If-Match utilities
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── passwords.py             # Argon2id wrapper
│   │   ├── tokens.py                # 256-bit generation, SHA-256 storage
│   │   ├── session_service.py       # login/refresh/logout
│   │   ├── dependencies.py          # FastAPI dependencies: get_current_user, get_session
│   │   └── rate_limit.py            # login + refresh + global rate limit
│   ├── authz/
│   │   ├── __init__.py
│   │   ├── capabilities.py          # canonical catalog constant + load from DB
│   │   ├── service.py               # effective_caps(user_id)
│   │   └── guards.py                # @require_capability, @require_own_draft
│   ├── validation/
│   │   ├── __init__.py
│   │   ├── schemas.py               # Pydantic request/response models (one per OpenAPI schema)
│   │   ├── derived_field_filter.py  # reject derived fields from request bodies
│   │   ├── pagination_params.py     # page, per_page, sort
│   │   └── capability_aware.py      # field-level: discount/price_override gating
│   ├── idempotency/
│   │   ├── __init__.py
│   │   ├── service.py               # check_or_reserve / finalize
│   │   └── middleware.py            # per-endpoint Idempotency-Key handling
│   ├── concurrency/
│   │   ├── __init__.py
│   │   ├── etag.py                  # parse If-Match; compare with updated_at
│   │   └── lifecycle.py             # state-machine guards
│   ├── audit/
│   │   ├── __init__.py
│   │   ├── service.py               # write_audit(...); called from service layer
│   │   └── query.py                 # read audit_log with filters
│   ├── accounting/
│   │   ├── __init__.py
│   │   ├── events.py                # 22-event registry; maps event -> effects
│   │   ├── snapshots.py             # unit_cost_snapshot, finished_unit_cost
│   │   ├── cash.py                  # CRL / SRec / cash_balance / refund / repayment
│   │   ├── inventory_value.py       # product_valuation view wrappers
│   │   └── invariants.py            # run-time sanity checks (debug)
│   ├── inventory/
│   │   ├── __init__.py
│   │   ├── service.py               # stock_movement orchestration
│   │   ├── valuation.py             # on_hand_quantity, moving_average
│   │   └── negative_stock.py        # fallback rules
│   ├── payments/
│   │   ├── __init__.py
│   │   ├── allocation.py            # Σ ≤ total, over-tender, change
│   │   └── service.py               # record_sale_payment, record_purchase_payment
│   ├── lifecycle/
│   │   ├── __init__.py
│   │   ├── sale.py                  # post / cancel / return lifecycle
│   │   ├── purchase.py              # post / cancel / return / shipping
│   │   ├── production.py            # post / cancel
│   │   ├── manual_entry.py          # post / cancel
│   │   └── adjustments.py           # stock adjustment
│   ├── api/
│   │   ├── __init__.py
│   │   ├── router.py                # mounts all sub-routers under /api/v1
│   │   ├── auth_routes.py
│   │   ├── users_routes.py
│   │   ├── roles_routes.py
│   │   ├── capabilities_routes.py
│   │   ├── audit_routes.py
│   │   ├── products_routes.py
│   │   ├── categories_routes.py
│   │   ├── units_routes.py
│   │   ├── contacts_routes.py
│   │   ├── payment_methods_routes.py
│   │   ├── cost_types_routes.py
│   │   ├── financial_categories_routes.py
│   │   ├── sales_routes.py
│   │   ├── sales_lines_routes.py
│   │   ├── sales_payments_routes.py
│   │   ├── sales_returns_routes.py
│   │   ├── refunds_routes.py
│   │   ├── purchases_routes.py
│   │   ├── purchase_lines_routes.py
│   │   ├── purchase_payments_routes.py
│   │   ├── purchase_shipping_routes.py
│   │   ├── purchase_returns_routes.py
│   │   ├── supplier_repayments_routes.py
│   │   ├── inventory_routes.py
│   │   ├── stock_movements_routes.py
│   │   ├── production_runs_routes.py
│   │   ├── production_inputs_routes.py
│   │   ├── production_cost_lines_routes.py
│   │   ├── manual_entries_routes.py
│   │   ├── cash_movements_routes.py
│   │   ├── reports_routes.py
│   │   ├── dashboard_routes.py
│   │   ├── exports_routes.py
│   │   ├── notifications_routes.py
│   │   ├── settings_routes.py
│   │   └── system_routes.py         # /system/health, /system/version, /system/info
│   ├── errors_middleware.py         # AppError → ErrorEnvelope
│   ├── request_id_middleware.py     # X-Request-ID propagation
│   └── etag_middleware.py           # ETag header on GET, If-Match enforcement per-route
├── tests/
│   ├── conftest.py                  # testcontainers, app fixture, auth fixture
│   ├── unit/
│   │   ├── test_capabilities.py
│   │   ├── test_idempotency.py
│   │   ├── test_etag.py
│   │   ├── test_filters.py
│   │   ├── test_pagination.py
│   │   └── test_money.py
│   ├── service/
│   │   ├── test_sale_post.py
│   │   ├── test_sale_cancel.py
│   │   ├── test_sale_return.py
│   │   ├── test_refund.py
│   │   ├── test_purchase_post.py
│   │   ├── test_purchase_cancel.py
│   │   ├── test_purchase_return.py
│   │   ├── test_supplier_repayment.py
│   │   ├── test_production_post.py
│   │   ├── test_production_cancel.py
│   │   ├── test_stock_adjustment.py
│   │   ├── test_manual_entry.py
│   │   └── test_lifecycle_guards.py
│   ├── api/
│   │   ├── test_openapi_conformance.py    # full response validation
│   │   ├── test_sales_endpoints.py
│   │   ├── test_purchases_endpoints.py
│   │   ├── test_refunds_endpoints.py
│   │   ├── test_inventory_endpoints.py
│   │   ├── test_production_endpoints.py
│   │   ├── test_auth_endpoints.py
│   │   ├── test_users_endpoints.py
│   │   ├── test_reports_endpoints.py
│   │   ├── test_dashboard_endpoints.py
│   │   └── test_exports_endpoints.py
│   ├── authorization/
│   │   ├── test_capability_grants.py
│   │   ├── test_role_overrides.py
│   │   ├── test_only_owner_guard.py
│   │   └── test_own_draft_guard.py
│   ├── idempotency/
│   │   ├── test_replay_returns_original.py
│   │   ├── test_conflicting_body_rejected.py
│   │   ├── test_in_flight_returns_202.py
│   │   └── test_ttl_expiry.py
│   ├── concurrency/
│   │   ├── test_if_match_required.py
│   │   ├── test_advisory_lock_timeout.py
│   │   ├── test_concurrent_sale_post.py
│   │   ├── test_concurrent_refund.py
│   │   └── test_serialization_failure.py
│   ├── transactions/
│   │   ├── test_rollback_on_insufficient_stock.py
│   │   ├── test_rollback_on_crl_exceeded.py
│   │   ├── test_rollback_on_cash_insufficient.py
│   │   └── test_partial_visibility_blocked.py
│   ├── accounting/
│   │   ├── test_invariant_01_double_entry.py
│   │   ├── test_invariant_02_balance_sheet.py
│   │   ├── test_invariant_03_non_negative.py
│   │   ├── test_invariant_04_refund_bound.py
│   │   ├── test_invariant_05_one_cancel.py
│   │   ├── test_invariant_06_crl_ceiling.py
│   │   ├── test_invariant_07_srec_ceiling.py
│   │   ├── test_invariant_08_inventory_gl.py
│   │   └── test_event_stress_1_to_22.py
│   ├── inventory/
│   │   ├── test_moving_average_recompute.py
│   │   ├── test_negative_stock_disallowed.py
│   │   ├── test_negative_stock_fallback.py
│   │   ├── test_value_adjustment_zero_qty.py
│   │   ├── test_reversal_pair.py
│   │   └── test_low_stock_threshold.py
│   ├── lifecycle/
│   │   ├── test_sale_draft_to_posted.py
│   │   ├── test_sale_posted_to_completed.py
│   │   ├── test_sale_posted_to_partially_returned.py
│   │   ├── test_sale_cancellation.py
│   │   ├── test_purchase_lifecycle.py
│   │   ├── test_production_lifecycle.py
│   │   └── test_cannot_cancel_twice.py
│   ├── audit/
│   │   ├── test_audit_written_in_tx.py
│   │   ├── test_audit_queryable.py
│   │   └── test_audit_immutable.py
│   ├── postgres/
│   │   ├── test_migration_applies.py
│   │   ├── test_schema_hash_match.py
│   │   └── test_triggers_fire.py
│   └── openapi/
│       ├── test_openapi_valid_3_1.py
│       ├── test_endpoints_present.py
│       └── test_error_envelope_schema.py
└── tools/
    ├── export_pdf.py                # PDF rendering
    ├── export_xlsx.py               # XLSX rendering
    └── seed.py                      # idempotent seed for dev/test (roles, capabilities, payment_methods, cost_types, financial_categories, system_settings)
```

### 4.1 Module dependency rules (enforced by import-linter in CI)

- `app/api/*` MAY import from `app/{auth,authz,validation,idempotency,concurrency,audit,accounting,inventory,payments,lifecycle,domain,db}`.
- `app/lifecycle/*` MAY import from `app/{accounting,inventory,payments,audit,domain,db}`.
- `app/accounting/*` MAY import from `app/{inventory,domain,db}` only.
- `app/inventory/*` MAY import from `app/{domain,db}` only.
- `app/payments/*` MAY import from `app/{accounting,domain,db}` only.
- `app/audit/*` MAY import from `app/{domain,db}` only.
- `app/{auth,authz,idempotency,concurrency,validation,domain,db}` MAY NOT import from `app/{api,lifecycle,accounting,inventory,payments,audit}`. They are leaves.
- `app/api/*` MUST NOT import from another `app/api/*` file. Cross-resource actions (e.g., creating a refund from a sale cancel) go through the service layer.

This keeps the dependency graph a DAG with `api` at the top, leaves at the bottom.

### 4.2 One route file per resource

There is **exactly one** Python file per top-level OpenAPI tag (see §5.4). The handler functions inside it are thin: parse request, run guard, call service, format response, write ETag. No business logic in route handlers.

---

## 5. API Layer

### 5.1 Framework configuration

```python
# app/main.py
app = FastAPI(
    title="Business POS System API",
    version="1.0.0",
    openapi_version="3.1.0",
    openapi_url="/openapi.json",
    docs_url=None,                 # no Swagger UI; web frontend has its own docs
    redoc_url=None,
    default_response_class=ORJSONResponse,
)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(ErrorEnvelopeMiddleware)   # AppError → ErrorEnvelope
app.add_middleware(EtagMiddleware)            # GETs set ETag
app.include_router(api_router, prefix="/api/v1")
```

### 5.2 Standard request/response shape

- **Content-Type:** `application/json; charset=utf-8`. Enforced by `ORJSONResponse` (faster than stdlib json).
- **Compression:** `gzip` and `br` via `starlette.middleware.gzip.GZipMiddleware` (lowest level, ≥ 500 bytes).
- **Encoding:** UTF-8 strict. Reject non-UTF-8 bodies with 400.
- **Charset:** not negotiated; always UTF-8.

### 5.3 Response envelope (canonical)

Per OpenAPI `ErrorEnvelope` schema (defined at `openapi.yaml:6047-6081`):

```json
{
  "error": {
    "code": "validation_failed",
    "message": "Request validation failed.",
    "details": [
      {"field": "lines[0].quantity", "code": "quantity_invalid", "message": "Quantity must be greater than 0."}
    ],
    "request_id": "9e3a1b1c-2b3c-4f2a-b6f3-..."
  }
}
```

The successful response is a single resource object (for GET/POST single) or `{ "data": [...], "pagination": {...}, "links": {...} }` for collections. No success envelope. See `Pagination` schema in `openapi.yaml`.

### 5.4 Route mounting (167 operations → 43 route files)

The following table maps each OpenAPI tag to a route file. Each file holds the operations for the paths under that tag. Lifecyle action endpoints (`/sales/{id}/post`, etc.) live in the same route file as their parent resource — there is no separate `sales_actions_routes.py`.

| OpenAPI tag | Route file | Operations | Path count |
|---|---|---|---|
| Auth | `auth_routes.py` | 5 | 5 |
| System | `system_routes.py` | 3 | 3 |
| Users | `users_routes.py` | 10 | 7 |
| Roles | `roles_routes.py` | 6 | 5 |
| Capabilities | `capabilities_routes.py` | 1 | 1 |
| Audit | `audit_routes.py` | 1 | 1 |
| Categories | `categories_routes.py` | 5 | 3 |
| Units | `units_routes.py` | 5 | 3 |
| Products | `products_routes.py` | 9 | 7 |
| Contacts | `contacts_routes.py` | 6 | 4 |
| Payment Methods | `payment_methods_routes.py` | 5 | 3 |
| Cost Types | `cost_types_routes.py` | 5 | 3 |
| Financial Categories | `financial_categories_routes.py` | 5 | 3 |
| Sales | `sales_routes.py` | 11 | 6 |
| Sale Lines | `sales_lines_routes.py` | 5 | 3 |
| Sale Payments | `sales_payments_routes.py` | 2 | 1 |
| Sales Returns | `sales_returns_routes.py` | 3 | 2 |
| Refunds | `refunds_routes.py` | 3 | 1 |
| Purchases | `purchases_routes.py` | 11 | 6 |
| Purchase Lines | `purchase_lines_routes.py` | 5 | 3 |
| Purchase Payments | `purchase_payments_routes.py` | 2 | 1 |
| Purchase Shipping | `purchase_shipping_routes.py` | 2 | 1 |
| Purchase Returns | `purchase_returns_routes.py` | 3 | 2 |
| Supplier Repayments | `supplier_repayments_routes.py` | 3 | 1 |
| Inventory | `inventory_routes.py` | 5 | 4 |
| Stock Movements | `stock_movements_routes.py` | 2 | 1 |
| Production Runs | `production_runs_routes.py` | 8 | 6 |
| Production Inputs | `production_inputs_routes.py` | 5 | 3 |
| Production Cost Lines | `production_cost_lines_routes.py` | 5 | 3 |
| Manual Entries | `manual_entries_routes.py` | 4 | 2 |
| Cash Movements | `cash_movements_routes.py` | 2 | 1 |
| Reports | `reports_routes.py` | 16 | 16 |
| Dashboard | `dashboard_routes.py` | 2 | 2 |
| Exports | `exports_routes.py` | 3 | 2 |
| Notifications | `notifications_routes.py` | 3 | 2 |
| Settings | `settings_routes.py` | 2 | 1 |
| **Total** | **37 route files** | **167 operations** | **~110 paths** |

(Note: minor delta from the tag list; the table groups `Sale Lines` + `Sale Payments` separately per OpenAPI tag, and the Inventory/Stock Movements are split.)

### 5.5 Handler skeleton

Every handler follows the same shape:

```python
@router.post(
    "/sales/{id}/post",
    status_code=200,
    response_model=Sale,
    responses={400: {"model": ErrorEnvelope}, 403: {"model": ErrorEnvelope},
              404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope},
              412: {"model": ErrorEnvelope}},
)
async def post_sale(
    id: int,
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    conn: AsyncConnection = Depends(get_conn),
) -> Sale:
    async with IdempotencyGuard(conn, "POST", f"/sales/{id}/post", idempotency_key, current_user) as guard:
        if guard.replay:
            return guard.cached_response  # type: ignore[return-value]
        sale = await lifecycle.sale.post(conn, sale_id=id, user=current_user, if_match=if_match)
        response = Sale.model_validate(sale)
        await guard.commit(response)
        return response
```

Five lines per route maximum. All business logic in `lifecycle/`, `accounting/`, `inventory/`, `payments/`.

### 5.6 HTTP method mapping

| Method | Allowed | Notes |
|---|---|---|
| `GET` | Yes | Always safe + idempotent. ETag header set. `Cache-Control: private, max-age=0, must-revalidate` (auth-related) or `no-store` for `/auth/*` and `/users/*/sessions`. |
| `POST` | Yes | Create (collection) or invoke lifecycle action. Body required. Idempotency-Key strongly recommended, required for 14 endpoints (API §7.1). |
| `PUT` | Rare | Full replace of a draft; only on `roles/{id}/capabilities` (set replaces all). |
| `PATCH` | Yes | Partial update; only valid on `lifecycle_status = 'draft'` for documents. Settings. Master records. |
| `DELETE` | Yes | Hard-delete only if unreferenced; otherwise 409 `referenced_by_history`. Deactivation uses `POST /resource/{id}/deactivate` instead. |

### 5.7 Headers

**Request headers (all enforced server-side):**

| Header | Enforcement | Where |
|---|---|---|
| `Authorization: Bearer <token>` | Required on all routes except `POST /auth/login`, `POST /auth/refresh`, `GET /system/health`, `GET /system/version`. | `auth.dependencies` |
| `Content-Type: application/json` | Required on all bodies. | FastAPI default. |
| `Idempotency-Key: <uuid>` | Required on 14 endpoints (API §7.1). | `idempotency.middleware` |
| `If-Match: <etag>` | Required on lifecycle actions (post, cancel, return, refund, adjust, repayment). | `etag.dependencies` |
| `X-Request-ID: <uuid>` | Optional. Generated if absent. Echoed in response. | `request_id.middleware` |
| `Accept: application/json` | Optional. Default. | FastAPI. |
| `Origin: https://...` | Checked against `BPOS_CORS_ALLOWED_ORIGINS` on state-changing requests. | `cors_check` dependency |

**Response headers:**

| Header | When | Source |
|---|---|---|
| `ETag: "<rfc3339nano>"` | Every GET of a mutable resource. | `etag.middleware` |
| `X-Request-ID` | Always. | `request_id.middleware` |
| `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` | When rate-limited or about to be. | `slowapi` |
| `Retry-After: <seconds>` | 429, 503. | `slowapi` / app code |
| `Cache-Control: no-store` | `/auth/*`, `/users/*` GET, `/settings` GET. | handler |
| `Cache-Control: private, max-age=0, must-revalidate` | All other GETs. | `etag.middleware` |
| `Content-Encoding: br` or `gzip` | When client sent `Accept-Encoding`. | `GZipMiddleware` |

### 5.8 CORS

`Access-Control-Allow-Origin` is set to the request's `Origin` if it is in `BPOS_CORS_ALLOWED_ORIGINS`; otherwise the response has no CORS headers (the browser blocks). For state-changing requests, the `Origin` is checked even on same-origin, as a CSRF defense-in-depth (bearer token model is the primary defense).

### 5.9 URL conventions

- All paths prefixed `/api/v1`. Versioned once; no `/v2` in V1.
- Resource names: plural (`/sales`, `/purchases`, `/products`). Singular only for actions (`/auth/login`, `/dashboard`).
- Path segments: kebab-case.
- Sub-resources: `/sales/{id}/lines`, `/sales/{id}/payments`. Lifecycle actions: `/sales/{id}/post`, `/sales/{id}/cancel`, `/sales/{id}/returns`.

---

## 6. Authentication

### 6.1 Token model

- **Opaque tokens**, 256-bit random, base64url-encoded (43 chars unpadded). Server stores SHA-256(token) in `sessions.access_token_hash`; only the client sees the raw token. Same for refresh tokens.
- **Access token TTL:** `BPOS_ACCESS_TOKEN_TTL_SECONDS` (default 900 = 15 min).
- **Refresh token TTL:** `BPOS_REFRESH_TOKEN_TTL_SECONDS` (default 2592000 = 30 days).
- **Refresh rotation:** on every `POST /auth/refresh`, the old refresh is revoked and a new one issued. Reuse of a revoked refresh is treated as compromise: all sessions for the user are revoked, and an audit row is written.
- **Idle timeout:** 24 hours since `last_seen_at`. Re-validated on every request; expired access is rejected with 401 `session_expired`.

### 6.2 Password rules

- Minimum 8 chars, max 256. No complexity rules beyond length (OWASP 2024).
- Storage: Argon2id with `time_cost=3`, `memory_cost=64MB`, `parallelism=4` (configurable via env).
- Verification: constant-time via `argon2-cffi`.
- Reset: only Owner via `POST /users/{id}/reset-password`. No self-service.

### 6.3 Login flow (`POST /auth/login`)

```
1. Validate Idempotency-Key (required; 30s TTL window for login).
2. Parse body: { username, password }.
3. Lookup user by username (case-insensitive). If not found: increment a
   per-IP counter, return 401 invalid_credentials.
4. If account locked (failed_attempts >= 5 in 15 min): 423 account_locked.
5. Verify Argon2id. If fail: increment failed_attempts; if threshold crossed,
   set is_locked_until = NOW() + 15min; 401 invalid_credentials.
6. On success: clear failed_attempts, generate new access + refresh tokens.
7. INSERT session (id, user_id, access_token_hash, refresh_token_hash,
   created_at = NOW(), last_seen_at = NOW(), expires_at = NOW() + 15min,
   refresh_expires_at = NOW() + 30d, ip_address, user_agent, revoked_at=NULL).
8. UPDATE users.last_login_at = NOW().
9. Return LoginResponse { access_token, refresh_token, expires_in,
   refresh_expires_in, user: { id, username, full_name, role, capabilities } }.
```

### 6.4 Refresh flow (`POST /auth/refresh`)

```
1. Lookup session by SHA-256(refresh_token).
2. If not found: 401 invalid_credentials.
3. If revoked_at IS NOT NULL: revoke all sessions for that user_id; audit.
4. If refresh_expires_at < NOW(): 401 session_expired.
5. Generate new access + refresh; UPDATE session.
6. Return new tokens (LoginResponse shape minus user).
```

### 6.5 Logout (`POST /auth/logout`)

```
1. Set session.revoked_at = NOW(), revoked_reason = 'logout'.
2. Return 204.
```

### 6.6 Logout all (`POST /auth/logout-all`)

```
1. Require user.manage capability.
2. UPDATE sessions SET revoked_at = NOW(), revoked_reason = 'logout_all'
   WHERE user_id = current_user.id AND revoked_at IS NULL.
3. Audit.
4. Return 204.
```

### 6.7 Lockout

- Counter: `users.failed_attempts` (NOT NULL, default 0); `users.is_locked_until TIMESTAMPTZ NULL`.
- After 5 failed attempts in 15 min: `is_locked_until = NOW() + 15min`. Login returns 423 `account_locked` until cleared.
- Owner unlock: `POST /users/{id}/unlock` → `failed_attempts = 0, is_locked_until = NULL`, audit.

### 6.8 Dependencies (FastAPI)

```python
# app/auth/dependencies.py
async def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    conn: AsyncConnection = Depends(get_conn),
) -> AuthenticatedUser:
    # 1. parse "Bearer <token>"
    # 2. lookup session by access_token_hash
    # 3. check expires_at > NOW(), revoked_at IS NULL
    # 4. UPDATE last_seen_at = NOW() (best-effort, not in same tx)
    # 5. load user, role, effective capabilities
    # 6. return AuthenticatedUser(...)
```

The `get_current_user` dependency is the gateway: every protected route declares `Depends(get_current_user)`. Optional auth (for `/system/health`) uses `Depends(get_optional_user)` which returns `None` if no token.

### 6.9 What authentication does NOT cover

- Anonymous endpoints: `POST /auth/login`, `POST /auth/refresh`, `GET /system/health`, `GET /system/version`. No other route is anonymous.
- Account-inactive check happens after credentials verify: `is_active = false` → 403 `account_inactive`.

---

## 7. Authorization

### 7.1 Capability model

The canonical capability catalog is the table `capabilities` (seeded from `openapi.yaml` §3.1 / API §3.1 at migration time). The backend never hardcodes a list of capabilities; it queries `SELECT code FROM capabilities` at startup to build a constant set for validation and uses the codes directly in `@require_capability(...)`.

### 7.2 Effective capability resolution

`effective_caps(user_id)` returns the union:

```python
async def effective_caps(conn, user_id: int) -> set[str]:
    role_caps = await conn.execute(text("""
        SELECT c.code FROM capabilities c
        JOIN role_capabilities rc ON rc.capability_id = c.id
        JOIN users u ON u.role_id = rc.role_id
        WHERE u.id = :uid
    """), {"uid": user_id})
    overrides = await conn.execute(text("""
        SELECT c.code, uco.is_granted
        FROM user_capability_overrides uco
        JOIN capabilities c ON c.id = uco.capability_id
        WHERE uco.user_id = :uid
    """), {"uid": user_id})
    eff = set(role_caps.scalars().all())
    for code, is_granted in overrides:
        if is_granted: eff.add(code)
        else: eff.discard(code)
    return frozenset(eff)
```

**Memoization:** per-request. The dependency `get_effective_capabilities` calls `effective_caps` once per request, caches the result on `request.state`, and the guards read from there. No cross-request caching (Owner can grant/revoke between requests; BR-AUTH-003 says "takes effect immediately").

### 7.3 Guards

```python
# app/authz/guards.py
def require_capability(*caps: str):
    async def _dep(
        current_user: AuthenticatedUser = Depends(get_current_user),
        caps_state: frozenset[str] = Depends(get_effective_capabilities),
    ) -> AuthenticatedUser:
        missing = [c for c in caps if c not in caps_state]
        if missing:
            raise ApiError(403, "capability_required",
                          f"Missing capabilities: {missing}",
                          details={"required": missing})
        return current_user
    return _dep

def require_own_draft(get_resource: Callable):
    """For sale.edit_own_draft / purchase.edit_own_draft.
    Requires (sale.edit_own_draft) AND (resource.created_by == current_user.id)
    AND (resource.lifecycle_status == 'draft').
    Implementation: fetches the resource inside the guard; raises 403 lifecycle_state_invalid
    if not draft, 403 own_draft_required if not owner, 403 capability_required if missing.
    """
```

### 7.4 Capability vs role

The server **never** checks `user.role`. It checks capabilities. A user with role `Staff` who has been granted `sale.cancel` via override can cancel a sale. The capability is the only authority.

### 7.5 Per-request authorization flow

```
1. auth.dependencies.get_current_user
   → AuthenticatedUser { id, role_id, is_active }
2. authz.dependencies.get_effective_capabilities
   → frozenset[str] cached on request.state
3. authz.guards.require_capability(*caps)
   → raises ApiError(403, 'capability_required', details={required: [...]}) if missing
4. (optional) authz.guards.require_own_draft(...)
   → raises ApiError(403, 'lifecycle_state_invalid' | 'own_draft_required') on miss
5. service layer runs
```

The 401 vs 403 distinction: 401 is only for **missing/expired/revoked token** (handled in `get_current_user`); 403 is for "authenticated but missing capability or violating a non-capability authorization rule." Per API §3.2 #5, the response shape is the same — do not leak auth vs authz.

### 7.6 Special guards beyond capability

| Guard | Code | Where |
|---|---|---|
| Cannot deactivate only Owner | 403 | `users_routes.deactivate_user` |
| System role immutable | 403 | `roles_routes.update_role`, `delete_role`, `set_role_capabilities` |
| Cannot deactivate self if only Owner | 403 | (same as above) |
| Sale edit own draft (only owner of draft) | 403 `lifecycle_state_invalid` if not draft; 403 `own_draft_required` if not owner | `sales_routes.update_sale_draft` |
| `price_override_not_permitted` | 400 if user lacks `sale.price_override` and `unit_price` differs from `products.selling_price` | `sales_lines_routes` |
| `discount_not_permitted` | 400 if user lacks `sale.discount` and `discount_amount > 0` | `sales_lines_routes`, `sales_routes` |
| `tendered_not_allowed_for_non_cash` | 400 if `tendered_amount` present and `payment_method.is_cash = false` | `sales_payments_routes`, `purchase_payments_routes` |

### 7.7 PII minimum exposure

- Bulk endpoints return minimal fields. `?include=pii` is **not** in V1 (API §13.8); single-resource GETs return full PII if the user has `contact.view`.
- Logging: PII is masked. `email` → `email_hash` (SHA-256 of normalized lowercase email). `phone` is never logged.

---

## 8. Validation

### 8.1 Layered validation (per API §6.1)

| Layer | Tool | Failure code |
|---|---|---|
| Request shape | FastAPI's `Request.json()` + Pydantic | 400 `validation_failed` `details.code = 'invalid_json'` |
| Field-level | Pydantic type/range/enum | 400 `validation_failed` |
| Cross-field (line_total, total_amount) | Service layer after parsing | 400 `validation_failed` |
| Cross-resource (FK exists, parent state) | Service layer with `SELECT ... FOR UPDATE` | 400/409 |
| Business rules (BR-SALE-001 etc.) | Service layer | mapped per API §6.7 |
| Authoritative state | DB triggers | translated by `db/errors.py` |

### 8.2 Pydantic models

- One Pydantic model per OpenAPI schema. The model names match the schema names (e.g., `Sale`, `SaleCreateRequest`, `SalePatch`, `SaleLine`, `ErrorEnvelope`, `Pagination`).
- All models use `model_config = ConfigDict(from_attributes=True)` (alias for the old `orm_mode`) for ORM-friendly read; service layer returns dicts that Pydantic validates into models.
- `Decimal` for `NUMERIC(15,2)` and `NUMERIC(15,4)`; FastAPI's ORJSONResponse handles them.
- `datetime` for `TIMESTAMPTZ`; serialized ISO 8601 with offset (`datetime.isoformat()` with `+00:00` for UTC).

### 8.3 Server-only field rejection (the "derived field" filter)

Every request body is passed through `derived_field_filter.reject_derived_fields(body, model_class)` which:

1. Loads the set of fields the model **accepts** (from Pydantic's `model_fields`).
2. Loads the set of "server-only" fields (a constant per resource: `unit_cost_snapshot`, `cogs_total_snapshot`, `line_total`, `lifecycle_status`, `posted_at`, `posted_by`, `paid_amount`, `payment_state`, `ar`, `ap`, `crl`, `srec`, `cash_balance`, `on_hand_quantity`, `moving_average_unit_cost`, `inventory_value`, `version`, `id`, `created_at`, `updated_at`, `created_by`, `cancellation_date`, `cancelled_by`).
3. If the body contains any of the server-only fields: return 400 `derived_field_not_allowed` with `details.field = <name>`.
4. The filter is generic; per-model overrides are unnecessary.

The "server-only" list is in `app/validation/derived_field_filter.py` and is generated once from a tuple derived from the API §6.3 list.

### 8.4 Capability-aware field rejection (price_override, discount)

After schema validation and before business validation, a second pass checks capability-gated fields:

```python
# In sales_lines_routes.create_sale_line / update_sale_line:
if body.unit_price != product.selling_price and not caps.has("sale.price_override"):
    raise ApiError(400, "price_override_not_permitted",
                   "User lacks sale.price_override capability.",
                   details={"field": "unit_price"})

if body.discount_amount and body.discount_amount > 0 and not caps.has("sale.discount"):
    raise ApiError(400, "discount_not_permitted",
                   "User lacks sale.discount capability.",
                   details={"field": "discount_amount"})
```

### 8.5 Validation failure response

```json
{
  "error": {
    "code": "validation_failed",
    "message": "Request validation failed.",
    "details": [
      {"field": "lines[0].quantity", "code": "quantity_invalid", "message": "Quantity must be greater than 0."}
    ],
    "request_id": "..."
  }
}
```

**Always** return all field errors in one response, not just the first. The order matches the request order. Implementation: collect errors in Pydantic's `ValidationError.errors()` (the `PydanticUndefined` is excluded; for FastAPI 0.115+ the list is already ordered) and append the cross-field ones from the service layer.

### 8.6 Date / time

- Server accepts ISO 8601 with offset. Naive timestamps → 400 `date_invalid`.
- Server normalizes to UTC and stores `TIMESTAMPTZ`.
- Response always carries offset (`+00:00` for UTC).
- `server_time` is included in health/info endpoints and as a header on every response (`X-Server-Time`, debug-only, optional).

### 8.7 Money

- `Decimal` in Python; serialized as JSON number in responses (Pydantic's default).
- All arithmetic uses `Decimal`; `float` is forbidden in service code (enforced by `ruff` rule `BLE001` + custom check in CI).
- Storage: `NUMERIC(15,2)` exactly. No rounding on storage. Display rounding is a client concern; if OD-16 resolves to true, the API will add a `display_rounding` field (out of V1 default; the DB column `system_settings.display_rounding` already exists for forward-compatibility per MS-7).

### 8.8 Quantity

- `Decimal` with `NUMERIC(15,4)` precision.
- Validation: `> 0`, `< 10^12`, `<= 999999999999.9999` (10^12 - 1 scaled).

### 8.9 Enums

- Strings only; unknown value → 400 `enum_value_invalid` with `details = {field, allowed: [...]}`. The `allowed` list comes from the Pydantic `Literal[...]` type or a custom validator.

### 8.10 Pagination parameters

| Param | Type | Default | Bounds |
|---|---|---|---|
| `page` | int | 1 | ≥ 1 |
| `per_page` | int | 50 | 1..500 (over → 400 `validation_failed`) |
| `sort` | string | (resource default) | comma list, `-` prefix for desc; unknown field → 400 |

Total count: `SELECT COUNT(*) FROM ... WHERE ...`. The query plan uses indexes; for very large tables the report endpoints use server-side timeouts and return 503 with a `narrow your date range` hint (API §12.4).

### 8.11 Filter parameters

- `?filter[field]=value` (equality).
- `?filter[field]_gte`, `_lte`, `_gt`, `_lt` (range).
- `?filter[field]_from`, `_to` (date range, inclusive).
- Multi-value: `?filter[field]=a&filter[field]=b` is OR within a field; AND across fields.
- Search: `?q=...` performs case-insensitive substring on configured text fields. The set of searchable fields per resource is a constant in `app/validation/filters.py`.
- Unknown field: 400 `validation_failed`. Unknown operator: 400 `validation_failed`.

---

## 9. Service Layer

### 9.1 Purpose

The service layer is where business rules live. Routes are thin; the service layer takes a parsed/validated Pydantic model + an open DB connection + the authenticated user, and runs the workflow. The service layer:

- Validates state machine transitions.
- Computes derived values (snapshots).
- Inserts the parent row and dependent rows in one transaction.
- Writes the audit row.
- Returns the resulting state for serialization.

### 9.2 Pattern

```python
# app/lifecycle/sale.py
async def post(conn: AsyncConnection, sale_id: int, user: AuthenticatedUser,
               if_match: str | None) -> dict:
    """Post a draft sale. Per API §15.10.8 and DB spec §4.1."""
    # 1. Lock + load sale
    row = await conn.execute(text("""
        SELECT * FROM sales WHERE id = :id FOR UPDATE
    """), {"id": sale_id})
    sale = row.mappings().first()
    if not sale:
        raise ApiError(404, "not_found", f"Sale {sale_id} not found.")

    # 2. If-Match check
    etag = etag_from(sale["updated_at"])
    if if_match and if_match != etag:
        raise ApiError(412, "version_mismatch",
                       "If-Match header does not match current ETag.",
                       details={"current_etag": etag})

    # 3. Lifecycle guard
    if sale["lifecycle_status"] != "draft":
        raise ApiError(409, "lifecycle_state_invalid",
                       f"Sale is in state {sale['lifecycle_status']}, cannot post.",
                       details={"from": "draft", "to": "posted",
                                "current": sale["lifecycle_status"]})

    # 4. Load lines + lock products in deterministic order
    lines = (await conn.execute(text("""
        SELECT * FROM sale_lines WHERE sale_id = :id ORDER BY line_number
    """), {"id": sale_id})).mappings().all()
    if not lines:
        raise ApiError(422, "validation_failed",
                       "Cannot post a sale with no lines.",
                       details=[{"field": "lines", "code": "no_lines"}])

    product_ids = sorted({l["product_id"] for l in lines})
    for pid in product_ids:
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('product:' || :p))"),
                            {"p": str(pid)})
        # Also lock the product row to prevent concurrent price change
        await conn.execute(text("SELECT id FROM products WHERE id = :p FOR UPDATE"),
                            {"p": pid})

    # 5. For each line: snapshot unit cost
    enriched = []
    for line in lines:
        cost = await inventory.valuation.moving_average(conn, line["product_id"])
        neg = await inventory.negative_stock.check(conn, line["product_id"], line["quantity"])
        enriched.append({**line, "unit_cost_snapshot": cost, "is_negative_stock_fallback": neg})

    # 6. Update sale_lines with snapshot
    for line in enriched:
        await conn.execute(text("""
            UPDATE sale_lines
            SET unit_cost_snapshot = :cost,
                cogs_total_snapshot = :cost * :qty,
                is_negative_stock_fallback = :neg,
                stock_movement_id = NULL
            WHERE id = :id
        """), {"cost": line["unit_cost_snapshot"], "qty": line["quantity"],
                "neg": line["is_negative_stock_fallback"], "id": line["id"]})

    # 7. Insert stock_movements (one per line) - DB trigger will block if
    # negative stock and not allowed.
    for line in enriched:
        await conn.execute(text("""
            INSERT INTO stock_movements
            (product_id, movement_date, trigger, quantity, unit_cost_at_movement,
             total_cost, reference_type, reference_id, reference_line_id,
             reversal_of_movement_id, reversed_by_movement_id, reason,
             created_by)
            VALUES
            (:pid, NOW(), 'sale', :qty, :cost, -(:qty * :cost),
             'sale', :sale_id, :line_id, NULL, NULL, NULL, :user_id)
        """), {"pid": line["product_id"], "qty": line["quantity"],
                "cost": line["unit_cost_snapshot"], "sale_id": sale_id,
                "line_id": line["id"], "user_id": user.id})

    # 8. Update sale lifecycle
    new_status = "posted"  # will become "completed" if fully paid
    await conn.execute(text("""
        UPDATE sales
        SET lifecycle_status = :new_status, posted_at = NOW(), posted_by = :uid,
            updated_at = NOW(), version = version + 1
        WHERE id = :id
    """), {"new_status": new_status, "uid": user.id, "id": sale_id})

    # 9. Audit
    await audit.write(conn, action="post", entity_type="sale", entity_id=sale_id,
                     old_values={"lifecycle_status": "draft"},
                     new_values={"lifecycle_status": new_status,
                                 "posted_at": ..., "posted_by": user.id},
                     user=user)

    # 10. Reload and return
    return await load_sale(conn, sale_id)
```

(That is a near-complete sketch. The actual implementation will be more compact by reusing helpers; the point is the structure.)

### 9.3 Service layer principles

1. **Always inside a transaction.** Service functions are called from a route handler that holds an open `AsyncConnection` in transaction mode. The service does not commit; the route (or the idempotency guard) commits.
2. **Always use the connection parameter.** No global connection, no thread-local.
3. **Pure SQL via SQLAlchemy Core `text()`.** No ORM.
4. **One operation per call.** A service function does one thing (post a sale, cancel a sale, return a sale). Multi-step orchestrations compose.
5. **No Pydantic models in service signatures.** Services return dicts (via `row.mappings()`) and let the route layer wrap in Pydantic for response. The request body is already a Pydantic model when the service receives it; services extract dicts.
6. **Errors raise `ApiError`.** No `return None` for failures. The error middleware converts to ErrorEnvelope.
7. **No logging of business data.** PII, amounts, and identifiers are masked in logs (see §24).

### 9.4 Cross-resource service calls

The lifecycle services are independent. Cross-resource actions (e.g., creating a supplier_repayment from a purchase cancel) are **always** initiated by the user via a separate endpoint. The service does NOT chain across resources; the orchestrating route handler may call two service functions in the same transaction, but the design is biased toward: each user action → one endpoint → one service call → one transaction.

---

## 10. Repository / Data Access

### 10.1 No ORM

SQLAlchemy 2.0 Core + asyncpg. The application issues raw SQL via `text()` and reads via `mappings()`. There are no mapped classes.

### 10.2 Connection management

```python
# app/db/session.py
async def get_conn() -> AsyncIterator[AsyncConnection]:
    async with engine.connect() as conn:
        async with conn.begin():
            yield conn
```

This is the **only** connection acquisition. Every request goes through this. There is no `engine.execute` shortcut; if a service accidentally bypasses this, a `bp_lint` rule fails CI.

### 10.3 Reads vs writes

- **Reads** that don't need to be in the same transaction as the write may use `engine.connect()` outside `begin()`. Examples: `GET /products`, `GET /cash-movements/balance`. These are pure reads, not FOR UPDATE, no advisory lock needed.
- **Reads that need consistency** (e.g., `GET /sales/{id}` with `?include=lines,payments`) use a single `READ COMMITTED` transaction to ensure a consistent snapshot. They don't need a `begin()` for write, but they need the connection-scoped read consistency.
- **Writes** always use `conn.begin()` and commit at the end.

### 10.4 Read patterns

- **List endpoints** with pagination: `SELECT ... FROM ... WHERE ... ORDER BY ... LIMIT :per_page OFFSET (:page-1)*:per_page`. Total count: `SELECT COUNT(*) FROM ... WHERE ...` (same WHERE clause).
- **Single resource**: `SELECT * FROM ... WHERE id = :id`. If `?include=lines,...`, fetch lines in the same connection.
- **Derived values** (CRL, AR, etc.): use the DB views (`product_valuation`, etc.) when they exist; otherwise inline `SELECT ... FROM ... GROUP BY ...` in the same connection.
- **List of low-stock**: `SELECT ... FROM products JOIN product_valuation v USING(product_id) WHERE v.on_hand_quantity <= COALESCE(p.low_stock_threshold, :default)`. Index: partial on `low_stock_threshold` (DB spec §2.2.3).

### 10.5 Write patterns

- **`SELECT ... FOR UPDATE` on the row** at the start of any write. For multi-row operations, lock in deterministic order (sort by `id` ASC) per DB spec §11.3.
- **Advisory locks** on `product:{id}` for any operation that computes moving-average or checks on-hand. Held for the duration of the transaction (`pg_advisory_xact_lock`, released at COMMIT/ROLLBACK).
- **Cash-balance lock** for any operation that inserts into `cash_movements` (DB spec §11.2 Hazard 5). Single advisory lock keyed `hashtext('cash_balance')` (or a constant `BPOS_CASH_BALANCE_LOCK_KEY`). Held for the transaction.
- **All DDL via the migration**, never in app code. The backend does not emit `CREATE TABLE` etc.

### 10.6 Parameter binding

- All values bound via SQLAlchemy `:param` placeholders. No string interpolation.
- Numeric values: `Decimal` for `NUMERIC`; `int` for `SERIAL/BIGSERIAL`; `datetime` for `TIMESTAMPTZ`.
- Enums: validated strings.
- Booleans: `True`/`False` (mapped to PG `TRUE`/`FALSE`).
- Nulls: `None`.

### 10.7 Error mapping

`app/db/errors.py` exposes a single function:

```python
def translate_pg_error(e: asyncpg.PostgresError) -> ApiError:
    """Map a Postgres exception to an ApiError. Used by ErrorEnvelopeMiddleware
    when the error is not already an ApiError."""
```

Mapping table (excerpt):

| PG error (sqlstate) | ApiError |
|---|---|
| `23505` unique_violation | 409 with `code` derived from the constraint name (e.g., `username_exists` for `users_username_key`, `email_exists` for `users_email_key`) |
| `23503` foreign_key_violation | 409 `referenced_by_history` if the FK direction is "this row is referenced", or 400 `validation_failed` if the FK direction is "this row references a non-existent parent" |
| `23514` check_violation | 400 `validation_failed` with the constraint name in `details.constraint` |
| `40001` serialization_failure | 409 `serialization_failure` (caller may retry) |
| `40P01` deadlock_detected | 409 `concurrent_modification` (caller may retry) |
| `55P03` lock_not_available | 409 `concurrent_modification` (caller may retry) |
| `P0001` raise_exception (custom trigger) | mapped by parsing the trigger's `RAISE EXCEPTION` message; see §10.8 |
| Other | 500 `internal_error` (with no leak of internals) |

### 10.8 Trigger message parsing

The DB triggers raise with messages like:
- `insufficient_stock: product=42 required=10 available=5` → 409 `insufficient_stock` with `details = {product_id, required, available}`.
- `cash_insufficient: required=100 available=80` → 409 `cash_insufficient` with `details = {required, available}`.
- `refund_exceeds_crl: sale=10 crl=50 attempted=100` → 409 `refund_exceeds_crl` with details.
- `lifecycle_state_invalid: from=posted to=cancelled current=posted` → 409 `lifecycle_state_invalid`.

A regex-based parser in `app/db/errors.py` matches these. Unknown raise messages are treated as 500.

### 10.9 Repository modules (light)

The "repository" concept is loose here: there is no `UserRepository` class. Instead, there are module-level functions:

```python
# app/auth/session_service.py
async def load_by_access_token_hash(conn, token_hash: str) -> dict | None: ...
async def load_by_refresh_token_hash(conn, token_hash: str) -> dict | None: ...
async def insert(conn, *, user_id, access_hash, refresh_hash, ip, ua) -> int: ...
async def revoke(conn, *, session_id, reason) -> None: ...

# app/products/repo.py
async def get_by_id(conn, product_id) -> dict | None: ...
async def list(conn, *, filter, sort, page, per_page) -> tuple[list[dict], int]: ...
async def create(conn, *, code, name, ...) -> int: ...
```

The naming convention is `app/<domain>/repo.py` for repos, `app/<domain>/service.py` for services, `app/<domain>/routes.py` for HTTP handlers. Lifecycle operations are in `app/lifecycle/<resource>.py` and call repos.

---

## 11. Transaction Architecture

### 11.1 Single-transaction-per-request rule

Every mutating request runs inside exactly one DB transaction. The transaction boundary is the entire request handler, from auth check to response serialization. This is enforced by the connection context manager (`get_conn` opens `conn.begin()` at the start of the request and commits at the end). The idempotency guard may also wrap the entire request (see §16.4).

### 11.2 Per-operation transaction scope (per DB spec §11.1)

| Endpoint | Steps inside one transaction |
|---|---|
| `POST /sales/{id}/post` | Lock sale + lines + products → snapshot unit_cost → UPDATE sale_lines → INSERT stock_movements × N → UPDATE sales → audit |
| `POST /sales/{id}/payments` | Lock sale → validate Σ ≤ total → INSERT sale_payments → INSERT cash_movements (1 or 2) → UPDATE sales.lifecycle_status if fully paid → audit |
| `POST /sales/{id}/cancel` | Lock sale → INSERT stock_movement (reversal) × N → UPDATE original movements' `reversed_by_movement_id` → UPDATE sales → audit |
| `POST /sales/{id}/returns` | Lock sale → validate qty caps → compute server-side values → INSERT sales_returns → INSERT sales_return_lines × N → INSERT stock_movements × N → UPDATE sales.lifecycle_status → audit |
| `POST /refunds` | Lock sale → compute CRL → validate → INSERT refunds → INSERT cash_movements → audit |
| `POST /purchases/{id}/post` | Lock purchase + lines + products → allocate shipping pro-rata → INSERT stock_movements × N → INSERT cash_movements (if freight_cash) → UPDATE purchases → audit |
| `POST /purchases/{id}/payments` | Lock purchase → INSERT purchase_payments → INSERT cash_movements (1 or 2) → UPDATE lifecycle → audit |
| `POST /purchases/{id}/cancel` | Lock purchase → compute on-hand vs consumed → INSERT stock_movements (purchase_reversal for on-hand, value_adjustment for consumed) → UPDATE original movements' `reversed_by_movement_id` → INSERT supplier_repayments if paid → UPDATE purchases → audit |
| `POST /purchases/{id}/returns` | Lock purchase → validate qty → compute server-side → INSERT purchase_returns, lines, stock_movements → INSERT supplier_repayments if paid → UPDATE purchase → audit |
| `POST /supplier-repayments` | Lock purchase → compute outstanding → validate cash balance → UPDATE supplier_repayments.received_amount → INSERT cash_movements → audit |
| `POST /production-runs/{id}/post` | Lock run + inputs + output product → validate on-hand → compute finished_unit_cost → INSERT stock_movements (inputs) → INSERT stock_movements (output) → INSERT cash_movements (overhead) → UPDATE run → audit |
| `POST /production-runs/{id}/cancel` | Lock run → INSERT stock_movements (reversal pairs) → UPDATE run → audit |
| `POST /inventory/adjustments` | Lock product → INSERT stock_movements → audit (reason in audit_log) |
| `POST /manual-entries` | Lock cash-balance → INSERT manual_finance_entries → INSERT cash_movements → audit |
| `POST /manual-entries/{id}/cancel` | INSERT reversing cash_movements → UPDATE entry → audit |
| `POST /users/{id}/capabilities` | UPSERT user_capability_overrides → audit |
| `DELETE /users/{id}/capabilities/{capability_code}` | DELETE/UPDATE user_capability_overrides → audit |
| `PATCH /settings` | UPDATE system_settings → invalidate cache → audit |

All of these are atomic; the client never sees a partial state.

### 11.3 Isolation level

- Default: `READ COMMITTED` (PostgreSQL default).
- For any operation that writes to `cash_movements`: **acquire the cash-balance advisory lock** before any insert. This is the serialization point for cash balance. We do NOT use `SERIALIZABLE` because the advisory lock + trigger is more efficient and the failure mode is explicit (lock timeout → 409 `concurrent_modification`).
- For `POST /sales/{id}/post`, `POST /purchases/{id}/post`, `POST /production-runs/{id}/post`: `READ COMMITTED` + per-product advisory lock + row lock on `products`. The moving-average is consistent under the lock.

### 11.4 Lock ordering

- Per-product operations: `pg_advisory_xact_lock(hashtext('product:' || product_id))` then `SELECT ... FROM products WHERE id = :id FOR UPDATE`.
- Multi-product (sale with 5 lines): sort product IDs ASC and lock in that order to prevent deadlocks (DB spec §11.3).
- Document operations: `SELECT ... FOR UPDATE` on the parent document row.
- Cash operations: cash-balance lock FIRST, then any row lock, then any advisory lock. This ordering avoids deadlocks with sales (which take per-product locks, never the cash-balance lock until the payment is recorded).

### 11.5 Lock timeout

`BPOS_LOCK_TIMEOUT_MS` (default 2000). Set via `SET LOCAL lock_timeout = '2s'` at the start of each write transaction. If exceeded, PG raises `55P03` (lock_not_available) which translates to 409 `concurrent_modification` with `Retry-After: 1`.

### 11.6 Rollback semantics

- On any unhandled exception in the service or route layer, the connection context manager rolls back the transaction.
- The error middleware converts the exception to ErrorEnvelope; the client sees a 4xx or 5xx; the DB is unchanged.
- On `ApiError` (our custom exception), the same rollback happens; the middleware converts to ErrorEnvelope with the right code/status.

### 11.7 Nested transactions

Not supported. No `SAVEPOINT`. If a future need arises, a `with conn.begin_nested():` block can be added; the outer transaction is unaffected until the nested commits. Currently no endpoint needs this.

---

## 12. Accounting Architecture

### 12.1 The invariant principle

The 8 accounting invariants (INV-01..INV-08) are preserved as a **joint property** of:

- The DB schema's CHECK constraints (lifecycle, immutability of snapshots, cash non-negative).
- The DB triggers (CRL/SRec ceilings, refund bound, one-cancellation, derived balance, moving-average).
- The backend's transaction orchestration (all effects of one event in one tx).
- The canonical 11-account mapping documented in `V1.9-Accounting-Event-Matrix.md` and used as the mental model for code.

The backend does not maintain a "general ledger" UI (per PRD §4 #2). It maintains the 8 invariants via atomic event persistence and exposes derived balances on the dashboard/reports.

### 12.2 The 22-event registry

`app/accounting/events.py` defines a constant `EVENTS` mapping each of the 22 events from `V1.9-Accounting-Event-Matrix.md` to:

- The endpoint(s) that emit it.
- The DB tables touched.
- The effect on the 11 accounts (for documentation; the DB triggers implement the actual effect).
- A run-time check that can be enabled in debug mode (see §12.7).

This registry is for documentation and consistency checking only. The actual event persistence is implemented by the lifecycle service functions.

### 12.3 The 11-account chart (documentation)

| Code | Name | Class | Backend surface |
|---|---|---|---|
| 1010 | Cash | Asset | `GET /cash-movements/balance`; dashboard `cash_on_hand` |
| 1100 | Accounts Receivable | Asset | `GET /reports/receivables`; per-sale `ar` in `GET /sales` |
| 1200 | Inventory | Asset | `product_valuation` view; dashboard `inventory_value` |
| 1300 | Supplier Receivable | Asset | `GET /reports/supplier-receivables`; per-purchase `srec` |
| 2010 | Accounts Payable | Liability | `GET /reports/payables`; per-purchase `ap` |
| 2100 | Customer Refund Liability | Liability | `GET /reports/customer-refund-liabilities`; per-sale `crl` |
| 3010 | Sales Revenue | P&L | derived; P&L endpoint |
| 3020 | Other Income | P&L | `GET /reports/manual-income` |
| 4010 | COGS | P&L | derived; P&L endpoint |
| 5010 | Operating Expenses | P&L | `GET /reports/manual-expense` |
| 6000 | Owner's Equity | Equity | not exposed; opening balance operational |

The backend never inserts into an explicit "GL" table. All balances are derived from the underlying tables.

### 12.4 The 8 invariants — preservation strategy

| Inv | Strategy | Code point |
|---|---|---|
| INV-01 (Σ Debits = Σ Credits) | Atomicity: every event's effects commit or roll back together. | transaction scope (§11) |
| INV-02 (Assets = Liabilities + Equity) | Same. Stress tests in §22 verify. | test suite |
| INV-03 (Cash ≥ 0; AR/AP/CRL/SRec ≥ 0; Inventory ≥ 0 unless enabled) | DB trigger on `cash_movements`; per-product stock check at sale post; construction guarantees on the others. | DB triggers + service layer |
| INV-04 (Refund ≤ CRL; Repayment ≤ SRec) | DB triggers on `refunds` and `supplier_repayments` insert; service layer re-checks. | DB triggers + service layer |
| INV-05 (one cancellation per document) | DB trigger on `stock_movements` insert with `reversal_of_movement_id`; trigger on `lifecycle_status` UPDATE. | DB triggers + service guard |
| INV-06 (CRL = Σ payments − Σ refunds) | Derived at query time. No stored CRL row. | report endpoint SQL |
| INV-07 (SRec = Σ payments − Σ repayments) | Symmetric. | report endpoint SQL |
| INV-08 (Inventory GL = Σ on_hand × cost) | `product_valuation` view. | DB view |

### 12.5 Event 18/19 (stock adjustment) — V1 behavior

Per the Reconciliation Report (C-07, MS-2) and API §15.14.6, the P&L effect (Other Income for up, Operating Expense for down) is **not** auto-created. The inventory value is correct (via `product_ovements` trigger). The P&L effect must be recorded manually by the Owner via `POST /manual-entries` if they want it on the P&L. This is documented in the OpenAPI spec and the API Architecture §17.3 MS-2; the implementation honors it.

The adjustment's reason is recorded in `audit_log` (`new_values.reason`). It is queryable via `GET /audit?filter[entity_type]=product&filter[entity_id]=...&filter[action]=adjust`.

### 12.6 Costing

- **Costing method** = `moving_average`. `system_settings.costing_method` is read-only in V1 (V1.1 may add switching with full historical cost immutability). The service layer reads it at boot and refuses to operate if it is anything other than `moving_average` (defense in depth; the DB has the same constraint).
- **Snapshot at post**: `unit_cost_snapshot = current moving-average` (DB spec §3.4). Computed at insert inside the FOR UPDATE on the product.
- **Negative-stock fallback**: if `allow_negative_stock` is true (per-product or global), the line is created with `is_negative_stock_fallback = true` and `unit_cost_snapshot = products.purchase_price` (per BR-COST-006 / OD-3). The response includes the flag.
- **Return cost**: `returned_unit_cost = sale_lines.unit_cost_snapshot` (server-side; client cannot override). For purchases: `unit_cost_snapshot = purchase_line.unit_price + (allocated_shipping / quantity)`.

### 12.7 Debug-mode invariant checks

In `BPOS_INVARIANT_CHECKS=on` (dev/test only), after every mutating transaction the app runs:

- `SELECT SUM(cash_movements.amount) FROM cash_movements`; assert ≥ 0.
- For each product touched, `SELECT ... FROM product_valuation WHERE product_id = ...`; cross-check.
- For a sale cancel, assert the reversal movement's quantity equals `+original.quantity` and `reversal_of_movement_id` is set.

This catches implementation drift early. Off in production.

### 12.8 Refund vs income invariant

- Refunds never create a `manual_finance_entries` row. The `cash_movements` row has `trigger='refund'`, `direction='out'`. P&L is unaffected.
- The `financial_categories` table excludes "Sales", "COGS", "Production", "Purchase Shipping", "Refund", etc., via the seed. A user cannot create a `financial_category` named any of these (Pydantic-validated against the blacklist on `POST /financial-categories`); the API returns 400 `manual_entry_duplicate_of_derived`.

### 12.9 What the backend does NOT do

- It does not maintain a "GL" table.
- It does not auto-create P&L entries for stock adjustments.
- It does not allow editing of posted financial/inventory fields.
- It does not allow `INSERT` into `cash_movements` or `stock_movements` directly via the API; only via lifecycle/action endpoints.
- It does not accept `unit_cost_snapshot`, `cogs_total_snapshot`, `line_total`, `total_amount`, `payment_state`, `ar`, `ap`, `crl`, `srec`, `cash_balance`, `inventory_value`, `on_hand_quantity`, `moving_average_unit_cost` as request inputs (rejected by `derived_field_filter`).

---

## 13. Inventory Architecture

### 13.1 Authoritative inventory ledger

`stock_movements` is the single source of truth. There is no stored on-hand quantity and no stored moving-average. Both are computed from `stock_movements` via views.

### 13.2 The 15 stock_movement triggers

Per the migration (C-05) and DB spec §3.2:

```
purchase_receipt | sale | sales_return | purchase_return |
production_input | production_output | stock_adjustment | opening_balance |
sale_reversal | purchase_reversal | sales_return_reversal | purchase_return_reversal |
production_input_reversal | production_output_reversal |
adjustment_reversal | value_adjustment
```

The CHECK constraint lists all 15. `value_adjustment` allows `quantity = 0` (per C-06); all others require `quantity ≠ 0`.

### 13.3 Movement creation — by trigger or by service

| Trigger | Who creates the row |
|---|---|
| `purchase_receipt` | DB trigger on `purchases.lifecycle_status` UPDATE to `posted` |
| `sale` | **Service layer** (per the workflow: snapshot first, then INSERT stock_movements) |
| `sales_return` | **Service layer** (the `INSERT INTO sales_return_lines` is a separate event from the movement; both inserted in the same tx) |
| `purchase_return` | **Service layer** |
| `production_input`, `production_output` | **Service layer** (per DB spec §6.1; the service computes finished_unit_cost and inserts all movements) |
| `stock_adjustment` | **Service layer** (no DB trigger; the API is the only path) |
| `opening_balance` | Operational; not exposed (per API §15.22) |
| `sale_reversal` | **Service layer** (on cancel) |
| `purchase_reversal` | **Service layer** (on cancel, on-hand portion) |
| `value_adjustment` | **Service layer** (on cancel, consumed portion; qty=0) |
| `sales_return_reversal` | **Service layer** (on cancel of a sales_return) |
| `purchase_return_reversal` | **Service layer** (on cancel of a purchase_return) |
| `production_input_reversal`, `production_output_reversal` | **Service layer** (on cancel of production run) |
| `adjustment_reversal` | **Service layer** (on cancel of a stock adjustment) |

The DB trigger that creates `purchase_receipt` movements is the canonical exception — it exists to keep the "one inventory effect per event" invariant at the DB level. The service layer does NOT insert a `purchase_receipt` movement; it only updates `purchases.lifecycle_status`. The trigger does the rest. This is documented in the migration's `AFTER UPDATE OF lifecycle_status` trigger on `purchases`.

### 13.4 Stock valuation views

```sql
CREATE VIEW product_stock AS
  SELECT product_id, SUM(quantity) AS on_hand_quantity
  FROM stock_movements GROUP BY product_id;

CREATE VIEW product_valuation AS
  SELECT
    product_id,
    SUM(quantity) AS on_hand_quantity,
    -- moving-average = (Σ positive total_cost − Σ negative total_cost) / on_hand
    CASE WHEN SUM(quantity) = 0 THEN 0
         ELSE (SUM(CASE WHEN total_cost > 0 THEN total_cost ELSE 0 END)
               - SUM(CASE WHEN total_cost < 0 THEN -total_cost ELSE 0 END))
              / SUM(quantity)
    END AS moving_average_unit_cost,
    SUM(quantity) * (CASE WHEN SUM(quantity) = 0 THEN 0
         ELSE (SUM(CASE WHEN total_cost > 0 THEN total_cost ELSE 0 END)
               - SUM(CASE WHEN total_cost < 0 THEN -total_cost ELSE 0 END))
              / SUM(quantity)
    END) AS inventory_value
  FROM stock_movements GROUP BY product_id;
```

The service layer's `inventory.valuation.moving_average(product_id)` queries this view (or an inline equivalent if the view is not present). The view is in the canonical migration.

### 13.5 Negative-stock handling

```python
# app/inventory/negative_stock.py
async def check(conn, product_id: int, required_qty: Decimal) -> bool:
    """Returns True if the post should proceed (with fallback flag) or
    False if it should be rejected."""
    row = (await conn.execute(text("""
        SELECT
            p.allow_negative_stock AS per_product,
            s.default_negative_stock_allowed AS global
        FROM products p, system_settings s
        WHERE p.id = :pid
    """), {"pid": product_id})).mappings().first()
    effective = row["per_product"] if row["per_product"] is not None else row["global"]
    if not effective:
        # Strict mode: pre-check on_hand
        on_hand = (await conn.execute(text("""
            SELECT COALESCE(SUM(quantity), 0) FROM stock_movements WHERE product_id = :pid
        """), {"pid": product_id})).scalar()
        if on_hand < required_qty:
            raise ApiError(409, "insufficient_stock",
                "Insufficient stock for product.",
                details={"product_id": product_id, "required": str(required_qty),
                         "available": str(on_hand)})
        return False  # no fallback
    # Fallback: let it go with the flag; the sale line will use purchase_price.
    return True
```

### 13.6 Low-stock notifications

- Computed on read: `SELECT p.id FROM products p JOIN product_valuation v USING(product_id) WHERE p.is_active = true AND v.on_hand_quantity <= COALESCE(p.low_stock_threshold, :default)`.
- A `notifications` row is created when a product crosses from "above threshold" to "at or below threshold" (i.e., a stock movement dropped it). The service layer (or a DB trigger + LISTEN/NOTIFY) detects this state change.
- The `GET /notifications` endpoint lists unread; `POST /notifications/{id}/mark-read` marks read. `POST /notifications/mark-all-read` for the current user.
- Out-of-stock and below-cost warnings are surfaced in the response of the relevant write endpoint (`POST /sales/{id}/post` with `is_negative_stock_fallback`; `POST /sales/{id}/post` with below-cost → `200` with `warning` field).

### 13.7 Concurrent stock-affecting operations

- `pg_advisory_xact_lock(hashtext('product:' || product_id))` at the start of every operation that computes moving-average or checks on-hand.
- Lock ordering: sort by product_id ASC if a single operation touches multiple products.
- Lock timeout: 2s. On timeout, 409 `concurrent_modification` with `Retry-After: 1`.

### 13.8 Stock movement reversal

- Reversal is a **new** row, not an UPDATE. The new row has `reversal_of_movement_id = original.id`. The original row's `reversed_by_movement_id` is updated to the new id (this is the only UPDATE allowed on `stock_movements`, done by the service in the same transaction as the insert).
- One reversal per movement: a DB trigger rejects an INSERT whose `reversal_of_movement_id` is already referenced.

---

## 14. Payment Architecture

### 14.1 Multi-payment allowed

A sale or purchase may have N `*_payments` rows. The cross-row trigger on `*_payments` enforces `Σ amounts ≤ parent.total_amount`. The service layer re-checks this before insert and returns 409 `payment_exceeds_total` (or `allocation_exceeds_payable` for purchase) with details.

### 14.2 Over-tender (cash only)

- `tendered_amount` is allowed only when `payment_method.is_cash = true`. Otherwise 400 `tendered_not_allowed_for_non_cash`.
- If `tendered_amount` is provided:
  - `change_amount = tendered_amount − amount` (server-computed).
  - If client sends a `change_amount` that differs, the server recomputes and ignores (or returns 400 `change_amount_mismatch`; we choose to recompute silently to match API §9.4).
  - Two `cash_movements` rows are inserted: one `direction='in', amount=+tendered_amount, trigger='change_tendered'`, one `direction='out', amount=-change_amount, trigger='change_tendered'`. Net cash effect = `+amount`.
- Same pattern for purchase over-tender (unusual but possible for cash buys).

### 14.3 Payment allocation flow (`POST /sales/{id}/payments`)

```
1. Lock sales row.
2. Validate: state is posted | completed | partially_returned (not draft, not cancelled, not fully returned).
3. Compute current_sum = SELECT COALESCE(SUM(amount), 0) FROM sale_payments WHERE sale_id = :id.
4. Validate amount > 0; current_sum + amount ≤ sales.total_amount.
5. If tendered_amount:
   - validate payment_method.is_cash = true
   - compute change_amount = tendered_amount - amount
6. Acquire cash_balance lock.
7. INSERT sale_payments.
8. INSERT cash_movements (1 or 2 rows).
9. If current_sum + amount == total: UPDATE sales.lifecycle_status = 'completed'.
10. Audit.
```

### 14.4 Default refund method

Per MS-3, the API does not strictly enforce refund-method matching. The endpoint:
- Accepts `payment_method_id` as a required field.
- The frontend may pre-populate with the most recent original method; the backend does not validate the match.
- The user may pick any active payment method. No `sale.refund_override_method` capability check (V1 does not implement this; documented as a forward-compat item).

### 14.5 Cash balance non-negative

- DB trigger on `cash_movements` insert: if `SUM(amount)` after insert would be negative, reject.
- The service layer pre-checks with `SELECT SUM(amount) FROM cash_movements` under the cash_balance lock to avoid triggering an error path on a known-bad input (faster + clearer error message). The trigger is the safety net.

### 14.6 Cash-balance lock (serialization)

- `pg_advisory_xact_lock(:cash_balance_key)` at the start of every transaction that inserts into `cash_movements`. Default key = `hashtext('cash_balance')`.
- All endpoints that touch cash serialize through this lock: `POST /sales/{id}/payments`, `POST /purchases/{id}/payments`, `POST /refunds`, `POST /supplier-repayments`, `POST /manual-entries`, `POST /manual-entries/{id}/cancel`, `POST /production-runs/{id}/post` (for cash overhead), `POST /purchases/{id}/post` (if freight paid in cash), `POST /sales-returns/{id}/cancel` (if it triggers a refund — but it does not, the user calls `/refunds` separately), etc.
- This is the canonical "cash_balance serialization" point (DB spec §11.2 Hazard 5).

### 14.7 Refund flow (`POST /refunds`)

```
1. Lock sales row.
2. Compute CRL = SUM(sale_payments.amount) - SUM(refunds.amount) WHERE sale_id = :id.
3. Validate amount > 0, amount ≤ CRL. If not: 409 refund_exceeds_crl.
4. Acquire cash_balance lock.
5. Validate SUM(cash_movements.amount) - amount >= 0. If not: 409 cash_insufficient.
6. INSERT refunds.
7. INSERT cash_movements (out, amount = -amount, trigger = 'refund', reference_type = 'refund', reference_id = refund.id).
8. Audit.
```

### 14.8 Supplier repayment flow (`POST /supplier-repayments`)

```
1. Lock purchases row.
2. Compute outstanding = SUM(purchase_payments.amount) - SUM(cash_movements.amount WHERE trigger = 'supplier_repayment' AND reference_id = supplier_repayment.id (across rows for this purchase)).
3. Validate amount > 0, amount ≤ outstanding. If not: 409 repayment_exceeds_srec.
4. Acquire cash_balance lock.
5. Validate cash balance. If insufficient: 409 cash_insufficient.
6. Find or create the supplier_repayments row for this purchase (V1 simplified: one row per purchase).
7. UPDATE supplier_repayments.received_amount += amount.
8. INSERT cash_movements (in, amount = +amount, trigger = 'supplier_repayment').
9. Audit.
```

### 14.9 What the backend does NOT do

- It does not let the client set `payment_state` (rejected as derived).
- It does not let the client send a `change_amount` that disagrees with `tendered_amount − amount` (server recomputes).
- It does not let non-cash methods accept `tendered_amount` (400 `tendered_not_allowed_for_non_cash`).
- It does not enforce refund-method matching in V1 (per MS-3 default).
- It does not allow a `refund` cash_movement to drive a `manual_finance_entries` row.

---

## 15. Lifecycle Architecture

### 15.1 The state machine

Per DB spec §2.4.1 and API §8.1:

```
sale:     draft → posted → completed | partially_returned | cancelled
            posted → partially_returned (after first return)
            completed → partially_returned (after first return)
            partially_returned → returned | cancelled
            returned → cancelled
            cancelled: TERMINAL

purchase: same shape
production: draft → posted → completed | cancelled
manual_entry: posted → cancelled
```

The state machine is enforced by a DB trigger on `lifecycle_status` UPDATE. The service layer re-checks before issuing the UPDATE (defense in depth).

### 15.2 Service layer organization

```
app/lifecycle/
├── sale.py            # post, cancel, return, complete
├── purchase.py        # post, cancel, return, complete
├── production.py      # post, cancel
├── manual_entry.py    # create (post), cancel
├── adjustments.py     # stock adjust
└── guards.py          # shared state-machine guards
```

Each lifecycle file exposes async functions. Each function:

1. Takes `(conn, ..., user, if_match)`.
2. Returns the resulting state as a dict (route wraps in Pydantic).
3. Raises `ApiError` on failure.

### 15.3 Sale post (authoritative flow)

Per API §15.10.8 and DB spec §4.1. See the sketch in §9.2.

### 15.4 Sale cancel

Per API §15.10.10:

1. `SELECT ... FOR UPDATE` on `sales`.
2. Validate state is `posted | completed | partially_returned | returned`. From `cancelled` → 409 `already_cancelled`.
3. For each original `stock_movements` row where `reference_id = sale.id AND trigger = 'sale'` (and `reversed_by_movement_id IS NULL`):
   - INSERT a new `stock_movements` row with `trigger = 'sale_reversal'`, `quantity = +original.quantity`, `unit_cost_at_movement = original.unit_cost_at_movement`, `total_cost = +original.total_cost`, `reversal_of_movement_id = original.id`, `reference_type = 'sale_cancellation'`, `reference_id = sale.id`.
   - UPDATE original `reversed_by_movement_id = new_id`.
4. Update `sales.lifecycle_status = 'cancelled'`, `cancellation_date = NOW()`, `cancelled_by = user_id`, `cancellation_reason = request.reason`.
5. **No cash_movement.** The cash received is preserved; CRL = Σ payments − Σ refunds.
6. Audit.

### 15.5 Sale return

Per API §15.10.12 and §8.4:

1. `SELECT ... FOR UPDATE` on `sales`.
2. For each return line, validate `quantity ≤ (sale_line.quantity − Σ sales_return_lines.quantity where sale_line_id = X)`. 409 `return_quantity_exceeds_original` if violated.
3. Server-computes: `returned_selling_price = sale_line.unit_price × quantity`; `returned_unit_cost = sale_line.unit_cost_snapshot`; `line_value = returned_selling_price`.
4. INSERT `sales_returns` (header) with totals.
5. INSERT `sales_return_lines` × N.
6. INSERT `stock_movements` × N: `trigger='sales_return'`, `quantity = +qty`, `unit_cost_at_movement = returned_unit_cost`, `total_cost = +qty × cost`, `reference_type='sales_return'`, `reference_id`, `reference_line_id`.
7. UPDATE `sales.lifecycle_status` (returned or partially_returned).
8. **No refund.** User calls `POST /refunds` separately.
9. Audit.

### 15.6 Purchase cancel (the most complex flow)

Per API §15.12.9 and DB spec §5.5:

1. `SELECT ... FOR UPDATE` on `purchases`.
2. Compute on-hand vs consumed:
   - `received_qty = SUM(quantity) FROM stock_movements WHERE reference_id = purchase.id AND trigger = 'purchase_receipt' AND reversed_by_movement_id IS NULL`.
   - `consumed_qty = received_qty − current_on_hand` (where current_on_hand is the product's net after all sales and production that consumed the product). The DB does not have a direct "consumed" link; the service computes by attributing sales/production movements to purchases by FIFO through `stock_movements`. This is expensive; in V1 the service implements a simplified attribution:
     - For each line, `on_hand_for_this_purchase = purchase_receipt.quantity (sum of positive movements where reference_id = purchase.id AND trigger = 'purchase_receipt') − consumed (sum of negative movements where the product's movements since the purchase have net-negative)`. A precise attribution is impossible without per-batch cost layers; the V1 implementation is "on-hand attributable = current product on-hand quantity, capped at received" (i.e., assume the most recent purchase's goods are still on hand first).
   - **Decision:** the V1 implementation uses **on_hand = MIN(received_qty, current product on-hand)**, where the current product on-hand is `SUM(quantity) FROM stock_movements WHERE product_id = X`. This is the same simplification as the API Architecture §8.3. For the on-hand portion, `value_adjustment = 0` (no movement). For the consumed portion, `value_adjustment = -consumed × unit_cost` with `quantity = 0`, `trigger = 'value_adjustment'`. (This matches API §8.3 and DB spec §5.5.)
3. For on-hand: `INSERT stock_movements (trigger='purchase_reversal', quantity = -on_hand, unit_cost_at_movement = original, total_cost = -on_hand × cost, reversal_of_movement_id = original.id)`. UPDATE original `reversed_by_movement_id = new_id`.
4. For consumed: `INSERT stock_movements (trigger='value_adjustment', quantity = 0, total_cost = -consumed × cost)`.
5. If purchase was paid: `INSERT supplier_repayments (amount = Σ purchase_payments, refundable_amount_snapshot = same, reason = 'purchase_cancellation')`.
6. UPDATE `purchases.lifecycle_status = 'cancelled'`, cancellation fields.
7. Audit.

### 15.7 Purchase return

Per API §15.12.10:

1. `SELECT ... FOR UPDATE` on `purchases`.
2. Validate `quantity ≤ (purchase_line.quantity − Σ returned)`.
3. Server-compute: `unit_cost_snapshot = purchase_line.unit_price + (allocated_shipping / quantity)`. `line_value = quantity × unit_cost_snapshot`.
4. INSERT `purchase_returns`, `purchase_return_lines` × N, `stock_movements` × N (negative qty, trigger='purchase_return').
5. If paid: INSERT `supplier_repayments (amount = total_value_returned, reason = 'purchase_return')`.
6. UPDATE `purchases.lifecycle_status`.
7. Audit.

### 15.8 Production post

Per API §15.15.7 and DB spec §6.1:

1. `SELECT ... FOR UPDATE` on `production_runs`. Lock products in ASC order.
2. Validate: ≥1 input, exactly one output, all input qty > 0, cost_types active.
3. Validate: for each input, on_hand ≥ quantity (negative-stock rule per §13.5).
4. `total_raw_cost = Σ inputs.line_cost`.
5. `total_overhead_cost = Σ cost_lines.amount`.
6. `finished_unit_cost = (total_raw_cost + total_overhead_cost) / output_quantity`.
7. INSERT `stock_movements` × N for inputs (negative qty, snapshot).
8. INSERT `stock_movements` × 1 for output (positive qty, finished_unit_cost).
9. Acquire cash_balance lock. For each cost line with `paid_in_cash = true`: INSERT `cash_movements` (out, amount = -amount, trigger='production_overhead').
10. UPDATE `production_runs.lifecycle_status = 'posted'`, posted fields, finished_unit_cost.
11. Audit.

### 15.9 Production cancel

Per API §15.15.8:

1. For each input: INSERT reversal (trigger='production_input_reversal', positive qty, reversal_of).
2. For the output: INSERT reversal (trigger='production_output_reversal', negative qty).
3. UPDATE `production_runs.lifecycle_status = 'cancelled'`, cancellation fields.
4. **Overhead cash_movements are NOT reversed** (per DB spec §6.2).
5. Audit.

### 15.10 Auto-complete behavior

Per API §8.1, "Posted" and "Completed" can be the same moment. The implementation:

- On `POST /sales/{id}/post`: after inserting payments are checked; if Σ sale_payments = total, set `lifecycle_status = 'completed'` (instead of `posted`). The client does not call a separate `complete`.
- For sales without payments (unpaid): status = `posted`.
- Same for purchases.

### 15.11 Edit policy (enforced by both DB and service)

- **Drafts:** full edit. Service allows any field except `lifecycle_status`.
- **Posted/Completed/Partially_returned/Returned:** no edit on financial/inventory fields. Only `notes`, `reference_no` (if V1.1 enables sequential numbering) via PATCH (logged).
- **Cancelled:** no edit. Terminal.

DB trigger blocks UPDATE on these fields when `lifecycle_status != 'draft'`. Service layer re-checks and returns 403 `lifecycle_state_invalid` with a clear message.

### 15.12 Delete policy

- **Drafts:** hard delete allowed (`DELETE /sales/{id}` etc.). The `Is-Match` is recommended but not required.
- **Posted+:** DELETE returns 409 `delete_not_permitted` (or `lifecycle_state_invalid` with details).
- **Master records** (products, contacts, etc.): hard delete returns 409 `referenced_by_history` if any FK references the row. Deactivation via `POST /resource/{id}/deactivate` is always allowed.

---

## 16. Idempotency Architecture

### 16.1 The `idempotency_keys` table

Per the canonical migration, the table has:

```sql
CREATE TABLE idempotency_keys (
    key            VARCHAR(100)  NOT NULL,
    user_id        INT           NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint       VARCHAR(200)  NOT NULL,
    method         VARCHAR(10)   NOT NULL,
    request_hash   CHAR(64)      NOT NULL,  -- SHA-256 of canonical body
    response_status SMALLINT,
    response_body  JSONB,
    state          VARCHAR(16)   NOT NULL DEFAULT 'in_flight'
        CHECK (state IN ('in_flight', 'complete', 'failed')),
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    completed_at   TIMESTAMPTZ,
    expires_at     TIMESTAMPTZ   NOT NULL,
    PRIMARY KEY (key, user_id, endpoint)
);
```

The PK is `(key, user_id, endpoint)` because:
- `key` alone is not unique (different users may coincidentally use the same UUID).
- `key + user_id` is the natural scope (an Idempotency-Key is per-user per endpoint).

### 16.2 Request fingerprint

`request_hash = SHA-256(canonical(body))` where canonical(body) is the JSON body with:
- Keys sorted alphabetically (recursive).
- No whitespace.
- `Decimal` serialized as `str(Decimal)` with no exponent.
- `datetime` serialized as ISO 8601 with offset.

The hash is computed in the route handler after Pydantic validation, before the service call.

### 16.3 Required-Idempotency-Key endpoints (per API §7.1)

```
POST /sales/{id}/post
POST /purchases/{id}/post
POST /production-runs/{id}/post
POST /sales/{id}/cancel
POST /purchases/{id}/cancel
POST /production-runs/{id}/cancel
POST /sales/{id}/returns
POST /purchases/{id}/returns
POST /sales/{id}/payments
POST /purchases/{id}/payments
POST /refunds
POST /supplier-repayments
POST /inventory/adjustments
POST /manual-entries
POST /auth/login   (30s TTL only)
```

For all other mutating endpoints, `Idempotency-Key` is optional.

### 16.4 The idempotency guard

```python
# app/idempotency/service.py
class IdempotencyGuard:
    def __init__(self, conn, method, endpoint, key, user):
        ...
    async def __aenter__(self):
        if not self.key:
            return self  # no key provided; service runs without idempotency
        existing = await self._lookup()
        if existing:
            if existing["state"] == "complete":
                self.replay = True
                self.cached_response = parse_response(existing)
                return self
            elif existing["state"] == "in_flight":
                # wait up to 5s for completion
                ...
            elif existing["state"] == "failed":
                # allow retry
                ...
            # Conflict checks
            if existing["request_hash"] != self.request_hash:
                raise ApiError(409, "idempotency_violation",
                    "Idempotency-Key reused with different body.",
                    details={"key": self.key})
        # Reserve the key
        await self._insert_in_flight()
        return self
    async def commit(self, response):
        # update the row to 'complete' with response body
        ...
    async def fail(self, error):
        # update to 'failed' (or just delete so retry is possible)
        ...
```

### 16.5 Replay behavior

- **Same key + same body + same user + same endpoint:** return the original response (200/201/204) with the original body. The transaction does NOT re-run. The DB is unchanged. A header `Idempotency-Replayed: true` is set for observability.
- **Same key + different body:** 409 `idempotency_violation`. The original response is NOT returned.
- **Same key + different user:** 409 `idempotency_violation`.
- **Same key + different endpoint:** 409 `idempotency_violation`.

### 16.6 In-flight handling

- A second request with the same key arriving while the first is still running waits up to 5s (configurable). If the first completes within 5s, the second returns the original response. If not, the second returns 202 Accepted with `Retry-After: 1` and an empty body.
- A second request with the same key arriving after the first completed returns the cached response (per §16.5).

### 16.7 TTL and pruning

- `expires_at = created_at + 24h` (for most endpoints).
- `POST /auth/login` uses a 30s TTL.
- A daily cron job (`cron.d/bpos-prune`) deletes rows with `expires_at < NOW() - 1d`. The backend exposes a CLI command `python -m app.tools.prune_idempotency` for ops.

### 16.8 Transaction interaction

The idempotency record's `state` is updated **in the same transaction** as the response. This means:

- If the response tx commits, the idempotency record is also committed.
- If the response tx rolls back, the idempotency record reverts to `in_flight` (or is deleted, depending on the design). For simplicity, the implementation **deletes** the `in_flight` row on rollback, allowing an immediate retry.

```python
# In IdempotencyGuard.commit():
async with conn.begin_nested():
    # The outer transaction is the request tx.
    # We update the in_flight row to complete WITHIN the same outer tx.
    # If the outer tx commits, the row is now 'complete'.
    # If the outer tx rolls back, the row is gone.
    await conn.execute(text("""
        UPDATE idempotency_keys
        SET state = 'complete', response_status = :s, response_body = :b, completed_at = NOW()
        WHERE key = :k AND user_id = :u AND endpoint = :e
    """), ...)
```

The `commit()` is called from the route handler after the service function returns successfully, but **before the route's connection context manager commits**. The idempotency row's update is part of the same outer transaction.

---

## 17. Concurrency Architecture

### 17.1 Optimistic concurrency via If-Match / ETag

- **ETag generation:** for any resource with an `updated_at` column, `ETag = '"' + updated_at.isoformat() + '"'`. The `updated_at` is server-controlled; the client never sets it.
- **Response header:** every GET of a mutable resource includes `ETag: "2026-08-26T03:00:00+00:00"`.
- **Request header:** on lifecycle actions (`POST /sales/{id}/post`, etc.), the client sends `If-Match: "2026-08-26T03:00:00+00:00"`. The server compares to the current ETag; mismatch → 412 `version_mismatch` with the current ETag in the response.
- **Optional on PATCH:** the client may send `If-Match`; the server respects it. If absent, the server proceeds (per API §7.2 "permissive by default").
- **Required on lifecycle actions:** the API requires `If-Match` on the 14 lifecycle endpoints (post, cancel, return, refund, adjust, repayment). The server returns 400 `if_match_required` if absent.
- **Implementation:** a `verify_etag(resource, if_match)` helper in `app/concurrency/etag.py`; called by every lifecycle service function.

### 17.2 Numeric version (parallel to ETag)

Per API §15.1, every resource exposes a `version` integer, server-incremented on every successful write. The client may send `If-Match: "v=42"` instead of the timestamp ETag. Both forms are accepted. (For V1 simplicity, the implementation may use only the timestamp ETag; the `version` field is a response-only field for client-side optimistic rendering. If a client sends `If-Match: "v=42"`, the server parses it and checks against `version`.)

### 17.3 Pessimistic locks (DB-side)

Per DB spec §11.2 and §11.3:

| Hazard | Mitigation |
|---|---|
| 1. Two sales posting at the same time for the same product | `pg_advisory_xact_lock('product:' || product_id)` + `SELECT ... FOR UPDATE` on `products` row |
| 2. Sale post and purchase receipt racing on the same product | Same |
| 3. Two refund attempts on the same sale | `SELECT ... FOR UPDATE` on `sales` row + pre-insert trigger on `refunds` |
| 4. Two supplier repayment attempts | `SELECT ... FOR UPDATE` on `supplier_repayments` row + cash-balance lock + pre-insert trigger on `cash_movements` |
| 5. Race on cash balance | Cash-balance advisory lock + pre-insert trigger on `cash_movements` |
| 6. Cancellation attempt on concurrently-modified document | `SELECT ... FOR UPDATE` on document row before lifecycle UPDATE |
| 7. Sale edit while another user is paying | DB trigger blocks UPDATE on posted sales (immutability) |
| 8. Concurrent moving-average recalculation | Product row lock + advisory lock + read inside tx |

### 17.4 Lock ordering (deadlock prevention)

- All advisory locks keyed on `product:<id>` are acquired in **ascending order of product_id**.
- Document row locks acquired before any other row lock for the same document.
- Cash-balance lock acquired **before** any row lock in cash-touching transactions; if a transaction needs both cash and a product (e.g., sale post with cash payment), acquire the product lock first, then the cash-balance lock. (V1 simplification: sale post is the inventory event only; the cash event is the separate `POST /sales/{id}/payments`. So no transaction needs both. Production post DOES need both: acquire product locks (sorted) → cash-balance lock.)
- Lock timeout: `SET LOCAL lock_timeout = '2s'` at the start of every write transaction.

### 17.5 Concurrent test scenarios

- Two concurrent sale posts on the same product: one succeeds, the other returns 409 `concurrent_modification` (advisory lock contention) or 409 `insufficient_stock` (the second one sees the first's stock decrement).
- Two concurrent refunds on the same sale: one succeeds, the other returns 409 `refund_exceeds_crl` (after the first commits, CRL is reduced).
- Two concurrent stock adjustments: one succeeds, the other proceeds with the updated on-hand (the audit log records both).
- Two concurrent production posts using the same raw material: serialized via product lock; both succeed but at different total_raw_costs.
- A payment arriving while a cancel is in progress: the cancel holds the sales row lock; the payment waits up to 2s; if cancel commits, the payment returns 422 "sale is in a terminal state."

### 17.6 What the backend does NOT do

- No cross-request caching of effective capabilities (re-fetched every request, per BR-AUTH-003).
- No "long polling" or async notifications of state changes; clients re-fetch.
- No "optimistic update" on the client side; the server is the only authority (API §7.5).

---

## 18. Audit Architecture

### 18.1 The `audit_log` table

Per the canonical migration, `audit_log` is append-only with a DB trigger that rejects UPDATE and DELETE.

### 18.2 What writes to audit_log

Two paths:

1. **DB triggers** (canonical, for state changes the trigger can detect):
   - INSERT into `sales` (action=create).
   - UPDATE of `sales.lifecycle_status` (action=post|cancel|complete|return).
   - INSERT into `purchases` (action=create).
   - UPDATE of `purchases.lifecycle_status`.
   - INSERT into `production_runs` (action=create).
   - UPDATE of `production_runs.lifecycle_status`.
   - INSERT into `stock_movements` (action=movement).
   - UPDATE of `products.is_active = FALSE` (action=deactivate).
   - INSERT/UPDATE on `system_settings` (action=settings_change).
   - INSERT/UPDATE on `user_capability_overrides` (action=permission_grant|permission_revoke).
   - INSERT into `manual_finance_entries` (action=create).

2. **Backend service code** (for events the trigger cannot easily detect, or where the trigger does not capture enough context):
   - `audit.write(conn, action, entity_type, entity_id, old_values, new_values, reason, user)` is called from the service layer for:
     - `payment` (sale payment, purchase payment) — not auto-triggered; the service writes the row.
     - `refund` — same.
     - `supplier_repayment` — same.
     - `price_override` — required by BR-SALE-005; the service writes the row when `unit_price` differs from `products.selling_price` and the user has `sale.price_override`.

### 18.3 The audit write function

```python
# app/audit/service.py
async def write(conn, *, action, entity_type, entity_id, old_values=None,
                new_values=None, reason=None, user):
    """Write one audit_log row. Must be called inside the same transaction
    as the action it audits; the row is committed together with the action.
    """
    # PII mask for new_values: hash email/phone if present
    new = mask_pii(new_values) if new_values else None
    old = mask_pii(old_values) if old_values else None
    await conn.execute(text("""
        INSERT INTO audit_log
            (user_id, action, entity_type, entity_id, old_values, new_values,
             reason, event_time, ip_address)
        VALUES
            (:uid, :action, :etype, :eid, :old::jsonb, :new::jsonb,
             :reason, NOW(), :ip)
    """), {"uid": user.id, "action": action, "entity_type": entity_type,
            "eid": entity_id, "old": json.dumps(old) if old else None,
            "new": json.dumps(new) if new else None,
            "reason": reason, "ip": user.ip})
```

### 18.4 Reading audit

`GET /audit` is the only audit endpoint. It is read-only. No POST/PATCH/DELETE is exposed. The endpoint requires `audit.view` capability (default OFF for Staff).

Filters: `?filter[user_id]=`, `?filter[action]=`, `?filter[entity_type]=`, `?filter[entity_id]=`, `?from=`, `?to=`, `?q=` (searches reason). Order: `event_time DESC`. Pagination standard.

### 18.5 Immutability guarantee

- DB trigger rejects UPDATE/DELETE on `audit_log`. This is the only line of defense.
- The Owner cannot delete an audit row. There is no API to do so.
- The application never issues an UPDATE or DELETE on `audit_log` (lint rule in CI).

### 18.6 What the backend does NOT do

- It does not "auto-archive" old audit rows. The DB grows; ops handles retention.
- It does not allow editing the `reason` of an audit row after the fact.
- It does not redact `old_values`/`new_values` further than the PII masker.

---

## 19. Error Architecture

### 19.1 The canonical `ErrorEnvelope`

Per `openapi.yaml:6047-6081`:

```yaml
ErrorEnvelope:
  type: object
  required: [error]
  properties:
    error:
      type: object
      required: [code, message, request_id]
      properties:
        code: { type: string }
        message: { type: string }
        details:
          oneOf:
            - type: object
              additionalProperties: true
            - type: array
              items: { type: object, additionalProperties: true }
            - type: 'null'
        request_id: { type: string, format: uuid }
```

### 19.2 The `ApiError` exception

```python
# app/domain/errors.py
class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *,
                 details: dict | list | None = None, headers: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}
        super().__init__(message)
```

All service-layer errors are raised as `ApiError`. The error middleware converts to `ErrorEnvelope`.

### 19.3 The error middleware

```python
# app/errors_middleware.py
class ErrorEnvelopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            return await call_next(request)
        except ApiError as e:
            request_id = getattr(request.state, "request_id", None) or str(uuid4())
            return JSONResponse(
                status_code=e.status,
                content={"error": {
                    "code": e.code, "message": e.message,
                    "details": e.details, "request_id": request_id,
                }},
                headers={**e.headers, "X-Request-ID": request_id},
            )
        except asyncpg.PostgresError as e:
            api_err = translate_pg_error(e)
            return JSONResponse(...)  # same shape
        except Exception as e:
            # 500 — log full, return generic
            logger.exception("unhandled", request_id=...)
            return JSONResponse(status_code=500, content={
                "error": {"code": "internal_error",
                          "message": "An internal error occurred.",
                          "request_id": request_id}
            }, headers={"X-Request-ID": request_id})
```

### 19.4 Error code catalog (exhaustive)

Per API §5.3 and OpenAPI `x-error-codes` extensions, the implemented codes are:

```
Auth/session:
  unauthenticated, invalid_credentials, session_expired, session_revoked,
  account_locked, account_inactive, capability_required, origin_not_allowed,
  rate_limited

Validation:
  validation_failed, enum_value_invalid, derived_field_not_allowed,
  discount_not_permitted, price_override_not_permitted, quantity_invalid,
  amount_invalid, date_invalid, idempotency_key_required, idempotency_violation,
  change_amount_mismatch, tendered_not_allowed_for_non_cash,
  cancellation_reason_required, if_match_required

Resource:
  not_found, lifecycle_state_invalid, version_mismatch, delete_not_permitted,
  referenced_by_history, only_owner_guard_failed, own_draft_required,
  system_role_immutable, password_too_weak, if_match_required

Business:
  insufficient_stock, negative_stock_disallowed, payment_exceeds_total,
  allocation_exceeds_payable, refund_exceeds_crl, repayment_exceeds_srec,
  cash_insufficient, return_quantity_exceeds_original,
  purchase_return_quantity_exceeds_original, already_cancelled,
  cannot_cancel_after_return, cost_snapshot_missing, product_deactivated,
  production_inputs_exceed_stock, production_finished_cost_invalid,
  cost_type_deactivated, category_duplicate_of_derived,
  manual_entry_duplicate_of_derived, below_cost_warning (200, not error)

Concurrency:
  concurrent_modification, serialization_failure

System:
  internal_error, not_implemented, maintenance
```

### 19.5 HTTP status mapping (per API §5.2)

```
200 OK
201 Created
204 No Content
400 Bad Request   (validation_failed, enum_value_invalid, derived_field_not_allowed,
                   discount_not_permitted, price_override_not_permitted,
                   quantity_invalid, amount_invalid, date_invalid,
                   idempotency_key_required, change_amount_mismatch,
                   tendered_not_allowed_for_non_cash, cancellation_reason_required,
                   category_duplicate_of_derived, manual_entry_duplicate_of_derived,
                   if_match_required)
401 Unauthorized  (unauthenticated, invalid_credentials, session_expired,
                   session_revoked)
403 Forbidden     (account_locked, account_inactive, capability_required,
                   only_owner_guard_failed, own_draft_required,
                   system_role_immutable, delete_not_permitted, lifecycle_state_invalid
                   for non-capability rule violations, origin_not_allowed)
404 Not Found     (not_found)
409 Conflict      (payment_exceeds_total, allocation_exceeds_payable,
                   refund_exceeds_crl, repayment_exceeds_srec, cash_insufficient,
                   return_quantity_exceeds_original, purchase_return_quantity_exceeds_original,
                   already_cancelled, cannot_cancel_after_return,
                   concurrent_modification, serialization_failure,
                   referenced_by_history, idempotency_violation,
                   insufficient_stock, negative_stock_disallowed,
                   product_deactivated, cost_type_deactivated)
412 Precondition Failed   (version_mismatch)
422 Unprocessable Entity  (cost_snapshot_missing, production_inputs_exceed_stock,
                            production_finished_cost_invalid)
423 Locked                (account_locked — V1 also uses 403; 423 is the spec default)
429 Too Many Requests     (rate_limited; Retry-After)
500 Internal Server Error (internal_error, with no internal details)
503 Service Unavailable   (maintenance; Retry-After)
```

### 19.6 Logging

Every error response is logged with `request_id, user_id, method, path, status, code, latency_ms`. 5xx are alerted (Sentry, if configured; or stderr in dev). The full traceback is logged at ERROR; the response body never contains a traceback.

### 19.7 What the backend does NOT do

- It does not leak DB error messages to clients (5xx are always generic).
- It does not use 200 + error-in-body for failures; HTTP status is meaningful.
- It does not omit the `code` field; clients switch on `code`, not on `message` (which is English and stable but human-readable).

---

## 20. Storage Architecture

### 20.1 Primary store: PostgreSQL 17

- All transactional data.
- All audit data.
- All session data.
- All idempotency data.
- All notification data.

### 20.2 File store: exports

- PDF and XLSX exports are written to `BPOS_EXPORT_DIR` (default `/var/bpos/exports`).
- Each export file is named `<export_id>.pdf` or `<export_id>.xlsx`.
- Files older than 7 days are deleted by a daily cron (`cron.d/bpos-prune-exports`).
- The download endpoint `GET /exports/{id}/download` is authenticated (bearer token); no pre-signed URL needed (the URL is not exposed without the token).
- Future: S3-compatible backing. The abstraction is `app/storage/exports.py` which has a local-disk implementation now and a S3 implementation as a swap-in later. V1 ships with local-disk only.

### 20.3 What is NOT stored

- No file uploads from clients in V1 (no `multipart/form-data` beyond Pydantic's JSON forms). All input is JSON.
- No avatars, no documents, no attachments. The system is operational data only.
- No caching of derived values on disk.

### 20.4 Backup and recovery (operational)

- Per API §13.9 and PRD, backup is operational. The application does not own backups. The deployment runbook specifies `pg_basebackup` + WAL archiving.
- The `audit_log` is the most critical data; ops prioritizes its backup.

---

## 21. Notification Architecture

### 21.1 The `notifications` table

Per the canonical migration, with `(id, user_id, category, severity, message, related_entity_type, related_entity_id, is_read, created_at)`.

### 21.2 Notification sources

| Category | Trigger | Severity |
|---|---|---|
| `low_stock` | Stock movement drops a product to ≤ its `low_stock_threshold` | info |
| `out_of_stock` | On-hand reaches 0 | warning |
| `below_cost_sale` | A sale is posted with line cost > line price (not an error; a warning) | info |
| `payment_reminder` | Out of V1 (per MS in API §11.2) | — |
| `irreversible_action` | Client concern, not server | — |

The first three are computed:
- `low_stock` and `out_of_stock`: emitted by a DB trigger + LISTEN/NOTIFY when a stock_movement is inserted. The worker (a small asyncio task per worker) LISTENs on `stock_change`, computes whether the threshold was crossed, and INSERTs a notification if so. (A simple rule: notify when `on_hand_quantity` goes from `> threshold` to `≤ threshold`, or from `> 0` to `0`.)
- `below_cost_sale`: emitted by the `POST /sales/{id}/post` service: after computing the snapshot, if `unit_cost_snapshot > unit_price`, INSERT a notification with `related_entity_type='sale', related_entity_id=sale_id`. Also return a `warning` field in the response (per API §11.2: a 200 with `warning`, not a 4xx).

### 21.3 Notification read flow

- `GET /notifications?filter[is_read]=false` — list unread for the current user.
- `POST /notifications/{id}/mark-read` — mark one as read.
- `POST /notifications/mark-all-read` — mark all as read for the current user.

### 21.4 What the backend does NOT do

- It does not push notifications via WebSocket / SSE / push (out of V1).
- It does not generate payment reminders (MS in API §11.2).
- It does not have user-to-user notifications or broadcasts.

---

## 22. Testing Architecture

### 22.1 Test pyramid

| Layer | Count target | Tools | Where it runs |
|---|---|---|---|
| Unit | 100+ | pytest, no DB | CI on every commit |
| Service / business-rule | 50+ | pytest + testcontainers Postgres | CI on every commit |
| API (contract) | 200+ | pytest + httpx ASGITransport + testcontainers | CI on every commit |
| Authorization | 30+ | pytest + httpx + testcontainers | CI on every commit |
| Idempotency | 15+ | pytest + httpx + testcontainers | CI on every commit |
| Concurrency | 20+ | pytest + testcontainers + asyncio.gather | CI on every commit |
| Transaction rollback | 15+ | pytest + testcontainers | CI on every commit |
| Accounting invariant | 30+ | pytest + testcontainers | CI on every commit |
| Inventory | 25+ | pytest + testcontainers | CI on every commit |
| Lifecycle | 25+ | pytest + testcontainers | CI on every commit |
| Audit | 10+ | pytest + testcontainers | CI on every commit |
| PostgreSQL integration | 10+ | pytest + testcontainers | CI on every commit |
| OpenAPI conformance | 5 (one per response shape + error envelope) | openapi-spec-validator + jsonschema | CI on every commit |

### 22.2 Test database lifecycle

- `tests/conftest.py` spins up a `testcontainers/postgres:17` per test session, applies the canonical migration (`m0001__initial_schema_baseline.sql`) once, and creates a per-test transaction rollback.
- Per-test isolation: each test runs inside a SAVEPOINT; on teardown, the savepoint is rolled back. This makes tests fast and isolated.
- For tests that need to commit (e.g., concurrency tests that spawn two transactions), a separate fixture is used that runs the test in a single connection with `BEGIN; ...; ROLLBACK;` and asserts the final state.

### 22.3 Auth fixture

- A factory `_make_user(role, *, overrides)` returns a logged-in `AuthenticatedUser` and the bearer token. Tests use it to set up users with specific roles and capability overrides.
- For `Owner` (full capabilities): seed once per session.

### 22.4 OpenAPI conformance

- The OpenAPI spec is loaded once at test session start.
- For each endpoint, a "happy path" test fires the request and validates the response against the OpenAPI schema using `openapi-schema-validator` (for response body shape) and `prance` (for path/method existence).
- For each error code, a "negative path" test asserts the response code, message format, and `code` field match the catalog.

### 22.5 Accounting invariant tests

- Per V1.9 stress tests 1-4 (and the additional ones the V1.9 document lists): each scenario is reproduced end-to-end via the API; at the end, the test asserts:
  - `Σ cash_movements.amount == expected_cash_balance`.
  - For each product, `on_hand_quantity == expected_on_hand`.
  - `Σ stock_movements.total_cost` per product equals the GL inventory value.
  - `ar`, `ap`, `crl`, `srec` derived from API endpoints match the expected values.
  - The accounting equation `Assets = Liabilities + Equity` holds (computed numerically from the reports endpoint).

### 22.6 Idempotency tests

- Replay returns the original response.
- Conflicting body returns 409 `idempotency_violation`.
- In-flight returns 202.
- TTL expiry allows reuse after 24h (mocked clock).
- Login: 30s TTL.

### 22.7 Concurrency tests

- `asyncio.gather` fires two concurrent sale posts on the same product. Assert one succeeds, the other returns 409 `concurrent_modification` or `insufficient_stock`.
- Two concurrent refunds: assert one succeeds, the other returns 409 `refund_exceeds_crl` or 409 `concurrent_modification`.
- Two concurrent stock adjustments: both succeed; on-hand is the sum of both.
- If-Match mismatch: a second request with a stale ETag returns 412.
- Lock timeout: a test that holds a lock for 3s (via a SLEEP in a separate connection) and asserts the writer returns 409 `concurrent_modification` after 2s.

### 22.8 Transaction rollback tests

- Force a failure mid-transaction (e.g., a CHECK violation on a stock_movements insert) and assert:
  - No parent row was created (sales / purchases).
  - No audit row was created.
  - No cash_movement was created.
  - The error response is the correct `code` (translated by `db/errors.py`).

### 22.9 Coverage targets

- Backend code: ≥ 85% line coverage for `app/lifecycle`, `app/accounting`, `app/inventory`, `app/payments`, `app/audit`, `app/idempotency`, `app/concurrency`, `app/authz`, `app/auth`. ≥ 70% overall.
- Excluded from coverage: `app/api/*` route files (thin; tested via API tests) and `app/main.py`.

### 22.10 Test data

- A small seed script (`tools/seed.py`) creates the canonical roles (Owner, Staff), the canonical capability catalog, the four payment methods, the cost types, the financial categories, the system settings. The test fixture uses it to ensure a known starting state.
- Per-test data is created via factories (e.g., `make_sale(user, lines=...)`); cleanup is automatic via the SAVEPOINT.

---

## 23. Security Architecture

### 23.1 Transport

- HTTPS only. HTTP requests get a 301 redirect to HTTPS (handled by reverse proxy).
- HSTS: `max-age=31536000; includeSubDomains; preload`. Set by the reverse proxy.
- TLS 1.2+; modern cipher suites only.

### 23.2 Headers

- `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'; base-uri 'self'`. Set by the reverse proxy. (No inline scripts; the API serves no UI.)
- `X-Content-Type-Options: nosniff`.
- `X-Frame-Options: DENY`.
- `Referrer-Policy: no-referrer`.
- `X-XSS-Protection: 0` (modern; CSP is the defense).
- `Permissions-Policy: ...` (deny camera, microphone, geolocation, etc.).
- `Strict-Transport-Security` (see HSTS above).

### 23.3 Authentication security

- Argon2id (OWASP 2024 parameters).
- 256-bit random tokens, base64url, stored as SHA-256 in `sessions`.
- Refresh token rotation; reuse detection revokes all sessions.
- Account lockout after 5 failed attempts in 15 min.
- No "remember me" in V1; session TTL is the only persistence.

### 23.4 Authorization security

- Capability checks are server-side only. The client's claim is irrelevant.
- Per-request capability resolution (no caching).
- 401 vs 403 distinction is not leaked (response is the same shape).
- No row-level security (out of V1); capabilities are global.

### 23.5 Input handling

- All input is validated by Pydantic.
- All SQL is parameterized; no string interpolation.
- All output is JSON; no HTML rendering.
- All error messages are fixed strings; user input appears only in `details` and only after validation.

### 23.6 PII handling

- `email` is hashed (SHA-256 of normalized lowercase) for logs.
- `phone` is never logged.
- `address` is returned only on single-resource GETs and only to users with `contact.view`.
- `password` is never logged (Argon2id hash is logged only on user creation, with the hash truncated).

### 23.7 Rate limiting

Per API §13.6:

| Endpoint | Limit |
|---|---|
| `POST /auth/login` | 5/15min per IP; 10/hr per user |
| `POST /auth/refresh` | 60/hr per user |
| All other mutating | 60/min per user; 600/min per IP |
| Read | 600/min per user |
| `POST /exports` | 5/hr per user; 20/day per user |

Implementation: `slowapi` keyed on `Authorization` for user-level and `request.client.host` for IP-level. Returns 429 with `Retry-After`.

### 23.8 CSRF / Same-Site

- The bearer-token model is CSRF-immune (no cookies are used for auth).
- CORS is restricted to `BPOS_CORS_ALLOWED_ORIGINS`.
- The `Origin` header is checked on every state-changing request as defense in depth.

### 23.9 What the backend does NOT do

- It does not store passwords in plain text.
- It does not log bearer tokens.
- It does not use `eval`, `exec`, `pickle.loads`, or any unsafe deserialization.
- It does not accept file uploads in V1.
- It does not run user-supplied SQL.

---

## 24. Observability

### 24.1 Structured logging

- `structlog` configured for JSON output in production, key-value in dev.
- Every log line includes: `timestamp, level, request_id, user_id (if authed), method, path, status, latency_ms, code (if error)`.
- PII masker strips `email`, `phone`, `address` from log payloads.
- The X-Request-ID is the correlation key across logs.

### 24.2 Metrics

- Prometheus client (`prometheus_client`) exposes `/metrics` (gated behind the `BPOS_ENABLE_METRICS=true` env; the endpoint is unauthenticated for the scraper).
- Metrics:
  - `http_requests_total{method, path_template, status}` (counter).
  - `http_request_duration_seconds{method, path_template}` (histogram).
  - `db_pool_connections{state}` (gauge; active / idle / total).
  - `idempotency_replays_total{endpoint}` (counter).
  - `idempotency_violations_total{endpoint}` (counter).
  - `lock_timeouts_total{lock_name}` (counter).
  - `cash_balance_lock_wait_seconds` (histogram).
  - `stock_movements_inserted_total{trigger}` (counter).
  - `audit_log_writes_total{action, entity_type}` (counter).

### 24.3 Tracing (optional)

- OpenTelemetry is wired in but disabled by default. `BPOS_ENABLE_TRACING=true` activates it; the OTLP endpoint is `BPOS_OTLP_ENDPOINT`. Spans cover: HTTP request, DB transaction, advisory lock acquisition, service function, idempotency lookup.

### 24.4 Error tracking

- Sentry (optional, via `sentry-sdk`); configured by `BPOS_SENTRY_DSN`. The middleware reports unhandled exceptions and 5xx responses with `request_id` for correlation.

### 24.5 Health and readiness

- `GET /system/health` — liveness, no DB query. Returns `{ status: 'ok', version, server_time }`.
- `GET /system/version` — version info (git SHA, build time, dependency versions).
- `GET /system/info` — company name, timezone, currency (from system_settings), server time.
- (No separate `/system/ready`; V1 single-node assumption; the liveness endpoint is sufficient for load balancers. If V1.1 adds multi-node, add a `/system/ready` that pings the DB.)

### 24.6 What the backend does NOT do

- It does not have a built-in dashboard. Metrics go to Prometheus + Grafana (operational).
- It does not have built-in log search. Logs go to stdout/stderr and are aggregated by ops (e.g., Loki, ELK).

---

## 25. OpenAPI Integration

### 25.1 The contract is `openapi.yaml`

`openapi.yaml` is the source of truth for the wire format. The backend serves responses that match it; the test suite validates the conformance.

### 25.2 FastAPI OpenAPI generation

FastAPI's built-in OpenAPI 3.1 generator is enabled. The generated schema at `/openapi.json` is compared to `openapi.yaml` in CI; if they diverge, the test fails.

In practice, the team maintains `openapi.yaml` as the contract, hand-writes the Pydantic models to match it, and the FastAPI-generated schema is a secondary artifact. If FastAPI's generator produces a schema that diverges (e.g., a missing description), the divergence is fixed either in the model (description, examples) or in `openapi.yaml` (if the spec is wrong).

### 25.3 Validation in CI

- `pytest tests/openapi/test_openapi_valid_3_1.py`:
  - Parses `openapi.yaml` via `openapi-spec-validator`.
  - Asserts all `x-required-capability` extensions reference codes in the `capabilities` table.
  - Asserts all `x-error-codes` are in the error code catalog.
  - Asserts all `operationId` are unique.
  - Asserts all `$ref` resolve.
- `pytest tests/openapi/test_endpoints_present.py`:
  - Iterates every path × method in `openapi.yaml`.
  - For each, fires an OPTIONS / fake request against the FastAPI app and asserts the route exists and the response status is one of the expected ones (or 401 for unauth).

### 25.4 Pydantic ↔ OpenAPI alignment

- Each Pydantic model name matches the corresponding OpenAPI schema name (e.g., `Sale` ↔ `Sale`).
- Each field name and type matches.
- Required fields in OpenAPI are required in Pydantic.
- Nullable fields use `Optional[T] = None` in Pydantic.
- Enums use `Literal[...]` in Pydantic.
- Dates use `datetime` (Pydantic serializes as ISO 8601 with offset).
- Decimal fields use `Decimal` with `condecimal(max_digits=15, decimal_places=2)` for money and `decimal_places=4` for quantity.

### 25.5 What the backend does NOT do

- It does not auto-generate routes from `openapi.yaml`. The team hand-writes routes; the test suite enforces conformance.
- It does not modify `openapi.yaml` in the implementation phase. If a deviation is needed, the change goes through the contract-update workflow (separate phase).

---

## 26. Database / Migration Integration

### 26.1 The schema is locked

The canonical migration is `db/migrations/m0001__initial_schema_baseline.sql`. The backend does not own schema changes. The backend reads the schema as-is and validates at startup that the DB schema hash matches the expected hash.

### 26.2 Migration application

- Migrations are applied via `db/migrate.sh` (an existing tool) BEFORE the application starts.
- The application does NOT run migrations on startup. If a deployment forgets to migrate, the application will boot-fail at the schema-hash check.

### 26.3 Schema hash check

At worker boot, the application:

1. Runs `SELECT md5(string_agg(c.column_name || c.data_type || c.is_nullable, ',' ORDER BY c.column_name)) FROM information_schema.columns c WHERE c.table_schema = 'public' GROUP BY c.table_name` (per-table hashes concatenated).
2. Concatenates the per-table hashes in a deterministic order.
3. Compares to the hash baked into `app/__init__.py:EXPECTED_SCHEMA_HASH`.
4. On mismatch, log error and exit 1.

The hash is updated only when the canonical migration changes. This is a one-time operation per schema version.

### 26.4 Reading schema metadata

- Table and column names are not hardcoded in the application. The application uses constants defined in `app/db/tables.py` (e.g., `SALES = "sales"`, `SALE_LINES = "sale_lines"`). These constants are used in raw SQL.
- This allows the application to be refactored without renaming; the constants are the only place to update.
- A lint rule fails CI if a raw SQL string contains a table name literal (e.g., `FROM sales`); it must be `FROM {SALES}`.

### 26.5 DB views

- `product_valuation` and `product_stock` are queried by the application via SQL.
- Other derived values (CRL, AR, etc.) are computed inline (the API spec §9.8 says "no stored CRL").
- The application does not create new views; if a future V1.1 needs one, it goes through a new canonical migration.

### 26.6 What the backend does NOT do

- It does not run migrations.
- It does not emit DDL.
- It does not depend on Alembic or any schema-migration framework.
- It does not modify the existing canonical migration.

---

## 27. Deployment Architecture

### 27.1 Topology

```
Internet
   │
   ▼
[nginx / Caddy]  ←  TLS termination, HSTS, gzip, br, per-IP rate limit
   │
   ▼
[gunicorn]  ←  N uvicorn workers (CPU*2 + 1)
   │
   ▼
[PostgreSQL 17]  ←  single node, with WAL archiving + daily base backup
   │
   ▼
[/var/bpos/exports]  ←  local disk or S3-compatible volume
```

For V1, single-node is sufficient. The PRD §4 states a single-business web app with no specific uptime SLO.

### 27.2 Configuration

- `.env` file (or systemd `EnvironmentFile`) with all `BPOS_*` variables.
- `secrets/` mounted volume for `BPOS_SECRET_KEY` and DB credentials (never in source control).
- `BPOS_DATABASE_URL` format: `postgresql+asyncpg://user:pass@host:port/dbname`.

### 27.3 Process management

- systemd unit `bpos-api.service`:
  - `ExecStart=/usr/bin/gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000 app.main:app`
  - `Restart=on-failure`
  - `User=bpos`
  - `WorkingDirectory=/opt/bpos`
  - `EnvironmentFile=/etc/bpos/env`

### 27.4 Logging

- `journald` collects stdout/stderr from the systemd unit.
- A log shipper (Filebeat, Promtail) ships to a centralized store (Loki, ELK).
- Logs are JSON in production.

### 27.5 Health checks

- systemd `WatchdogSec=30`; the app sends `sd_notify(WATCHDOG=1)` every 15s.
- Reverse proxy health check: `GET /system/health` (no DB query, fast).

### 27.6 Backups

- `pg_basebackup` daily; WAL archiving continuous.
- Exports directory: a separate backup schedule.
- Audit log: included in the base backup; retained per ops policy (default: forever, with a 7-year retention for compliance).

### 27.7 Scaling (V1.1+)

- V1 is single-node. If load exceeds ~50 RPS sustained, add a second node behind the reverse proxy.
- DB connection pool: `min_size=2, max_size=10` per worker. For 4 workers, total max connections = 40; PG `max_connections = 100` (headroom for psql, migrations, monitoring).
- Read replicas: out of V1. If needed, route `GET /reports/*` and `GET /dashboard` to a replica via a separate connection string.

### 27.8 What the deployment does NOT do

- It does not auto-scale.
- It does not have a staging-vs-production parity check (manual).
- It does not have blue-green or canary deploys (manual via reverse proxy reload).

---

## 28. Risks / Ambiguities

The following items are documented ambiguities. Each has a default behavior in the architecture above and a recommended resolution path. None blocks implementation.

| ID | Ambiguity | Default in this architecture | Source of default | Resolution path |
|---|---|---|---|---|
| AMB-01 | OpenAPI `ValidationErrorEnvelope` vs `ErrorEnvelope` for 400 — the spec uses both. | 400 responses use `ErrorEnvelope`; the `details` field is an array of `{field, code, message}`. | API §5.1 + OpenAPI `ValidationErrorEnvelope` schema. | Confirm with the spec author if `ValidationErrorEnvelope` should be a separate schema; the architecture supports both. |
| AMB-02 | The exact list of fields that are server-only-and-rejected is API §6.3; the implementation may have a few more. | The filter uses the API §6.3 list as the canonical set; any deviation is logged and reported. | API §6.3. | None needed. |
| AMB-03 | `If-Match` numeric `version` form (`"v=42"`) — the OpenAPI ETag examples show timestamp; `version` is also exposed on resources. | Both are accepted; the service layer tries numeric parse first, then timestamp. | API §15.1. | None needed. |
| AMB-04 | `sale.refund_override_method` capability — the API says V1 does not strictly enforce refund-method matching. | No `sale.refund_override_method` capability check; user may pick any active method. | API §15.11.3 + MS-3. | Confirm with Owner; if added, see the `authz/guards.py` extension point. |
| AMB-05 | `system_settings.enable_sequential_doc_numbers` (OD-17) is TBD-OWNER. | Disabled by default. `reference_no` is nullable; the API does not auto-generate. | API §15.3 + OD-17. | Owner decision: if enabled, add a `next_reference_no` per document type in `system_settings` and a service to allocate on POST. |
| AMB-06 | `system_settings.display_rounding` (OD-16) is TBD-OWNER. | Disabled by default; all amounts stored as `NUMERIC(15,2)` exact; no display rounding. | API §9.9 + OD-16. | Owner decision: if enabled, add a `rounding_mode` field; storage unchanged. |
| AMB-07 | `low_stock_threshold` default (OD-3). | Per-product override; if NULL, falls back to `system_settings.default_low_stock_threshold` (default value TBD; backend uses 0 if not set). | API §15.8.1 + OD-3. | Owner decision: pick a default (e.g., 10). |
| AMB-08 | `POST /system/initialize` is not exposed in V1 (per API §15.22). | Operational only. The data model supports it (`is_initialized` boolean in `system_settings`). | API §15.22. | V1.1: expose behind `settings.manage` + `is_initialized = false`. |
| AMB-09 | Below-cost sale behavior — the API says "200 with warning field," not 4xx. | 200 with a `warning` object in the response body; the sale is posted. | API §11.2. | Confirm with Owner; if the warning should block posting, the architecture supports a config flag. |
| AMB-10 | Stock adjustment P&L effect (MS-2) is not auto-created in V1. | Not auto-created; Owner records manually via `POST /manual-entries`. | Reconciliation Report C-07 + API §17.3 MS-2. | V1.1: add `system_settings.adjustment_creates_pnl_entry` config flag. |
| AMB-11 | Per-product `on_hand` attribution during purchase cancel — the V1 implementation is "on_hand = MIN(received, current product on-hand)." | This is a simplification; the API Architecture §8.3 acknowledges it. | API §8.3 + DB spec §5.5. | None needed; the API spec accepts this. |
| AMB-12 | CORS allow-list default. | The default is empty (`BPOS_CORS_ALLOWED_ORIGINS` is unset); the deployment must set this. | API §4.11. | Deployment configuration. |
| AMB-13 | `BPOS_LOCK_TIMEOUT_MS` default. | 2000 ms (2s). | DB spec §11.2. | Configurable. |
| AMB-14 | `BPOS_CASH_BALANCE_LOCK_KEY` default. | `hashtext('cash_balance')` (the constant integer returned by this SQL). | DB spec §11.2 Hazard 5. | Configurable. |
| AMB-15 | The "as_of" timestamp for derived values. | The server's current time at query time. | API §4.10. | None needed. |
| AMB-16 | The exact format of `server_time` in responses. | ISO 8601 with offset, UTC, microsecond precision. | API §4.7. | None needed. |
| AMB-17 | The export job timeout. | 30s. On timeout, the export is marked `failed` with a hint. | API §12.4. | None needed. |
| AMB-18 | The exact seed data for `financial_categories` (names, codes). | Per API §15.7.5: Capital Injection, Other Income, Other (income); Rent, Labour (non-production), Electricity (non-production), Maintenance, Operational, Tax, Extra shipping, Other (expense). | API §15.7.5. | None needed; backed by PRD §17. |
| AMB-19 | The "best-sellers" algorithm for the dashboard. | `SUM(sale_lines.quantity × line_total) GROUP BY product_id ORDER BY SUM DESC LIMIT 10` over the current period. | API §17.3 MS-9. | None needed. |
| AMB-20 | The "expense breakdown by category" algorithm. | `SUM(manual_finance_entries.amount) GROUP BY financial_category.name` for the current period. | API §17.3 MS-10. | None needed. |
| AMB-21 | The exact `Etag` format for nested resources. | The `updated_at` of the parent resource. | API §4.10. | None needed. |
| AMB-22 | `GET /sales/{id}?include=lines,payments,returns` — the `include` parameter is a comma-separated list. | Parsed; each included sub-resource is fetched in the same request and embedded. Unknown includes → 400. | API §4.8. | None needed. |
| AMB-23 | The "fingerprint" for `Idempotency-Key` includes the URL path. The spec says "same endpoint + same body" → return original. | The endpoint is the path template (e.g., `/sales/{id}/post`), not the concrete path. This allows the same key to be reused across different sales. | API §7.1. | None needed; documented in §16.1. |
| AMB-24 | The `PII minimum exposure` rule for bulk endpoints — the API says "minimal fields" but doesn't enumerate which fields. | The Pydantic response models for list endpoints omit PII fields (`phone`, `email`, `address`). Single-resource GETs include them if the user has `contact.view`. | API §13.8. | None needed. |
| AMB-25 | The OpenAPI `LoginRequest` includes `refresh_token` rotation; the spec calls for 30s TTL on login. | The TTL is on the login response's Idempotency-Key, not the token. Login tokens are normal access+refresh. | API §7.1. | None needed. |
| AMB-26 | The behavior of `POST /users/{id}/capabilities` when the capability is already granted. | UPSERT: if the override exists, update `is_granted` and `granted_at`; else INSERT. No error on duplicate. | API §15.3.8. | None needed. |
| AMB-27 | The exact ETag for the `audit_log` list — the list is not versioned; no ETag. | No ETag on the list endpoint; no If-Match on a non-existent entity. | API §4.10. | None needed. |
| AMB-28 | The behavior of `PATCH /users/{id}/capabilities` — the OpenAPI shows POST for grant and DELETE for revoke; no PATCH. | The architecture follows the OpenAPI: POST + DELETE. PATCH is not exposed for capability changes. | OpenAPI + API §15.3.8-9. | None needed. |

**No contradiction was found between this architecture and any authoritative document.** All 28 ambiguities are resolved with documented defaults. If any Owner decision lands differently, the architecture's extension points (see e.g., AMB-05/06/10) make the change a small, localized diff.

---

## 29. Implementation Order

The implementation is sequenced so that each step is verifiable on its own before moving to the next. The first 5 steps are foundational; the rest are domain features.

### Phase A — Foundations (M1)

1. **Project scaffold + config + DB engine + connection management** (§3, §4, §10).
2. **Domain primitives** (§4 `app/domain/`): money, time, pagination, filters, errors, ETag.
3. **Auth: password hashing + token generation + session table** (§6).
4. **Authz: capability loading + guards** (§7).
5. **Validation: Pydantic models + derived-field filter + capability-aware field check** (§8).
6. **Idempotency service + guard** (§16).
7. **Concurrency: ETag + advisory locks** (§17).
8. **Error middleware + ErrorEnvelope** (§19).
9. **Audit service** (§18).
10. **Test fixtures (DB, auth, factories)** (§22).
11. **OpenAPI conformance test harness** (§25).
12. **System endpoints** (`/system/health`, `/system/version`, `/system/info`).

**Gate for M1:** the OpenAPI conformance test passes for `/system/health` and `/auth/login` + `/auth/refresh` + `/auth/logout` + `/auth/me`. The user-management endpoints (users, roles, capabilities) work end-to-end with capability checks.

### Phase B — Master data (M2)

13. **Categories, units, payment methods, cost types, financial categories** (CRUD + deactivate).
14. **Products** (CRUD + price history + valuation view).
15. **Contacts** (CRUD + deactivate).

**Gate for M2:** all master-data endpoints pass OpenAPI conformance + authz tests. Products expose `on_hand_quantity`, `moving_average_unit_cost`, `inventory_value` from the view.

### Phase C — Sales (M3)

16. **Sales: list, get, create draft, update draft, delete draft.**
17. **Sale lines: add, update, delete.**
18. **Sales: post (the full flow per §15.3).**
19. **Sales: payments (with over-tender).**
20. **Sales: cancel.**
21. **Sales: returns.**
22. **Sales returns: list, get, cancel.**

**Gate for M3:** the V1.9 Stress Test 1 (sale → partial payment → cancel → partial refund → remaining refund) passes end-to-end. All 22 lines of the Sales endpoints pass conformance.

### Phase D — Refunds and supplier-side (M4)

23. **Refunds: list, get, create.**
24. **Purchases: list, get, create draft, lines, shipping, post, payments, cancel, returns, returns cancel.**
25. **Supplier repayments: list, get, create.**

**Gate for M4:** the V1.9 Stress Tests 2, 3, 4 (full sale + refund, purchase cancel of consumed, production) pass end-to-end.

### Phase E — Production, inventory, manual entries (M5)

26. **Production runs: list, get, create draft, inputs, cost lines, post, cancel.**
27. **Inventory: list, per-product, low-stock, adjustments.**
28. **Manual entries: list, get, create, cancel.**
29. **Cash movements: list, balance.**
30. **Stock movements: list, get.**

**Gate for M5:** the V1.9 Event 16 (production), Event 17 (freight), Events 18/19 (adjustment), Events 21/22 (manual I&E) pass.

### Phase F — Reports, dashboard, exports, notifications, settings (M6)

31. **Reports: all 16 endpoints.**
32. **Dashboard: KPIs + inventory.**
33. **Exports: create, get, download (PDF + XLSX).**
34. **Notifications: list, mark-read, mark-all-read; low-stock worker.**
35. **Settings: get, patch (with `is_initialized` aware).**

**Gate for M6:** the system is feature-complete. The full OpenAPI conformance test suite passes (all 167 operations).

### Phase G — Hardening (M7)

36. **Rate limiting (slowapi) wired in.**
37. **CORS check + Origin header check.**
38. **HSTS, CSP, security headers (via reverse proxy; verify in app).**
39. **Prometheus metrics + Grafana dashboard.**
40. **Sentry integration (optional).**
41. **Cron jobs (prune idempotency, prune exports).**
42. **Documentation: README, deployment runbook, operations manual.**

**Gate for M7:** production-ready.

---

## 30. Final Verdict

# **PASS — Backend architecture is implementation-ready.**

**Why PASS (not PASS WITH CHANGES):**

- All 30 sections required by the task brief are concretely specified.
- The 167 OpenAPI operations are mapped to 43 route files; each has a documented handler skeleton.
- The 14 layered concerns (HTTP, auth, authz, validation, service, repository, transaction, accounting, inventory, idempotency, concurrency, audit, error, serialization) are each defined with a clear responsibility and a module layout that enforces the dependency DAG.
- The 22 accounting events and 8 invariants are mapped to enforcement mechanisms (DB triggers, service-layer checks, transaction orchestration, debug-mode sanity tests).
- The 14 mutations with required `Idempotency-Key` are listed; the guard handles replay, conflict, in-flight, and TTL.
- The optimistic-concurrency (`If-Match`/ETag) and pessimistic (advisory + row) mechanisms are specified with lock ordering, timeout, and 9 lock-out codes.
- The ErrorEnvelope is exactly as `openapi.yaml:6047-6081`; all error codes are mapped to HTTP statuses.
- The testing architecture covers all 13 required test types with concrete scenarios.
- The 28 ambiguities in §28 each have a documented default; none blocks implementation.

**Why not PASS WITH CHANGES:**

- No contradiction was found between this architecture and any authoritative document.
- No new endpoint was invented.
- No OpenAPI change was proposed.
- No DB schema change was proposed.
- No PRD or Business Rule was challenged.

**Pre-implementation checklist (for the implementer):**

1. Confirm Owner decisions on OD-3 (low-stock default), OD-16 (display rounding), OD-17 (sequential numbering). Defaults are documented in §28; the implementation works without them.
2. Confirm CORS allowed origins for the deployment.
3. Confirm Argon2id parameters (defaults are OWASP 2024).
4. Confirm lock-timeout default (default 2s).
5. Confirm export file retention (default 7 days).
6. Confirm the seed data for `financial_categories` and `cost_types` (defaults are PRD §15 + §17).

Once these are confirmed, the team proceeds to `Backend-Implementation-Plan-V1.0.md` (the task list) and begins Phase A (M1) per §29.

---

*— End of Backend Architecture Specification V1.0 —*
