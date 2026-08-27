"""Typed exception hierarchy.

Every raised business / validation / system error is an instance of
``AppError`` (or one of its subclasses) so that the FastAPI exception
handlers in ``app.errors.handlers`` can convert it deterministically
into a canonical ``ErrorEnvelope``.

Why a custom hierarchy (and not raising ``HTTPException``)?

* ``AppError`` is **framework-agnostic**: it carries the code, message,
  HTTP status, and details — no Starlette coupling. That makes it
  trivial to surface the same errors from background jobs, scripts,
  or tests.
* Subclasses are **type-checkable**; mypy will warn you if you forget
  to handle a particular error class.
* The envelope layer (see ``app.errors.handlers``) is the only
  place that knows about HTTP. Business code never touches status
  codes.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from app.errors.codes import ErrorCode

__all__ = [
    "AccountLocked",
    "AppError",
    "BelowCost",
    "BusinessRuleViolation",
    "Conflict",
    "IdempotencyViolation",
    "InsufficientCash",
    "InsufficientStock",
    "InternalError",
    "InvalidCredentials",
    "InvalidHeader",
    "LifecycleViolation",
    "MissingHeader",
    "NotFound",
    "PasswordUnset",
    "PermissionDenied",
    "RateLimited",
    "SessionExpired",
    "SessionRevoked",
    "Unauthenticated",
    "ValidationFailed",
    "VersionMismatch",
]


# Default mapping of ErrorCode -> HTTP status. We keep this tight; the
# only way to deviate is to pass an explicit ``status`` to AppError.
DEFAULT_STATUS: Final[dict[ErrorCode, int]] = {
    ErrorCode.VALIDATION_FAILED: 400,
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.MISSING_HEADER: 400,
    ErrorCode.INVALID_HEADER: 400,
    ErrorCode.ENUM_VALUE_INVALID: 400,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.INVALID_CREDENTIALS: 401,
    ErrorCode.SESSION_EXPIRED: 401,
    ErrorCode.SESSION_REVOKED: 401,
    ErrorCode.PASSWORD_UNSET: 401,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.ROUTE_NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.USERNAME_EXISTS: 409,
    ErrorCode.EMAIL_EXISTS: 409,
    ErrorCode.IDEMPOTENCY_VIOLATION: 409,
    ErrorCode.REFERENCED_BY_HISTORY: 409,
    ErrorCode.IN_USE: 409,
    ErrorCode.VERSION_MISMATCH: 412,
    ErrorCode.UNPROCESSABLE_ENTITY: 422,
    ErrorCode.BUSINESS_RULE_VIOLATION: 422,
    ErrorCode.LIFECYCLE_VIOLATION: 422,
    ErrorCode.INSUFFICIENT_STOCK: 422,
    ErrorCode.INSUFFICIENT_CASH: 422,
    ErrorCode.INSUFFICIENT_BALANCE: 422,
    ErrorCode.BELOW_COST: 422,
    ErrorCode.ACCOUNT_LOCKED: 423,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.DATABASE_ERROR: 500,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
}


class AppError(Exception):
    """Base class for all application errors.

    Subclassed errors set ``code`` and (optionally) ``details`` and
    ``status``. The ``request_id`` is injected by the handler; the
    constructor does not require it.
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status: int = 500
    default_message: str = "An internal error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | list[dict[str, Any]] | None = None,
        status: int | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        super().__init__(message or self.default_message)
        self.message: str = message or self.default_message
        self.details: dict[str, Any] | list[dict[str, Any]] | None = details
        self.status = status if status is not None else DEFAULT_STATUS[self.code]
        self.request_id = request_id

    def with_request_id(self, request_id: uuid.UUID) -> AppError:
        """Return a copy carrying the given request_id (for handler use)."""
        new = self.__class__(self.message, details=self.details, status=self.status)
        new.request_id = request_id
        return new

    def to_envelope(self, request_id: uuid.UUID) -> dict[str, Any]:
        """Render the error as the canonical envelope JSON-serializable body."""
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "details": self.details,
                "request_id": str(request_id),
            },
        }


# ---------------------------------------------------------------------------
# Concrete subclasses
# ---------------------------------------------------------------------------


class ValidationFailed(AppError):
    code = ErrorCode.VALIDATION_FAILED
    status = 400
    default_message = "Request validation failed."

    def __init__(
        self,
        message: str | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        super().__init__(message, details=errors, request_id=request_id)
        self.errors = errors or []


class Unauthenticated(AppError):
    code = ErrorCode.UNAUTHENTICATED
    default_message = "Authentication required."


class InvalidCredentials(AppError):
    code = ErrorCode.INVALID_CREDENTIALS
    default_message = "Invalid username or password."


class SessionExpired(AppError):
    code = ErrorCode.SESSION_EXPIRED
    default_message = "Session expired. Please log in again."


class SessionRevoked(AppError):
    code = ErrorCode.SESSION_REVOKED
    default_message = "Session was revoked."


class PasswordUnset(AppError):
    code = ErrorCode.PASSWORD_UNSET
    default_message = "Owner password has not been set. Run the initialization script."


class PermissionDenied(AppError):
    code = ErrorCode.PERMISSION_DENIED
    default_message = "You do not have permission to perform this action."


class NotFound(AppError):
    code = ErrorCode.NOT_FOUND
    default_message = "Resource not found."


class Conflict(AppError):
    code = ErrorCode.CONFLICT
    default_message = "Conflict."


class IdempotencyViolation(AppError):
    code = ErrorCode.IDEMPOTENCY_VIOLATION
    default_message = "Idempotency-Key has been used with a different request body."


class VersionMismatch(AppError):
    code = ErrorCode.VERSION_MISMATCH
    status = 412
    default_message = "Resource version mismatch. Refresh and retry."


class BusinessRuleViolation(AppError):
    code = ErrorCode.BUSINESS_RULE_VIOLATION
    default_message = "Operation violates a business rule."


class LifecycleViolation(AppError):
    code = ErrorCode.LIFECYCLE_VIOLATION
    default_message = "Operation is not allowed in the current lifecycle state."


class InsufficientStock(AppError):
    code = ErrorCode.INSUFFICIENT_STOCK
    default_message = "Insufficient stock."


class InsufficientCash(AppError):
    code = ErrorCode.INSUFFICIENT_CASH
    default_message = "Insufficient cash on hand."


class BelowCost(AppError):
    code = ErrorCode.BELOW_COST
    default_message = "Price is below cost."


class AccountLocked(AppError):
    code = ErrorCode.ACCOUNT_LOCKED
    default_message = "Account is temporarily locked. Try again later."


class RateLimited(AppError):
    code = ErrorCode.RATE_LIMITED
    default_message = "Too many requests. Please slow down."


class MissingHeader(AppError):
    code = ErrorCode.MISSING_HEADER
    default_message = "A required HTTP header is missing."


class InvalidHeader(AppError):
    code = ErrorCode.INVALID_HEADER
    default_message = "An HTTP header is invalid."


class InternalError(AppError):
    code = ErrorCode.INTERNAL_ERROR
    default_message = "An internal error occurred."
