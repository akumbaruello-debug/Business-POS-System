"""Idempotency infrastructure foundation.

Implements the storage layer for the ``idempotency_keys`` table
(``Backend-Architecture-V1.0.md`` §16) plus a helper to resolve an
Idempotency-Key to an existing response or mark one in-flight.

Per the OpenAPI ``IdempotencyKey`` parameter spec, the contract is:

    Same key + same endpoint + same user + same request_hash
        → return the cached response (exact replay).

    Same key + same endpoint + same user + DIFFERENT request_hash
        → 409 idempotency_violation.

The request_hash is computed over the canonical request body so
semantically-identical retries (same body, different formatting) still
match.

M1 does NOT wire idempotency into any endpoint beyond ``/auth/login``
(that wiring happens in M2 when we add the auth route decorators here).
What M1 provides is the *storage layer* and the *fingerprint* helper,
plus the in-flight 202 semantics.

**Pre-auth note:** ``/auth/login`` is a pre-authentication endpoint —
the caller's ``user_id`` is not known until the credentials are
verified. The ``idempotency_keys`` table has ``user_id INT NOT NULL``
with a FK to ``users``. To keep the FK satisfied we pass
``user_id = 0`` as a sentinel during the *lookup*, and on a cache miss
we defer the INSERT until after auth succeeds, re-inserting with the
resolved ``user_id``. Both the lookup and the deferred INSERT happen
inside the same UoW transaction so no orphaned rows escape a rollback.

This does NOT modify the locked schema (no migration change);
``user_id = 0`` simply never persists because the INSERT only runs on a
successful auth.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.config import get_settings
from app.core.clock import get_clock
from app.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def compute_request_fingerprint(
    *,
    method: str,
    path: str,
    body: Any,
) -> str:
    """Compute the deterministic fingerprint of a request.

    The fingerprint is over ``(method, path, canonical(body))``. It is
    stored as ``request_hash`` in ``idempotency_keys``. Two retries of
    the same logical operation must produce the same fingerprint so the
    cached response is returned.

    Canonicalisation: ``json.dumps(body, sort_keys=True, separators=...)``
    so whitespace / key-order differences don't cause false misses.

    For a request with no body (e.g. a GET), ``body`` should be ``None``.
    """
    payload = {
        "method": method.upper(),
        "path": path.split("?")[0],  # strip query string
        "body": body,
    }
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Record type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdempotencyRecord:
    """A row from ``idempotency_keys``.

    The ``user_id`` here is the FK value from the row (``0`` while
    pre-auth, the real id once the record is completed). ``response_*``
    fields are ``None`` for an in-flight record.
    """

    id: int
    key: str  # UUID string
    user_id: int
    endpoint: str
    request_hash: str
    response_status: int | None  # None = in-flight
    response_body: Any | None  # stored JSON (dict/list/etc.)
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime

    @property
    def in_flight(self) -> bool:
        """True if the request is still being processed."""
        return self.response_status is None

    @property
    def is_completed(self) -> bool:
        """True if the request completed and a response is cached."""
        return self.response_status is not None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IdempotencyViolationConflict(Exception):
    """Raised when the same key is reused with a different fingerprint.

    The FastAPI dependency layer converts this into a 422
    ``idempotency_violation``.
    """

    def __init__(self, details: dict[str, Any] | None = None) -> None:
        self.details = details or {}
        super().__init__("Idempotency-key conflict")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

#: Sentinel ``user_id`` for pre-authentication flows where the caller
#: identity is not yet known at lookup time. This value is NEVER written
#: to the database as a completed record — the ``complete`` / re-insert
#: step always runs after auth succeeds with the real ``user_id``.
ANON_USER_ID: int = 0


class IdempotencyStore:
    """Storage layer for the ``idempotency_keys`` table.

    All methods take a UoW so the idempotency check and the response
    insertion happen in the **same transaction** as the business
    mutation (§16 transaction interaction). If the mutation rolls
    back, the idempotency entry is rolled back too — no orphaned 202s.
    """

    def __init__(self, uow: Any) -> None:
        self._uow = uow

    def _default_ttl(self, is_login: bool = False) -> timedelta:
        s = get_settings()
        if is_login:
            return timedelta(seconds=s.idempotency_login_ttl_seconds)
        return timedelta(seconds=s.idempotency_default_ttl_seconds)

    async def lookup(
        self,
        *,
        key: str,
        user_id: int,
        endpoint: str,
    ) -> IdempotencyRecord | None:
        """Look up an idempotency record.

        ``user_id`` filters the lookup. For pre-auth flows pass
        :data:`ANON_USER_ID`; the lookup still returns any existing
        completed/in-flight row for the *key+endpoint* pair (the
        ``user_id`` filter is applied only when the caller is
        authenticated).
        """
        # When the caller is not yet authenticated (user_id == ANON),
        # we match by key + endpoint only. This lets a retry of a login
        # request find its cached row even though we don't yet know
        # the user.
        if user_id == ANON_USER_ID:
            row = await self._uow.first_row(
                """
                SELECT
                    id, key, user_id, endpoint,
                    request_hash, response_status, response_body,
                    created_at, completed_at, expires_at
                FROM idempotency_keys
                WHERE key = :key AND endpoint = :endpoint
                """,
                {"key": key, "endpoint": endpoint},
            )
        else:
            row = await self._uow.first_row(
                """
                SELECT
                    id, key, user_id, endpoint,
                    request_hash, response_status,
                    response_body,
                    created_at, completed_at, expires_at
                FROM idempotency_keys
                WHERE key = :key
                  AND user_id = :uid
                  AND endpoint = :endpoint
                """,
                {"key": key, "uid": user_id, "endpoint": endpoint},
            )
        if row is None:
            return None
        return self._row_to_record(row)

    async def lookup_raw(self, *, key: str, endpoint: str) -> IdempotencyRecord | None:
        """Lookup by (key, endpoint) only — for pre-auth flows."""
        row = await self._uow.first_row(
            """
            SELECT
                id, key, user_id, endpoint,
                request_hash, response_status, response_body,
                created_at, completed_at, expires_at
            FROM idempotency_keys
            WHERE key = :key AND endpoint = :endpoint
            """,
            {"key": key, "endpoint": endpoint},
        )
        if row is None:
            return None
        return self._row_to_record(row)

    async def start(
        self,
        *,
        key: str,
        user_id: int,
        endpoint: str,
        fingerprint: str,
        is_login: bool = False,
    ) -> IdempotencyRecord:
        """Insert a "pending" idempotency row, or return the existing one.

        Returns the (existing or freshly-inserted) record. A conflict
        (different fingerprint, same key) raises
        :class:`IdempotencyViolationConflict`.
        """
        existing = await self.lookup(
            key=key,
            user_id=user_id,
            endpoint=endpoint,
        )
        if existing is not None:
            if existing.request_hash != fingerprint:
                raise IdempotencyViolationConflict(
                    details={"key": key, "endpoint": endpoint},
                )
            return existing

        now = get_clock().now()
        expires_at = now + self._default_ttl(is_login=is_login)

        # Insert the pending row.
        await self._uow.execute(
            """
            INSERT INTO idempotency_keys (
                key, user_id, endpoint, request_hash,
                response_status, response_body, completed_at, expires_at
            )
            VALUES (:key, :uid, :endpoint, :hash,
                    NULL, NULL, NULL, :expires)
            """,
            {
                "key": key,
                "uid": user_id,
                "endpoint": endpoint,
                "hash": fingerprint,
                "expires": expires_at,
            },
        )
        # Re-lookup with the user_id filter so the newly-inserted row
        # is the one we return (not another user's record that might
        # share the same key).
        record = await self.lookup(
            key=key,
            user_id=user_id,
            endpoint=endpoint,
        )
        if record is None:  # pragma: no cover - defensive
            raise RuntimeError("Idempotency row missing after insert")
        return record

    async def start_anonymous(
        self,
        *,
        key: str,
        endpoint: str,
        fingerprint: str,
        is_login: bool = False,
    ) -> IdempotencyRecord | None:
        """Reserve a key for a pre-auth flow.

        Looks up by (key, endpoint) only. Returns an existing completed
        row (for replay), an existing in-flight row (caller should 202),
        *or* ``None`` when a fresh reservation is needed.

        The caller must subsequently call :meth:`complete_anonymous`
        with the resolved ``user_id`` to persist the completed record.
        """
        existing = await self.lookup_raw(key=key, endpoint=endpoint)
        if existing is not None:
            if existing.request_hash != fingerprint:
                raise IdempotencyViolationConflict(
                    details={"key": key, "endpoint": endpoint},
                )
            return existing

        # No existing row — signal the caller to proceed (it will
        # insert a completed row after auth succeeds).
        return None

    async def complete(
        self,
        *,
        record_id: int,
        status: int,
        body: dict[str, Any] | list[Any] | str,
        user_id: int,
    ) -> None:
        """Mark a pending idempotency record as completed.

        Also re-links the resolved ``user_id`` (relevant for pre-auth
        flows where the row was looked up with ``user_id = 0``).
        """
        await self._uow.execute(
            """
            UPDATE idempotency_keys
            SET response_status = :status,
                response_body   = :body,
                user_id         = :uid,
                completed_at    = NOW()
            WHERE id = :id
            """,
            {"status": status, "body": json.dumps(body), "id": record_id, "uid": user_id},
        )

    async def insert_completed(
        self,
        *,
        key: str,
        endpoint: str,
        fingerprint: str,
        response_status: int,
        response_body: str,
        user_id: int,
    ) -> int:
        """Insert a completed (response-cached) idempotency row.

        Used by pre-auth flows (login) where the row is only created
        after the user is resolved.
        """
        now = get_clock().now()
        expires_at = now + self._default_ttl(is_login=True)
        await self._uow.execute(
            """
            INSERT INTO idempotency_keys (
                key, user_id, endpoint, request_hash,
                response_status, response_body, completed_at, expires_at
            )
            VALUES (:key, :uid, :endpoint, :hash,
                    :status, :body, NOW(), :expires)
            """,
            {
                "key": key,
                "uid": user_id,
                "endpoint": endpoint,
                "hash": fingerprint,
                "status": response_status,
                "body": response_body,
                "expires": expires_at,
            },
        )
        row = await self._uow.first_row(
            "SELECT id FROM idempotency_keys WHERE key = :key AND endpoint = :endpoint",
            {"key": key, "endpoint": endpoint},
        )
        return int(row["id"]) if row else 0

    # ---- internal ----

    def _row_to_record(self, row: dict[str, Any]) -> IdempotencyRecord:
        return IdempotencyRecord(
            id=int(row["id"]),
            key=str(row["key"]),
            user_id=int(row["user_id"]) if row["user_id"] is not None else 0,
            endpoint=str(row["endpoint"]),
            request_hash=str(row["request_hash"]),
            response_status=int(row["response_status"])
            if row["response_status"] is not None
            else None,
            response_body=row["response_body"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            expires_at=row["expires_at"],
        )


__all__ = [
    "ANON_USER_ID",
    "IdempotencyRecord",
    "IdempotencyStore",
    "IdempotencyViolationConflict",
    "compute_request_fingerprint",
]
