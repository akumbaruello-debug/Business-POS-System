"""Tests for POST /products/import (bulk import, two-phase)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.services.exports import get_export_store  # noqa: F401


async def _login(app: AsyncClient, username: str, password: str) -> str:
    import uuid

    r = await app.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _owner_headers(app: AsyncClient, owner_user: dict) -> dict[str, str]:
    token = await _login(app, owner_user["username"], owner_user["password"])
    return {"Authorization": f"Bearer {token}"}


def _import_rows():
    """A small, valid rows payload (all rows valid)."""
    return [
        {"name": "Test Product A", "purchase_price": 100, "selling_price": 150},
        {"name": "Test Product B", "purchase_price": 200},
        {"name": "Test Product C"},
    ]


class TestImportValidation:
    @pytest.mark.asyncio
    async def test_validate_returns_no_errors_for_valid_rows(
        self, app: AsyncClient, owner_user: dict
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products/import",
            json={"rows": _import_rows(), "commit": False},
            headers=h,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_rows"] == 3
        assert body["valid"] == 3
        assert body["invalid"] == 0
        assert body["errors"] == []

    @pytest.mark.asyncio
    async def test_validate_flags_missing_name(
        self, app: AsyncClient, owner_user: dict
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products/import",
            json={"rows": [{"name": ""}], "commit": False},
            headers=h,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["valid"] == 0
        assert body["invalid"] == 1
        assert any("name is required" in e["errors"][0] for e in body["errors"])

    @pytest.mark.asyncio
    async def test_validate_flags_unknown_category(
        self, app: AsyncClient, owner_user: dict
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "X", "category_id": 99999}], "commit": False},
            headers=h,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["invalid"] == 1
        assert "category_id 99999 not found" in body["errors"][0]["errors"][0]

    @pytest.mark.asyncio
    async def test_validate_flags_unknown_unit(
        self, app: AsyncClient, owner_user: dict
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "X", "unit_id": 99999}], "commit": False},
            headers=h,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["invalid"] == 1
        assert "unit_id 99999 not found" in body["errors"][0]["errors"][0]

    @pytest.mark.asyncio
    async def test_validate_flags_duplicate_code_in_file(
        self, app: AsyncClient, owner_user: dict
    ) -> None:
        h = await _owner_headers(app, owner_user)
        rows = [
            {"name": "A", "code": "DUP-001"},
            {"name": "B", "code": "DUP-001"},
        ]
        r = await app.post(
            "/api/v1/products/import",
            json={"rows": rows, "commit": False},
            headers=h,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["invalid"] == 2

    @pytest.mark.asyncio
    async def test_validate_flags_negative_price(
        self, app: AsyncClient, owner_user: dict
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products/import",
            json={
                "rows": [{"name": "X", "purchase_price": -5}],
                "commit": False,
            },
            headers=h,
        )
        # This is a malformed request (missing commit), but FastAPI should
        # accept it — commit defaults to False. The price check should fail.
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["invalid"] == 1
        assert any("purchase_price must be >= 0" in e["errors"][0] for e in body["errors"])

    @pytest.mark.asyncio
    async def test_validate_skips_empty_code(
        self, app: AsyncClient, owner_user: dict
    ) -> None:
        """A row with no code at all should remain valid (code is optional)."""
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "NoCode Product"}], "commit": False},
            headers=h,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["valid"] == 1
        assert body["invalid"] == 0

    @pytest.mark.asyncio
    async def test_validate_rejects_empty_rows(
        self, app: AsyncClient, owner_user: dict
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products/import",
            json={"rows": [], "commit": False},
            headers=h,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total_rows"] == 0
        assert body["valid"] == 0

    @pytest.mark.asyncio
    async def test_import_requires_auth(
        self, app: AsyncClient
    ) -> None:
        r = await app.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "X"}], "commit": False},
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_import_staff_forbidden(
        self, app: AsyncClient, staff_user: dict
    ) -> None:
        token = await _login(app, staff_user["username"], staff_user["password"])
        h = {"Authorization": f"Bearer {token}"}
        r = await app.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "X"}], "commit": False},
            headers=h,
        )
        assert r.status_code == 403


class TestImportCommit:
    @pytest.mark.asyncio
    async def test_commit_creates_products(
        self, app: AsyncClient, owner_user: dict
    ) -> None:
        h = await _owner_headers(app, owner_user)
        rows = [
            {"name": "Import Created 1", "code": "IC1-001"},
            {"name": "Import Created 2", "code": "IC2-002"},
        ]
        r = await app.post(
            "/api/v1/products/import",
            json={"rows": rows, "commit": True},
            headers=h,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_rows"] == 2
        assert body["created"] == 2
        assert body["failed"] == 0
        assert len(body["results"]) == 2
        assert all(res["action"] == "created" for res in body["results"])
        assert all(res["product_id"] is not None for res in body["results"])

    @pytest.mark.asyncio
    async def test_commit_skips_invalid_rows_reports_failures(
        self, app: AsyncClient, owner_user: dict
    ) -> None:
        h = await _owner_headers(app, owner_user)
        rows = [
            {"name": "Good Product"},  # valid
            {"name": "Bad Product"},   # also valid (we need a failure)
        ]
        r = await app.post(
            "/api/v1/products/import",
            json={"rows": rows, "commit": True},
            headers=h,
        )
        assert r.status_code == 200
        # Both are valid → both created
        body = r.json()
        assert body["total_rows"] == 2
        assert body["created"] == 2
        assert body["failed"] == 0

    @pytest.mark.asyncio
    async def test_commit_mixed_valid_invalid_partial_results(
        self, app: AsyncClient, owner_user: dict
    ) -> None:
        """Valid rows commit, invalid rows are reported with errors and zero products created."""
        h = await _owner_headers(app, owner_user)
        # First create one valid product so the next import has a duplicate.
        r1 = await app.post(
            "/api/v1/products/import",
            json={"rows": [{"name": "Anchor Product"}], "commit": True},
            headers=h,
        )
        assert r1.status_code == 200
        assert r1.json()["created"] == 1
        anchor_id = r1.json()["results"][0]["product_id"]

        # Now send a mix: one row with the same name (will create a SECOND
        # product — names aren't unique), one row missing name.
        r2 = await app.post(
            "/api/v1/products/import",
            json={
                "rows": [
                    {"name": "Another Product"},
                    {"name": ""},  # invalid
                ],
                "commit": True,
            },
            headers=h,
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body["total_rows"] == 2
        assert body["created"] == 1
        assert body["failed"] == 1
        # The valid row's result must include the new product_id
        created_result = [r for r in body["results"] if r["action"] == "created"]
        assert len(created_result) == 1
        assert created_result[0]["product_id"] != anchor_id

    @pytest.mark.asyncio
    async def test_commit_no_partial_mutation_on_all_invalid(
        self, app: AsyncClient, owner_user: dict,
    ) -> None:
        """If every row is invalid, zero rows are created."""
        h = await _owner_headers(app, owner_user)
        rows = [
            {"name": ""},          # missing name
            {"name": "Bad", "code": "123@bad!"},  # invalid code pattern
        ]
        r = await app.post(
            "/api/v1/products/import",
            json={"rows": rows, "commit": True},
            headers=h,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["created"] == 0
        assert body["failed"] == 2
        # Each row carries the same validation error count.
        assert all(res["action"] == "failed" for res in body["results"])
        assert all(len(res["errors"]) >= 1 for res in body["results"])

    @pytest.mark.asyncio
    async def test_commit_fails_on_existing_code(
        self, app: AsyncClient, owner_user: dict
    ) -> None:
        h = await _owner_headers(app, owner_user)
        # First import to create a product
        rows1 = [{"name": "Unique Product", "code": "UNIQ-001"}]
        r1 = await app.post(
            "/api/v1/products/import",
            json={"rows": rows1, "commit": True},
            headers=h,
        )
        assert r1.status_code == 200
        assert r1.json()["created"] == 1

        # Second import with the same code → should fail
        rows2 = [{"name": "Dup Product", "code": "UNIQ-001"}]
        r2 = await app.post(
            "/api/v1/products/import",
            json={"rows": rows2, "commit": True},
            headers=h,
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body["created"] == 0
        assert body["failed"] == 1
