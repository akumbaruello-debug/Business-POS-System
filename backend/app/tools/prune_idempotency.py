"""Prune expired idempotency keys.

Daily cron job per G.6 / Backend-Implementation-Plan-V1.0.md §7.

Deletes rows from ``idempotency_keys`` where
``expires_at < NOW() - INTERVAL '1 day'``.

Usage::

    python -m app.tools.prune_idempotency

Safe to run repeatedly: DELETE is idempotent; no rows = 0 deleted.
"""

from __future__ import annotations

import asyncio

from app.config import get_settings
from app.config.env import load_env
from app.db import UnitOfWork, close_db, init_db
from app.logging import configure_logging, get_logger


async def prune_expired_idempotency_keys() -> int:
    """Delete idempotency_keys rows expired for more than 1 day.

    Returns the number of rows deleted.
    """
    async with UnitOfWork() as uow:
        result = await uow.execute(
            """
            DELETE FROM idempotency_keys
            WHERE expires_at < NOW() - INTERVAL '1 day'
            """,
        )
        deleted = result.rowcount
        await uow.commit()
    return deleted


async def main() -> None:
    """CLI entry point."""
    load_env()
    settings = get_settings()
    configure_logging(log_level=settings.log_level, log_format=settings.log_format)
    logger = get_logger("prune_idempotency")

    await init_db(settings)
    try:
        deleted = await prune_expired_idempotency_keys()
        logger.info("prune_idempotency_complete", deleted=deleted)
        print(f"Pruned {deleted} expired idempotency key(s).")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
