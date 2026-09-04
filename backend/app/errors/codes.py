"""Canonical error code catalog.

The OpenAPI spec lists dozens of error codes via ``x-error-codes``
extensions. Rather than duplicate those as raw strings, we centralize
them here as constants. The set of codes is the union of those listed
in:

* ``openapi.yaml``  ``x-error-codes`` annotations
* ``API-Architecture-V1.0.md`` §13 Error Architecture
* ``Backend-Architecture-V1.0.md`` §19 Error Architecture

Adding a new code is a contract change and must be reflected in the
OpenAPI spec + the catalog here. Removing a code is a breaking change.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Canonical error codes returned in ``error.code``.

    The string value is the public contract; the member name is for
    in-code use. Names are grouped by HTTP family for readability.
    """

    # ---- 400 Bad Request ---------------------------------------------
    VALIDATION_FAILED = "validation_failed"
    BAD_REQUEST = "bad_request"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    MISSING_HEADER = "missing_header"
    INVALID_HEADER = "invalid_header"
    ENUM_VALUE_INVALID = "enum_value_invalid"
    DERIVED_FIELD_NOT_ALLOWED = "derived_field_not_allowed"
    MANUAL_ENTRY_DUPLICATE_OF_DERIVED = "manual_entry_duplicate_of_derived"
    TENDERED_NOT_ALLOWED_FOR_NON_CASH = "tendered_not_allowed_for_non_cash"
    REFUND_EXCEEDS_CRL = "refund_exceeds_crl"
    REPAYMENT_EXCEEDS_SREC = "repayment_exceeds_srec"
    ALLOCATION_EXCEEDS_PAYABLE = "allocation_exceeds_payable"
    PURCHASE_RETURN_QUANTITY_EXCEEDS_ORIGINAL = "purchase_return_quantity_exceeds_original"

    # ---- 401 Unauthorized --------------------------------------------
    UNAUTHENTICATED = "unauthenticated"
    INVALID_CREDENTIALS = "invalid_credentials"
    SESSION_EXPIRED = "session_expired"
    SESSION_REVOKED = "session_revoked"
    PASSWORD_UNSET = "password_unset"  # noqa: S105

    # ---- 403 Forbidden ------------------------------------------------
    PERMISSION_DENIED = "permission_denied"
    ORIGIN_NOT_ALLOWED = "origin_not_allowed"

    # ---- 404 Not Found -----------------------------------------------
    NOT_FOUND = "not_found"
    ROUTE_NOT_FOUND = "route_not_found"

    # ---- 409 Conflict ------------------------------------------------
    CONFLICT = "conflict"
    USERNAME_EXISTS = "username_exists"
    EMAIL_EXISTS = "email_exists"
    IDEMPOTENCY_VIOLATION = "idempotency_violation"
    REFERENCED_BY_HISTORY = "referenced_by_history"
    IN_USE = "in_use"
    DELETE_NOT_PERMITTED = "delete_not_permitted"
    LIFECYCLE_STATE_INVALID = "lifecycle_state_invalid"

    # ---- 412 Precondition Failed -------------------------------------
    VERSION_MISMATCH = "version_mismatch"

    # ---- 422 Unprocessable Entity ------------------------------------
    UNPROCESSABLE_ENTITY = "unprocessable_entity"
    BUSINESS_RULE_VIOLATION = "business_rule_violation"
    LIFECYCLE_VIOLATION = "lifecycle_violation"
    INSUFFICIENT_STOCK = "insufficient_stock"
    INSUFFICIENT_CASH = "insufficient_cash"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    BELOW_COST = "below_cost"
    PRODUCTION_INPUTS_EXCEED_STOCK = "production_inputs_exceed_stock"
    PRODUCTION_FINISHED_COST_INVALID = "production_finished_cost_invalid"
    NEGATIVE_STOCK_DISALLOWED = "negative_stock_disallowed"

    # ---- 423 Locked ---------------------------------------------------
    ACCOUNT_LOCKED = "account_locked"

    # ---- 429 Too Many Requests ---------------------------------------
    RATE_LIMITED = "rate_limited"

    # ---- 5xx Server Error --------------------------------------------
    INTERNAL_ERROR = "internal_error"
    DATABASE_ERROR = "database_error"
    SERVICE_UNAVAILABLE = "service_unavailable"


__all__ = ["ErrorCode"]
