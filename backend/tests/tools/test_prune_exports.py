"""Tests for export pruning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient


class TestPruneExports:
    """Focused G.6 tests for prune_exports.py."""

    async def test_prune_removes_expired_jobs(self, owner_user: dict[str, str]) -> None:
        """Expired jobs (7d ago) are removed; current kept."""
        from app.services.exports import get_export_store

        store = get_export_store()
        store.clear()

        now = datetime.now(UTC)
        expired = now - timedelta(days=8)
        current = now + timedelta(hours=1)

        exp_job = store.create(report="sales", fmt="csv", filters={})
        exp_job.status = "complete"
        exp_job.expires_at = expired

        curr_job = store.create(report="inventory", fmt="csv", filters={})
        curr_job.status = "complete"
        curr_job.expires_at = current

        from app.tools.prune_exports import prune_expired_exports

        pruned = prune_expired_exports()
        assert pruned == 1, f"Expected 1 pruned; got {pruned}"
        assert store.get(exp_job.export_id) is None
        assert store.get(curr_job.export_id) is not None

    async def test_prune_preserves_non_expired_jobs(self, owner_user: dict[str, str]) -> None:
        """Jobs with expires_at >= cutoff remain untouched."""
        from app.services.exports import get_export_store

        store = get_export_store()
        store.clear()

        now = datetime.now(UTC)
        # Exactly at boundary → preserved
        boundary_job = store.create(report="best_sellers", fmt="csv", filters={})
        boundary_job.status = "complete"
        boundary_job.expires_at = now - timedelta(days=7)

        future_job = store.create(report="p-and-l", fmt="csv", filters={})
        future_job.expires_at = now + timedelta(days=30)

        from app.tools.prune_exports import prune_expired_exports

        pruned = prune_expired_exports()
        assert pruned == 0
        assert store.get(boundary_job.export_id) is not None
        assert store.get(future_job.export_id) is not None

    async def test_prune_safe_when_no_expires(self, owner_user: dict[str, str]) -> None:
        """Jobs without expires_at are never deleted."""
        from app.services.exports import get_export_store

        store = get_export_store()
        store.clear()

        job = store.create(report="refunds", fmt="csv", filters={})
        job.status = "complete"
        # No expires_at set

        from app.tools.prune_exports import prune_expired_exports

        pruned = prune_expired_exports()
        assert pruned == 0
        assert store.get(job.export_id) is not None

    async def test_prune_safe_when_already_deleted(self, owner_user: dict[str, str]) -> None:
        """Repeated execution is safe when all jobs already removed."""
        from app.services.exports import get_export_store

        store = get_export_store()
        store.clear()

        now = datetime.now(UTC)
        job = store.create(report="payables", fmt="csv", filters={})
        job.status = "complete"
        job.expires_at = now - timedelta(days=8)

        from app.tools.prune_exports import prune_expired_exports

        p1 = prune_expired_exports()
        assert p1 == 1
        assert store.get(job.export_id) is None

        p2 = prune_expired_exports()
        assert p2 == 0  # nothing left to remove
