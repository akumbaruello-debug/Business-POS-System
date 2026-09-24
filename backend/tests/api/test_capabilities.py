"""Capabilities API tests.

Covers ``GET /api/v1/capabilities`` (Phase 1, read-only):

* Authorized request returns paginated capability list.
* All 53 canonical capabilities are present.
* ``domain``, ``default_owner``, ``default_staff`` match the catalog.
* ``q`` search works.
* Pagination works.
* Unauthorized/unauthenticated requests get 401/403.
* Response shape does not leak internal implementation details.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from httpx import AsyncClient

from app.authz.caps import (
    _CANONICAL,
    _STAFF_DEFAULT_CODES,
    CANONICAL_CAPABILITIES,
    CapabilityCategory,
)
from app.errors.codes import ErrorCode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _owner_token(app: AsyncClient, owner_user: dict[str, Any]) -> str:
    """Log in as owner and return the access token."""
    r = await app.post(
        "/api/v1/auth/login",
        json={"username": "owner", "password": owner_user["password"]},
        headers={"Idempotency-Key": str(_uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _staff_token(app: AsyncClient, staff_user: dict[str, Any]) -> str:
    """Log in as staff and return the access token."""
    r = await app.post(
        "/api/v1/auth/login",
        json={"username": "staff", "password": staff_user["password"]},
        headers={"Idempotency-Key": str(_uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListCapabilities:
    """GET /api/v1/capabilities tests."""

    async def test_authorized_returns_paginated_list(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Owner can list capabilities; response has ``data`` + ``pagination``."""
        token = await _owner_token(app, owner_user)
        r = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "data" in body
        assert "pagination" in body
        assert isinstance(body["data"], list)
        assert body["pagination"]["page"] == 1

    async def test_all_canonical_capabilities_present(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Every canonical capability code must appear in the response."""
        token = await _owner_token(app, owner_user)
        # Fetch all by using a high per_page
        r = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 200},
        )
        assert r.status_code == 200, r.text
        codes = {item["code"] for item in r.json()["data"]}
        assert codes == set(CANONICAL_CAPABILITIES)

    async def test_domain_matches_catalog(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Each capability's ``domain`` matches the catalog category."""
        token = await _owner_token(app, owner_user)
        r = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 200},
        )
        assert r.status_code == 200, r.text
        for item in r.json()["data"]:
            cat, _desc = _CANONICAL[item["code"]]
            expected_domain = cat.value if isinstance(cat, CapabilityCategory) else str(cat)
            assert item["domain"] == expected_domain, (
                f"{item['code']}: got domain={item['domain']}, expected={expected_domain}"
            )

    async def test_default_owner_always_true(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """``default_owner`` is True for every capability (Owner gets all)."""
        token = await _owner_token(app, owner_user)
        r = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 200},
        )
        assert r.status_code == 200, r.text
        for item in r.json()["data"]:
            assert item["default_owner"] is True, item["code"]

    async def test_default_staff_matches_catalog(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """``default_staff`` is True only for Staff default capabilities."""
        token = await _owner_token(app, owner_user)
        r = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 200},
        )
        assert r.status_code == 200, r.text
        expected_staff = set(_STAFF_DEFAULT_CODES)
        for item in r.json()["data"]:
            expected = item["code"] in expected_staff
            assert item["default_staff"] == expected, (
                f"{item['code']}: got default_staff={item['default_staff']}, "
                f"expected={expected}"
            )

    async def test_q_filter_works(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """``q`` filters by case-insensitive substring on code or description."""
        token = await _owner_token(app, owner_user)
        r = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": "sale", "per_page": 200},
        )
        assert r.status_code == 200, r.text
        items = r.json()["data"]
        assert len(items) > 0
        # "sale" appears in codes like sale.view, sale.create, etc.
        for item in items:
            assert "sale" in item["code"] or (
                item["description"] is not None and "sale" in item["description"].lower()
            ), item

    async def test_q_filter_case_insensitive(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Search query is case-insensitive."""
        token = await _owner_token(app, owner_user)
        r_upper = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": "SALE", "per_page": 200},
        )
        r_lower = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": "sale", "per_page": 200},
        )
        assert r_upper.status_code == 200
        assert r_lower.status_code == 200
        assert len(r_upper.json()["data"]) == len(r_lower.json()["data"])

    async def test_pagination_works(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Pagination respects page and per_page."""
        token = await _owner_token(app, owner_user)
        # Get page 1, per_page=10
        r1 = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
            params={"page": 1, "per_page": 10},
        )
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        assert len(body1["data"]) == 10
        assert body1["pagination"]["total"] > 50
        assert body1["pagination"]["per_page"] == 10
        total_pages = body1["pagination"]["total_pages"]
        assert total_pages > 1

        # Get last page with remaining items
        r_last = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
            params={"page": total_pages, "per_page": 10},
        )
        assert r_last.status_code == 200, r_last.text
        body_last = r_last.json()
        assert len(body_last["data"]) > 0

        # No overlap between page 1 and last page
        page1_codes = {i["code"] for i in body1["data"]}
        page_last_codes = {i["code"] for i in body_last["data"]}
        assert page1_codes.isdisjoint(page_last_codes)

    async def test_unauthenticated_returns_401(
        self, app: AsyncClient
    ) -> None:
        """Request without token gets 401."""
        r = await app.get("/api/v1/capabilities")
        assert r.status_code == 401, r.text
        err = r.json()["error"]
        assert err["code"] == ErrorCode.UNAUTHENTICATED.value

    async def test_staff_without_user_view_gets_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        """Staff user without user.view cap gets 403 permission_denied.

        NOTE: Staff default caps DO include auth.login but NOT user.view.
        """
        token = await _staff_token(app, staff_user)
        r = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403, r.text
        err = r.json()["error"]
        assert err["code"] == ErrorCode.PERMISSION_DENIED.value

    async def test_response_does_not_expose_password_hash(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Response body must not contain sensitive internal fields."""
        token = await _owner_token(app, owner_user)
        r = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        text = r.text
        # Capability response should never expose user/session internals.
        for field in ("password_hash", "access_token", "session"):
            assert field not in text.lower(), f"response leaks '{field}'"

    async def test_response_fields_exact(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Each item exposes exactly the contract fields and nothing else."""
        token = await _owner_token(app, owner_user)
        r = await app.get(
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 1},
        )
        assert r.status_code == 200, r.text
        item = r.json()["data"][0]
        expected_keys = {"id", "code", "domain", "description", "default_owner", "default_staff"}
        assert set(item.keys()) == expected_keys
        assert isinstance(item["id"], int)
        assert isinstance(item["code"], str)
        assert isinstance(item["domain"], str)
        assert isinstance(item["default_owner"], bool)
        assert isinstance(item["default_staff"], bool)
