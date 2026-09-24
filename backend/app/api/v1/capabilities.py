"""API routes for the canonical Capability catalog.

Per OpenAPI ``GET /capabilities`` — 1 endpoint:

  GET  /capabilities   — list (user.view)

The capability catalog is a read-only, system-managed asset. The
``capabilities`` table stores ``id``, ``code``, ``description``,
``created_at``; the OpenAPI response additionally requires ``domain``,
``default_owner``, and ``default_staff`` which are derived from the
frozen catalog in ``app.authz.caps`` (no DDL change).

Business rules
--------------
* Read-only; no mutation endpoints.
* ``default_owner`` is always ``True`` (Owner role receives every
  capability per ``schema.sql`` §19.3 CROSS JOIN seed).
* ``default_staff`` is ``True`` for codes in
  ``STAFF_DEFAULT_CAPABILITIES``.
* Paginated with optional ``q`` search on code/description.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_uow, require_capability
from app.db import UnitOfWork
from app.services.capabilities import CapabilityService
from app.validation.capabilities_schemas import CapabilityResponse
from app.validation.pagination import MetaEnvelope, make_pagination

RequireView = require_capability("user.view")

router = APIRouter(prefix="/capabilities", tags=["Capabilities"])


@router.get(
    "",
    operation_id="listCapabilities",
    summary="List canonical capability codes.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireView)],
)
async def list_capabilities(
    uow: UnitOfWork = Depends(get_uow),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    q: str | None = Query(default=None, max_length=200),
) -> MetaEnvelope[CapabilityResponse]:
    """List the canonical capability catalog with derived metadata."""
    svc = CapabilityService(uow)
    rows, total = await svc.list_capabilities(
        page=page,
        per_page=per_page,
        q=q,
    )
    data = [CapabilityResponse(**r) for r in rows]
    return MetaEnvelope[CapabilityResponse](
        data=data,
        pagination=make_pagination(page, per_page, total),
    )
