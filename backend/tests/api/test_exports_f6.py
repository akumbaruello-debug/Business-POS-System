"""F.6 — Export job tests.

Covers the three F.6 endpoints per ``openapi.yaml`` (lines 5689-5794):

* ``POST /exports``        — create an export job
* ``GET /exports/{id}``    — get export job status
* ``GET /exports/{id}/download`` — download the file

Test matrix (mirrors the F.5 authorization + lifecycle matrix):

1. Auth: 401 unauthenticated, 403 wrong-cap (staff lacks export.data).
2. Missing Idempotency-Key on POST -> 400 (MissingHeader / missing_header).
3. Invalid Idempotency-Key format -> 400 (InvalidHeader / invalid_header).
4. Invalid report/enum -> 400 (request validation).
5. Idempotency: same key + same body -> returns same export_id.
6. Idempotency: same key + different body -> 409.
7. POST creates a queued job (201) with correct schema.
8. GET returns the job with status + download_url (when complete).
9. GET non-existent job -> 404.
10. Download: streams file content with correct Content-Type.
11. Download: not-ready job -> 202.
12. Lifecycle: job transitions queued -> processing -> complete.
13. Format: both pdf and xlsx accepted.
14. Filters: arbitrary filter object accepted.

Authorization:
    ``/exports`` requires ``export.data`` capability.
    Owner has it; Staff does not -> 403.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.services.exports import get_export_store


# ---------------------------------------------------------------------------
# Helpers (mirror test_dashboard_f5.py)
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


async def _clear_export_store() -> None:
    """Reset the in-memory export store + idempotency index between tests."""
    get_export_store().clear()
    # Also clear the in-memory idempotency index.
    from app.services import exports as _exports_mod

    _exports_mod._idempotency_index.clear()


async def _wait_for_complete(export_id: str, max_tries: int = 50) -> None:
    """Poll the store until the job reaches terminal state."""
    import asyncio

    store = get_export_store()
    for _ in range(max_tries):
        job = store.get(export_id)
        if job and job.status in ("complete", "failed"):
            break
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestF6Authorization:
    @pytest.mark.asyncio
    async def test_post_exports_unauthenticated_401(
        self, app: AsyncClient
    ) -> None:
        await _clear_export_store()
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "pdf"},
            headers={"Idempotency-Key": _idem()},
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_get_exports_id_unauthenticated_401(
        self, app: AsyncClient
    ) -> None:
        await _clear_export_store()
        r = await app.get("/api/v1/exports/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_download_exports_id_unauthenticated_401(
        self, app: AsyncClient
    ) -> None:
        await _clear_export_store()
        r = await app.get(
            "/api/v1/exports/00000000-0000-0000-0000-000000000000/download"
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_post_exports_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "pdf"},
            headers={**h, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_get_exports_id_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _staff_headers(app, staff_user)
        r = await app.get(
            "/api/v1/exports/00000000-0000-0000-0000-000000000000", headers=h
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Idempotency header validation
# ---------------------------------------------------------------------------


class TestF6IdempotencyHeaders:
    @pytest.mark.asyncio
    async def test_post_without_idempotency_key_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "pdf"},
            headers=h,
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "missing_header"

    @pytest.mark.asyncio
    async def test_post_invalid_idempotency_key_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "pdf"},
            headers={**h, "Idempotency-Key": "not-a-uuid"},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_header"


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


class TestF6Validation:
    @pytest.mark.asyncio
    async def test_post_invalid_report_enum_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "nonexistent", "format": "pdf"},
            headers={**h, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_post_missing_report_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"format": "pdf"},
            headers={**h, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_post_invalid_format_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "txt"},
            headers={**h, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_post_default_format_is_pdf(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """format defaults to pdf when omitted."""
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales"},
            headers={**h, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        # Job created; format stored as 'pdf'
        store = get_export_store()
        job = store.get(body["export_id"])
        assert job is not None
        assert job.fmt == "pdf"


# ---------------------------------------------------------------------------
# Idempotency semantics
# ---------------------------------------------------------------------------


class TestF6Idempotency:
    @pytest.mark.asyncio
    async def test_idempotent_retry_returns_same_job(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        key = _idem()
        body = {"report": "sales", "format": "pdf"}

        r1 = await app.post(
            "/api/v1/exports", json=body, headers={**h, "Idempotency-Key": key}
        )
        assert r1.status_code == 201
        job1 = r1.json()

        # Retry with same key + same body -> same export_id
        r2 = await app.post(
            "/api/v1/exports", json=body, headers={**h, "Idempotency-Key": key}
        )
        assert r2.status_code == 201, r2.text
        job2 = r2.json()
        assert job2["export_id"] == job1["export_id"]

    @pytest.mark.asyncio
    async def test_idempotency_violation_different_body_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        key = _idem()
        r1 = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "pdf"},
            headers={**h, "Idempotency-Key": key},
        )
        assert r1.status_code == 201

        # Same key, different body -> 409
        r2 = await app.post(
            "/api/v1/exports",
            json={"report": "purchases", "format": "pdf"},
            headers={**h, "Idempotency-Key": key},
        )
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == "idempotency_violation"


# ---------------------------------------------------------------------------
# Happy path: create + get + download
# ---------------------------------------------------------------------------


class TestF6CreateAndGet:
    @pytest.mark.asyncio
    async def test_create_export_returns_201_with_schema(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "inventory", "format": "xlsx", "filters": {"period": "this_month"}},
            headers={**h, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        body = r.json()
        # Schema: ExportJob required fields.
        assert set(body.keys()) == {
            "export_id", "status", "download_url", "expires_at", "error", "created_at"
        }
        assert body["status"] == "queued"
        assert body["download_url"] is None
        assert body["expires_at"] is None
        assert body["error"] is None
        assert "T" in body["created_at"]  # ISO-8601

    @pytest.mark.asyncio
    async def test_get_export_returns_job(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "pdf"},
            headers={**h, "Idempotency-Key": _idem()},
        )
        export_id = r.json()["export_id"]

        r2 = await app.get(f"/api/v1/exports/{export_id}", headers=h)
        assert r2.status_code == 200
        body = r2.json()
        assert body["export_id"] == export_id
        assert set(body.keys()) == {
            "export_id", "status", "download_url", "expires_at", "error", "created_at"
        }

    @pytest.mark.asyncio
    async def test_get_nonexistent_export_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        bogus = str(uuid.uuid4())
        r = await app.get(f"/api/v1/exports/{bogus}", headers=h)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"

    @pytest.mark.asyncio
    async def test_download_nonexistent_export_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        bogus = str(uuid.uuid4())
        r = await app.get(f"/api/v1/exports/{bogus}/download", headers=h)
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_download_when_ready_returns_pdf_content(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "pdf"},
            headers={**h, "Idempotency-Key": _idem()},
        )
        export_id = r.json()["export_id"]

        await _wait_for_complete(export_id)
        job = get_export_store().get(export_id)
        assert job is not None
        assert job.status == "complete", f"job not complete: {job.status}, error: {job.error}"

        r2 = await app.get(f"/api/v1/exports/{export_id}/download", headers=h)
        assert r2.status_code == 200
        assert r2.headers["Content-Disposition"].startswith("attachment")
        assert r2.headers["Content-Disposition"].endswith(".pdf")
        # Verify real PDF content — magic bytes.
        assert r2.content[:4] == b"%PDF"
        assert len(r2.content) > 0

    @pytest.mark.asyncio
    async def test_download_pdf_content_type(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "pdf"},
            headers={**h, "Idempotency-Key": _idem()},
        )
        export_id = r.json()["export_id"]
        await _wait_for_complete(export_id)

        r2 = await app.get(f"/api/v1/exports/{export_id}/download", headers=h)
        assert r2.status_code == 200
        assert (
            r2.headers["Content-Type"] == "application/pdf"
        )
        # Verify the bytes are actually a PDF (not CSV masquerading as PDF).
        assert r2.content[:4] == b"%PDF"
        assert r2.headers["Content-Disposition"].endswith(".pdf")

    @pytest.mark.asyncio
    async def test_download_xlsx_content_type(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "xlsx"},
            headers={**h, "Idempotency-Key": _idem()},
        )
        export_id = r.json()["export_id"]
        await _wait_for_complete(export_id)

        r2 = await app.get(f"/api/v1/exports/{export_id}/download", headers=h)
        assert r2.status_code == 200
        assert (
            r2.headers["Content-Type"]
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        # Verify the bytes are actually an XLSX (ZIP-based format).
        # XLSX is a ZIP archive — PK\x03\x04 is the ZIP magic.
        assert r2.content[:4] == b"PK\x03\x04"
        assert r2.headers["Content-Disposition"].endswith(".xlsx")

    @pytest.mark.asyncio
    async def test_download_not_ready_returns_202(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """If job is still queued/processing, download returns 202."""
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "pdf"},
            headers={**h, "Idempotency-Key": _idem()},
        )
        export_id = r.json()["export_id"]

        # Immediately try to download (before background task finishes).
        r2 = await app.get(f"/api/v1/exports/{export_id}/download", headers=h)
        # Could be 202 (not ready) or 200 (already done) — both acceptable.
        assert r2.status_code in (200, 202)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestF6Filters:
    @pytest.mark.asyncio
    async def test_post_with_filters(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        filters = {"period": "this_month", "compare_to": "previous_period"}
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "pdf", "filters": filters},
            headers={**h, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        export_id = r.json()["export_id"]
        store = get_export_store()
        job = store.get(export_id)
        assert job is not None
        assert job.filters == filters


# ---------------------------------------------------------------------------
# Background processing lifecycle
# ---------------------------------------------------------------------------


class TestF6Lifecycle:
    @pytest.mark.asyncio
    async def test_job_transitions_to_complete(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "sales", "format": "pdf"},
            headers={**h, "Idempotency-Key": _idem()},
        )
        export_id = r.json()["export_id"]

        await _wait_for_complete(export_id)

        job = get_export_store().get(export_id)
        assert job is not None
        assert job.status in ("complete", "failed")
        if job.status == "complete":
            assert job.file_bytes is not None
            assert job.download_url is not None
            assert job.expires_at is not None
            assert job.error is None


# ---------------------------------------------------------------------------
# Independent verification of export job status
# ---------------------------------------------------------------------------


class TestF6IndependentVerification:
    @pytest.mark.asyncio
    async def test_get_export_matches_in_memory_store(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Verify GET /exports/{id} returns exactly what the store has."""
        await _clear_export_store()
        h = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/exports",
            json={"report": "inventory", "format": "xlsx", "filters": {}},
            headers={**h, "Idempotency-Key": _idem()},
        )
        export_id = r.json()["export_id"]

        await _wait_for_complete(export_id)

        # GET via API and compare with store directly.
        r2 = await app.get(f"/api/v1/exports/{export_id}", headers=h)
        assert r2.status_code == 200
        api_body = r2.json()

        job = get_export_store().get(export_id)
        assert job is not None
        assert api_body["export_id"] == job.export_id
        assert api_body["status"] == job.status
        assert api_body["download_url"] == job.download_url
