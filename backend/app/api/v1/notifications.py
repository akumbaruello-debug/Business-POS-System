"""F.8 - Notifications read API.

Three read endpoints per ``openapi.yaml`` section 15.19 (lines 5790-5876):

* ``GET  /notifications``                — ``listNotifications`` (paginated)
* ``POST /notifications/{id}/mark-read`` — ``markNotificationRead``
* ``POST /notifications/mark-all-read``  — ``markAllNotificationsRead``

Authorization:
    * ``GET  /notifications``           — ``notification.view``
    * ``POST /notifications/{id}/mark-read`` — ``notification.mark_read``
    * ``POST /notifications/mark-all-read``  — ``notification.mark_read``

Both ``notification.view`` and ``notification.mark_read`` are in the
Staff default capability set (see ``app.authz.caps`` STAFF_DEFAULT_CODES),
so Owner and Staff alike may list + mark-read.

Query parameters mirror the OpenAPI ``collection_get_op`` contract:
``page``, ``per_page``, ``sort``, ``q``, ``from``, ``to`` plus the
``filter[is_read]`` boolean. Sort is whitelisted to ``created_at`` and
``id`` (ascending/descending) at the repo/service layer.

Notification type mapping (DB ``category`` → OAS ``type``):
    ``insufficient_stock`` → ``out_of_stock``
    ``below_cost``         → ``below_cost_sale``
(Resolved in the service layer, see ``app.services.notifications``.)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import current_principal, get_uow, require_capability
from app.auth.principal import Principal
from app.db import UnitOfWork
from app.services.notifications import NotificationService
from app.validation.notifications_schemas import (
    Notification,
    NotificationListResponse,
)

RequireNotificationView = require_capability("notification.view")
RequireNotificationMarkRead = require_capability("notification.mark_read")

router = APIRouter(prefix="/notifications", tags=["Notifications"])


# ---------------------------------------------------------------------------
# GET /notifications  — listNotifications
# ---------------------------------------------------------------------------


@router.get(
    "",
    operation_id="listNotifications",
    summary="List in-app notifications for the current user (paginated).",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireNotificationView)],
)
async def list_notifications(
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
    principal: Principal = Depends(current_principal),
    page: int = Query(default=1, ge=1, le=1000),
    per_page: int = Query(default=50, ge=1, le=500),
    sort: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    filter_is_read: bool | None = Query(default=None, alias="filter[is_read]"),
) -> NotificationListResponse:
    """Return notifications scoped to the authenticated user.

    ``filter[is_read]`` narrows to read (true) / unread (false) /
    both (omitted). ``q`` is a case-insensitive substring search over
    ``title`` and ``body``. ``from`` / ``to`` are inclusive bounds on
    ``created_at``. ``sort`` is whitelisted to ``created_at`` and
    ``id`` (``-`` prefix for descending).
    """

    svc = NotificationService(uow, user_id=principal.user_id)
    result = await svc.list_notifications(
        page=page,
        per_page=per_page,
        sort=sort,
        is_read=filter_is_read,
        from_iso=_parse_iso(from_iso) if from_iso is not None else None,
        to_iso=_parse_iso(to_iso) if to_iso is not None else None,
        q=q,
    )
    data = [_to_notification(r) for r in result["data"]]
    return NotificationListResponse(
        data=data,
        pagination=result["pagination"],
    )


# ---------------------------------------------------------------------------
# POST /notifications/{id}/mark-read  — markNotificationRead
# ---------------------------------------------------------------------------


@router.post(
    "/{id}/mark-read",
    operation_id="markNotificationRead",
    summary="Mark a notification as read.",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(RequireNotificationMarkRead)],
)
async def mark_notification_read(
    request: Request,
    id: int,
    uow: UnitOfWork = Depends(get_uow),
    principal: Principal = Depends(current_principal),
) -> JSONResponse:
    """Mark a single notification belonging to the current user as read.

    The ownership check is folded into the UPDATE's ``WHERE`` clause so
    a foreign/nonexistent id is indistinguishable from "not found" —
    the API returns 404 and does not leak existence.
    """

    svc = NotificationService(uow, user_id=principal.user_id)
    await svc.mark_read(id)
    await uow.commit()
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)


# ---------------------------------------------------------------------------
# POST /notifications/mark-all-read  — markAllNotificationsRead
# ---------------------------------------------------------------------------


@router.post(
    "/mark-all-read",
    operation_id="markAllNotificationsRead",
    summary="Mark all of the current user's unread notifications as read.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireNotificationMarkRead)],
)
async def mark_all_notifications_read(
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
    principal: Principal = Depends(current_principal),
) -> JSONResponse:
    """Mark every unread notification belonging to the current user as read.

    Returns the count of rows updated in the body so clients can update
    their badge without a refetch. Only the caller's own notifications
    are affected (``user_id`` is pinned in the UPDATE).
    """

    svc = NotificationService(uow, user_id=principal.user_id)
    updated = await svc.mark_all_read()
    await uow.commit()
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"updated": updated},
    )


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 timestamp into a tz-aware ``datetime``.

    Used to coerce the ``from`` / ``to`` query parameters into the
    Python type asyncpg's ``TIMESTAMPTZ`` binder expects. The F.1
    reports happen to use ``TIMESTAMP`` columns, where the DB accepts
    the string directly; ``created_at`` here is ``TIMESTAMPTZ`` and
    asyncpg rejects raw strings.
    """

    return datetime.fromisoformat(value)


def _to_notification(row: dict[str, Any]) -> Notification:
    """Coerce a raw row dict (already mapped to the OAS shape) into the
    ``Notification`` Pydantic model for the response envelope.
    """

    return Notification(**row)


__all__ = [
    "router",
]
