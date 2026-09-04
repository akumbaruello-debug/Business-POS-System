"""Prune expired export jobs.

Daily cron job per G.6 / Backend-Implementation-Plan-V1.0.md §7.

Removes completed export jobs whose ``expires_at`` is older than
7 days from the in-memory ``ExportStore``. Also drops associated
``file_bytes`` to free memory.

Usage::

    python -m app.tools.prune_exports

Safe to run repeatedly: ``dict.pop(key, None)`` handles already-removed
jobs; no jobs = 0 pruned.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.logging import configure_logging, get_logger
from app.services.exports import get_export_store

# Export files are retained for 7 days after completion.
EXPORT_RETENTION_DAYS = 7


def prune_expired_exports() -> int:
    """Remove completed export jobs older than 7 days.

    Returns the number of jobs pruned.
    """
    store = get_export_store()
    cutoff = datetime.now(UTC) - timedelta(days=EXPORT_RETENTION_DAYS)
    pruned = 0

    for job in store.all_jobs():
        if (
            job.status == "complete"
            and job.expires_at is not None
            and job.expires_at < cutoff
        ):
            store.delete(job.export_id)
            pruned += 1

    return pruned


def main() -> None:
    """CLI entry point."""
    from app.config import get_settings
    from app.config.env import load_env

    load_env()
    settings = get_settings()
    configure_logging(log_level=settings.log_level, log_format=settings.log_format)
    logger = get_logger("prune_exports")

    pruned = prune_expired_exports()
    logger.info("prune_exports_complete", pruned=pruned)
    print(f"Pruned {pruned} expired export job(s).")


if __name__ == "__main__":
    main()
