"""Pytest configuration: shared fixtures and environment setup.

All M1 tests run against a real PostgreSQL database (running on the
configured ``POS_DATABASE_URL``) — no mocks for DB behavior. The
``engine`` / ``session_factory`` are module-level singletons in
``app.db``, so tests reuse the same engine across the whole run for
performance. Each test runs in its own transaction rolled back at
teardown, isolating state without paying the cost of schema rebuilds.

Schema creation is done via the ``psql`` client (the canonical
Postgres toolchain), not a Python SQL parser, to avoid quoting /
encoding edge-cases in the 2000-line schema.sql.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import AsyncGenerator
from typing import Any

# Configure test-friendly env vars BEFORE importing app modules so that
# Settings() picks them up. Override POS_ENV only for DB tests.
os.environ.setdefault("POS_DEBUG", "false")
os.environ.setdefault("POS_LOG_LEVEL", "WARNING")
os.environ.setdefault("POS_LOG_FORMAT", "json")
# Use a known test DB on the running embedded PG instance.
os.environ.setdefault(
    "POS_DATABASE_URL",
    "postgresql+asyncpg://postgres@127.0.0.1:5433/pos_test",
)
# Tests must not inherit the .env CORS allow-list (http://localhost:3000):
# the httpx test client sends no browser Origin, and OriginCheckMiddleware
# would reject every state-changing request with 403. Set the allow-list to
# the test base_url origin and have the client send a matching Origin.
os.environ.setdefault("POS_CORS_ALLOWED_ORIGINS", "http://test")

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import RowMapping

from app.config import get_settings
from app.config.env import load_env
from app.core.ids import new_uuid
from app.core.security import PasswordHasher
from app.db import _engine as _db_engine
from app.db import close_db, get_session_factory, init_db
from app.errors.codes import ErrorCode
from app.main import create_app
from app.middleware.rate_limit import get_limiter

# Reload settings after env override so Settings() picks up env changes.
load_env(force_reload=True)

# Schema file path (project root).
_SCHEMA_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        "schema.sql",
    )
)


def _find_psql() -> str | None:
    """Locate the psql binary from the embedded Postgres bundle."""
    candidates = [
        # Same dir as the project's embedded PG
        os.path.join(
            os.path.dirname(_SCHEMA_PATH),
            "pg-tmp",
            "pg17",
            "pgsql",
            "bin",
            "psql.exe",
        ),
        "psql",
    ]
    for cand in candidates:
        if shutil.which(cand) or (os.path.isfile(cand) and os.access(cand, os.X_OK)):
            return cand
    return None


def _ensure_schema() -> None:
    """Load schema.sql into the test DB using psql (once per process).

    This is a sync function. We use psql because it handles all
    PostgreSQL SQL/encoding dialect correctly, whereas parsing 2000-line
    schema files in Python risks edge-cases.
    """
    settings = get_settings()
    url = settings.database_url
    from urllib.parse import urlparse

    parsed = urlparse(url)
    user = parsed.username or "postgres"
    password = parsed.password or ""
    host = parsed.hostname or "127.0.0.1"
    port = str(parsed.port or 5432)
    dbname = parsed.path.lstrip("/") or "postgres"

    psql_bin = _find_psql() or "psql"
    env = os.environ.copy()
    env["PGPASSWORD"] = password

    # Drop & recreate schema to ensure clean schema state for tests
    drop_sql = (
        f"DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO {user};"
    )
    subprocess.run(
        [
            psql_bin,
            "-h",
            host,
            "-p",
            port,
            "-U",
            user,
            "-d",
            dbname,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            drop_sql,
        ],
        env=env,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [
            psql_bin,
            "-h",
            host,
            "-p",
            port,
            "-U",
            user,
            "-d",
            dbname,
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            _SCHEMA_PATH,
        ],
        env=env,
        capture_output=True,
        check=True,
    )


_bootstrap_done: bool = False


def _bootstrap_db_sync() -> None:
    """Load schema.sql once per test session (sync, via psql).

    This runs for the entire session — it's idempotent (DROP+CREATE).
    Only triggered for tests that need the DB (marked with
    ``requires_db`` or that depend on the ``db`` fixture).
    """
    global _bootstrap_done
    if _bootstrap_done:
        return
    _ensure_schema()
    # Eagerly initialise the async DB engine + session factory on a
    # fresh event loop so the module-level globals in ``app.db`` are
    # populated before any test fixture (including class-scope
    # autouse ones) tries to use ``get_session_factory()``. NullPool
    # is used for the test DB URL, so the engine handles cross-loop
    # use safely — see ``create_engine`` in ``app.db``.
    import asyncio

    asyncio.run(init_db(get_settings()))
    _bootstrap_done = True


@pytest.fixture(scope="session", autouse=True)
def _bootstrap() -> None:
    """Session-scoped fixture: load schema once before tests."""
    _bootstrap_db_sync()


def pytest_collection_modifyitems(items: list[pytest.Item], config: pytest.Config) -> None:
    """Auto-mark integration/api tests as requiring the DB.

    Unit tests (tests/unit/) run standalone without DB.
    """
    for item in items:
        # Mark all non-unit tests as integration (need DB)
        parts = item.nodeid.split("/")
        if len(parts) > 1 and parts[0] == "unit":
            item.add_marker(pytest.mark.unit)
        else:
            # api/health/integration tests need the DB
            if not any(m.name == "unit" for m in item.iter_markers()):
                item.add_marker(pytest.mark.integration)


async def _reset_rate_limits() -> None:
    """Reset the slowapi limiter's in-memory storage between tests."""
    limiter = get_limiter()
    storage = limiter._storage
    # limits >= 3.0 MemoryStorage.clear() requires a key arg; use reset() on
    # the storage instance itself, which always works.
    if hasattr(storage, "reset"):
        storage.reset()
    elif hasattr(storage, "clear"):
        # Older signature: clear(key) — iterate known keys.
        try:
            storage.clear(":")  # type: ignore[call-arg]
        except TypeError:
            pass  # signature changed again; skip


@pytest.fixture
async def db() -> AsyncGenerator[None, None]:
    """Initialize the engine (once) and truncate tables per test.

    Tests that need the DB depend on this fixture (directly or
    transitively via ``app``, ``owner_user``, ``staff_user``).
    """
    # init_db sets the module-level _engine / _session_factory.
    # It's idempotent — if already set, returns the existing engine.
    # For tests, force NullPool to avoid asyncpg "another operation
    # in progress" errors from connection reuse across fixture boundaries.
    # The engine is session-scoped; we just TRUNCATE on it.
    if _db_engine is None:
        await init_db(get_settings())
    # Terminate any leftover "idle in transaction" backends from the
    # previous test. Without this, a test whose final `async with
    # factory() as session:` block ran a SELECT can leave the backend
    # holding a lock that blocks the next test's TRUNCATE
    # (ACCESS EXCLUSIVE on every table). With NullPool, the Python
    # connection object is released as soon as the session closes, but
    # the PostgreSQL backend only sees the disconnect after a tick —
    # terminating the backend PIDs is a deterministic way to clear the
    # lock immediately.
    import asyncio
    import os
    import subprocess

    settings = get_settings()
    from urllib.parse import urlparse

    parsed = urlparse(settings.database_url)
    psql_bin = os.path.join(
        os.path.dirname(_SCHEMA_PATH), "pg-tmp", "pg17", "pgsql", "bin", "psql.exe"
    )
    if os.path.isfile(psql_bin):
        env = os.environ.copy()
        env["PGPASSWORD"] = parsed.password or ""
        # Run on a background thread to avoid blocking the event loop.
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [
                    psql_bin,
                    "-h", parsed.hostname or "127.0.0.1",
                    "-p", str(parsed.port or 5432),
                    "-U", parsed.username or "postgres",
                    "-d", (parsed.path or "/postgres").lstrip("/"),
                    "-c",
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid != pg_backend_pid() "
                    "AND state IN ('idle in transaction', 'idle in transaction (aborted)')",
                ],
                env=env,
                capture_output=True,
                check=False,
            ),
        )
    # Truncate all mutable tables for isolation.
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "TRUNCATE TABLE "
                "idempotency_keys, sessions, "
                "user_capability_overrides, "
                "categories, units, products, payment_methods, financial_categories, cost_types, contacts, "
                "stock_movements, sale_lines, sale_payments, sales, "
                "sales_return_lines, sales_returns, refunds, "
                "purchase_lines, purchase_payments, purchases, "
                "purchase_return_lines, purchase_returns, "
                "supplier_repayments, purchase_shipping, "
                "production_inputs, production_outputs, "
                "production_cost_lines, production_runs, "
                "manual_finance_entries, cash_movements, "
                "product_price_history, notifications "
                "RESTART IDENTITY CASCADE"
            )
        )
        # audit_log is append-only (trigger blocks DELETE/TRUNCATE)
        # Clear non-static users but keep the 'owner' seed.
        # SKIPPED: DELETE FROM users triggers cascade that hits audit_log
        # append-only trigger. Test fixtures use ON CONFLICT to manage users.
        # Reset owner password to a known value so each test starts clean.
        await session.execute(
            text(
                """
                UPDATE users SET password_hash = :ph,
                                   is_active = true,
                                   failed_login_count = 0,
                                   locked_until = NULL
                WHERE username = 'owner'
                """
            ),
            {"ph": PasswordHasher().hash("OwnerPass123!")},
        )
        # Re-seed reference rows that schema.sql inserts. These tables are
        # TRUNCATEd above for isolation, but several M2 tests depend on the
        # canonical seed values (Rent/Capital Injection/Tax for
        # financial_categories; cash/bank_transfer/e_wallet/other for
        # payment_methods; labor/electricity/gas/packaging/other for
        # cost_types). Re-insert with fixed IDs so test expectations hold
        # across runs.
        await session.execute(
            text(
                """
                INSERT INTO payment_methods (id, code, name, is_cash, is_active) VALUES
                  (1, 'cash',           'Cash',          TRUE,  TRUE),
                  (2, 'bank_transfer',  'Bank Transfer', FALSE, TRUE),
                  (3, 'e_wallet',       'E-Wallet',      FALSE, TRUE),
                  (4, 'other',          'Other',         FALSE, TRUE)
                ON CONFLICT (id) DO UPDATE SET
                  code      = EXCLUDED.code,
                  name      = EXCLUDED.name,
                  is_cash   = EXCLUDED.is_cash,
                  is_active = EXCLUDED.is_active
                """
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO financial_categories (id, code, name, entry_type, is_active) VALUES
                  (1,  'capital_injection',   'Capital Injection',           'income',  TRUE),
                  (2,  'other_income',        'Other Income',                'income',  TRUE),
                  (3,  'other_income_misc',   'Other',                       'income',  TRUE),
                  (4,  'rent',                'Rent',                        'expense', TRUE),
                  (5,  'labour_non_prod',     'Labour (non-production)',     'expense', TRUE),
                  (6,  'electricity_non_prod','Electricity (non-production)','expense', TRUE),
                  (7,  'maintenance',         'Maintenance',                 'expense', TRUE),
                  (8,  'operational',         'Operational',                 'expense', TRUE),
                  (9,  'tax',                 'Tax',                         'expense', TRUE),
                  (10, 'extra_shipping',      'Extra shipping',              'expense', TRUE),
                  (11, 'other_expense',       'Other',                       'expense', TRUE)
                ON CONFLICT (id) DO UPDATE SET
                  code       = EXCLUDED.code,
                  name       = EXCLUDED.name,
                  entry_type = EXCLUDED.entry_type,
                  is_active  = EXCLUDED.is_active
                """
            )
        )
        await session.execute(
            text(
                """
                INSERT INTO cost_types (id, code, name, is_active) VALUES
                  (1, 'labor',       'Labor',       TRUE),
                  (2, 'electricity', 'Electricity', TRUE),
                  (3, 'gas',         'Gas',         TRUE),
                  (4, 'packaging',   'Packaging',   TRUE),
                  (5, 'other',       'Other',       TRUE)
                ON CONFLICT (id) DO UPDATE SET
                  code      = EXCLUDED.code,
                  name      = EXCLUDED.name,
                  is_active = EXCLUDED.is_active
                """
            )
        )
        # Reset the SERIAL sequences so subsequent INSERTs in tests get
        # IDs that do not collide with the canonical seed IDs.
        await session.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('payment_methods', 'id'), "
                "(SELECT MAX(id) FROM payment_methods))"
            )
        )
        await session.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('financial_categories', 'id'), "
                "(SELECT MAX(id) FROM financial_categories))"
            )
        )
        await session.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('cost_types', 'id'), "
                "(SELECT MAX(id) FROM cost_types))"
            )
        )
        await session.commit()
        await _reset_rate_limits()
    yield


@pytest.fixture
async def app(db: None) -> AsyncGenerator[AsyncClient, None]:
    # Settings are lru_cached; clear so POS_CORS_ALLOWED_ORIGINS override is
    # picked up (create_app reads it to install OriginCheckMiddleware).
    get_settings.cache_clear()
    fastapi_app = create_app(get_settings())
    async with AsyncClient(
        transport=__import__("httpx").ASGITransport(app=fastapi_app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    ) as client:
        yield client


@pytest.fixture
async def owner_user(db: None) -> dict[str, Any]:
    """Ensure an Owner user exists with a known password.

    Returns a dict with ``user_id``, ``username``, ``password``,
    ``role_id``, ``role_name``, ``capabilities``.
    """
    hasher = PasswordHasher()
    pw = "OwnerPass123!"
    pwd_hash = hasher.hash(pw)
    factory = get_session_factory()
    async with factory() as session:
        row: RowMapping | None = (
            (await session.execute(text("SELECT id, name FROM roles WHERE name = 'Owner'")))
            .mappings()
            .first()
        )
        if row is None:
            raise RuntimeError("Owner role missing — schema.sql must seed it")
        role_id = int(row["id"])
        await session.execute(
            text(
                """
                INSERT INTO users (username, full_name, email, is_active, role_id, password_hash)
                VALUES (:u, :fn, :em, true, :rid, :ph)
                ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash,
                                                     is_active = true,
                                                     failed_login_count = 0,
                                                     locked_until = NULL
                """
            ),
            {
                "u": "owner",
                "fn": "System Owner",
                "em": "owner@example.com",
                "rid": role_id,
                "ph": pwd_hash,
            },
        )
        await session.commit()
        user: RowMapping | None = (
            (
                await session.execute(
                    text(
                        """
                    SELECT u.id AS user_id, u.username, u.role_id, r.name AS role_name
                    FROM users u JOIN roles r ON r.id = u.role_id
                    WHERE u.username = 'owner'
                    """
                    )
                )
            )
            .mappings()
            .first()
        )
        result: dict[str, Any] = {
            "user_id": int(user["user_id"]) if user else 0,
            "username": "owner",
            "password": pw,
            "role_id": int(user["role_id"]) if user else 0,
            "role_name": str(user["role_name"]) if user else "",
            "capabilities": set(),  # filled below
        }

    from app.authz.caps import OWNER_CAPABILITIES

    result["capabilities"] = set(OWNER_CAPABILITIES)
    return result


@pytest.fixture
async def staff_user(owner_user: dict[str, Any]) -> dict[str, Any]:
    """Create a Staff user with the canonical Staff default capability set.

    Owner user is required first to ensure role seeds exist.
    """
    hasher = PasswordHasher()
    pw = "StaffPass123!"
    pwd_hash = hasher.hash(pw)
    factory = get_session_factory()
    async with factory() as session:
        row: RowMapping | None = (
            (await session.execute(text("SELECT id FROM roles WHERE name = 'Staff'")))
            .mappings()
            .first()
        )
        if row is None:
            raise RuntimeError("Staff role missing — schema.sql must seed it")
        role_id = int(row["id"])
        await session.execute(
            text(
                """
                INSERT INTO users (username, full_name, email, is_active, role_id, password_hash)
                VALUES (:u, :fn, :em, true, :rid, :ph)
                ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash,
                                                    is_active = true,
                                                    failed_login_count = 0,
                                                    locked_until = NULL
                """
            ),
            {
                "u": "staff",
                "fn": "Test Staff",
                "em": "staff@example.com",
                "rid": role_id,
                "ph": pwd_hash,
            },
        )
        await session.commit()
        user_row: RowMapping | None = (
            (await session.execute(text("SELECT id FROM users WHERE username = 'staff'")))
            .mappings()
            .first()
        )
        user_id = int(user_row["id"]) if user_row else 0
    from app.authz.caps import STAFF_DEFAULT_CAPABILITIES

    return {
        "user_id": user_id,
        "username": "staff",
        "password": pw,
        "role_id": role_id,
        "role_name": "Staff",
        "capabilities": set(STAFF_DEFAULT_CAPABILITIES),
    }


# Re-export common helpers for tests.
__all__ = [
    "ErrorCode",
    "new_uuid",
]
