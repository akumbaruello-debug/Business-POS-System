"""Prometheus metrics for the Business POS backend.

Exposes `/metrics` (gated by `BPOS_ENABLE_METRICS=true`) with:

- `http_requests_total{method, path_template, status}` (counter)
- `http_request_duration_seconds{method, path_template}` (histogram)
- `db_pool_connections{state}` (gauge; active / idle / total)
- `idempotency_replays_total{endpoint}` (counter)
- `idempotency_violations_total{endpoint}` (counter)
- `lock_timeouts_total{lock_name}` (counter)
- `cash_balance_lock_wait_seconds` (histogram)
- `stock_movements_inserted_total{trigger}` (counter)
- `audit_log_writes_total{action, entity_type}` (counter)

Per Backend-Architecture-V1.0.md §24.2 and
Backend-Implementation-Plan-V1.0.md §7 G.4.
"""

from __future__ import annotations

from typing import Any

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    REGISTRY,
)

# ---------------------------------------------------------------------------
# HTTP metrics (recorded by MetricsMiddleware)
# ---------------------------------------------------------------------------

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path_template", "status"],
    registry=REGISTRY,
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path_template"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# DB pool metrics
# ---------------------------------------------------------------------------

DB_POOL_CONNECTIONS = Gauge(
    "db_pool_connections",
    "Database connection pool state",
    ["state"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Idempotency metrics
# ---------------------------------------------------------------------------

IDEMPOTENCY_REPLAYS_TOTAL = Counter(
    "idempotency_replays_total",
    "Idempotency key replays (cached responses returned)",
    ["endpoint"],
    registry=REGISTRY,
)

IDEMPOTENCY_VIOLATIONS_TOTAL = Counter(
    "idempotency_violations_total",
    "Idempotency key violations (same key, different body)",
    ["endpoint"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Concurrency / lock metrics
# ---------------------------------------------------------------------------

LOCK_TIMEOUTS_TOTAL = Counter(
    "lock_timeouts_total",
    "Advisory lock acquisition timeouts",
    ["lock_name"],
    registry=REGISTRY,
)

CASH_BALANCE_LOCK_WAIT_SECONDS = Histogram(
    "cash_balance_lock_wait_seconds",
    "Time spent waiting for cash-balance advisory lock",
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Inventory metrics
# ---------------------------------------------------------------------------

STOCK_MOVEMENTS_INSERTED_TOTAL = Counter(
    "stock_movements_inserted_total",
    "Total stock_movements rows inserted",
    ["trigger"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Audit metrics
# ---------------------------------------------------------------------------

AUDIT_LOG_WRITES_TOTAL = Counter(
    "audit_log_writes_total",
    "Total audit_log rows written",
    ["action", "entity_type"],
    registry=REGISTRY,
)


def get_registry() -> Any:
    """Return the Prometheus registry used by this application."""
    return REGISTRY


__all__ = [
    "REGISTRY",
    "get_registry",
    "HTTP_REQUESTS_TOTAL",
    "HTTP_REQUEST_DURATION_SECONDS",
    "DB_POOL_CONNECTIONS",
    "IDEMPOTENCY_REPLAYS_TOTAL",
    "IDEMPOTENCY_VIOLATIONS_TOTAL",
    "LOCK_TIMEOUTS_TOTAL",
    "CASH_BALANCE_LOCK_WAIT_SECONDS",
    "STOCK_MOVEMENTS_INSERTED_TOTAL",
    "AUDIT_LOG_WRITES_TOTAL",
]
