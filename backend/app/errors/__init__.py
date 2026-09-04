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
    "AllocationExceedsPayable",
    "AppError",
    "BelowCost",
    "BusinessRuleViolation",
    "Conflict",
    "DeleteNotPermitted",
    "IdempotencyViolation",
    "InsufficientCash",
    "InsufficientStock",
    "InternalError",
    "InvalidCredentials",
    "InvalidHeader",
    "LifecycleStateInvalid",
    "LifecycleViolation",
    "ManualEntryDuplicateOfDerived",
    "MissingHeader",
    "NegativeStockDisallowed",
    "NotFound",
    "OriginNotAllowed",
    "PasswordUnset",
    "PermissionDenied",
    "ProductionFinishedCostInvalid",
    "ProductionInputsExceedStock",
    "RateLimited",
    "ReferencedByHistory",
    "RefundExceedsCRL",
    "RepaymentExceedsSREC",
    "SessionExpired",
    "SessionRevoked",
    "TenderedNotAllowedForNonCash",
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
    ErrorCode.DERIVED_FIELD_NOT_ALLOWED: 400,
    ErrorCode.MANUAL_ENTRY_DUPLICATE_OF_DERIVED: 400,
    ErrorCode.TENDERED_NOT_ALLOWED_FOR_NON_CASH: 400,
    ErrorCode.REFUND_EXCEEDS_CRL: 409,
    ErrorCode.REPAYMENT_EXCEEDS_SREC: 409,
    ErrorCode.ALLOCATION_EXCEEDS_PAYABLE: 409,
    ErrorCode.PURCHASE_RETURN_QUANTITY_EXCEEDS_ORIGINAL: 409,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.INVALID_CREDENTIALS: 401,
    ErrorCode.SESSION_EXPIRED: 401,
    ErrorCode.SESSION_REVOKED: 401,
    ErrorCode.PASSWORD_UNSET: 401,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.ORIGIN_NOT_ALLOWED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.ROUTE_NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.USERNAME_EXISTS: 409,
    ErrorCode.EMAIL_EXISTS: 409,
    ErrorCode.IDEMPOTENCY_VIOLATION: 409,
    ErrorCode.REFERENCED_BY_HISTORY: 409,
    ErrorCode.IN_USE: 409,
    ErrorCode.DELETE_NOT_PERMITTED: 409,
    ErrorCode.LIFECYCLE_STATE_INVALID: 409,
    ErrorCode.VERSION_MISMATCH: 412,
    ErrorCode.UNPROCESSABLE_ENTITY: 422,
    ErrorCode.BUSINESS_RULE_VIOLATION: 422,
    ErrorCode.LIFECYCLE_VIOLATION: 422,
    ErrorCode.INSUFFICIENT_STOCK: 422,
    ErrorCode.INSUFFICIENT_CASH: 422,
    ErrorCode.INSUFFICIENT_BALANCE: 422,
    ErrorCode.BELOW_COST: 422,
    ErrorCode.PRODUCTION_INPUTS_EXCEED_STOCK: 409,
    ErrorCode.PRODUCTION_FINISHED_COST_INVALID: 409,
    ErrorCode.NEGATIVE_STOCK_DISALLOWED: 422,
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


class OriginNotAllowed(AppError):
    """403 — the request's Origin header is missing or not in the
    configured allow-list.

    Raised by ``OriginCheckMiddleware`` on state-changing requests
    (POST/PUT/PATCH/DELETE) when ``BPOS_CORS_ALLOWED_ORIGINS`` is
    configured and the request Origin does not match.
    """

    code = ErrorCode.ORIGIN_NOT_ALLOWED
    default_message = "Origin not allowed."


class NotFound(AppError):
    code = ErrorCode.NOT_FOUND
    default_message = "Resource not found."


class Conflict(AppError):
    code = ErrorCode.CONFLICT
    default_message = "Conflict."


class DeleteNotPermitted(Conflict):
    code = ErrorCode.DELETE_NOT_PERMITTED
    status = 409
    default_message = "This resource cannot be deleted."


class ReferencedByHistory(Conflict):
    """409 — the resource is referenced by historical/transactional data.

    Raised when a hard-delete is blocked because other rows reference
    this one (e.g. a unit referenced by products). The DB FK constraint
    is the authoritative guard; this exception gives a uniform error
    envelope instead of a raw constraint message.
    """

    code = ErrorCode.REFERENCED_BY_HISTORY
    status = 409
    default_message = "This resource is referenced by history and cannot be deleted."


class IdempotencyViolation(AppError):
    code = ErrorCode.IDEMPOTENCY_VIOLATION
    default_message = "Idempotency-Key has been used with a different request body."


class LifecycleStateInvalid(Conflict):
    """409 — the entity is not in a postable lifecycle state.

    Covers: not-draft (already posted), cancelled, no inputs,
    deactivated input product, etc.
    """

    code = ErrorCode.LIFECYCLE_STATE_INVALID
    status = 409
    default_message = "Operation is not allowed in the current lifecycle state."


class ProductionInputsExceedStock(Conflict):
    """409 — a raw input's requested quantity exceeds on-hand stock."""

    code = ErrorCode.PRODUCTION_INPUTS_EXCEED_STOCK
    status = 409
    default_message = "Production inputs exceed available on-hand stock."


class ProductionFinishedCostInvalid(Conflict):
    """409 — computed finished_unit_cost is zero/non-positive or
    output_quantity is not positive.
    """

    code = ErrorCode.PRODUCTION_FINISHED_COST_INVALID
    status = 409
    default_message = "Computed finished unit cost is invalid."


class NegativeStockDisallowed(AppError):
    """422 — a stock decrement would drive on-hand below zero and the
    product (or global default) does not permit negative stock.

    Raised by ``POST /inventory/adjustments`` (E.6) when the requested
    signed quantity would push on-hand below zero and negative stock is
    not allowed for that product (per BR-STOCK-010/011).
    """

    code = ErrorCode.NEGATIVE_STOCK_DISALLOWED
    status = 422
    default_message = (
        "Negative stock is not allowed for this product. "
        "Enable allow_negative_stock on the product or the global default."
    )


class ManualEntryDuplicateOfDerived(AppError):
    code = ErrorCode.MANUAL_ENTRY_DUPLICATE_OF_DERIVED
    status = 400
    default_message = (
        "Cannot create or rename a financial category to match a derived accounting category."
    )


class TenderedNotAllowedForNonCash(AppError):
    code = ErrorCode.TENDERED_NOT_ALLOWED_FOR_NON_CASH
    status = 400
    default_message = "Tendered amount is not allowed for a non-cash payment method."


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


class RefundExceedsCRL(AppError):
    code = ErrorCode.REFUND_EXCEEDS_CRL
    status = 409
    default_message = "Refund exceeds the current refundable balance (CRL)."


class AllocationExceedsPayable(AppError):
    code = ErrorCode.ALLOCATION_EXCEEDS_PAYABLE
    status = 409
    default_message = "Purchase payment allocation exceeds the outstanding payable."


class RepaymentExceedsSREC(AppError):
    code = ErrorCode.REPAYMENT_EXCEEDS_SREC
    status = 409
    default_message = "Supplier repayment exceeds the current supplier receivable (SREC)."


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
