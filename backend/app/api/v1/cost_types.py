"""API routes for Cost Types (M2 / B.4).

Per ``openapi.yaml`` §15.6:

  GET    /cost-types               — list (paginated, filter[is_active], q)
  POST   /cost-types               — create (Idempotency-Key required,
                                      cost_type.manage)
  GET    /cost-types/{id}          — get one
  PATCH  /cost-types/{id}          — update name/is_active (cost_type.manage)
  DELETE /cost-types/{id}          — hard delete if unreferenced (Idempotency-Key,
                                      cost_type.manage)
  POST   /cost-types/{id}/deactivate — soft delete (Idempotency-Key, cost_type.manage)

Documented contradiction (see ``app/services/cost_types.py`` and
``app/validation/cost_types_schemas.py``): the OpenAPI ``CostType``
response requires a ``version`` integer, but the locked ``cost_types``
table has no ``version``/``updated_at`` column. The response layer
injects ``version = 1``; no ETag source exists, so ``If-Match`` on
PATCH is not enforced (mirrors the committed ``units`` module behavior).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Request, status

from app.api.deps import get_uow, require_capability
from app.audit.service import AuditContext
from app.auth.principal import Principal
from app.db import UnitOfWork
from app.services.cost_types import CostTypeService
from app.validation.cost_types_schemas import (
    CostTypePatch,
    CostTypeRequest,
    CostTypeResponse,
)
from app.validation.headers import parse_idempotency_key
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.schemas import DeactivateRequest

router = APIRouter(prefix="/cost-types", tags=["cost_types"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_response(row: dict[str, Any]) -> CostTypeResponse:
    return CostTypeResponse(
        id=int(row["id"]),
        code=str(row["code"]),
        name=str(row["name"]),
        is_active=bool(row["is_active"]),
        version=1,
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

RequireView = require_capability("cost_type.view")
RequireManage = require_capability("cost_type.manage")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get(
    "/",
    operation_id="listCostTypes",
    summary="List cost types",
    status_code=status.HTTP_200_OK,
)
async def list_cost_types(
    request: Request,
    principal: Annotated[Principal, Depends(RequireView)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(1, ge=1, le=1000),
    per_page: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, max_length=200),
    active_only: bool = Query(False, alias="filter[is_active]"),
) -> MetaEnvelope[CostTypeResponse]:
    """List cost types, paginated.

    ``$?filter[is_active]=true`` restricts to active rows; otherwise all
    rows (including deactivated) are returned.
    """
    svc = CostTypeService(uow)
    rows, total = await svc.list(page=page, per_page=per_page, q=q, active_only=active_only)
    return MetaEnvelope[CostTypeResponse](
        data=[_to_response(r) for r in rows],
        pagination=make_pagination(page, per_page, total),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "/",
    operation_id="createCostTypes",
    summary="Create cost type",
    status_code=status.HTTP_201_CREATED,
)
async def create_cost_type(
    request: Request,
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> CostTypeResponse:
    """Create a cost type. Idempotency-Key required per OpenAPI."""
    parse_idempotency_key(idempotency_key)
    body = CostTypeRequest.model_validate(await request.json())

    svc = CostTypeService(uow)
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
    operation_id="getCostTypes",
    summary="Get cost type",
    status_code=status.HTTP_200_OK,
)
async def get_cost_type(
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireView)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> CostTypeResponse:
    """Get a single cost type by id."""
    svc = CostTypeService(uow)
    row = await svc.get(id)
    return _to_response(row)


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


@router.patch(
    "/{id}",
    operation_id="updateCostTypes",
    summary="Update cost type (PATCH)",
    status_code=status.HTTP_200_OK,
)
async def update_cost_type(
    request: Request,
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> CostTypeResponse:
    """Update a cost type's name and/or is_active.

    ``code`` is immutable via the API (``CostTypePatch`` has no ``code``
    field). ``If-Match`` is *declared* required by openapi but is not
    enforced here: the locked ``cost_types`` table has no ``updated_at``
    / version column, so there is no ETag source. This mirrors the
    committed ``units`` module; see the documented contradiction in the
    module docstring.
    """
    body = CostTypePatch.model_validate(await request.json())
    svc = CostTypeService(uow)
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
    operation_id="deactivateCostTypes",
    summary="Deactivate cost type. Preserves history.",
    status_code=status.HTTP_200_OK,
)
async def deactivate_cost_type(
    request: Request,
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> CostTypeResponse:
    """Deactivate a cost type (sets is_active=FALSE, reversible)."""
    parse_idempotency_key(idempotency_key)
    body = DeactivateRequest.model_validate_json(await request.body())
    svc = CostTypeService(uow)
    row = await svc.deactivate(id, reason=body.reason, ctx=_audit_ctx(request, principal))
    await uow.commit()
    return _to_response(row)


# ---------------------------------------------------------------------------
# Delete (hard, ON DELETE RESTRICT from production_cost_lines)
# ---------------------------------------------------------------------------


@router.delete(
    "/{id}",
    operation_id="deleteCostTypes",
    summary="Hard delete cost type (only if unreferenced)",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_cost_type(
    request: Request,
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> None:
    """Hard-delete only when no production_cost_lines reference the type."""
    parse_idempotency_key(idempotency_key)
    svc = CostTypeService(uow)
    await svc.delete(id, ctx=_audit_ctx(request, principal))
    await uow.commit()
    return None


__all__ = [
    "router",
]
