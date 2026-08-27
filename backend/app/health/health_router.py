"""Health package (re-exports the router for the API layer)."""

from __future__ import annotations

from app.health import router

__all__ = ["router"]
