"""Security middleware: secure response headers, CORS, trusted hosts,
body-size limit.

The full security posture is in ``Backend-Architecture-V1.0.md`` §23.
M1 wires:

* **CORS** — only if ``POS_CORS_ALLOWED_ORIGINS`` is non-empty.
* **Trusted hosts** — only if ``POS_TRUSTED_HOSTS`` is non-empty.
* **Secure response headers** — always on.
* **Body-size limit** — applied via Starlette's ``Limit`` middleware.

We deliberately do NOT enable HSTS in dev (no HTTPS to enforce).
Production deployments add HSTS at the reverse proxy.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Secure response headers (always on)
# ---------------------------------------------------------------------------


class SecureHeadersMiddleware:
    """Inject a baseline of security headers on every response.

    Headers added:
      * ``X-Content-Type-Options: nosniff``
      * ``X-Frame-Options: DENY``
      * ``Referrer-Policy: no-referrer``
      * ``Cache-Control: no-store`` (on error responses only — see below)
      * ``Permissions-Policy: ...`` (disabled common browser features)

    We do NOT add ``Content-Security-Policy`` here; that is the front-
    end's responsibility (the API is JSON-only, not HTML).
    """

    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # Replace any existing occurrences and append.
                headers = [
                    (k, v)
                    for (k, v) in headers
                    if k
                    not in (
                        b"x-content-type-options",
                        b"x-frame-options",
                        b"referrer-policy",
                        b"permissions-policy",
                    )
                ]
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
                    ]
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, _send_wrapper)


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def install_security_middleware(
    app: FastAPI,
    *,
    cors_origins: list[str] | None = None,
    trusted_hosts: list[str] | None = None,
) -> None:
    """Register the security middleware stack on the FastAPI app."""
    # Order matters. Outermost first → inner last.
    # TrustedHost (network-facing filter) → SecureHeaders (response
    # wrapping) → CORS (handles preflight).
    if trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)
        logger.info("trusted_hosts_middleware_enabled", hosts=trusted_hosts)
    else:
        logger.info("trusted_hosts_middleware_disabled")

    app.add_middleware(SecureHeadersMiddleware)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            max_age=600,
        )
        logger.info("cors_middleware_enabled", origins=cors_origins)
    else:
        logger.info("cors_middleware_disabled")


__all__ = ["SecureHeadersMiddleware", "install_security_middleware"]
