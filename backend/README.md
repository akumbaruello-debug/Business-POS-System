# Business POS Backend — Phase M1 (Foundation)

Backend for the **Business Management & POS System**.

This phase implements the **M1 foundation** described in
`Backend-Implementation-Plan-V1.0.md` — strictly the cross-cutting
infrastructure required by every later phase. **No business endpoints
(sales, purchases, refunds, production, etc.) are implemented in M1.**

## Scope of M1

| Layer                         | Status |
|-------------------------------|--------|
| Project structure             | ✅     |
| Config / env loading          | ✅     |
| PostgreSQL connection pool    | ✅     |
| SQLAlchemy 2.0 Core wrapper   | ✅     |
| Pydantic v2 validation infra  | ✅     |
| Canonical `ErrorEnvelope`     | ✅     |
| Structured logging (structlog)| ✅     |
| Request context (request_id)  | ✅     |
| Auth foundation (Argon2id)    | ✅     |
| Session model (token store)   | ✅     |
| Bearer auth dependency        | ✅     |
| `/auth/*` (5 endpoints)       | ✅     |
| Capability resolution         | ✅     |
| `require_capability` dep      | ✅     |
| Transaction unit of work      | ✅     |
| Idempotency foundation        | ✅     |
| If-Match / ETag foundation    | ✅     |
| Security middleware           | ✅     |
| Health & readiness endpoints  | ✅     |
| Test infrastructure           | ✅     |
| M1 test suite                 | ✅     |

> Business endpoints (sales, purchases, refunds, production,
> manual entries, reports, exports, etc.) are out of scope for M1 and
> are reserved for M2+.

## Technology stack (locked)

Per `Backend-Architecture-V1.0.md`:

- **Language**: Python 3.11
- **Web framework**: FastAPI 0.115+ (with uvicorn ASGI server)
- **Validation**: Pydantic v2
- **Database driver**: SQLAlchemy 2.0 Core + asyncpg
- **DB**: PostgreSQL 17 (validated against the canonical migration)
- **Password hashing**: Argon2id (`argon2-cffi`)
- **Logging**: structlog
- **Rate limiting**: slowapi
- **Testing**: pytest + pytest-asyncio + httpx

## Repository layout

```
backend/
├── pyproject.toml
├── README.md
├── .env.example
├── scripts/
│   ├── run_dev.sh
│   ├── reset_dev_db.sh
│   └── seed_owner_password.py
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app factory
│   ├── cli.py                   # `pos-backend` entrypoint
│   ├── lifespan.py              # startup / shutdown hooks
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py          # pydantic-settings Settings
│   │   └── env.py               # .env loader
│   ├── core/
│   │   ├── __init__.py
│   │   ├── clock.py             # Clock protocol + system clock
│   │   ├── ids.py               # UUIDv4 + request_id helpers
│   │   └── security.py          # Argon2id wrapper
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py            # async engine + pool
│   │   ├── session.py           # session factory + async context
│   │   └── uow.py               # unit of work (transaction boundary)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py              # shared dependencies
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── router.py        # v1 API router
│   │   │   ├── auth.py          # /auth/* (5 endpoints)
│   │   │   └── health.py        # /healthz, /readyz, /livez
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── tokens.py            # opaque token gen + hash
│   │   ├── session_store.py     # session CRUD (sessions table)
│   │   ├── service.py           # login / refresh / logout flows
│   │   ├── principal.py         # Principal / Actor model
│   │   └── password.py          # password verify / lockout
│   ├── authz/
│   │   ├── __init__.py
│   │   ├── capabilities.py      # capability codes catalog (FROZEN)
│   │   ├── resolver.py          # role ∪ user_overrides
│   │   └── deps.py              # require_capability dependency
│   ├── domain/                  # reserved for M2+ business rules
│   │   └── __init__.py
│   ├── repositories/            # reserved for M2+
│   │   └── __init__.py
│   ├── services/                # reserved for M2+
│   │   └── __init__.py
│   ├── validation/
│   │   ├── __init__.py
│   │   ├── enums.py             # canonical enums from OpenAPI
│   │   ├── pagination.py        # Pagination envelope
│   │   └── headers.py           # Idempotency-Key / If-Match helpers
│   ├── errors/
│   │   ├── __init__.py
│   │   ├── envelope.py          # ErrorEnvelope + ValidationErrorEnvelope
│   │   ├── codes.py             # canonical error code catalog
│   │   └── handlers.py          # FastAPI exception handlers
│   ├── logging/
│   │   ├── __init__.py
│   │   └── config.py            # structlog configuration
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── request_id.py        # X-Request-ID + log context
│   │   ├── security.py          # secure response headers
│   │   └── rate_limit.py        # slowapi setup
│   ├── health/
│   │   ├── __init__.py
│   │   └── probes.py            # health / readiness / liveness
│   └── util/
│       ├── __init__.py
│       ├── http.py              # client IP, forwarded headers
│       └── etag.py              # ETag generation + If-Match compare
├── tests/
│   ├── conftest.py
│   ├── unit/                    # pure unit tests
│   ├── integration/             # PostgreSQL-backed tests
│   ├── api/                     # API contract tests
│   ├── auth/                    # auth foundation tests
│   ├── authz/                   # capability resolver tests
│   ├── health/                  # health probe tests
│   └── errors/                  # error envelope tests
└── docs/
    └── M1.md                    # M1 deliverable summary
```

## Running

### 1. Apply the migration to your dev database

```bash
psql -h 127.0.0.1 -p 5433 -U postgres -d pos_dev \
  -f ../supabase/migrations/20260826000001_initial_schema_baseline.sql
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env to point at your database
```

### 3. Set the Owner password (one-time)

The migration seeds an Owner user with `password_hash = '!UNSET'`. M1
provides a script to set the initial password:

```bash
python scripts/seed_owner_password.py --username owner --password 'ChangeMe!23'
```

### 4. Start the backend

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Or via the entrypoint:

```bash
pos-backend serve
```

## Testing

```bash
cd backend
pytest                  # run all tests
pytest -m unit         # unit tests only
pytest -m integration  # PostgreSQL-backed tests only
pytest --cov=app       # with coverage
```

Integration tests require a running PostgreSQL 17 instance matching
the validation cluster. By default they connect to:

```
postgresql+asyncpg://postgres@127.0.0.1:5433/pos_test
```

Override with `POS_TEST_DATABASE_URL`.

## M1 deliverable

See `docs/M1.md` for the M1 deliverable summary (files created,
tests executed, results, lint/typecheck, blockers, deviations, run
commands, and final verdict).
