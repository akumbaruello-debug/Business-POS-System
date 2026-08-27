import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.middleware.rate_limit import _on_rate_limit

pytestmark = pytest.mark.asyncio


class TestIt:
    async def test_it(self) -> None:
        app = FastAPI()
        limiter = Limiter(key_func=get_remote_address, default_limits=["1/minute"])
        app.state.limiter = limiter
        app.add_middleware(SlowAPIMiddleware)
        app.exception_handler(RateLimitExceeded)(_on_rate_limit)

        @app.get("/limited")
        @limiter.limit("1/minute")
        async def limited(request: Request) -> dict[str, bool]:
            return {"ok": True}

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            r1 = await c.get("/limited")
            print(f"First: {r1.status_code} {r1.text}")
            assert r1.status_code == 200, r1.text
            r2 = await c.get("/limited")
            print(f"Second: {r2.status_code} {r2.text}")
            assert r2.status_code == 429
            body = r2.json()
            assert body["error"]["code"] == "rate_limited"
