"""API routes for financial_categories (M2 foundation).

Per ``openapi.yaml`` §15.7.5 + Database-Design §3.3.

Endpoints:
  GET    /financial-categories                   — list (paginated)
  POST   /financial-categories                   — create (Idempotency-Key, financial_category.manage)
  GET    /financial-categories/{id}              — get one
  PATCH  /financial-categories/{id}              — update (financial_category.manage, blacklist on rename)
  DELETE /financial-categories/{id}              — hard delete if unreferenced (Idempotency-Key)
  POST   /financial-categories/{id}/deactivate   — soft delete (Idempotency-Key)

Blacklist enforcement (BR-FIN-003 / API §15.7.5):
The ``financial_categories.name`` field is validated against a closed
set of derived-category names on **every** create and update(rename).
The check is case-insensitive and whitespace-normalized. A violation
returns 400 ``manual_entry_duplicate_of_derived``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Request, status
from pydantic import TypeAdapter

from app.api.deps import get_uow, require_capability
from app.audit.service import AuditContext
from app.auth.principal import Principal
from app.concurrency.etag import check_if_match
from app.db import UnitOfWork
from app.domain.blacklist import assert_not_blacklisted
from app.errors import ManualEntryDuplicateOfDerived
from app.services.finance import FinancialCategoryService
from app.util import format_etag
from app.validation.enums import FinancialEntryType
from app.validation.headers import parse_idempotency_key, parse_if_match
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.schemas import (
    DeactivateRequest,
    FinancialCategoryPatch,
    FinancialCategoryRequest,
    FinancialCategoryResponse,
)

# Module-level adapter for datetime serialization (ETag round-trip).
_DATETIME_ADAPTER = TypeAdapter(datetime)

router = APIRouter(prefix="/financial-categories", tags=["financial_categories"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_response(row: dict[str, Any]) -> FinancialCategoryResponse:
    return FinancialCategoryResponse(
        id=int(row["id"]),
        code=str(row["code"]),
        name=str(row["name"]),
        entry_type=FinancialEntryType(str(row["entry_type"])),
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
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

RequireView = require_capability("financial_category.view")
RequireManage = require_capability("financial_category.manage")


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get(
    "/",
    operation_id="listFinancialCategories",
    summary="List financial categories",
    status_code=status.HTTP_200_OK,
)
async def list_financial_categories(
    request: Request,
    principal: Annotated[Principal, Depends(RequireView)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(1, ge=1, le=1000),
    per_page: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, max_length=200),
    active_only: bool = Query(False, alias="filter[is_active]"),
) -> MetaEnvelope[FinancialCategoryResponse]:
    """List financial categories, paginated."""
    svc = FinancialCategoryService(uow)
    rows, total = await svc.list(page=page, per_page=per_page, q=q, active_only=active_only)
    return MetaEnvelope[FinancialCategoryResponse](
        data=[_to_response(r) for r in rows],
        pagination=make_pagination(page, per_page, total),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "/",
    operation_id="createFinancialCategories",
    summary="Create financial category",
    status_code=status.HTTP_201_CREATED,
)
async def create_financial_category(
    request: Request,
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> FinancialCategoryResponse:
    """Create a financial category. Idempotency-Key required.

    Blacklist rule (API §15.7.5): ``name`` must not match any derived
    accounting category (Sales, COGS, Production, Purchase Shipping,
    Refund, Purchase, Sales Revenue, Cost of Goods Sold, Other Income,
    Operating Expenses — case-insensitive). Violation → 400
    ``manual_entry_duplicate_of_derived``.
    """
    parse_idempotency_key(idempotency_key)
    body = FinancialCategoryRequest.model_validate(await request.json())

    # Blacklist enforcement — single source of truth.
    # The service layer *also* calls assert_not_blacklisted on create,
    # but we do it here too so the HTTP path fails fast with the exact
    # error code before any DB round-trip.
    assert_not_blacklisted(body.name, field="name")

    svc = FinancialCategoryService(uow)
    try:
        row = await svc.create(
            code=body.code,
            name=body.name,
            entry_type=body.entry_type.value,
            is_active=body.is_active,
            ctx=_audit_ctx(request, principal),
        )
        await uow.commit()
    except ManualEntryDuplicateOfDerived:
        # Service layer may also raise; re-raise to the error handler.
        raise
    return _to_response(row)


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


@router.get(
    "/{id}",
    operation_id="getFinancialCategories",
    summary="Get financial category",
    status_code=status.HTTP_200_OK,
)
async def get_financial_category(
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireView)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> FinancialCategoryResponse:
    """Get a single financial category by id."""
    svc = FinancialCategoryService(uow)
    row = await svc.get(id)
    return _to_response(row)


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


@router.patch(
    "/{id}",
    operation_id="updateFinancialCategories",
    summary="Update financial category (PATCH)",
    status_code=status.HTTP_200_OK,
)
async def update_financial_category(
    request: Request,
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    if_match: str | None = Header(None, alias="If-Match"),
) -> FinancialCategoryResponse:
    """Update a financial category. ``code`` and ``entry_type`` are immutable.

    Requires ``If-Match`` per OpenAPI §15.7.5 — concurrency check runs
    against the current ``updated_at`` before any write. Rename is
    subject to the same blacklist rule as create.
    """
    parsed_if_match = parse_if_match(if_match)
    body = FinancialCategoryPatch.model_validate(await request.json())

    # Blacklist enforcement on rename — called before any DB write so
    # the check happens even if the DB layer is bypassed.
    if body.name is not None:
        assert_not_blacklisted(body.name, field="name")

    svc = FinancialCategoryService(uow)
    # If-Match check: read current ETag (updated_at), compare to client's.
    # NOTE: The client obtains the ETag from the ``updated_at`` field in
    # the POST/PATCH response body, which Pydantic v2 serializes to ISO-8601
    # with a ``Z`` suffix and full microseconds
    # (e.g. ``2026-08-28T16:21:14.164165Z``). To keep the round-trip
    # byte-exact, we reproduce that exact form rather than using
    # ``app.util.iso_utc`` (which strips sub-second digits and breaks the
    # round-trip — see test_patch_with_stale_if_match_returns_412).
    # ``pydantic.TypeAdapter(datetime).dump_python(d, mode='json')`` is the
    # single source of truth for the canonical response form.
    current = await svc.get(id)
    cur_updated = current["updated_at"]
    cur_iso = _DATETIME_ADAPTER.dump_python(cur_updated, mode="json")
    current_etag = format_etag(cur_iso)
    # parse_if_match already raises InvalidHeader on malformed values.
    # When the header is absent we MUST reject (OpenAPI declares
    # IfMatchRequired on PATCH /financial-categories/{id}).
    if parsed_if_match is None:
        from app.errors import MissingHeader

        raise MissingHeader("If-Match header is required.")
    check_if_match(provided=parsed_if_match.etag, current_etag=current_etag)

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
    operation_id="deactivateFinancialCategories",
    summary="Deactivate financial category. Preserves history.",
    status_code=status.HTTP_200_OK,
)
async def deactivate_financial_category(
    request: Request,
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> FinancialCategoryResponse:
    """Deactivate a financial category (sets is_active=FALSE)."""
    parse_idempotency_key(idempotency_key)
    body = DeactivateRequest.model_validate_json(await request.body())
    svc = FinancialCategoryService(uow)
    row = await svc.deactivate(id, reason=body.reason, ctx=_audit_ctx(request, principal))
    await uow.commit()
    return _to_response(row)


# ---------------------------------------------------------------------------
# Delete (hard, only if unreferenced)
# ---------------------------------------------------------------------------


@router.delete(
    "/{id}",
    operation_id="deleteFinancialCategories",
    summary="Hard delete financial category (only if unreferenced)",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_financial_category(
    request: Request,
    id: Annotated[int, Path(..., ge=1)],
    principal: Annotated[Principal, Depends(RequireManage)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> None:
    """Hard-delete only when no history references the category."""
    parse_idempotency_key(idempotency_key)
    svc = FinancialCategoryService(uow)
    await svc.delete(id, ctx=_audit_ctx(request, principal))
    await uow.commit()
    return None


__all__ = ["router"]
