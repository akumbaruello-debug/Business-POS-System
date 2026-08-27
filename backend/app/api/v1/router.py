"""API v1 router — composes sub-routers for M1 + future modules."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth

router = APIRouter()
router.include_router(auth.router)

__all__ = ["router"]
