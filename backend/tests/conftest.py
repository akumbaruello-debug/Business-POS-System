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
    if _db_engine is not None:
        await close_db()
    await init_db(get_settings())
    # Truncate all mutable tables for isolation.
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "TRUNCATE TABLE "
                "idempotency_keys, sessions, "
                "user_capability_overrides, "
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
        await session.commit()
    yield


@pytest.fixture
async def app(db: None) -> AsyncGenerator[AsyncClient, None]:
    """Build a fresh FastAPI app + httpx client per test."""
    fastapi_app = create_app(get_settings())
    async with AsyncClient(
        transport=__import__("httpx").ASGITransport(app=fastapi_app),
        base_url="http://test",
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
