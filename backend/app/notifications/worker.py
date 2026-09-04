"""E.10 — Low-stock notifications asyncio worker.

An asyncio background task that:

1. Opens a **dedicated** raw ``asyncpg`` connection (separate from the
   SQLAlchemy pool — LISTEN holds a connection for the lifetime of the
   listener and must not compete with request-handling connections).
2. Issues ``LISTEN stock_change``.
3. On each notification, parses the JSON payload (``product_id``,
   ``movement_id``), opens a short-lived ``UnitOfWork``, and calls
   :func:`evaluate_stock_notification`.

Lifecycle:
  - Started by ``app.lifespan`` on application startup.
  - Cancelled on shutdown (``task.cancel()``).

The raw asyncpg DSN is derived from the SQLAlchemy
``postgresql+asyncpg://...`` URL by stripping the ``+asyncpg`` prefix.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg  # type: ignore[import-untyped]

from app.config import get_settings
from app.db import UnitOfWork
from app.logging import get_logger
from app.notifications.service import evaluate_stock_notification

logger = get_logger(__name__)

__all__ = ["start_stock_change_worker", "stop_stock_change_worker"]

# Module-level handle so lifespan can cancel.
_worker_task: asyncio.Task[None] | None = None
_worker_conn: asyncpg.Connection | None = None


def _asyncpg_dsn() -> str:
    """Derive a raw asyncpg DSN from the SQLAlchemy URL.

    SQLAlchemy URL: ``postgresql+asyncpg://user:pass@host:port/db``
    asyncpg expects: ``postgresql://user:pass@host:port/db``
    """
    sa_url = get_settings().database_url
    return sa_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _process_event(payload: str) -> None:
    """Parse the payload and evaluate threshold crossings."""
    try:
        data: dict[str, Any] = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        logger.warning("stock_change_invalid_payload", payload=payload)
        return

    product_id = data.get("product_id")
    movement_id = data.get("movement_id")

    if product_id is None or movement_id is None:
        logger.warning(
            "stock_change_missing_fields",
            payload=payload,
        )
        return

    try:
        async with UnitOfWork() as uow:
            created = await evaluate_stock_notification(
                uow,
                product_id=int(product_id),
                movement_id=int(movement_id),
            )
            if created:
                await uow.commit()
                logger.info(
                    "stock_change_notifications_committed",
                    product_id=product_id,
                    count=len(created),
                )
    except Exception:
        logger.exception(
            "stock_change_processing_error",
            product_id=product_id,
            movement_id=movement_id,
        )


async def _worker_loop() -> None:
    """Main LISTEN loop.  Reconnects on connection loss."""
    global _worker_conn
    dsn = _asyncpg_dsn()

    while True:
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(dsn)
            _worker_conn = conn
            logger.info("stock_change_worker_connected")

            # Use a Queue so we can process sequentially with proper
            # error handling (the asyncpg add_listener callback runs
            # inside asyncpg's internal task and exceptions there are
            # swallowed).
            queue: asyncio.Queue[str] = asyncio.Queue()

            def _make_on_notification(
                q: asyncio.Queue[str],
            ) -> Any:
                def on_notification(
                    _conn: asyncpg.Connection,
                    _pid: int,
                    _channel: str,
                    payload: str,
                ) -> None:
                    q.put_nowait(payload)
                return on_notification

            cb = _make_on_notification(queue)
            await conn.add_listener("stock_change", cb)
            logger.info("stock_change_worker_listening")

            while True:
                item: str = await queue.get()
                await _process_event(item)

        except asyncio.CancelledError:
            logger.info("stock_change_worker_cancelled")
            break
        except Exception:
            logger.exception("stock_change_worker_error")
            # Reconnect after a brief delay.
            await asyncio.sleep(2)
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception as exc:
                    logger.warning(
                        "stock_change_worker_close_error",
                        error=str(exc),
                    )
            _worker_conn = None


async def start_stock_change_worker() -> None:
    """Start the background worker task.  Called from lifespan."""
    global _worker_task
    if _worker_task is not None:
        return  # already running
    _worker_task = asyncio.create_task(_worker_loop(), name="stock_change_worker")
    logger.info("stock_change_worker_started")


async def stop_stock_change_worker() -> None:
    """Cancel the background worker.  Called from lifespan shutdown."""
    global _worker_task
    if _worker_task is None:
        return
    _worker_task.cancel()
    try:
        await _worker_task
    except asyncio.CancelledError:
        pass
    _worker_task = None
    logger.info("stock_change_worker_stopped")
