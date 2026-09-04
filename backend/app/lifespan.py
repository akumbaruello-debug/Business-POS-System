"""Application lifespan — startup / shutdown hooks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import close_db, init_db
from app.logging import get_logger
from app.notifications.worker import start_stock_change_worker, stop_stock_change_worker

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise + dispose resources on the FastAPI app lifecycle."""
    settings = get_settings()
    logger.info(
        "app_starting",
        env=settings.env,
        host=settings.host,
        port=settings.port,
        debug=settings.debug,
    )
    # Initialise the database engine. Skip in 'test' environment when
    # the test fixture is in control; init_db is idempotent so this is
    # safe even if tests call it again.
    try:
        await init_db(settings)
    except Exception as exc:
        # We log and re-raise: a broken DB connection at startup is
        # fatal in production; in dev the operator can decide to
        # continue without the DB. The /readyz probe will report
        # 503 if the connection comes up later.
        logger.error("db_init_failed", error=str(exc))
        raise

    # E.10: start the low-stock notification worker.
    # In test mode the worker is not started — tests drive
    # notifications explicitly via the service layer or by
    # issuing NOTIFY on the test connection.
    if not settings.is_test:
        await start_stock_change_worker()

    try:
        yield
    finally:
        logger.info("app_stopping")
        await stop_stock_change_worker()
        await close_db()
