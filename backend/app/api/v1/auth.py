"""Auth API routes — matches ``openapi.yaml`` ``/auth/*`` exactly.

Endpoints (M1):
  * POST /auth/login        (operationId: loginUser)        — Idempotency-Key required
  * POST /auth/refresh      (operationId: refreshToken)
  * POST /auth/logout       (operationId: logoutSession)    — auth required
  * POST /auth/logout-all   (operationId: logoutAllSessions)— auth + user.manage
  * GET  /auth/me           (operationId: getCurrentUser)   — auth required

M1 implementation note
----------------------
``/auth/login`` is a pre-auth endpoint. Its idempotency record is only
written **after** the caller has been authenticated and the
``user_id`` is known — this keeps the ``fk_idem_user`` FK satisfied
without modifying the locked schema. See
:mod:`app.services.idempotency` for the storage contract.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel, ConfigDict, Field
from slowapi.util import get_remote_address

from app.auth.principal import Principal
from app.auth.service import AuthService
from app.authz.deps import current_principal, require_capability
from app.db import UnitOfWork
from app.errors import AppError, IdempotencyViolation
from app.logging import get_logger
from app.middleware.rate_limit import check_limit
from app.services.idempotency import (
    IdempotencyStore,
    IdempotencyViolationConflict,
    compute_request_fingerprint,
)
from app.util import client_ip
from app.validation.headers import parse_idempotency_key

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Request / response models (Pydantic)
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Matches ``openapi.yaml`` ``LoginRequest``."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    """Matches ``openapi.yaml`` ``RefreshRequest``."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str | None:
    return client_ip(request.scope.get("headers", []))


def _ua(request: Request) -> str | None:
    headers: Any = request.scope.get("headers", [])
    for name, value in headers:
        if name == b"user-agent":
            try:
                return str(value.decode("latin-1"))
            except UnicodeDecodeError:
                return None
    return None


def _login_response(result: Any) -> dict[str, Any]:
    """Render an AuthService LoginResult as the OpenAPI LoginResponse shape."""
    return {
        "access_token": result.access_token,
        "refresh_token": result.refresh_token,
        "expires_in": result.expires_in,
        "refresh_expires_in": result.refresh_expires_in,
        "user": result.principal.to_session_user(),
    }


def _idem_conflict(details: dict[str, Any]) -> AppError:
    return IdempotencyViolation(
        "Idempotency-Key reused with a different request body.",
        details=details,
    )


def _idem_in_flight() -> AppError:
    return IdempotencyViolation(
        "A request with this Idempotency-Key is in flight; retry shortly.",
        details={"status": "in_flight"},
    )


# ---------------------------------------------------------------------------
# /auth/login
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    operation_id="loginUser",
    summary="Authenticate user, issue access + refresh tokens. Required Idempotency-Key (30s TTL) prevents replay.",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "OK"},
        401: {"description": "Unauthorized"},
        409: {"description": "Conflict (idempotency_violation)"},
        423: {"description": "Locked"},
        429: {"description": "Too Many Requests"},
    },
)
async def login(
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Authenticate and issue tokens.

    The Idempotency-Key is required per the OpenAPI ``IdempotencyKeyRequired``
    parameter on this endpoint. Same key + same body replays the cached
    response; same key + different body → 409 ``idempotency_violation``.
    """
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()

    # Validate body shape (LoginRequest).
    parsed_body = LoginRequest.model_validate(body_dict)
    # IP rate limit: 5 per 15 minutes
    ip = get_remote_address(request)
    await check_limit("5/15minute", ip, request)
    # Login-user rate limit: 10 per hour
    await check_limit("10/hour", parsed_body.username, request)
    body = parsed_body.model_dump()

    # Idempotency fingerprint.
    fingerprint = compute_request_fingerprint(
        method="POST",
        path="/auth/login",
        body=body,
    )

    key_str = str(parsed_key.value)
    endpoint = "POST /auth/login"

    auth_service = AuthService()

    async with UnitOfWork() as uow:
        store = IdempotencyStore(uow)

        # 1. Pre-auth idempotency lookup (by key + endpoint only).
        try:
            existing = await store.start_anonymous(
                key=key_str,
                endpoint=endpoint,
                fingerprint=fingerprint,
                is_login=True,
            )
        except IdempotencyViolationConflict as exc:
            raise _idem_conflict(exc.details) from exc

        # Replay an already-completed login.
        if existing is not None and existing.is_completed:
            await uow.commit()
            return existing.response_body  # type: ignore[return-value]

        # A duplicate request is still in flight — reject so the client
        # retries once it has reason to believe the prior request
        # failed.
        if existing is not None and existing.in_flight:
            raise _idem_in_flight()

        # 2. Run the auth.
        try:
            result = await auth_service.login(
                username=parsed_body.username,
                password=parsed_body.password,
                ip_address=_client_ip(request),
                user_agent=_ua(request),
                uow=uow,
            )
        except Exception:
            # Don't persist anything for failed auth — the row goes
            # away with the rollback.
            await uow.rollback()
            raise

        response_body = _login_response(result)
        user_id = int(result.principal.user_id)

        # 3. Persist the response under the idempotency key (now with
        # the real user_id, which satisfies the fk_idem_user FK).
        await store.insert_completed(
            key=key_str,
            endpoint=endpoint,
            fingerprint=fingerprint,
            response_status=200,
            response_body=json.dumps(response_body),
            user_id=user_id,
        )
        await uow.commit()

    return response_body


# ---------------------------------------------------------------------------
# /auth/refresh
# ---------------------------------------------------------------------------


@router.post(
    "/refresh",
    operation_id="refreshToken",
    summary="Rotate access and refresh tokens. Old refresh is invalidated.",
    status_code=status.HTTP_200_OK,
)
async def refresh(request: Request) -> dict[str, Any]:
    """Rotate the (access, refresh) token pair."""
    body_dict = await request.json()
    parsed = RefreshRequest.model_validate(body_dict)
    auth_service = AuthService()
    async with UnitOfWork() as uow:
        result = await auth_service.refresh(
            refresh_token=parsed.refresh_token,
            ip_address=_client_ip(request),
            user_agent=_ua(request),
            uow=uow,
        )
        # Refresh rate limit: 60 per hour per user
        await check_limit("60/hour", f"user:{result.principal.user_id}", request)
        await uow.commit()
    return _login_response(result)


# ---------------------------------------------------------------------------
# /auth/logout
# ---------------------------------------------------------------------------


@router.post(
    "/logout",
    operation_id="logoutSession",
    summary="Revoke the current session.",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def logout(
    principal: Annotated[Principal, Depends(current_principal)],
) -> None:
    """Revoke the caller's current session."""
    if principal.session is None:
        return None
    auth_service = AuthService()
    async with UnitOfWork() as uow:
        await auth_service.logout_current(
            session_id=principal.session.id,
            uow=uow,
        )
        await uow.commit()
    return None


# ---------------------------------------------------------------------------
# /auth/logout-all
# ---------------------------------------------------------------------------


@router.post(
    "/logout-all",
    operation_id="logoutAllSessions",
    summary="Revoke all sessions for the current user.",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def logout_all(
    principal: Annotated[
        Principal,
        Depends(require_capability("user.manage")),
    ],
) -> None:
    """Revoke every non-revoked session for the caller (except the current one)."""
    auth_service = AuthService()
    if principal.session is None:
        return None
    async with UnitOfWork() as uow:
        await auth_service.logout_all(
            user_id=principal.user_id,
            except_session_id=principal.session.id,
            uow=uow,
        )
        await uow.commit()
    return None


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    operation_id="getCurrentUser",
    summary="Return current user with effective capabilities.",
    status_code=status.HTTP_200_OK,
)
async def me(
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    """Render the caller's effective session-user object."""
    return principal.to_session_user()


__all__ = ["router"]
