"""Shared FastAPI dependencies (auth, db, ip, etc.)."""

from __future__ import annotations

from app.authz.deps import (
    current_principal,
    extract_bearer_token,
    require_authenticated,
    require_capability,
)
from app.db import UnitOfWork, get_session, get_uow

__all__ = [
    "UnitOfWork",
    "current_principal",
    "extract_bearer_token",
    "get_session",
    "get_uow",
    "require_authenticated",
    "require_capability",
]
