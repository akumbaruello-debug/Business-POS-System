"""API routes for Units (M2).

Per ``openapi.yaml`` §15.6:

  GET    /units                 — list (paginated, filter/search)
  POST   /units                 — create (Idempotency-Key, unit.manage)
  GET    /units/{id}            — get one
  PATCH  /units/{id}            — update name/is_active (unit.manage)
  DELETE /units/{id}            — hard delete (Idempotency-Key, unit.manage)
  POST   /units/{id}/deactivate — soft delete (Idempotency-Key, unit.manage)
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Request, status

from app.api.deps import get_uow, require_capability
from app.audit.service import AuditContext
from app.auth.principal import Principal
from app.db import UnitOfWork
from app.services.units import UnitService
from app.validation.headers import parse_idempotency_key
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.units_schemas import (
    DeactivateRequest,
    UnitPatch,
    UnitRequest,
    UnitResponse,
)

router = APIRouter(prefix="/units", tags=["units"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_response(row: dict[str, Any]) -> UnitResponse:
    return UnitResponse(
        id=int(row["id"]),
        code=str(row["code"]),
        name=str(row["name"]),
        is_active=bool(row["is_active"]),
    )


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _audit_ctx(request: Request, principal: Principal) -> AuditContext:
    return AuditContext(
        user_id=principal.user_id,
        request_id=request.headers.get("x-request-id"),
        ip_address=_client_ip(request),
    )


# ---------------------------------------------------------------------------
# Capability gates
# ---------------------------------------------------------------------------

RequireView = require_capability("unit.view")
RequireManage = require_capability("unit.manage")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get(
    "/",
    operation_id="listUnits",
    summary="List units",
    status_code=status.HTTP_200_OK,
)
async def list_units(
    request: Request,
    principal: Annotated[Principal, Depends(RequireView)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(1, ge=1, le=1000),
    per_page: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, max_length=200),
    active_only: bool = Query(False, alias="filter[is_active]"),
) -> MetaEnvelope[UnitResponse]:
    """List units, paginated.

    ``$?filter[is_active]=true`` restricts to active units.
    """
    svc = UnitService(uow)
    rows, total = await svc.list(page=page, per_page=per_page, q=q, active_only=active_only)
    return MetaEnvelope[UnitResponse](
        data=[_to_response(r) for r in rows],
        pagination=make_pagination(page, per_page, total),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "/",
    operation_id="createUnits",
    summary="Create unit",
    status_code=status.HTTP_201_CREATED,
)
async def create_unit(
    request: Request,
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> UnitResponse:
    """Create a unit. Idempotency-Key required per OpenAPI."""
    parse_idempotency_key(idempotency_key)
    body = UnitRequest.model_validate(await request.json())

    svc = UnitService(uow)
    row = await svc.create(
        code=body.code,
        name=body.name,
        is_active=body.is_active,
        ctx=_audit_ctx(request, principal),
    )
    await uow.commit()
    return _to_response(row)


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


@router.get(
    "/{id}",
    operation_id="getUnits",
    summary="Get unit",
    status_code=status.HTTP_200_OK,
)
async def get_unit(
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireView)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> UnitResponse:
    """Get a single unit by id."""
    svc = UnitService(uow)
    row = await svc.get(id)
    return _to_response(row)


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


@router.patch(
    "/{id}",
    operation_id="updateUnits",
    summary="Update unit",
    status_code=status.HTTP_200_OK,
)
async def update_unit(
    request: Request,
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> UnitResponse:
    """Update a unit's name and/or is_active. ``code`` is immutable via API."""
    body = UnitPatch.model_validate(await request.json())
    svc = UnitService(uow)
    row = await svc.update(
        id,
        name=body.name,
        is_active=body.is_active,
        ctx=_audit_ctx(request, principal),
    )
    await uow.commit()
    return _to_response(row)


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


@router.post(
    "/{id}/deactivate",
    operation_id="deactivateUnits",
    summary="Deactivate unit. Preserves history.",
    status_code=status.HTTP_200_OK,
)
async def deactivate_unit(
    request: Request,
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> UnitResponse:
    """Deactivate a unit (sets is_active=FALSE)."""
    parse_idempotency_key(idempotency_key)
    body = DeactivateRequest.model_validate_json(await request.body())
    svc = UnitService(uow)
    row = await svc.deactivate(id, reason=body.reason, ctx=_audit_ctx(request, principal))
    await uow.commit()
    return _to_response(row)


# ---------------------------------------------------------------------------
# Delete (hard, ON DELETE RESTRICT from products)
# ---------------------------------------------------------------------------


@router.delete(
    "/{id}",
    operation_id="deleteUnits",
    summary="Hard delete unit (only if unreferenced)",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_unit(
    request: Request,
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> None:
    """Hard-delete only when no product references the unit."""
    parse_idempotency_key(idempotency_key)
    svc = UnitService(uow)
    await svc.delete(id, ctx=_audit_ctx(request, principal))
    await uow.commit()
    return None


__all__ = ["router"]
