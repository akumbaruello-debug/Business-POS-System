"""F.8 — Notification response schemas.

Mirrors ``openapi.yaml`` ``Notification`` (line 8931) exactly:

* ``id`` — bigint PK from the ``notifications`` table.
* ``type`` — the OpenAPI enum (``low_stock``, ``out_of_stock``,
  ``below_cost_sale``, ``system``, ``action_confirmation``).
  This is the **API read-model** value, distinct from the DB
  ``category`` CHECK (see the DB→API category mapping in
  :mod:`app.services.notifications`).
* ``message`` — display string for the client.
* ``related_entity_type`` / ``related_entity_id`` — the trigger
  reference (nullable).
* ``is_read`` / ``created_at`` as stored.

The ``user_id`` FK is intentionally NOT in the response envelope
(notifications are scoped to the authenticated user on read).
"""

from __future__ import annotations

from datetime import datetime
from typing import TypeVar

from pydantic import BaseModel, ConfigDict

from app.validation.pagination import MetaEnvelope, Pagination

T = TypeVar("T")


class Notification(BaseModel):
    """A single in-app notification. Mirrors openapi.yaml ``Notification``."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    type: str
    message: str
    related_entity_type: str | None = None
    related_entity_id: int | None = None
    is_read: bool
    created_at: datetime


class NotificationListResponse(MetaEnvelope[Notification]):
    """``GET /notifications`` response envelope.

    ``MetaEnvelope`` is generic over ``data`` + ``pagination``; this
    subclass fixes the item type to ``Notification`` so the route
    signature is typed and OpenAPI generation picks up the right schema.
    """


# Re-export the pagination type for convenience at call sites.
__all__ = [
    "Notification",
    "NotificationListResponse",
    "MetaEnvelope",
    "Pagination",
]
