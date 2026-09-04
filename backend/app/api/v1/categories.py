"""API routes for Categories (M2 — B.1).

Per OpenAPI §15.2 — 6 endpoints:

  GET    /categories               — list (category.view)
  POST   /categories               — create (category.manage, Idempotency-Key)
  GET    /categories/{id}          — get (category.view)
  PATCH  /categories/{id}          — update (category.manage, If-Match required)
  DELETE /categories/{id}          — hard delete (category.manage, Idempotency-Key)
  POST   /categories/{id}/deactivate — deactivate (category.manage, Idempotency-Key)

Follows the same patterns as the Units (B.2) and Financial-Categories (B.5)
routes already present in the live codebase.
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
from app.services.categories import CategoryService
from app.util import client_ip
from app.validation.categories_schemas import (
    CategoryPatch,
    CategoryRequest,
    CategoryResponse,
    DeactivateRequest,
)
from app.validation.headers import parse_idempotency_key
from app.validation.pagination import (
    MetaEnvelope,
    make_pagination,
)

RequireView = require_capability("category.view")
RequireManage = require_capability("category.manage")

logger = __import__("logging").getLogger(__name__)

router = APIRouter(prefix="/categories", tags=["Categories"])


def _audit_ctx(request: Request, principal: Principal) -> Any:
    """Build an AuditContext from the request + principal."""
    from app.audit.service import AuditContext

    return AuditContext(
        user_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request.scope.get("headers", [])),
    )


def _to_response(row: dict[str, Any], etag: str, version: int) -> CategoryResponse:
    """Convert a DB row dict into a CategoryResponse."""
    return CategoryResponse(
        id=int(row["id"]),
        name=str(row["name"]),
        is_active=bool(row["is_active"]),
        version=version,
        parent_id=int(row["parent_id"]) if row.get("parent_id") else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _etag_from_row(row: dict[str, Any]) -> tuple[str, int]:
    """Derive (etag, version) from a category row's updated_at."""
    return etag_and_version_from_updated_at(row["updated_at"])


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get(
    "",
    operation_id="listCategories",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def list_categories(
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    q: str | None = Query(default=None, max_length=200),
    filter_is_active: bool | None = Query(default=None, alias="filter[is_active]"),
    sort: str = Query(default="id"),
) -> MetaEnvelope[CategoryResponse]:
    """List categories with pagination, search, and optional active filter."""
    svc = CategoryService(uow)
    rows, total, _ = await svc.list(
        page=page,
        per_page=per_page,
        q=q,
        active_only=filter_is_active if filter_is_active is not None else None,
        sort=sort,
    )
    data = [_to_response(r, *_etag_from_row(r)) for r in rows]
    return MetaEnvelope[CategoryResponse](
        data=data,
        pagination=make_pagination(page, per_page, total),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "",
    operation_id="createCategories",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RequireManage)],
)
async def create_category(
    request: Request,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CategoryResponse:
    """Create a new category. Idempotency-Key required."""
    # Validate Idempotency-Key presence + format.
    idem = parse_idempotency_key(idempotency_key_header)
    key_str = str(idem.value)

    body = await request.json()
    payload = CategoryRequest(**body)

    svc = CategoryService(uow)
    row = await svc.create(
        name=payload.name,
        parent_id=payload.parent_id,
        is_active=payload.is_active,
        ctx=_audit_ctx(request, principal),
        idempotency_key=key_str,
        request_body=body,
    )
    etag, version = _etag_from_row(row)
    return _to_response(row, etag, version)


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


@router.get(
    "/{id}",
    operation_id="getCategories",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def get_category(
    request: Request,
    id: int,
    uow: UnitOfWork = Depends(get_uow),
) -> CategoryResponse:
    """Get a single category by id."""
    svc = CategoryService(uow)
    row = await svc.get(id)
    etag, version = _etag_from_row(row)
    return _to_response(row, etag, version)


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


@router.patch(
    "/{id}",
    operation_id="updateCategories",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireManage)],
)
async def update_category(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> CategoryResponse:
    """Update a category. ``If-Match`` is REQUIRED (OpenAPI IfMatchRequired)."""
    # If-Match is required per OpenAPI on PATCH.
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader("If-Match header is required for PATCH /categories/{id}.")

    body = await request.json()
    payload = CategoryPatch(**body)

    svc = CategoryService(uow)
    row = await svc.update(
        id,
        name=payload.name,
        is_active=payload.is_active,
        parent_id=payload.parent_id,
        if_match=etag_parsed,
        ctx=_audit_ctx(request, principal),
    )
    resp_etag, version = _etag_from_row(row)
    resp = _to_response(row, resp_etag, version)
    return resp


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


@router.post(
    "/{id}/deactivate",
    operation_id="deactivateCategories",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireManage)],
)
async def deactivate_category(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CategoryResponse:
    """Deactivate a category. Always allowed (preserves history). Idempotency-Key required."""
    idem = parse_idempotency_key(idempotency_key_header)

    body = await request.json()
    payload = DeactivateRequest(**body)

    svc = CategoryService(uow)
    row = await svc.deactivate(
        id,
        reason=payload.reason,
        idempotency_key=str(idem.value),
        ctx=_audit_ctx(request, principal),
    )
    etag, version = _etag_from_row(row)
    return _to_response(row, etag, version)


# ---------------------------------------------------------------------------
# Delete (hard)
# ---------------------------------------------------------------------------


@router.delete(
    "/{id}",
    operation_id="deleteCategories",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(RequireManage)],
)
async def delete_category(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> None:
    """Hard delete a category (only if unreferenced). Idempotency-Key required."""
    idem = parse_idempotency_key(idempotency_key_header)

    svc = CategoryService(uow)
    await svc.delete(
        id,
        idempotency_key=str(idem.value),
        ctx=_audit_ctx(request, principal),
    )
