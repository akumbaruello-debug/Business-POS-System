"""Origin header validation for state-changing requests.

Checks ``Origin`` on POST/PUT/PATCH/DELETE against the configured CORS
allow-list. Rejects missing or unlisted origins with 403
``origin_not_allowed``.

Preflight OPTIONS and safe methods (GET/HEAD) are skipped because
browsers do not send ``Origin`` for same-origin safe requests and the
CORS middleware already handles cross-origin preflight.
"""

from __future__ import annotations

from typing import Any

from app.core.ids import new_request_id
from app.errors import OriginNotAllowed
from app.middleware.request_id import current_request_id


class OriginCheckMiddleware:
    """ASGI middleware that validates Origin on mutating HTTP requests."""

    def __init__(self, app, allowed_origins: list[str]) -> None:  # type: ignore[no-untyped-def]
        self.app = app
        self.allowed = set(allowed_origins)

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        if method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        origin = None
        for key, value in headers:
            if key.lower() == b"origin":
                origin = value.decode("utf-8") if isinstance(value, bytes) else value
                break

        if not origin or origin not in self.allowed:
            # Build canonical envelope with request_id so client sees consistent shape.
            rid = current_request_id.get() or new_request_id()
            body = OriginNotAllowed("Origin not allowed.").to_envelope(rid)
            await self._send_json(send, status=403, body=body)
            return

        await self.app(scope, receive, send)

    async def _send_json(
        self,
        send: Any,
        *,
        status: int,
        body: dict[str, Any],
    ) -> None:
        import json

        content = json.dumps(body).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(content)).encode()),
                ],
            },
        )
        await send({"type": "http.response.body", "body": content})


__all__ = ["OriginCheckMiddleware"]
