"""Prometheus metrics middleware.

Records HTTP request duration and response status into Prometheus counters/histograms.
Exposes the /metrics endpoint via app/metrics_routes.py (registered in main.py when enabled).

Per Backend-Architecture-V1.0.md §24.2 and G.4 plan tasks.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, Request, Response
from prometheus_client import generate_latest


async def metrics_endpoint() -> Response:
    """Return the metrics collection in Prometheus exposition format."""
    return Response(content=generate_latest(), media_type="text/plain; version=0.0.4")


class MetricsMiddleware:
    """ASGI middleware that records HTTP request metrics to Prometheus."""

    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        method = request.method
        route_path = scope.get("route", {}).get("path") or request.url.path

        start_time = time.time()

        # Wrap send to capture status
        sent_headers: list[tuple[bytes, bytes]] = []

        async def _send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name.lower(), value) for name, value in message.get("headers", [])
                ]
                sent_headers.extend(headers)
                await send(message)

            elif message["type"] == "http.response.body":
                await send(message)

        await self.app(scope, receive, _send_wrapper)

        end_time = time.time()
        duration_s = end_time - start_time

        # Determine status code from sent headers
        status_str = ""
        for name, value in sent_headers:
            if name == b"status":
                status_str = str(value)
                break

        # Use empty string if not found
        if not status_str:
            # Fallback: try extracting from the message if we can't find header
            # In case status header was missed, default to unknown
            status_str = "unknown"

        labels_method_status = {
            "method": method,
            "path_template": route_path,
            "status": status_str,
        }
        labels_method = {"method": method, "path_template": route_path}

        HTTP_REQUESTS_TOTAL.labels(**labels_method_status).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(**labels_method).observe(duration_s)


def install_metrics_middleware(app: FastAPI) -> None:
    """Register MetricsMiddleware on the FastAPI app."""
    app.add_middleware(MetricsMiddleware)


__all__ = ["metrics_endpoint", "MetricsMiddleware", "install_metrics_middleware"]
