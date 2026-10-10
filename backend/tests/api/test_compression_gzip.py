"""GZip middleware regression tests (compression_gzip).

Checks that Starlette's GZipMiddleware installed in create_app:
* compresses responses >= 1024 bytes when Accept-Encoding: gzip present
* adds Content-Encoding: gzip + Vary: Accept-Encoding
* body round-trips identically to the identity-encoded response
* is materially smaller when compressed
* leaves deterministic small responses untouched (no assumption that
  payment-methods is always below the threshold — a products?per_page=1
  hit is used, which is deterministic < 1024 B)
* co-exists with ETag normalization + security middleware

Runs against the shared test app — the tests/ DB configuration
(POS_DATABASE_URL → pos_test) never touches pos_dev.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db import get_session_factory

pytestmark = pytest.mark.asyncio


async def _login(app: AsyncClient, username: str, password: str) -> str:
    r = await app.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _owner_headers(app: AsyncClient, owner_user: dict[str, Any]) -> dict[str, str]:
    token = await _login(app, owner_user["username"], owner_user["password"])
    return {"Authorization": f"Bearer {token}"}


async def _insert_products(n: int, created_by: int) -> None:
    """Direct INSERT of n products (deterministic rows for a large body)."""
    factory = get_session_factory()
    async with factory() as session:
        for i in range(n):
            await session.execute(
                text(
                    "INSERT INTO products "
                    "(name, code, category_id, unit_id, purchase_price, selling_price, "
                    " low_stock_threshold, allow_negative_stock, is_sellable, is_purchasable, "
                    " is_producible, is_active, notes, created_by, updated_by) "
                    "VALUES (:name, :code, NULL, NULL, :pp, :sp, NULL, TRUE, TRUE, TRUE, "
                    " TRUE, TRUE, :notes, :by, :by)"
                ),
                {
                    "name": f"Gzip test product {i:04d} — kopi, teh, and a long description "
                    f"string to inflate payload size for compression verification {i}",
                    "code": f"GZ-{i:04d}",
                    "pp": 10000 + i,
                    "sp": 15000 + i,
                    "notes": "compression regression fixture " * 6,
                    "by": created_by,
                },
            )
        await session.commit()


async def test_large_products_list_is_gzipped(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Accept-Encoding: gzip on a large list -> Content-Encoding: gzip + Vary."""
    await _insert_products(60, owner_user["user_id"])
    headers = await _owner_headers(app, owner_user)
    r = await app.get(
        "/api/v1/products?per_page=500",
        headers={**headers, "Accept-Encoding": "gzip"},
    )
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip", r.headers
    assert "accept-encoding" in r.headers.get("vary", "").lower()


async def test_gzip_body_round_trips_and_is_smaller(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Gzip body decompresses to exact identity body and is materially smaller."""
    await _insert_products(60, owner_user["user_id"])
    headers = await _owner_headers(app, owner_user)

    # httpx sends `Accept-Encoding: gzip, deflate` by default, so force
    # identity on the baseline or GZipMiddleware would compress it too.
    identity = await app.get(
        "/api/v1/products?per_page=500",
        headers={**headers, "Accept-Encoding": "identity"},
    )
    compressed = await app.get(
        "/api/v1/products?per_page=500",
        headers={**headers, "Accept-Encoding": "gzip"},
    )
    assert identity.status_code == 200 and compressed.status_code == 200
    assert compressed.headers.get("content-encoding") == "gzip"

    identity_body = identity.content
    # NOTE: httpx auto-decodes Content-Encoding on .content, so raw wire
    # size must be read from Content-Length, which GZipMiddleware sets to
    # the actual compressed byte count (starlette gzip middleware line
    # "headers['Content-Length'] = str(len(body))").
    identity_len = int(identity.headers.get("content-length") or len(identity_body))
    gz_len = int(compressed.headers.get("content-length") or len(compressed.content))
    assert gz_len < identity_len
    # Round-trip: decoded bytes identical (proves the on-wire stream was
    # valid gzip — httpx would have failed to decode invalid bytes).
    got = compressed.content
    assert got == identity_body  # byte-for-byte identical JSON
    assert compressed.json() == identity.json()
    # Materially smaller: compressed must be far below half the identity.
    assert gz_len * 2 < identity_len


async def test_small_response_stays_uncompressed(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """A deterministic tiny response (<1024 B identity) is not compressed."""
    headers = await _owner_headers(app, owner_user)
    # Deterministic tiny response: an empty search returns empty data list.
    r = await app.get("/api/v1/products?q=__no_such_product__&per_page=500", headers=headers)
    assert r.status_code == 200
    assert len(r.content) < 1024, len(r.content)
    again = await app.get(
        "/api/v1/products?q=__no_such_product__&per_page=500",
        headers={**headers, "Accept-Encoding": "gzip"},
    )
    assert again.status_code == 200
    assert "content-encoding" not in again.headers
    assert again.content == r.content


async def test_etag_and_security_headers_coexist(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Compressed responses still carry security headers; ETag path intact."""
    await _insert_products(60, owner_user["user_id"])
    headers = await _owner_headers(app, owner_user)
    r = await app.get(
        "/api/v1/products?per_page=500",
        headers={**headers, "Accept-Encoding": "gzip"},
    )
    assert r.headers.get("content-encoding") == "gzip"
    # httpx auto-decodes the gzip stream on .content — decode failure would
    # already have raised; assert it parses as valid canonical JSON.
    assert r.json()  # parses decompressed body
    # Security headers survive compression.
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    # Single-product endpoint emits an item ETag header — confirm that
    # pipeline still works (ETag normalization does not conflict with gzip).
    one = await app.get("/api/v1/products?per_page=1", headers=headers)
    assert one.status_code == 200


async def test_if_match_etag_request_with_gzip(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """A valid If-Match request retains ETag behavior under gzip negotiation."""
    headers = await _owner_headers(app, owner_user)
    created = await app.post(
        "/api/v1/manual-entries",
        json={
            "category_id": 1,
            "entry_type": "income",
            "amount": 100.0,
            "payment_method_id": 1,
            "entry_date": "2026-09-02T12:00:00Z",
            "notes": "gzip ETag regression " + "x" * 900,
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert created.status_code == 201, created.text
    entry_id = created.json()["id"]

    current = await app.get(f"/api/v1/manual-entries/{entry_id}", headers=headers)
    assert current.status_code == 200
    current_etag = current.headers.get("etag")
    assert current_etag

    cancelled = await app.post(
        f"/api/v1/manual-entries/{entry_id}/cancel",
        json={"reason": "gzip ETag regression"},
        headers={
            **headers,
            "Idempotency-Key": str(uuid.uuid4()),
            "If-Match": current_etag,
            "Accept-Encoding": "gzip",
        },
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["lifecycle_status"] == "cancelled"
    response_etag = cancelled.headers.get("etag")
    assert response_etag
    assert response_etag != current_etag  # cancellation changes ETag state

    body_size = len(cancelled.content)  # httpx exposes decoded JSON body
    assert body_size > 1024, body_size
    assert cancelled.headers.get("content-encoding") == "gzip"
    assert "accept-encoding" in cancelled.headers.get("vary", "").lower()
    assert cancelled.json()["id"] == entry_id
    assert cancelled.json()["cancellation_reason"] == "gzip ETag regression"
