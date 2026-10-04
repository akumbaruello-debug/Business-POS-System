"""Keep JSON timestamps in the canonical UTC representation."""

from __future__ import annotations

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_UTC_Z_RE = re.compile(
    rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"
)


class ETagDatetimeNormalizationMiddleware:
    """Normalize Pydantic's UTC ``Z`` timestamps in JSON bodies.

    ETags use ``+00:00`` via :func:`app.util.iso_utc`. Pydantic's JSON
    serializer emits UTC datetimes with ``Z``, so normalize JSON bodies
    to keep response timestamps usable as If-Match tokens too.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_message: Message | None = None
        body_chunks: list[bytes] = []

        async def send_wrapper(message: Message) -> None:
            nonlocal start_message

            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                content_type = next(
                    (
                        value.lower()
                        for name, value in headers
                        if name == b"content-type"
                    ),
                    b"",
                )
                if b"application/json" in content_type:
                    start_message = message
                else:
                    await send(message)
                return

            if (
                message["type"] == "http.response.body"
                and start_message is not None
            ):
                body_chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return

                body = _UTC_Z_RE.sub(
                    lambda match: match.group(0)[:-1] + b"+00:00",
                    b"".join(body_chunks),
                )
                headers = [
                    (
                        name,
                        str(len(body)).encode("ascii")
                        if name == b"content-length"
                        else value,
                    )
                    for name, value in start_message.get("headers", [])
                ]
                start_message["headers"] = headers
                await send(start_message)
                final_message = dict(message)
                final_message["body"] = body
                final_message["more_body"] = False
                await send(final_message)
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)


__all__ = ["ETagDatetimeNormalizationMiddleware"]
