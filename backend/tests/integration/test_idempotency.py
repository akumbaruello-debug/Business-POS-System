"""Idempotency store tests.

Covers the IdempotencyStore storage layer:
* compute_request_fingerprint — deterministic
* start — first request creates row, replay returns existing
* start — conflict on different fingerprint raises IdempotencyViolationConflict
* insert_completed — stores response
* lookup — finds completed records

These tests are against the real PostgreSQL DB.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.core.ids import new_uuid
from app.db import UnitOfWork, get_session_factory, init_db
from app.services.idempotency import (
    IdempotencyRecord,
    IdempotencyStore,
    IdempotencyViolationConflict,
    compute_request_fingerprint,
)


class TestRequestFingerprint:
    """Tests for compute_request_fingerprint."""

    def test_deterministic(self) -> None:
        """Same inputs → same hash."""
        body = {"username": "u", "password": "p"}
        fp1 = compute_request_fingerprint(method="POST", path="/a", body=body)
        fp2 = compute_request_fingerprint(method="POST", path="/a", body=body)
        assert fp1 == fp2

    def test_method_case_insensitive(self) -> None:
        """Method is normalized to uppercase."""
        a = compute_request_fingerprint(method="post", path="/x", body=None)
        b = compute_request_fingerprint(method="POST", path="/x", body=None)
        assert a == b

    def test_path_strips_query(self) -> None:
        """Path query strings are stripped from fingerprint."""
        a = compute_request_fingerprint(method="GET", path="/a?x=1&y=2", body=None)
        b = compute_request_fingerprint(method="GET", path="/a", body=None)
        assert a == b

    def test_body_normalized(self) -> None:
        """Body key order doesn't matter (sort_keys)."""
        a = compute_request_fingerprint(
            method="POST",
            path="/p",
            body={"a": 1, "b": 2},
        )
        b = compute_request_fingerprint(
            method="POST",
            path="/p",
            body={"b": 2, "a": 1},
        )
        assert a == b

    def test_different_bodies_different_fps(self) -> None:
        """Different bodies → different fingerprints."""
        a = compute_request_fingerprint(method="POST", path="/p", body={"k": 1})
        b = compute_request_fingerprint(method="POST", path="/p", body={"k": 2})
        assert a != b

    def test_none_body(self) -> None:
        """None body is supported (e.g. GET requests)."""
        fp = compute_request_fingerprint(method="GET", path="/a", body=None)
        assert isinstance(fp, str)
        assert len(fp) == 64  # SHA-256 hex


class TestIdempotencyStore:
    """Tests for the IdempotencyStore storage layer."""

    @pytest.fixture
    async def store(self, db: None) -> AsyncGenerator[IdempotencyStore, None]:
        """Yield an IdempotencyStore bound to a fresh UoW.

        Depends on ``db`` to ensure the engine is initialized and
        tables are truncated before each test. Closes the UoW when
        the test ends.
        """
        await init_db(get_settings())
        factory = get_session_factory()
        from app.core.security import PasswordHasher

        async with factory() as session:
            # Ensure a second user exists for isolation-by-user tests
            # (the FK on idempotency_keys.user_id requires a real user).
            # Use ON CONFLICT to upsert and read back the actual id.
            await session.execute(
                text(
                    "INSERT INTO users (username, full_name, email, role_id, is_active, password_hash) "
                    "VALUES ('test_user2', 'Test User 2', 'u2@test.com', 1, true, :ph) "
                    "ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash"
                ),
                {"ph": PasswordHasher().hash("TestPass123!")},
            )
            await session.commit()
        uow = UnitOfWork(session_factory=factory)
        await uow.__aenter__()
        try:
            yield IdempotencyStore(uow)
        finally:
            await uow.__aexit__(None, None, None)

    @pytest.fixture
    async def second_user_id(self, db: None) -> int:
        """Return the id of test_user2 (the second user for isolation tests)."""
        await init_db(get_settings())
        factory = get_session_factory()
        from app.core.security import PasswordHasher

        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO users (username, full_name, email, role_id, is_active, password_hash) "
                    "VALUES ('test_user2', 'Test User 2', 'u2@test.com', 1, true, :ph) "
                    "ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash"
                ),
                {"ph": PasswordHasher().hash("TestPass123!")},
            )
            await session.commit()
            row = (
                await session.execute(text("SELECT id FROM users WHERE username = 'test_user2'"))
            ).first()
            assert row is not None
            return int(row[0])

    async def test_first_request_returns_none(self, store: IdempotencyStore) -> None:
        """start_anonymous for a fresh key returns None (caller proceeds)."""
        key = str(new_uuid())
        endpoint = "POST /test/endpoint"
        fp = compute_request_fingerprint(method="POST", path="/t", body={})
        result = await store.start_anonymous(
            key=key,
            endpoint=endpoint,
            fingerprint=fp,
            is_login=False,
        )
        assert result is None

    async def test_replay_returns_existing(self, store: IdempotencyStore) -> None:
        """Same key + same fingerprint returns the existing record."""
        key = str(new_uuid())
        endpoint = "POST /test/endpoint"
        body = {"foo": "bar"}
        fp = compute_request_fingerprint(method="POST", path="/t", body=body)

        # Insert a completed record
        await store.insert_completed(
            key=key,
            endpoint=endpoint,
            fingerprint=fp,
            response_status=200,
            response_body='{"ok": true}',
            user_id=1,
        )
        # Now lookup should find it
        result = await store.start_anonymous(
            key=key,
            endpoint=endpoint,
            fingerprint=fp,
            is_login=False,
        )
        assert result is not None
        assert result.key == key
        assert result.is_completed
        assert result.response_status == 200

    async def test_conflict_raises(self, store: IdempotencyStore) -> None:
        """Same key + different fingerprint raises IdempotencyViolationConflict."""
        key = str(new_uuid())
        endpoint = "POST /test/endpoint"
        body1 = {"foo": "bar"}
        body2 = {"foo": "baz"}
        fp1 = compute_request_fingerprint(method="POST", path="/t", body=body1)
        fp2 = compute_request_fingerprint(method="POST", path="/t", body=body2)

        await store.insert_completed(
            key=key,
            endpoint=endpoint,
            fingerprint=fp1,
            response_status=200,
            response_body='{"ok": true}',
            user_id=1,
        )

        with pytest.raises(IdempotencyViolationConflict) as exc_info:
            await store.start_anonymous(
                key=key,
                endpoint=endpoint,
                fingerprint=fp2,
                is_login=False,
            )
        assert exc_info.value.details == {"key": key, "endpoint": endpoint}

    async def test_start_authenticated_first(self, store: IdempotencyStore) -> None:
        """Authenticated start: first call inserts a new row."""
        key = str(new_uuid())
        endpoint = "POST /test/auth"
        user_id = 1
        fp = compute_request_fingerprint(method="POST", path="/t", body={"x": 1})

        record = await store.start(
            key=key,
            user_id=user_id,
            endpoint=endpoint,
            fingerprint=fp,
            is_login=False,
        )
        assert record is not None
        assert record.in_flight  # NULL response_status
        assert record.user_id == user_id

    async def test_start_authenticated_replay(self, store: IdempotencyStore) -> None:
        """Authenticated start: identical replay returns existing in-flight row."""
        key = str(new_uuid())
        endpoint = "POST /test/auth"
        user_id = 1
        fp = compute_request_fingerprint(method="POST", path="/t", body={"x": 1})

        r1 = await store.start(
            key=key,
            user_id=user_id,
            endpoint=endpoint,
            fingerprint=fp,
            is_login=False,
        )
        r2 = await store.start(
            key=key,
            user_id=user_id,
            endpoint=endpoint,
            fingerprint=fp,
            is_login=False,
        )
        assert r1.id == r2.id
        assert r2.in_flight

    async def test_start_authenticated_conflict(self, store: IdempotencyStore) -> None:
        """Authenticated start: different fingerprint raises conflict."""
        key = str(new_uuid())
        endpoint = "POST /test/auth"
        user_id = 1
        fp1 = compute_request_fingerprint(method="POST", path="/t", body={"x": 1})
        fp2 = compute_request_fingerprint(method="POST", path="/t", body={"x": 2})

        await store.start(
            key=key,
            user_id=user_id,
            endpoint=endpoint,
            fingerprint=fp1,
            is_login=False,
        )
        with pytest.raises(IdempotencyViolationConflict):
            await store.start(
                key=key,
                user_id=user_id,
                endpoint=endpoint,
                fingerprint=fp2,
                is_login=False,
            )

    async def test_complete_marks_record(self, store: IdempotencyStore) -> None:
        """complete() updates response_status and marks record as completed."""
        key = str(new_uuid())
        endpoint = "POST /test/complete"
        user_id = 1
        fp = compute_request_fingerprint(method="POST", path="/t", body={})

        record = await store.start(
            key=key,
            user_id=user_id,
            endpoint=endpoint,
            fingerprint=fp,
            is_login=False,
        )
        await store.complete(
            record_id=record.id,
            status=200,
            body={"ok": True},
            user_id=user_id,
        )
        # Re-lookup
        result = await store.lookup(
            key=key,
            user_id=user_id,
            endpoint=endpoint,
        )
        assert result is not None
        assert result.is_completed
        assert result.response_status == 200

    async def test_isolation_by_user(self, store: IdempotencyStore, second_user_id: int) -> None:
        """Different users with the same key don't conflict (separate rows)."""
        key = str(new_uuid())
        endpoint = "POST /test/iso"
        fp = compute_request_fingerprint(method="POST", path="/t", body={})

        # Insert for user 1
        await store.insert_completed(
            key=key,
            endpoint=endpoint,
            fingerprint=fp,
            response_status=200,
            response_body='{"u":1}',
            user_id=1,
        )
        # Same key for user 1: must return existing
        r1 = await store.start(
            key=key,
            user_id=1,
            endpoint=endpoint,
            fingerprint=fp,
        )
        # Same key for second_user_id: must not exist (lookup returns None)
        r2 = await store.start(
            key=key,
            user_id=second_user_id,
            endpoint=endpoint,
            fingerprint=fp,
        )
        assert r1.is_completed
        assert r2.in_flight  # inserted a new row

    async def test_record_dataclass_properties(self) -> None:
        """IdempotencyRecord properties: in_flight / is_completed."""
        now = __import__("datetime").datetime.now()
        rec_inflight = IdempotencyRecord(
            id=1,
            key="x",
            user_id=1,
            endpoint="e",
            request_hash="h",
            response_status=None,
            response_body=None,
            created_at=now,
            completed_at=None,
            expires_at=now,
        )
        rec_done = IdempotencyRecord(
            id=2,
            key="y",
            user_id=1,
            endpoint="e",
            request_hash="h",
            response_status=200,
            response_body={},
            created_at=now,
            completed_at=now,
            expires_at=now,
        )
        assert rec_inflight.in_flight
        assert not rec_inflight.is_completed
        assert not rec_done.in_flight
        assert rec_done.is_completed
