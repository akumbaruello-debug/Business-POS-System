"""API routes for Users (Phase 1.4).

Per OpenAPI §Users — 10 endpoints.

Follows the sales/settings route patterns for idempotency + ETag.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import current_principal, get_uow, require_capability
from app.auth.principal import Principal
from app.concurrency.etag import parse_if_match_optional
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.db import UnitOfWork
from app.errors import IdempotencyViolation, MissingHeader, PermissionDenied
from app.services.idempotency import (
    IdempotencyRecord,
    IdempotencyStore,
    IdempotencyViolationConflict,
    compute_request_fingerprint,
)
from app.services.users import UserService
from app.util import client_ip
from app.validation.headers import parse_idempotency_key
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.users_schemas import (
    PasswordResetRequest,
    UserCapabilityOverrideRequest,
    UserCreateRequest,
    UserEffectiveCapabilities,
    UserResponse,
    UserUpdateRequest,
)

RequireView = require_capability("user.view")
RequireManage = require_capability("user.manage")
RequireGrantCap = require_capability("user.grant_capability")
RequireRevokeCap = require_capability("user.revoke_capability")


async def _require_password_reset_others(
    principal: Principal = Depends(current_principal),
) -> Principal:
    """Reset-password requires user.manage OR auth.password_reset_others."""
    if not principal.has_any("user.manage", "auth.password_reset_others"):
        raise PermissionDenied(
            "User lacks required capability.",
            details={"required": ["user.manage", "auth.password_reset_others"]},
        )
    return principal


logger = __import__("logging").getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])


def _audit_ctx(request: Request, principal: Principal) -> Any:
    from app.audit.service import AuditContext
    return AuditContext(
        user_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request.scope.get("headers", [])),
    )


def _to_response(row: dict[str, Any], etag: str, version: int) -> UserResponse:
    return UserResponse(
        id=int(row["id"]), username=str(row["username"]),
        full_name=str(row["full_name"]), email=row.get("email"),
        is_active=bool(row["is_active"]), role_id=int(row["role_id"]),
        role_name=str(row["role_name"]), last_login_at=row.get("last_login_at"),
        created_at=row["created_at"], updated_at=row["updated_at"],
        version=version,
    )


def _etag_from_row(row: dict[str, Any]) -> tuple[str, int]:
    return etag_and_version_from_updated_at(row["updated_at"])


def _idem_exc(exc: IdempotencyViolationConflict) -> IdempotencyViolation:
    return IdempotencyViolation(
        "Idempotency-Key has been used with a different request body.",
        details=exc.details,
    )


def _json_response(resp_dict: dict[str, Any], status_code: int = 200, replay: bool = False) -> JSONResponse:
    headers: dict[str, str] = {}
    if replay:
        headers["Idempotent-Replay"] = "true"
    return JSONResponse(status_code=status_code, content=resp_dict, headers=headers)


async def _idem_lookup(
    store: IdempotencyStore, key: str, user_id: int, endpoint: str, fingerprint: str,
) -> IdempotencyRecord | None:
    """Lookup or insert pending idempotency record. Returns existing.completed for replay."""
    try:
        return await store.start(key=key, user_id=user_id, endpoint=endpoint, fingerprint=fingerprint)
    except IdempotencyViolationConflict as exc:
        raise _idem_exc(exc) from exc


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("", operation_id="listUsers", status_code=status.HTTP_200_OK,
            dependencies=[Depends(RequireView)])
async def list_users(
    request: Request, uow: UnitOfWork = Depends(get_uow),
    page: int = Query(1, ge=1), per_page: int = Query(50, ge=1, le=500),
    q: str | None = Query(default=None, max_length=200),
    filter_is_active: bool | None = Query(default=None, alias="filter[is_active]"),
    filter_role_id: int | None = Query(default=None, alias="filter[role_id]"),
    sort: str = Query(default="id"),
) -> MetaEnvelope[UserResponse]:
    svc = UserService(uow)
    rows, total, _ = await svc.list(
        page=page, per_page=per_page, q=q,
        active_only=filter_is_active if filter_is_active is not None else None,
        role_id=filter_role_id, sort=sort,
    )
    data = [_to_response(r, *_etag_from_row(r)) for r in rows]
    return MetaEnvelope[UserResponse](data=data, pagination=make_pagination(page, per_page, total))


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("", operation_id="createUser", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(RequireManage)])
async def create_user(
    request: Request,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    idem = parse_idempotency_key(idempotency_key_header)
    body = await request.json()
    payload = UserCreateRequest(**body)
    endpoint = "POST /users"
    fingerprint = compute_request_fingerprint(method="POST", path="/users", body=body)

    store = IdempotencyStore(uow)
    existing = await _idem_lookup(store, str(idem.value), principal.user_id, endpoint, fingerprint)

    if existing is not None and existing.is_completed:
        resp_body = existing.response_body
        if isinstance(resp_body, str):
            resp_body = json.loads(resp_body)
        return _json_response(resp_body, status_code=201, replay=True)

    svc = UserService(uow)
    row = await svc.create(
        username=payload.username, full_name=payload.full_name,
        email=payload.email, password=payload.password,
        role_id=payload.role_id, created_by=principal.user_id,
        ctx=_audit_ctx(request, principal),
    )
    etag, version = _etag_from_row(row)
    resp = _to_response(row, etag, version).model_dump(mode="json")

    if existing is not None:
        await store.complete(record_id=existing.id, status=201, body=resp, user_id=principal.user_id)

    await uow.commit()
    return _json_response(resp, status_code=201)


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


@router.get("/{id}", operation_id="getUser", status_code=status.HTTP_200_OK,
            dependencies=[Depends(RequireView)])
async def get_user(id: int, uow: UnitOfWork = Depends(get_uow)) -> UserResponse:
    svc = UserService(uow)
    row = await svc.get(id)
    return _to_response(row, *_etag_from_row(row))


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


@router.patch("/{id}", operation_id="updateUser", status_code=status.HTTP_200_OK,
              dependencies=[Depends(RequireManage)])
async def update_user(
    request: Request, id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> UserResponse:
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader("If-Match header is required for PATCH /users/{id}.")

    body = await request.json()
    payload = UserUpdateRequest(**body)
    update_kwargs: dict[str, Any] = {}
    for field in ("full_name", "email", "is_active", "role_id"):
        if field in payload.model_dump(exclude_unset=True):
            update_kwargs[field] = getattr(payload, field)

    svc = UserService(uow)
    row = await svc.update(id, if_match=etag_parsed, ctx=_audit_ctx(request, principal), **update_kwargs)
    return _to_response(row, *_etag_from_row(row))


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


@router.post("/{id}/deactivate", operation_id="deactivateUser",
             summary="Deactivate user. Revokes all sessions.",
             status_code=status.HTTP_200_OK, dependencies=[Depends(RequireManage)])
async def deactivate_user(
    request: Request, id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    idem = parse_idempotency_key(idempotency_key_header)
    body = await request.json()
    endpoint = f"POST /users/{id}/deactivate"
    fingerprint = compute_request_fingerprint(method="POST", path=endpoint, body=body)

    store = IdempotencyStore(uow)
    existing = await _idem_lookup(store, str(idem.value), principal.user_id, endpoint, fingerprint)

    if existing is not None and existing.is_completed:
        resp_body = existing.response_body
        if isinstance(resp_body, str):
            resp_body = json.loads(resp_body)
        return _json_response(resp_body, status_code=200, replay=True)

    svc = UserService(uow)
    row = await svc.deactivate(id, reason=body.get("reason"), ctx=_audit_ctx(request, principal))
    resp = _to_response(row, *_etag_from_row(row)).model_dump(mode="json")

    if existing is not None:
        await store.complete(record_id=existing.id, status=200, body=resp, user_id=principal.user_id)

    await uow.commit()
    return _json_response(resp, status_code=200)


# ---------------------------------------------------------------------------
# Reset password
# ---------------------------------------------------------------------------


@router.post("/{id}/reset-password", operation_id="resetUserPassword",
             summary="Reset a user's password; revokes all sessions.",
             status_code=status.HTTP_200_OK)
async def reset_user_password(
    request: Request, id: int,
    principal: Principal = Depends(_require_password_reset_others),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    idem = parse_idempotency_key(idempotency_key_header)
    body = await request.json()
    payload = PasswordResetRequest(**body)
    endpoint = f"POST /users/{id}/reset-password"
    fingerprint = compute_request_fingerprint(method="POST", path=endpoint, body=body)

    store = IdempotencyStore(uow)
    existing = await _idem_lookup(store, str(idem.value), principal.user_id, endpoint, fingerprint)

    if existing is not None and existing.is_completed:
        resp_body = existing.response_body
        if isinstance(resp_body, str):
            resp_body = json.loads(resp_body)
        return _json_response(resp_body, status_code=200, replay=True)

    svc = UserService(uow)
    row = await svc.reset_password(id, new_password=payload.new_password, ctx=_audit_ctx(request, principal))
    resp = _to_response(row, *_etag_from_row(row)).model_dump(mode="json")

    if existing is not None:
        await store.complete(record_id=existing.id, status=200, body=resp, user_id=principal.user_id)

    await uow.commit()
    return _json_response(resp, status_code=200)


# ---------------------------------------------------------------------------
# Unlock
# ---------------------------------------------------------------------------


@router.post("/{id}/unlock", operation_id="unlockUser",
             summary="Clear failed-attempt counter.",
             status_code=status.HTTP_200_OK, dependencies=[Depends(RequireManage)])
async def unlock_user(
    request: Request, id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    idem = parse_idempotency_key(idempotency_key_header)
    body = await request.json()
    endpoint = f"POST /users/{id}/unlock"
    fingerprint = compute_request_fingerprint(method="POST", path=endpoint, body=body)

    store = IdempotencyStore(uow)
    existing = await _idem_lookup(store, str(idem.value), principal.user_id, endpoint, fingerprint)

    if existing is not None and existing.is_completed:
        resp_body = existing.response_body
        if isinstance(resp_body, str):
            resp_body = json.loads(resp_body)
        return _json_response(resp_body, status_code=200, replay=True)

    svc = UserService(uow)
    row = await svc.unlock(id, ctx=_audit_ctx(request, principal))
    resp = _to_response(row, *_etag_from_row(row)).model_dump(mode="json")

    if existing is not None:
        await store.complete(record_id=existing.id, status=200, body=resp, user_id=principal.user_id)

    await uow.commit()
    return _json_response(resp, status_code=200)


# ---------------------------------------------------------------------------
# Effective capabilities (GET)
# ---------------------------------------------------------------------------


@router.get("/{id}/capabilities", operation_id="listUserEffectiveCapabilities",
            status_code=status.HTTP_200_OK, dependencies=[Depends(RequireView)])
async def list_user_effective_capabilities(
    id: int, uow: UnitOfWork = Depends(get_uow),
) -> UserEffectiveCapabilities:
    svc = UserService(uow)
    return UserEffectiveCapabilities(**await svc.effective_capabilities(id))


# ---------------------------------------------------------------------------
# Capability grant (POST)
# ---------------------------------------------------------------------------


@router.post("/{id}/capabilities", operation_id="grantUserCapability",
             status_code=status.HTTP_200_OK, dependencies=[Depends(RequireGrantCap)])
async def grant_user_capability(
    request: Request, id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> UserEffectiveCapabilities:
    idem = parse_idempotency_key(idempotency_key_header)
    etag_parsed = parse_if_match_optional(if_match)
    body = await request.json()
    payload = UserCapabilityOverrideRequest(**body)
    endpoint = f"POST /users/{id}/capabilities"
    fingerprint = compute_request_fingerprint(method="POST", path=endpoint, body=body)

    store = IdempotencyStore(uow)
    existing = await _idem_lookup(store, str(idem.value), principal.user_id, endpoint, fingerprint)

    if existing is not None and existing.is_completed:
        svc = UserService(uow)
        return UserEffectiveCapabilities(**await svc.effective_capabilities(id))

    svc = UserService(uow)
    result = await svc.grant_capability(
        id, capability_code=payload.capability_code, is_granted=payload.is_granted,
        if_match=etag_parsed, granted_by=principal.user_id, ctx=_audit_ctx(request, principal),
    )
    resp = UserEffectiveCapabilities(**result).model_dump(mode="json")

    if existing is not None:
        await store.complete(record_id=existing.id, status=200, body=resp, user_id=principal.user_id)

    await uow.commit()
    return UserEffectiveCapabilities(**result)


# ---------------------------------------------------------------------------
# Capability revoke (DELETE)
# ---------------------------------------------------------------------------


@router.delete("/{id}/capabilities/{capability_code}", operation_id="revokeUserCapability",
               summary="Revoke capability override for a user. Effect is immediate.",
               status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(RequireRevokeCap)])
async def revoke_user_capability(
    request: Request, id: int, capability_code: str,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> None:
    idem = parse_idempotency_key(idempotency_key_header)
    endpoint = f"DELETE /users/{id}/capabilities/{capability_code}"
    fingerprint = compute_request_fingerprint(method="DELETE", path=endpoint, body=None)

    store = IdempotencyStore(uow)
    existing = await _idem_lookup(store, str(idem.value), principal.user_id, endpoint, fingerprint)

    if existing is not None and existing.is_completed:
        return

    svc = UserService(uow)
    await svc.revoke_capability(id, capability_code, revoked_by=principal.user_id,
                                ctx=_audit_ctx(request, principal))

    if existing is not None:
        await store.complete(record_id=existing.id, status=204, body={}, user_id=principal.user_id)

    await uow.commit()


__all__ = ["router"]
