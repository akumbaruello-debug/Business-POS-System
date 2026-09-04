"""Request/response schemas for Contacts (M3).

Mirrors ``openapi.yaml`` §Contacts exactly.

DB vs OpenAPI note:
- The DB ``contacts`` table has ``id SERIAL``, ``type``, ``name``,
  ``phone``, ``email``, ``address``, ``notes``, ``is_active``,
  ``created_at``, ``updated_at``, ``created_by`` (FK users). No
  ``version`` column.
- The OpenAPI ``Contact`` schema exposes ``version`` (integer), derived
  server-side from ``updated_at`` via
  :func:`app.concurrency.master_etag.etag_and_version_from_updated_at`
  (the standard master-data pattern per ``Backend-Architecture-V1.0.md``
  §12).

Email validation uses a Pydantic ``pattern`` matching the DB CHECK
(``ck_contacts_email_format``) rather than ``EmailStr``, so the project
does not require the optional ``email-validator`` dependency.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.validation.enums import ContactType

#: Mirrors `schema.sql` ck_contacts_email_format CHECK constraint.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ContactBase(BaseModel):
    """Common fields for contact create/patch."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    type: ContactType = Field(..., description="customer | supplier | both")
    name: str = Field(..., min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(
        default=None, max_length=150, pattern=_EMAIL_PATTERN
    )
    address: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class ContactRequest(ContactBase):
    """Body for ``POST /contacts``.

    Per OpenAPI: ``type`` and ``name`` are required. ``is_active``
    defaults to true. ``created_by`` is NOT accepted from clients — it
    is always the authenticated principal.
    """

    is_active: bool = True


class ContactPatch(BaseModel):
    """Body for ``PATCH /contacts/{id}``.

    Per OpenAPI: all fields optional. ``type`` and ``name`` remain
    mutable; ``is_active`` can be toggled. Server-owned fields (``id``,
    ``created_at``, ``updated_at``, ``created_by``, ``version``) are
    rejected if present (enforced by the route via extra="forbid").
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    type: ContactType | None = Field(default=None)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(
        default=None, max_length=150, pattern=_EMAIL_PATTERN
    )
    address: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = Field(default=None)


class ContactResponse(BaseModel):
    """Response shape for contact endpoints (matches ``Contact`` in openapi.yaml)."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: int = Field(..., ge=1)
    type: ContactType
    name: str
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    notes: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    version: int = Field(..., description="Derived from updated_at; not a DB column.")
    created_by: int | None = Field(default=None)


__all__ = [
    "ContactBase",
    "ContactRequest",
    "ContactPatch",
    "ContactResponse",
]
