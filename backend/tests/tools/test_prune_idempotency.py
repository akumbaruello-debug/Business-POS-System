"""Tests for idempotency pruning."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

BACKEND_DIR = "C:/Users/ratus/Desktop/ello/projects/Business-POS-System/backend"


class TestPruneIdempotency:
    """Focused G.6 tests for prune_idempotency.py."""

    async def _run_prune(self) -> None:
        """Run the CLI script and assert it exits cleanly."""
        import os
        import sys

        python = sys.executable
        env = os.environ.copy()
        env["PYTHONPATH"] = BACKEND_DIR
        proc = await asyncio.create_subprocess_exec(
            python, "-m", "app.tools.prune_idempotency",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=BACKEND_DIR,
            env=env,
        )
        _, _ = await proc.communicate()
        assert proc.returncode == 0

    async def test_prune_removes_expired_keys(self, owner_user: dict[str, str], app: AsyncClient) -> None:
        """Expired rows are deleted; non-expired remain."""
        from app.db import UnitOfWork

        cutoff = datetime.now(UTC) - timedelta(days=2)
        now = datetime.now(UTC)
        uid = int(owner_user["user_id"])
        async with UnitOfWork() as uow:
            await uow.execute(
                """
                INSERT INTO idempotency_keys (key, user_id, endpoint, request_hash, response_status, response_body, created_at, completed_at, expires_at)
                VALUES
                    ('abc1', :uid, 'POST /auth/login', 'hash1', NULL, NULL, :now, NULL, :expired),
                    ('def2', :uid, 'POST /auth/login', 'hash2', NULL, NULL, :now, NULL, :expired),
                    ('ghi3', :uid, 'POST /auth/login', 'hash3', NULL, NULL, :now, NULL, :current),
                    ('jkl4', :uid, 'POST /auth/login', 'hash4', NULL, NULL, :now, NULL, :current)
                """,
                {
                    "now": now,
                    "uid": uid,
                    "expired": cutoff,
                    "current": now + timedelta(hours=1),
                },
            )
            await uow.commit()

        await self._run_prune()

        async with UnitOfWork() as uow:
            total = await uow.first_row("SELECT COUNT(*) AS cnt FROM idempotency_keys")
            count = int(total["cnt"]) if total else 0
            assert count == 2, f"Expected 2 remaining rows; got {count}"

    async def test_prune_preserves_current_keys(self, owner_user: dict[str, str]) -> None:
        """Non-expired keys are never removed."""
        from app.db import UnitOfWork

        now = datetime.now(UTC)
        uid = int(owner_user["user_id"])
        future1 = now + timedelta(hours=1)
        future2 = now + timedelta(days=1)
        async with UnitOfWork() as uow:
            await uow.execute(
                """
                INSERT INTO idempotency_keys (key, user_id, endpoint, request_hash, response_status, response_body, created_at, completed_at, expires_at)
                VALUES
                    ('curr1', :uid, 'POST /auth/login', 'h1', NULL, NULL, :now, NULL, :f1),
                    ('curr2', :uid, 'POST /auth/login', 'h2', NULL, NULL, :now, NULL, :f2)
                """,
                {"now": now, "uid": uid, "f1": future1, "f2": future2},
            )
            await uow.commit()

        await self._run_prune()

        async with UnitOfWork() as uow:
            total = await uow.first_row("SELECT COUNT(*) AS cnt FROM idempotency_keys")
            assert int(total["cnt"]) == 2

    async def test_prune_idempotent(self, owner_user: dict[str, str]) -> None:
        """Repeated execution is safe; second run deletes 0 rows."""
        await self._run_prune()
        await self._run_prune()
