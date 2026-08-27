"""FastAPI application factory.

This module wires the entire M1 stack:

1. Settings (pydantic-settings) → environment configuration.
2. Logging (structlog) → request-correlated JSON logs.
3. Error handlers → canonical ``ErrorEnvelope`` for every 4xx/5xx.
4. Security middleware → trusted hosts, secure headers, CORS.
5. Rate limiting (slowapi).
6. Request-id middleware → ``X-Request-ID`` echo + contextvar.
7. DB engine + session factory (initialised by lifespan).
8. v1 API router + health probes.

Per ``Backend-Architecture-V1.0.md`` §5 API Layer.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.api.v1.router import router as v1_router
from app.config import Settings, get_settings
from app.errors.handlers import register_error_handlers
from app.lifespan import lifespan
from app.logging import configure_logging, get_logger
from app.middleware.rate_limit import init_rate_limit
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.security import install_security_middleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI app instance.

    Args:
        settings: Optional override. If omitted, loads from env via
            ``get_settings()``.

    Returns:
        The fully-configured FastAPI app.
    """
    if settings is None:
        settings = get_settings()

    # 1. Configure logging FIRST so subsequent events are captured.
    configure_logging(log_level=settings.log_level, log_format=settings.log_format)

    logger = get_logger("app")
    logger.info(
        "creating_app",
        env=settings.env,
        debug=settings.debug,
    )

    # 2. Create the FastAPI app with OpenAPI metadata.
    app = FastAPI(
        title="Business POS API",
        version="0.1.0",
        description=(
            "Business Management & POS System — backend API. "
            "Phase M1 foundation (auth, errors, health, logging). "
            "See Backend-Architecture-V1.0.md."
        ),
        lifespan=lifespan,
        # M1: we do not auto-generate an OpenAPI document because the
        # canonical one lives in /openapi.yaml. M2+ will flip this
        # to defer to the OpenAPI spec file via FastAPI's
        # ``openapi()`` override.
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        openapi_url="/openapi.json" if settings.debug else None,
    )

    # 3. Register exception handlers (must come BEFORE middleware that
    # might catch them first).
    register_error_handlers(app)

    # 4. Install security middleware (outermost).
    install_security_middleware(
        app,
        cors_origins=settings.cors_origins_list or None,
        trusted_hosts=settings.trusted_hosts_list or None,
    )

    # 5. Rate limiting (slowapi).
    init_rate_limit(app)

    # 6. Request-id middleware (innermost on top of routes, outermost
    # after security — it must be outside the v1 router so even error
    # responses get a request id).
    app.add_middleware(RequestIDMiddleware)

    # 7. Mount the v1 router.
    app.include_router(v1_router, prefix="/api/v1")

    # 7b. Mount health probes at the root (k8s / load balancer convention).
    from app.health import router as health_router

    app.include_router(health_router)

    # 8. Top-level "service info" route (no auth) — useful for
    #    debugging environments.
    @app.get("/", tags=["meta"], include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "name": "Business POS API",
            "version": "0.1.0",
            "phase": "M1",
        }

    logger.info("app_created", env=settings.env)
    return app


# Module-level default app for `uvicorn app.main:app` convenience.
app = create_app()

__all__ = ["app", "create_app"]
