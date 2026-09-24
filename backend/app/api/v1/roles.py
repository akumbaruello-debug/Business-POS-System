"""API routes for Roles (Phase 1.5).

Per OpenAPI §15.4 — 6 endpoints:

  GET    /roles                    — list (role.view)
  POST   /roles                    — create (role.manage, Idempotency-Key)
  GET    /roles/{id}               — get (role.view)
  PATCH  /roles/{id}               — update (role.manage, If-Match required)
  DELETE /roles/{id}               — hard delete (role.manage, Idempotency-Key)
  PUT    /roles/{id}/capabilities  — replace capabilities (role.manage, Idempotency-Key)

Follows the categories/users route patterns for idempotency + ETag.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, status

from app.api.deps import current_principal, get_uow, require_capability
from app.auth.principal import Principal
from app.concurrency.etag import parse_if_match_optional
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.db import UnitOfWork
from app.errors import MissingHeader
from app.services.roles import RoleService
from app.util import client_ip
from app.validation.headers import parse_idempotency_key
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.roles_schemas import (
    RoleCapabilitiesRequest,
    RoleCreateRequest,
    RoleResponse,
    RoleUpdateRequest,
)

RequireView = require_capability("role.view")
RequireManage = require_capability("role.manage")

logger = __import__("logging").getLogger(__name__)

router = APIRouter(prefix="/roles", tags=["Roles"])


def _audit_ctx(request: Request, principal: Principal) -> Any:
    """Build an AuditContext from the request + principal."""
    from app.audit.service import AuditContext

    return AuditContext(
        user_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request.scope.get("headers", [])),
    )


def _to_response(row: dict[str, Any], capability_codes: list[str]) -> RoleResponse:
    """Convert a DB row dict into a RoleResponse."""
    _etag, version = etag_and_version_from_updated_at(row["updated_at"])
    return RoleResponse(
        id=int(row["id"]),
        name=str(row["name"]),
        description=row.get("description"),
        is_system_role=bool(row["is_system_role"]),
        is_active=True,
        capability_codes=capability_codes,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=version,
    )


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get(
    "",
    operation_id="listRoles",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def list_roles(
    uow: UnitOfWork = Depends(get_uow),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    q: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="id"),
) -> MetaEnvelope[RoleResponse]:
    """List roles with pagination, search, and sort."""
    svc = RoleService(uow)
    rows, total = await svc.list_roles(page=page, per_page=per_page, q=q, sort=sort)
    data: list[RoleResponse] = []
    for r in rows:
        caps = await svc._repo.list_capability_codes(int(r["id"]))
        data.append(_to_response(r, caps))
    return MetaEnvelope[RoleResponse](
        data=data,
        pagination=make_pagination(page, per_page, total),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "",
    operation_id="createRole",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RequireManage)],
)
async def create_role(
    request: Request,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RoleResponse:
    """Create a new custom role. Idempotency-Key required."""
    parse_idempotency_key(idempotency_key_header)
    body = await request.json()
    payload = RoleCreateRequest(**body)

    svc = RoleService(uow)
    row = await svc.create(
        name=payload.name,
        description=payload.description,
        ctx=_audit_ctx(request, principal),
    )
    return _to_response(row, [])


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


@router.get(
    "/{id}",
    operation_id="getRole",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def get_role(
    id: int,
    uow: UnitOfWork = Depends(get_uow),
) -> RoleResponse:
    """Get a single role by id."""
    svc = RoleService(uow)
    row = await svc.get(id)
    caps = await svc._repo.list_capability_codes(id)
    return _to_response(row, caps)


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


@router.patch(
    "/{id}",
    operation_id="updateRole",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireManage)],
)
async def update_role(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> RoleResponse:
    """Update a role. ``If-Match`` is REQUIRED (OpenAPI IfMatchRequired)."""
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader("If-Match header is required for PATCH /roles/{id}.")

    body = await request.json()
    payload = RoleUpdateRequest(**body)

    svc = RoleService(uow)
    row = await svc.update(
        id,
        name=payload.name,
        description=payload.description,
        if_match=etag_parsed,
        ctx=_audit_ctx(request, principal),
    )
    caps = await svc._repo.list_capability_codes(id)
    return _to_response(row, caps)


# ---------------------------------------------------------------------------
# Delete (hard)
# ---------------------------------------------------------------------------


@router.delete(
    "/{id}",
    operation_id="deleteRole",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(RequireManage)],
)
async def delete_role(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> None:
    """Hard delete a custom role (only if unreferenced). Idempotency-Key required."""
    parse_idempotency_key(idempotency_key_header)

    svc = RoleService(uow)
    await svc.delete(id, ctx=_audit_ctx(request, principal))


# ---------------------------------------------------------------------------
# Set capabilities
# ---------------------------------------------------------------------------


@router.put(
    "/{id}/capabilities",
    operation_id="setRoleCapabilities",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireManage)],
)
async def set_role_capabilities(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RoleResponse:
    """Replace the role's full capability set. Idempotency-Key required."""
    parse_idempotency_key(idempotency_key_header)

    body = await request.json()
    payload = RoleCapabilitiesRequest(**body)

    svc = RoleService(uow)
    row = await svc.set_capabilities(
        id,
        capability_codes=payload.capability_codes,
        ctx=_audit_ctx(request, principal),
    )
    caps = await svc._repo.list_capability_codes(id)
    return _to_response(row, caps)


__all__ = ["router"]
