"""API routes for Product Attributes (V3 Phase 2 — Jenis / Size / Kualitas).

Master-data endpoints covering the three product dimensions, following
the same 6-endpoint pattern as Categories (B.1) and Units (B.2):

  GET    /product-attributes                  — list (product_attribute.view)
  POST   /product-attributes                  — create (product_attribute.manage, Idempotency-Key)
  GET    /product-attributes/{id}             — get (product_attribute.view)
  PATCH  /product-attributes/{id}             — update (product_attribute.manage, If-Match required)
  DELETE /product-attributes/{id}             — hard delete (product_attribute.manage, Idempotency-Key)
  POST   /product-attributes/{id}/deactivate  — deactivate (product_attribute.manage, Idempotency-Key)

List supports ``filter[kind]`` (jenis | size | kualitas) so the settings
UI can render one table per dimension without client-side filtering.
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
from app.services.product_attributes import ProductAttributeService
from app.util import client_ip
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.product_attributes_schemas import (
    DeactivateRequest,
    ProductAttributePatch,
    ProductAttributeRequest,
    ProductAttributeResponse,
)
from app.validation.headers import parse_idempotency_key

RequireView = require_capability("product_attribute.view")
RequireManage = require_capability("product_attribute.manage")

logger = __import__("logging").getLogger(__name__)

router = APIRouter(prefix="/product-attributes", tags=["Product Attributes"])


def _audit_ctx(request: Request, principal: Principal) -> Any:
    """Build an AuditContext from the request + principal."""
    from app.audit.service import AuditContext

    return AuditContext(
        user_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request.scope.get("headers", [])),
    )


def _to_response(row: dict[str, Any], etag: str, version: int) -> ProductAttributeResponse:
    """Convert a DB row dict into a ProductAttributeResponse."""
    return ProductAttributeResponse(
        id=int(row["id"]),
        kind=str(row["kind"]),
        name=str(row["name"]),
        is_active=bool(row["is_active"]),
        version=version,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _etag_from_row(row: dict[str, Any]) -> tuple[str, int]:
    """Derive (etag, version) from a row's updated_at."""
    return etag_and_version_from_updated_at(row["updated_at"])


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get(
    "",
    operation_id="listProductAttributes",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def list_product_attributes(
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    q: str | None = Query(default=None, max_length=200),
    filter_kind: str | None = Query(default=None, alias="filter[kind]"),
    filter_is_active: bool | None = Query(default=None, alias="filter[is_active]"),
    sort: str = Query(default="id"),
) -> MetaEnvelope[ProductAttributeResponse]:
    """List product attributes with pagination, kind filter, search, active filter."""
    svc = ProductAttributeService(uow)
    rows, total, _ = await svc.list(
        page=page,
        per_page=per_page,
        kind=filter_kind,
        q=q,
        active_only=filter_is_active if filter_is_active is not None else None,
        sort=sort,
    )
    data = [_to_response(r, *_etag_from_row(r)) for r in rows]
    return MetaEnvelope[ProductAttributeResponse](
        data=data,
        pagination=make_pagination(page, per_page, total),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "",
    operation_id="createProductAttributes",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RequireManage)],
)
async def create_product_attribute(
    request: Request,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProductAttributeResponse:
    """Create a new product attribute value. Idempotency-Key required."""
    idem = parse_idempotency_key(idempotency_key_header)
    key_str = str(idem.value)

    body = await request.json()
    payload = ProductAttributeRequest(**body)

    svc = ProductAttributeService(uow)
    row = await svc.create(
        kind=payload.kind,
        name=payload.name,
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
    operation_id="getProductAttributes",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def get_product_attribute(
    request: Request,
    id: int,
    uow: UnitOfWork = Depends(get_uow),
) -> ProductAttributeResponse:
    """Get a single product attribute by id."""
    svc = ProductAttributeService(uow)
    row = await svc.get(id)
    etag, version = _etag_from_row(row)
    return _to_response(row, etag, version)


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


@router.patch(
    "/{id}",
    operation_id="updateProductAttributes",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireManage)],
)
async def update_product_attribute(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> ProductAttributeResponse:
    """Update an attribute. ``If-Match`` is REQUIRED; ``kind`` is immutable."""
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader(
            "If-Match header is required for PATCH /product-attributes/{id}."
        )

    body = await request.json()
    payload = ProductAttributePatch(**body)

    svc = ProductAttributeService(uow)
    row = await svc.update(
        id,
        name=payload.name,
        is_active=payload.is_active,
        if_match=etag_parsed,
        ctx=_audit_ctx(request, principal),
    )
    resp_etag, version = _etag_from_row(row)
    return _to_response(row, resp_etag, version)


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


@router.post(
    "/{id}/deactivate",
    operation_id="deactivateProductAttributes",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireManage)],
)
async def deactivate_product_attribute(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProductAttributeResponse:
    """Deactivate an attribute. Always allowed (preserves history). Idempotency-Key required."""
    idem = parse_idempotency_key(idempotency_key_header)

    body = await request.json()
    payload = DeactivateRequest(**body)

    svc = ProductAttributeService(uow)
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
    operation_id="deleteProductAttributes",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(RequireManage)],
)
async def delete_product_attribute(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> None:
    """Hard delete an attribute (only if unreferenced). Idempotency-Key required."""
    idem = parse_idempotency_key(idempotency_key_header)

    svc = ProductAttributeService(uow)
    await svc.delete(
        id,
        idempotency_key=str(idem.value),
        ctx=_audit_ctx(request, principal),
    )