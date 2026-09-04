"""F.8 — Notification read-side service.

Provides the list / mark-read / mark-all-read operations that back the
``GET /notifications``, ``POST /notifications/{id}/mark-read`` and
``POST /notifications/mark-all-read`` endpoints (openapi.yaml §15.19).

This is the **read** API surface. The **write** surface (threshold-cross
detection + notification INSERT) lives in :mod:`app.notifications.service`
(E.10 worker). This module is strictly a consumer of whatever rows
already exist in ``notifications``.

Business rules:
* ``GET /notifications`` returns ONLY rows where ``user_id`` equals
  the authenticated caller. Broadcast/system messages (``user_id IS
  NULL``) are NOT surfaced here — they require an admin broadcast
  endpoint that is out of V1 scope.
* ``filter[is_read]`` is a strict boolean; omitted = both read and
  unread.
* ``q`` is a case-insensitive substring search over ``title`` and
  ``body``.
* ``from`` / ``to`` filter on ``created_at`` (inclusive).
* ``sort`` is whitelisted to ``created_at`` (the natural reverse-chron
  feed order) and ``id``; anything else falls back to ``created_at``.
* DB ``category`` → API ``type`` mapping (the DB CHECK does NOT allow
  ``out_of_stock`` but the OAS enum does — they mean the same thing):

  ``low_stock``           → ``low_stock``
  ``insufficient_stock``  → ``out_of_stock``
  ``below_cost``          → ``below_cost_sale``
  ``system``              → ``system``
  ``action_confirmation`` → ``action_confirmation``

  Unknown categories fall through to ``system`` so we never emit a value
  that violates the OAS enum.
"""

from __future__ import annotations

# ruff: noqa: S608  -- all interpolated string fragments are hard-coded
# identifiers (column/table names, sort direction) drawn from a fixed
# whitelist; every user-supplied value is a bind parameter.
from datetime import datetime
from typing import Any

from app.db import UnitOfWork
from app.errors import NotFound
from app.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "NotificationType",
    "NotificationService",
]


# DB ``category`` value → OAS ``Notification.type`` value.
# See class docstring for the rationale (the OAS enum uses
# ``out_of_stock`` / ``below_cost_sale`` while the DB CHECK uses
# ``insufficient_stock`` / ``below_cost``).
_CATEGORY_TO_TYPE: dict[str, str] = {
    "low_stock": "low_stock",
    "insufficient_stock": "out_of_stock",
    "below_cost": "below_cost_sale",
    "system": "system",
    "action_confirmation": "action_confirmation",
}


class NotificationType:
    """Opaque namespace for the OAS Notification.type enum values."""

    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    BELOW_COST_SALE = "below_cost_sale"
    SYSTEM = "system"
    ACTION_CONFIRMATION = "action_confirmation"


# Whitelisted sort keys. ``-`` prefix = DESC. Everything else
# falls back to a stable created_at DESC, id DESC order.
_NOTIF_SORT: dict[str, str] = {
    "id": "n.created_at DESC, n.id ASC",
    "-id": "n.created_at DESC, n.id DESC",
    "created_at": "n.created_at DESC, n.id ASC",
    "-created_at": "n.created_at DESC, n.id DESC",
}


def _notif_order_by(sort: str | None) -> str:
    return _NOTIF_SORT.get((sort or "created_at").strip(), _NOTIF_SORT["created_at"])


def _notif_where(
    *,
    is_read: bool | None,
    from_iso: datetime | None,
    to_iso: datetime | None,
    q: str | None,
) -> tuple[list[str], dict[str, Any]]:
    """Build the extra WHERE clauses + bind params for the notification list.

    The user-scope clause (``n.user_id = :uid``) is always added by the
    caller; this function returns the optional additional filter clauses
    so the final WHERE chain is well-formed.
    """

    clauses: list[str] = []
    params: dict[str, Any] = {}
    if is_read is not None:
        clauses.append("n.is_read = :is_read")
        params["is_read"] = bool(is_read)
    if from_iso is not None:
        clauses.append("n.created_at >= :from_iso")
        params["from_iso"] = from_iso
    if to_iso is not None:
        clauses.append("n.created_at <= :to_iso")
        params["to_iso"] = to_iso
    if q:
        clauses.append("(n.title ILIKE :q OR n.body ILIKE :q)")
        params["q"] = f"%{q}%"
    return clauses, params


_NOTIF_COLUMNS = (
    "n.id, n.category, n.severity, n.title, n.body, "
    "n.reference_type, n.reference_id, n.is_read, n.read_at, "
    "n.created_at"
)


def _to_response(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a raw ``notifications`` row into the OAS ``Notification`` shape.

    Applies the DB ``category`` → OAS ``type`` mapping at the read
    boundary. ``message`` is synthesised from ``title`` + ``body``:
    the OAS enum has no ``title``/``body`` fields (the schema exposes a
    single ``message`` string), so we concatenate them.
    """

    category = str(row.get("category") or "")
    notif_type = _CATEGORY_TO_TYPE.get(category, NotificationType.SYSTEM)
    title = row.get("title")
    body = row.get("body")
    message = _compose_message(title, body)
    return {
        "id": int(row["id"]),
        "type": notif_type,
        "message": message,
        "related_entity_type": row.get("reference_type"),
        "related_entity_id": (
            int(row["reference_id"]) if row.get("reference_id") is not None else None
        ),
        "is_read": bool(row["is_read"]),
        "created_at": row["created_at"],
    }


def _compose_message(title: str | None, body: str | None) -> str:
    """Build the single ``message`` field from title + body.

    The OAS ``Notification`` schema exposes ``message`` only (no
    title/body). The service stored both, so we join them. If only a
    title is present it becomes the message; if only a body it becomes
    the message; both are joined with a space.
    """

    if title and body:
        return f"{title} {body}"
    if title:
        return title
    if body:
        return body
    return ""


class NotificationService:
    """Read-side service for in-app notifications (F.8)."""

    def __init__(self, uow: UnitOfWork, user_id: int) -> None:
        self._uow = uow
        # Notifications are user-scoped. Every query is pinned to this
        # user_id so a caller can never read or mutate another user's
        # notifications. ``user_id`` is the authenticated principal's id.
        self._user_id = int(user_id)

    async def list_notifications(
        self,
        *,
        page: int,
        per_page: int,
        sort: str | None,
        is_read: bool | None,
        from_iso: datetime | None,
        to_iso: datetime | None,
        q: str | None,
    ) -> dict[str, Any]:
        """Paginated list of notifications scoped to the current user.

        Ownership filter (``user_id = :uid``) is applied on every query
        so the list can never leak another user's rows.
        """

        where, params = _notif_where(
            is_read=is_read,
            from_iso=from_iso,
            to_iso=to_iso,
            q=q,
        )
        params["uid"] = self._user_id
        # User-scope clause is always present; extra filters are
        # ANDed in. The chain is well-formed because the user-scope
        # clause is added before any optional filter.
        scope_clauses: list[str] = ["n.user_id = :uid", *where]
        scope = "WHERE " + " AND ".join(scope_clauses)
        total = await self._count(scope, params)
        rows = await self._fetch_page(
            scope,
            params,
            page=page,
            per_page=per_page,
            sort=sort,
        )
        data = [_to_response(r) for r in rows]
        from app.validation.pagination import make_pagination

        return {
            "data": data,
            "pagination": make_pagination(page=page, per_page=per_page, total=total),
        }

    async def mark_read(self, notification_id: int, *, is_read: bool = True) -> None:
        """Mark a single notification belonging to the current user.

        Raises ``NotFound`` if the notification does not exist OR does
        not belong to the current user (ownership check is folded into
        the WHERE so a foreign id yields 404, not 403 — the caller
        cannot distinguish "missing" from "not yours" by ID).
        """

        result = await self._uow.scalar(
            "UPDATE notifications "
            "SET is_read = :is_read, read_at = NOW() "
            "WHERE id = :nid AND user_id = :uid "
            "RETURNING id",
            {"nid": int(notification_id), "uid": self._user_id, "is_read": is_read},
        )
        if result is None:
            # rowCount when RETURNING produced nothing → no row matched
            # the (id, user_id) pair.
            raise NotFound(f"Notification {notification_id} not found.")

    async def mark_all_read(self) -> int:
        """Mark all of the current user's unread notifications read.

        Returns the number of rows updated.
        """

        return int(
            await self._uow.scalar(
                "UPDATE notifications "
                "SET is_read = TRUE, read_at = NOW() "
                "WHERE user_id = :uid AND is_read = FALSE",
                {"uid": self._user_id},
            )
        )

    # -------------------------------------------------------------------
    # internal
    # -------------------------------------------------------------------

    async def _count(self, where: str, params: dict[str, Any]) -> int:
        sql = f"SELECT COUNT(*) FROM notifications n {where}"
        return int(await self._uow.scalar(sql, params) or 0)

    async def _fetch_page(
        self,
        where: str,
        params: dict[str, Any],
        *,
        page: int,
        per_page: int,
        sort: str | None,
    ) -> list[dict[str, Any]]:
        order = _notif_order_by(sort)
        offset = (max(1, page) - 1) * per_page
        params.update({"limit": per_page, "offset": offset})
        sql = (
            f"SELECT {_NOTIF_COLUMNS} FROM notifications n {where} "
            f"ORDER BY {order} LIMIT :limit OFFSET :offset"
        )
        return await self._uow.fetch_all(sql, params)
