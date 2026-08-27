"""FastAPI exception handlers → canonical ``ErrorEnvelope`` responses.

Reference: ``Backend-Architecture-V1.0.md`` §19 Error Architecture.

Design rules enforced here:

1. **Single render point.** Every 4xx/5xx JSON response is produced
   by one of these handlers — no other code path writes an error body.
2. **Strict envelope shape.** The output matches ``openapi.yaml``
   ``ErrorEnvelope`` exactly:

       {"error": {"code": "...", "message": "...", "details": ..., "request_id": "..."}}

3. **request_id is always present**. If the exception was raised before
   the request-id middleware could attach one, we generate a fresh UUID
   so the client always has a correlation token.
4. **details is null by default.** It is present only when the error
   explicitly carries code-specific structure (e.g. validation array).
5. **Validation failures** use ``ValidationErrorEnvelope`` semantics:
   code is always ``validation_failed`` and ``details`` is an array of
   ``{field, code, message}``.
6. **No stack traces leak** to the client. The server-side log (via
   structlog) carries the traceback; the response body is generic
   ``internal_error``.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from pydantic import ValidationError
from starlette.responses import JSONResponse

from app.core.ids import new_request_id
from app.errors import AppError
from app.errors.codes import ErrorCode
from app.logging import get_logger

# ContextVar that the request-id middleware sets on entry to every
# request. Handlers read it so the correlation token is available even
# for exceptions raised deep in middleware.
from app.middleware.request_id import current_request_id

logger = get_logger("app.errors.handlers")

#: The canonical error-field names, as constants so we can't typo them.
_ERROR_KEY = "error"
_CODE = "code"
_MESSAGE = "message"
_DETAILS = "details"
_REQUEST_ID = "request_id"


def _get_request_id(request: Request) -> uuid.UUID:
    """Resolve the request-id for this request.

    Order of precedence:
      1. Value attached to the request scope by middleware (a UUID).
      2. The incoming ``X-Request-ID`` header (if valid UUID).
      3. A freshly-generated UUID.
    """
    # 1. From middleware contextvar
    rid: uuid.UUID | None = current_request_id.get(None)
    if rid is not None:
        return rid

    # 2. From the request scope (set by middleware as a raw UUID string)
    rid_raw = request.scope.get("request_id")
    if isinstance(rid_raw, str):
        try:
            return uuid.UUID(rid_raw)
        except (ValueError, AttributeError):
            pass

    # 3. Generate one so the envelope is always populated.
    return new_request_id()


def _render(
    request: Request,
    status: int,
    code: ErrorCode,
    message: str,
    details: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> JSONResponse:
    rid = _get_request_id(request)
    body: dict[str, Any] = {
        _ERROR_KEY: {
            _CODE: code.value,
            _MESSAGE: message,
            _DETAILS: details,
            _REQUEST_ID: str(rid),
        },
    }
    return JSONResponse(status_code=status, content=body)


# ---------------------------------------------------------------------------
# AppError handler (covers all subclasses, plus direct AppError raises)
# ---------------------------------------------------------------------------


def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Convert any ``AppError`` subclass into a canonical ``ErrorEnvelope``."""
    logger.warning(
        "app_error",
        code=exc.code.value,
        status=exc.status,
        message=exc.message,
        path=request.url.path,
        method=request.method,
    )
    return _render(request, exc.status, exc.code, exc.message, exc.details)


# ---------------------------------------------------------------------------
# Pydantic / FastAPI request-validation failures
# ---------------------------------------------------------------------------


def _pydantic_to_validation_items(
    raw: Any,
) -> list[dict[str, str]]:
    """Convert raw ``ValidationError.errors()`` entries into the OpenAPI
    ``ValidationErrorEnvelope`` detail format:
    ``[{field, code, message}]``.
    """
    items: list[dict[str, str]] = []
    for err in raw:
        # Pydantic v2 returns ValidationErrorDetailDict (a dict subclass).
        # We coerce to a plain dict for uniform access.
        err_dict: dict[str, Any] = dict(err) if not isinstance(err, dict) else err
        loc: Any = err_dict.get("loc") or []
        field = ".".join(str(p) for p in loc) or "body"
        msg = err_dict.get("msg", "validation error")
        etype = err_dict.get("type", "invalid")
        # Normalise type to a code-ish string.
        code = str(etype).split(".")[-1]
        items.append({"field": field, "code": code, "message": str(msg)})
    return items


def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Handle FastAPI's body / query / path validation errors.

    Per ``openapi.yaml``: 400 with ``ValidationErrorEnvelope`` (code =
    ``validation_failed``).
    """
    items = _pydantic_to_validation_items(exc.errors())
    # Pydantic v2 also attaches ``input`` to each error; the OpenAPI
    # schema only requires field/code/message, so we omit input.
    return _render(
        request,
        400,
        ErrorCode.VALIDATION_FAILED,
        "Request validation failed.",
        items,
    )


def pydantic_validation_handler(
    request: Request,
    exc: ValidationError,
) -> JSONResponse:
    """Handle raw ``pydantic.ValidationError`` (e.g. from settings, or
    service-layer model construction).

    We use the same shape as request validation (400 + validation_failed
    + array) so clients see one consistent failure mode.
    """
    items = _pydantic_to_validation_items(exc.errors())
    return _render(
        request,
        400,
        ErrorCode.VALIDATION_FAILED,
        "Request validation failed.",
        items,
    )


# ---------------------------------------------------------------------------
# HTTPException (from dependencies / Starlette middleware)
# ---------------------------------------------------------------------------


def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Map Starlette ``HTTPException`` into the canonical envelope.

    We never raise ``HTTPException`` from business code — they come from
    framework plumbing (404 routing, 413 body-size middleware, etc.).
    The status code is mapped to the closest canonical code.
    """
    status = exc.status_code
    match status:
        case 401:
            code = ErrorCode.UNAUTHENTICATED
            msg = exc.detail or "Authentication required."
        case 403:
            code = ErrorCode.PERMISSION_DENIED
            msg = exc.detail or "You do not have permission to perform this action."
        case 404:
            code = ErrorCode.NOT_FOUND
            msg = exc.detail or "Resource not found."
        case 409:
            code = ErrorCode.CONFLICT
            msg = exc.detail or "Conflict."
        case 412:
            code = ErrorCode.VERSION_MISMATCH
            msg = exc.detail or "Resource version mismatch. Refresh and retry."
        case 413:
            code = ErrorCode.PAYLOAD_TOO_LARGE
            msg = exc.detail or "Request body too large."
        case 415:
            code = ErrorCode.UNSUPPORTED_MEDIA_TYPE
            msg = exc.detail or "Unsupported media type."
        case 429:
            code = ErrorCode.RATE_LIMITED
            msg = exc.detail or "Too many requests. Please slow down."
        case _:
            code = ErrorCode.INTERNAL_ERROR
            msg = exc.detail or "An internal error occurred."
    return _render(request, status, code, msg, dict(exc.headers) if exc.headers else None)


# ---------------------------------------------------------------------------
# Fallback — catches everything else, logs, returns 500
# ---------------------------------------------------------------------------


def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler. Logs the traceback, returns a generic 500."""
    rid = _get_request_id(request)
    logger.exception(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        request_id=str(rid),
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": "An internal error occurred.",
                "details": None,
                "request_id": str(rid),
            },
        },
    )


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_error_handlers(app: FastAPI) -> None:
    """Attach all handlers to the FastAPI app."""
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.exception_handler(ValidationError)(pydantic_validation_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    # Fallback must be last (Starlette uses the first match, so the
    # broad ``Exception`` handler must be registered after all specific ones).
    app.add_exception_handler(Exception, unhandled_handler)
