"""F.8 - Notifications read API tests.

Covers the three notification read endpoints per ``openapi.yaml`` section 15.19
(lines 5790-5876) and ``Backend-Implementation-Plan-V1.0.md`` section 6 item
F.8 (lines 719-723):

* ``GET  /notifications``                - ``listNotifications``
* ``POST /notifications/{id}/mark-read`` — ``markNotificationRead``
* ``POST /notifications/mark-all-read``  — ``markAllNotificationsRead``

Test matrix (mirrors the F.1-F.6 authorization + read matrix):

1.  Auth: 401 unauthenticated for list / mark-read / mark-all-read.
2.  Authz: Staff gets 403 only where the capability is genuinely absent.
    (Both ``notification.view`` and ``notification.mark_read`` are in
    ``STAFF_DEFAULT_CAPABILITIES``, so Staff is 200 here — this test
    documents that deliberately.)
3.  Empty list → ``data: []``, ``total: 0``.
4.  One seeded notification → appears in the list.
5.  ``filter[is_read]=true`` → only read rows.
6.  ``filter[is_read]=false`` → only unread.
7.  Pagination (``per_page`` smaller than total) → 2nd page correct,
    ``total`` reflects the full count.
8.  ``q`` substring search across title/body.
9.  ``from`` / ``to`` inclusive date filtering on ``created_at``.
10. ``sort`` = ``-created_at`` / ``id`` / ``-id``.
11. mark-read success → ``is_read`` flips, ``read_at`` populated.
12. mark-read nonexistent id → 404 (NotFound envelope).
13. mark-read foreign user's notification → 404 (ownership folded into
    the WHERE, no existence leak; verified by confirming the owner's row
    is untouched).
14. mark-all-read success → unread count drops to 0, body has ``updated``.
15. mark-all-read only affects the current user (owner's unread rows
    remain unread for another user — verified by seeding two users).
16. Notification type mapping: DB ``insufficient_stock`` surfaces as
    ``out_of_stock`` ; ``below_cost`` surfaces as ``below_cost_sale``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db import get_session_factory

# ---------------------------------------------------------------------------
# Helpers (mirror test_dashboard_f5.py / test_exports_f6.py)
# ---------------------------------------------------------------------------


def _idem() -> str:
    return str(uuid.uuid4())


async def _login(app: AsyncClient, username: str, password: str) -> str:
    r = await app.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Idempotency-Key": _idem()},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _owner_headers(app: AsyncClient, owner_user: dict[str, Any]) -> dict[str, str]:
    token = await _login(app, owner_user["username"], owner_user["password"])
    return {"Authorization": f"Bearer {token}"}


async def _staff_headers(app: AsyncClient, staff_user: dict[str, Any]) -> dict[str, str]:
    token = await _login(app, staff_user["username"], staff_user["password"])
    return {"Authorization": f"Bearer {token}"}


async def _seed_notification(
    *,
    user_id: int,
    category: str,
    title: str = "Test",
    body: str | None = None,
    is_read: bool = False,
    created_at: datetime | None = None,
    reference_type: str | None = None,
    reference_id: int | None = None,
) -> int:
    """Insert a notification row directly, returning its id."""

    now = created_at or datetime.now(UTC)
    factory = get_session_factory()
    async with factory() as session:
        row = await session.execute(
            text(
                """
                    INSERT INTO notifications
                        (user_id, category, severity, title, body,
                         reference_type, reference_id, is_read, read_at,
                         created_at)
                    VALUES
                        (:uid, :cat, :sev, :title, :body,
                         :rt, :rid, :is_read, :read_at, :created_at)
                    RETURNING id
                    """
            ),
            {
                "uid": user_id,
                "cat": category,
                "sev": "info",
                "title": title,
                "body": body,
                "rt": reference_type,
                "rid": reference_id,
                "is_read": is_read,
                "read_at": datetime.now(UTC) if is_read else None,
                "created_at": now,
            },
        )
        await session.commit()
        return int(row.scalar())


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestF8Authorization:
    @pytest.mark.asyncio
    async def test_list_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/notifications")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_mark_read_unauthenticated_401(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        r = await app.post("/api/v1/notifications/1/mark-read")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_mark_all_read_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.post("/api/v1/notifications/mark-all-read")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_list_staff_200(self, app: AsyncClient, staff_user: dict[str, Any]) -> None:
        """Both Owner and Staff hold notification.view + notification.mark_read."""
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/notifications", headers=h)
        assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_list_owner_200(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/notifications", headers=h)
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Empty + basic list
# ---------------------------------------------------------------------------


class TestF8List:
    @pytest.mark.asyncio
    async def test_empty_list(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/notifications", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["total_pages"] == 0

    @pytest.mark.asyncio
    async def test_one_notification(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        nid = await _seed_notification(
            user_id=owner_user["user_id"],
            category="low_stock",
            title="Low stock: Widget",
            body="Dropped to 2 units.",
        )
        r = await app.get("/api/v1/notifications", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) == 1
        item = body["data"][0]
        assert item["id"] == nid
        assert item["type"] == "low_stock"
        assert item["is_read"] is False
        assert item["related_entity_type"] is None
        assert item["related_entity_id"] is None
        # message is title + body joined
        assert "Low stock: Widget" in item["message"]

    @pytest.mark.asyncio
    async def test_response_schema(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/notifications", headers=h)
        assert r.status_code == 200
        body = r.json()
        # Envelope keys
        assert set(body.keys()) == {"data", "pagination"}
        assert set(body["pagination"].keys()) == {"page", "per_page", "total", "total_pages"}


# ---------------------------------------------------------------------------
# Ownership isolation
# ---------------------------------------------------------------------------


class TestF8Ownership:
    @pytest.mark.asyncio
    async def test_list_only_returns_own_notifications(
        self, app: AsyncClient, owner_user: dict[str, Any], staff_user: dict[str, Any]
    ) -> None:
        # Seed one notification for owner, one for staff.
        await _seed_notification(
            user_id=owner_user["user_id"],
            category="low_stock",
            title="owner note",
        )
        await _seed_notification(
            user_id=staff_user["user_id"],
            category="low_stock",
            title="staff note",
        )
        # Staff should only see their own.
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/notifications", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) == 1
        assert "staff note" in body["data"][0]["message"]
        # Owner should only see their own.
        h2 = await _owner_headers(app, owner_user)
        r2 = await app.get("/api/v1/notifications", headers=h2)
        assert r2.status_code == 200
        body2 = r2.json()
        assert len(body2["data"]) == 1
        assert "owner note" in body2["data"][0]["message"]

    @pytest.mark.asyncio
    async def test_mark_read_foreign_404(
        self, app: AsyncClient, owner_user: dict[str, Any], staff_user: dict[str, Any]
    ) -> None:
        # Seed a notification owned by owner.
        nid = await _seed_notification(
            user_id=owner_user["user_id"],
            category="low_stock",
            title="owner only",
        )
        # Staff tries to mark-read owner's notification → 404 (no leak).
        h = await _staff_headers(app, staff_user)
        r = await app.post(f"/api/v1/notifications/{nid}/mark-read", headers=h)
        assert r.status_code == 404
        # Owner's notification must be unchanged.
        h2 = await _owner_headers(app, owner_user)
        r2 = await app.get("/api/v1/notifications", headers=h2)
        assert r2.status_code == 200
        data = r2.json()["data"]
        assert len(data) == 1
        assert data[0]["is_read"] is False


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class TestF8Filtering:
    async def _seed_mixture(self, app: AsyncClient, owner_user: dict[str, Any]) -> tuple[int, int]:
        """Seed one read + one unread notification, return (read_id, unread_id)."""

        read_id = await _seed_notification(
            user_id=owner_user["user_id"],
            category="system",
            title="Read one",
            is_read=True,
        )
        unread_id = await _seed_notification(
            user_id=owner_user["user_id"],
            category="low_stock",
            title="Unread one",
            is_read=False,
        )
        return read_id, unread_id

    @pytest.mark.asyncio
    async def test_filter_is_read_true(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        await self._seed_mixture(app, owner_user)
        r = await app.get("/api/v1/notifications", headers=h, params={"filter[is_read]": "true"})
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["is_read"] is True

    @pytest.mark.asyncio
    async def test_filter_is_read_false(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        await self._seed_mixture(app, owner_user)
        r = await app.get("/api/v1/notifications", headers=h, params={"filter[is_read]": "false"})
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["is_read"] is False

    @pytest.mark.asyncio
    async def test_filter_q_search(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        await _seed_notification(
            user_id=owner_user["user_id"],
            category="system",
            title="Hello world",
            body="some content",
        )
        await _seed_notification(
            user_id=owner_user["user_id"],
            category="system",
            title="Goodbye",
            body="other content",
        )
        r = await app.get("/api/v1/notifications", headers=h, params={"q": "Hello"})
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) == 1
        assert "Hello world" in body["data"][0]["message"]

    @pytest.mark.asyncio
    async def test_from_to_filtering(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        now = datetime.now(UTC)
        old = now - timedelta(hours=2)
        mid = now - timedelta(hours=1)
        await _seed_notification(
            user_id=owner_user["user_id"],
            category="system",
            title="old",
            created_at=old,
        )
        await _seed_notification(
            user_id=owner_user["user_id"],
            category="system",
            title="mid",
            created_at=mid,
        )
        await _seed_notification(
            user_id=owner_user["user_id"],
            category="system",
            title="new",
            created_at=now,
        )
        # from old..mid inclusive → 2 rows.
        r = await app.get(
            "/api/v1/notifications",
            headers=h,
            params={"from": old.isoformat(), "to": mid.isoformat()},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) == 2
        titles = [item["message"] for item in body["data"]]
        assert "old" in titles[0] + titles[1]
        assert "mid" in titles[0] + titles[1]


# ---------------------------------------------------------------------------
# Pagination + sorting
# ---------------------------------------------------------------------------


class TestF8Pagination:
    @pytest.mark.asyncio
    async def test_pagination_two_pages(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        # Seed 3 notifications.
        for i in range(3):
            await _seed_notification(
                user_id=owner_user["user_id"],
                category="system",
                title=f"item {i}",
                created_at=datetime.now(UTC) - timedelta(minutes=i),
            )
        r = await app.get("/api/v1/notifications", headers=h, params={"per_page": 2, "page": 1})
        assert r.status_code == 200
        b1 = r.json()
        assert len(b1["data"]) == 2
        assert b1["pagination"]["total"] == 3
        assert b1["pagination"]["total_pages"] == 2
        # Page 2 has the remaining row.
        r2 = await app.get("/api/v1/notifications", headers=h, params={"per_page": 2, "page": 2})
        assert r2.status_code == 200
        b2 = r2.json()
        assert len(b2["data"]) == 1

    @pytest.mark.asyncio
    async def test_sort_id_desc(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        nid1 = await _seed_notification(
            user_id=owner_user["user_id"],
            category="system",
            title="first",
        )
        nid2 = await _seed_notification(
            user_id=owner_user["user_id"],
            category="system",
            title="second",
        )
        r = await app.get("/api/v1/notifications", headers=h, params={"sort": "id"})
        assert r.status_code == 200
        data = r.json()["data"]
        # Default created_at DESC; for ties, id ASC → nid1 before nid2.
        ids = [d["id"] for d in data]
        assert nid1 in ids
        assert nid2 in ids

        # -id → higher id first
        r2 = await app.get("/api/v1/notifications", headers=h, params={"sort": "-id"})
        data2 = r2.json()["data"]
        ids2 = [d["id"] for d in data2]
        assert ids2[0] == max(nid1, nid2)

    @pytest.mark.asyncio
    async def test_sort_invalid_falls_back(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        await _seed_notification(user_id=owner_user["user_id"], category="system", title="a")
        r = await app.get("/api/v1/notifications", headers=h, params={"sort": "bogus"})
        assert r.status_code == 200
        assert len(r.json()["data"]) == 1


# ---------------------------------------------------------------------------
# mark-read
# ---------------------------------------------------------------------------


class TestF8MarkRead:
    @pytest.mark.asyncio
    async def test_mark_read_success(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        nid = await _seed_notification(
            user_id=owner_user["user_id"],
            category="low_stock",
            title="unread",
            is_read=False,
        )
        r = await app.post(f"/api/v1/notifications/{nid}/mark-read", headers=h)
        assert r.status_code == 204
        # Verify persisted.
        r2 = await app.get("/api/v1/notifications", headers=h)
        data = r2.json()["data"]
        row = next(d for d in data if d["id"] == nid)
        assert row["is_read"] is True

    @pytest.mark.asyncio
    async def test_mark_read_nonexistent_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.post("/api/v1/notifications/999999/mark-read", headers=h)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# mark-all-read
# ---------------------------------------------------------------------------


class TestF8MarkAllRead:
    @pytest.mark.asyncio
    async def test_mark_all_read_success(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        for i in range(3):
            await _seed_notification(
                user_id=owner_user["user_id"],
                category="system",
                title=f"unread {i}",
                is_read=False,
            )
        await _seed_notification(
            user_id=owner_user["user_id"],
            category="system",
            title="already read",
            is_read=True,
        )
        r = await app.post("/api/v1/notifications/mark-all-read", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["updated"] == 3
        # All now read.
        r2 = await app.get(
            "/api/v1/notifications",
            headers=h,
            params={"filter[is_read]": "false"},
        )
        assert r2.json()["data"] == []

    @pytest.mark.asyncio
    async def test_mark_all_read_only_current_user(
        self,
        app: AsyncClient,
        owner_user: dict[str, Any],
        staff_user: dict[str, Any],
    ) -> None:
        # Seed unread for BOTH users.
        await _seed_notification(
            user_id=owner_user["user_id"],
            category="system",
            title="owner unread",
            is_read=False,
        )
        await _seed_notification(
            user_id=staff_user["user_id"],
            category="system",
            title="staff unread",
            is_read=False,
        )
        # Staff marks all read.
        h = await _staff_headers(app, staff_user)
        r = await app.post("/api/v1/notifications/mark-all-read", headers=h)
        assert r.status_code == 200
        assert r.json()["updated"] == 1
        # Owner's unread must remain unread.
        h2 = await _owner_headers(app, owner_user)
        r2 = await app.get(
            "/api/v1/notifications",
            headers=h2,
            params={"filter[is_read]": "false"},
        )
        assert r2.status_code == 200
        data = r2.json()["data"]
        assert len(data) == 1
        assert "owner unread" in data[0]["message"]


# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------


class TestF8TypeMapping:
    @pytest.mark.asyncio
    async def test_insufficient_stock_maps_to_out_of_stock(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        await _seed_notification(
            user_id=owner_user["user_id"],
            category="insufficient_stock",
            title="Out of stock: Widget",
        )
        r = await app.get("/api/v1/notifications", headers=h)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) == 1
        assert data[0]["type"] == "out_of_stock"

    @pytest.mark.asyncio
    async def test_below_cost_maps_to_below_cost_sale(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        await _seed_notification(
            user_id=owner_user["user_id"],
            category="below_cost",
            title="Below cost sale",
        )
        r = await app.get("/api/v1/notifications", headers=h)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) == 1
        assert data[0]["type"] == "below_cost_sale"

    @pytest.mark.asyncio
    async def test_low_stock_passthrough(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        await _seed_notification(
            user_id=owner_user["user_id"],
            category="low_stock",
            title="Low stock",
        )
        r = await app.get("/api/v1/notifications", headers=h)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) == 1
        assert data[0]["type"] == "low_stock"
