"""API routes for Contacts (M3 — B.3).

Per OpenAPI §Contacts — 6 endpoints:

  GET    /contacts                        — list (contact.view)
  POST   /contacts                        — create (contact.create, Idempotency-Key)
  GET    /contacts/{id}                  — get (contact.view)
  PATCH  /contacts/{id}                  — update (contact.edit, If-Match required)
  DELETE /contacts/{id}                  — hard delete (contact.manage, Idempotency-Key)
  POST   /contacts/{id}/deactivate       — deactivate (contact.manage, Idempotency-Key)

Follows the same patterns as the ``categories`` route (master data CRUD
with idempotency + ETag + pagination + caps + audit).
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
from app.services.contacts import ContactService
from app.util import client_ip
from app.validation.contacts_schemas import (
    ContactPatch,
    ContactRequest,
    ContactResponse,
)
from app.validation.enums import ContactType
from app.validation.headers import parse_idempotency_key
from app.validation.pagination import MetaEnvelope, make_pagination

RequireView = require_capability("contact.view")
RequireCreate = require_capability("contact.create")
RequireEdit = require_capability("contact.edit")
RequireManage = require_capability("contact.manage")

logger = __import__("logging").getLogger(__name__)

router = APIRouter(prefix="/contacts", tags=["Contacts"])

#: Server-owned fields rejected if present in a request body.
_FIG_FIELDS: frozenset[str] = frozenset(
    {"id", "is_active", "created_at", "updated_at", "created_by", "version"}
)


def _audit_ctx(request: Request, principal: Principal) -> Any:
    """Build an AuditContext from the request + principal."""
    from app.audit.service import AuditContext

    return AuditContext(
        user_id=principal.user_id,
        request_id=getattr(request.state, "request_id", None),
        ip_address=client_ip(request.scope.get("headers", [])),
    )


def _to_response(row: dict[str, Any], etag: str, version: int) -> ContactResponse:
    """Convert a DB row dict into a ContactResponse."""
    return ContactResponse(
        id=int(row["id"]),
        type=row["type"],
        name=str(row["name"]),
        phone=row["phone"],
        email=row["email"],
        address=row["address"],
        notes=row["notes"],
        is_active=bool(row["is_active"]),
        version=version,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by=int(row["created_by"]) if row.get("created_by") else None,
    )


def _etag_from_row(row: dict[str, Any]) -> tuple[str, int]:
    """Derive (etag, version) from a contact row's updated_at."""
    return etag_and_version_from_updated_at(row["updated_at"])


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@router.get(
    "",
    operation_id="listContacts",
    summary="List contacts (customers and/or suppliers).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def list_contacts(
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    q: str | None = Query(default=None, max_length=200),
    filter_type: str | None = Query(default=None, alias="filter[type]"),
    filter_is_active: bool | None = Query(default=None, alias="filter[is_active]"),
    sort: str = Query(default="id"),
) -> MetaEnvelope[ContactResponse]:
    """List contacts with pagination, search, type/active filter, and sort.

    ``filter[type]`` accepts ``customer``, ``supplier``, or ``both``.
    """
    # Validate contact type filter against the closed enum.
    if filter_type is not None and filter_type not in ContactType._value2member_map_:
        from app.errors import ValidationFailed

        raise ValidationFailed(
            "Invalid contact type filter.",
            errors=[
                {
                    "code": "enum_value_invalid",
                    "field": "filter[type]",
                    "message": (
                        f"{filter_type!r} is not a valid contact type. "
                        f"Allowed: {', '.join(t.value for t in ContactType)}."
                    ),
                }
            ],
        )

    svc = ContactService(uow)
    rows, total, _ = await svc.list(
        page=page,
        per_page=per_page,
        q=q,
        contact_type=filter_type,
        active_only=filter_is_active if filter_is_active is not None else None,
        sort=sort,
    )
    data = [_to_response(r, *_etag_from_row(r)) for r in rows]
    await uow.commit()
    return MetaEnvelope[ContactResponse](
        data=data,
        pagination=make_pagination(page, per_page, total),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@router.post(
    "",
    operation_id="createContact",
    summary="Create a new contact.",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RequireCreate)],
)
async def create_contact(
    request: Request,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ContactResponse:
    """Create a new contact. Idempotency-Key required."""
    idem = parse_idempotency_key(idempotency_key_header)
    key_str = str(idem.value)

    body = await request.json()
    payload = ContactRequest(**body)

    svc = ContactService(uow)
    row = await svc.create(
        contact_type=payload.type,
        name=payload.name,
        phone=payload.phone,
        email=payload.email,
        address=payload.address,
        notes=payload.notes,
        is_active=payload.is_active,
        created_by=principal.user_id,
        idempotency_key=key_str,
        request_body=body,
        ctx=_audit_ctx(request, principal),
    )
    etag, version = _etag_from_row(row)
    return _to_response(row, etag, version)


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------
@router.get(
    "/{id}",
    operation_id="getContact",
    summary="Get a single contact by id.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def get_contact(
    request: Request,
    id: int,
    uow: UnitOfWork = Depends(get_uow),
) -> ContactResponse:
    """Get a single contact by id."""
    svc = ContactService(uow)
    row = await svc.get(id)
    etag, version = _etag_from_row(row)
    await uow.commit()
    return _to_response(row, etag, version)


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------
@router.patch(
    "/{id}",
    operation_id="updateContact",
    summary="Update a contact.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireEdit)],
)
async def update_contact(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> ContactResponse:
    """Update a contact. ``If-Match`` is REQUIRED (OpenAPI IfMatchRequired)."""
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader("If-Match header is required for PATCH /contacts/{id}.")

    body = await request.json()
    payload = ContactPatch(**body)

    svc = ContactService(uow)
    row = await svc.update(
        id,
        type=payload.type,
        name=payload.name,
        phone=payload.phone,
        email=payload.email,
        address=payload.address,
        notes=payload.notes,
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
    operation_id="deactivateContact",
    summary="Deactivate contact.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireManage)],
)
async def deactivate_contact(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ContactResponse:
    """Deactivate a contact (sets is_active=FALSE). Preserves history.

    Idempotency-Key required, reason optional.
    """
    from app.validation.schemas import DeactivateRequest

    idem = parse_idempotency_key(idempotency_key_header)

    body_dict = await request.json()
    payload = DeactivateRequest(**body_dict)

    svc = ContactService(uow)
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
    operation_id="deleteContact",
    summary="Hard-delete a contact (only if unreferenced).",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(RequireManage)],
)
async def delete_contact(
    request: Request,
    id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> None:
    """Hard-delete a contact. Idempotency-Key required; blocked by references."""
    idem = parse_idempotency_key(idempotency_key_header)

    svc = ContactService(uow)
    await svc.delete(
        id,
        idempotency_key=str(idem.value),
        ctx=_audit_ctx(request, principal),
    )


__all__ = ["router"]
