"""Database access layer.

Uses **SQLAlchemy 2.0 Core** (not ORM) with ``asyncpg`` as the async
driver, per ``Backend-Architecture-V1.0.md`` §2 / §10.

Public surface:

* :func:`create_engine` — build the async engine from config.
* :func:`get_session` — FastAPI dependency returning an
  ``AsyncSession``-like unit of work (the raw SQLAlchemy connection
  wrapped as a UnitOfWork).
* :func:`init_db` / :func:`close_db` — lifecycle hooks.
* :class:`UnitOfWork` — the transaction boundary abstraction (§11).

No ORM models are defined here. Business tables live in the PostgreSQL
migration; we issue raw ``sqlalchemy.text`` / ``sqlalchemy.sql``
statements against them. This keeps us 100% aligned with the canonical
migration — no object↔table drift.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.logging import get_logger

# Use structlog so the "database_connected" event gets the
# request_id injected automatically.
logger = get_logger(__name__)

# Module-level holder; set on app startup.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


def create_engine(settings: Any = None) -> AsyncEngine:
    """Create a configured async SQLAlchemy engine."""
    if settings is None:
        settings = get_settings()
    kwargs: dict[str, Any] = {
        "echo": settings.debug,
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_timeout": settings.database_pool_timeout_seconds,
        "future": True,
    }
    # Test DBs / sqlite: avoid the asyncpg pool (NullPool works fine
    # and avoids "database is locked" style issues under heavy fixture
    # reuse. But our contract is PostgreSQL + asyncpg. We honor the URL.
    if "sqlite" in settings.database_url:
        # SQLite needs a different pool strategy + aiosqlite.
        kwargs.pop("pool_size", None)
        kwargs.pop("max_overflow", None)
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = NullPool
    elif "pos_test" in settings.database_url:
        # Test DB: use NullPool to avoid "another operation in progress"
        # errors from connection reuse across fixture boundaries. Each
        # session gets its own connection, preventing asyncpg from
        # seeing stale transaction state on a pooled connection.
        kwargs.pop("pool_size", None)
        kwargs.pop("max_overflow", None)
        kwargs.pop("pool_timeout", None)
        kwargs["poolclass"] = NullPool
    return create_async_engine(settings.database_url, **kwargs)


def get_engine() -> AsyncEngine:
    """Return the process-wide engine (must be initialised first)."""
    if _engine is None:
        raise RuntimeError("Database engine not initialised. Call init_db().")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the bound session factory."""
    if _session_factory is None:
        raise RuntimeError("DB session factory not initialised.")
    return _session_factory


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


async def _raw(
    conn: AsyncConnection,
    sql: str,
    params: dict[str, Any] | None = None,
) -> Any:
    """Execute a SQL statement returning scalar(s)."""
    stmt = text(sql)
    result = await conn.execute(stmt, params or {})
    return result


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def init_db(settings: Any = None) -> AsyncEngine:
    """Initialise the engine + session factory + run the DB ping."""
    global _engine, _session_factory
    if settings is None:
        settings = get_settings()
    if _engine is not None:
        return _engine  # already initialised (idempotent)

    _engine = create_engine(settings)
    _session_factory = async_sessionmaker(
        bind=_engine,
        expire_on_commit=False,  # we read what we inserted; no lazy reload
        class_=AsyncSession,
    )
    # Ping → confirm the connection works before serving traffic.
    async with _engine.connect() as conn:
        version = await conn.execute(text("SELECT version()"))
        row = version.scalar()
        logger.info("database_connected", version=str(row)[:80])
    return _engine


async def close_db() -> None:
    """Dispose the engine. Called on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        logger.info("database_disposed")
    _engine = None
    _session_factory = None


# ---------------------------------------------------------------------------
# Unit of Work (transaction boundary)
# ---------------------------------------------------------------------------


class UnitOfWork:
    """A single transactional unit.

    Per ``Backend-Architecture-V1.0.md`` §11: the transaction boundary
    is drawn in the **service** layer, not in repositories. A single
    UoW wraps one ``AsyncSession`` (``BEGIN`` on enter, ``COMMIT`` /
    ``ROLLBACK`` on exit).

    Usage::

        async with UnitOfWork() as uow:
            await uow.execute("UPDATE sales ...")
            await uow.commit()   # commits; commit() is idempotent

    A UoW can also be driven manually (``conn`` is exposed).
    """

    def __init__(self, *, session_factory: Any = None) -> None:
        if session_factory is None:
            session_factory = get_session_factory()
        self._factory = session_factory
        self._session: AsyncSession | None = None

    # ---- context manager ----

    async def __aenter__(self) -> UnitOfWork:
        self._session = self._factory()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self._session is None:
            return False
        if exc_type is None:
            # No auto-commit — but ensure any pending transaction is
            # rolled back so the connection returns cleanly to the pool
            # (asyncpg holds the connection as "in progress" if a
            # transaction is left open).
            await self.rollback()
        else:
            await self.rollback()
        await self._session.close()
        self._session = None
        return False  # never swallow

    # ---- access ----

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("Session not open. Use 'async with UnitOfWork()'.")
        return self._session

    @property
    async def conn(self) -> AsyncConnection:
        """The underlying async connection.

        For raw SQL / text() execution within the transaction.
        """
        # AsyncSession exposes the bind (the engine) but the connection
        # is acquired lazily. We force-start a transaction by executing
        # a trivial statement if needed.
        if self._session is None:
            raise RuntimeError("Session not open. Use 'async with UnitOfWork()'.")
        # `session.connection()` acquires a connection from the pool
        # within the session's transaction.
        return await self._session.connection()

    # ---- commit / rollback ----

    async def commit(self) -> None:
        """Commit the transaction explicitly."""
        if self._session is not None:
            await self._session.commit()

    async def rollback(self) -> None:
        """Roll back the transaction."""
        if self._session is not None:
            await self._session.rollback()

    # ---- query helpers ----

    async def execute(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Execute a SQL statement in this transaction's session."""
        if self._session is None:
            raise RuntimeError("Session not open. Use 'async with UnitOfWork()'.")
        return await self._session.execute(text(sql), params or {})

    async def scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Execute and return a single scalar."""
        result = await self.execute(sql, params)
        return result.scalar()

    async def scalars(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Execute and return a Scalars list."""
        result = await self.execute(sql, params)
        return result.scalars()

    async def first_row(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Execute and return the first row as a dict, or None.

        The mapping uses ``result.mappings().first()`` which returns a
        ``RowMapping`` that we convert to a plain dict.
        """
        result = await self.execute(sql, params)
        row = result.mappings().first()
        if row is None:
            return None
        return dict(row)

    async def fetch_all(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute and return all rows as a list of dicts."""
        result = await self.execute(sql, params)
        rows = result.mappings().all()
        return [dict(row) for row in rows]

    async def first_scalar(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        """Execute and return a single scalar."""
        result = await self.execute(sql, params)
        return result.scalar()

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[AsyncConnection, None]:
        """Yield a connection within this UoW's transaction.

        Convenience for code that wants a ``conn`` context.
        """
        if self._session is None:
            raise RuntimeError("Session not open.")
        # The session already holds a transaction; connection() returns
        # the connection bound to it (acquired lazily; await it).
        conn = await self._session.connection()
        yield conn


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield a session.

    This is for simple read endpoints. For any endpoint that needs a
    transaction boundary (writes, multi-step reads), use
    ``UnitOfWork`` directly inside the route handler.
    """
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def get_uow() -> AsyncGenerator[UnitOfWork, None]:
    """FastAPI dependency yielding an open UoW.

    The route handler is responsible for ``commit()`` / ``rollback()``.
    If the handler raises, ``__aexit__`` rolls back automatically.
    """
    async with UnitOfWork() as uow:
        yield uow


__all__ = [
    "AsyncEngine",
    "AsyncSession",
    "UnitOfWork",
    "close_db",
    "create_engine",
    "get_engine",
    "get_session",
    "get_session_factory",
    "get_uow",
    "init_db",
]
