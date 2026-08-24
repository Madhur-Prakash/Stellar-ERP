"""Domain exception hierarchy and the HTTP error contract.

Services raise semantic exceptions (:class:`NotFoundError`,
:class:`PermissionDeniedError`) and never touch HTTP concerns. A single set of
handlers registered in :mod:`app.main` maps them to responses. This keeps the
domain layer transport-agnostic - the same services will back a CLI, a worker,
and a GraphQL surface without carrying ``HTTPException`` into the core.

Every error response shares one envelope, so the frontend has exactly one shape
to parse::

    {
      "error": {
        "code": "not_found",
        "message": "Organization not found",
        "details": {"resource": "Organization"},
        "request_id": "01930f4c-..."
      }
    }

``code`` is a stable machine-readable slug - clients branch on it, never on the
human-facing ``message``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import current_log_context, get_logger

log = get_logger(__name__)


# =============================================================================
# Base
# =============================================================================
class AppError(Exception):
    """Base class for every expected, domain-level failure.

    "Expected" is the operative word: these describe outcomes the product has an
    opinion about (a missing record, a duplicate email) and are logged at
    warning level. Unexpected exceptions bubble up to the catch-all handler,
    are logged with a stack trace, and become an opaque 500.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}
        self.headers = headers or {}
        super().__init__(self.message)

    def to_response(self) -> JSONResponse:
        body: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.details:
            body["error"]["details"] = self.details
        if request_id := current_log_context().get("request_id"):
            body["error"]["request_id"] = request_id

        return JSONResponse(
            status_code=self.status_code,
            content=jsonable_encoder(body),
            headers=self.headers or None,
        )


# =============================================================================
# 4xx - client errors
# =============================================================================
class ValidationError(AppError):
    """Semantic validation failure that Pydantic cannot express."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"
    message = "The submitted data is invalid"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Resource not found"

    def __init__(self, resource: str = "Resource", **kwargs: Any) -> None:
        kwargs.setdefault("details", {})["resource"] = resource
        super().__init__(f"{resource} not found", **kwargs)


class ConflictError(AppError):
    """State conflict - a duplicate, or an illegal transition."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The request conflicts with the current state"


class BusinessRuleError(AppError):
    """A domain invariant was violated (e.g. unbalanced journal entry)."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "business_rule_violation"
    message = "This action violates a business rule"


# =============================================================================
# Authentication - 401
# =============================================================================
class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"
    message = "Authentication required"

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        # RFC 6750: a 401 must say how to authenticate.
        kwargs.setdefault("headers", {})["WWW-Authenticate"] = "Bearer"
        super().__init__(message, **kwargs)


class InvalidCredentialsError(AuthenticationError):
    code = "invalid_credentials"
    # Deliberately does not distinguish "no such user" from "wrong password":
    # a precise message is an account-enumeration oracle.
    message = "Incorrect email or password"


class InvalidTokenError(AuthenticationError):
    code = "invalid_token"
    message = "Token is invalid or has expired"


class TokenExpiredError(AuthenticationError):
    code = "token_expired"
    message = "Token has expired"


class EmailNotVerifiedError(AuthenticationError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "email_not_verified"
    message = "Verify your email address to continue"


class AccountDisabledError(AuthenticationError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "account_disabled"
    message = "This account has been disabled"


class AccountLockedError(AuthenticationError):
    status_code = status.HTTP_423_LOCKED
    code = "account_locked"
    message = "Too many failed attempts. Try again later."


class TwoFactorRequiredError(AppError):
    """Password was correct, but a second factor is outstanding.

    Not a failure - a continuation. Carries the challenge id the client must
    echo back with the TOTP code.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "two_factor_required"
    message = "Two-factor authentication required"

    def __init__(self, challenge_id: str, **kwargs: Any) -> None:
        kwargs.setdefault("details", {})["challenge_id"] = challenge_id
        super().__init__(**kwargs)


# =============================================================================
# Authorization - 403
# =============================================================================
class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    message = "You do not have permission to perform this action"

    def __init__(
        self,
        permission: str | None = None,
        message: str | None = None,
        **kwargs: Any,
    ) -> None:
        if permission:
            kwargs.setdefault("details", {})["required_permission"] = permission
        super().__init__(message, **kwargs)


# =============================================================================
# 429 / 5xx
# =============================================================================
class RateLimitExceededError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limit_exceeded"
    message = "Too many requests. Slow down."

    def __init__(self, retry_after: int, **kwargs: Any) -> None:
        kwargs.setdefault("headers", {})["Retry-After"] = str(retry_after)
        kwargs.setdefault("details", {})["retry_after_seconds"] = retry_after
        super().__init__(**kwargs)


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "A dependency is unavailable. Try again shortly."


# =============================================================================
# Handlers
# =============================================================================
async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 - registered per-type

    # 5xx from our own hierarchy still deserves a stack trace.
    if exc.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        log.error(
            "application error",
            extra={"code": exc.code, "path": request.url.path, "details": exc.details},
            exc_info=True,
        )
    else:
        log.warning(
            "request rejected",
            extra={
                "code": exc.code,
                "status": exc.status_code,
                "path": request.url.path,
                "details": exc.details,
            },
        )

    return exc.to_response()


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Flatten Pydantic's error list into ``{field: message}``."""
    assert isinstance(exc, RequestValidationError)  # noqa: S101

    fields: dict[str, str] = {}
    for error in exc.errors():
        # loc is like ("body", "email"); drop the source segment.
        location = [str(part) for part in error["loc"][1:]] or [str(part) for part in error["loc"]]
        fields[".".join(location)] = error["msg"]

    log.warning(
        "request validation failed",
        extra={"path": request.url.path, "fields": fields},
    )

    return ValidationError(
        "One or more fields are invalid",
        details={"fields": fields},
    ).to_response()


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Normalise Starlette's 404/405 into our envelope."""
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101

    codes = {
        status.HTTP_404_NOT_FOUND: "not_found",
        status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
        status.HTTP_401_UNAUTHORIZED: "unauthenticated",
        status.HTTP_403_FORBIDDEN: "permission_denied",
    }
    return AppError(
        str(exc.detail),
        code=codes.get(exc.status_code, "http_error"),
        status_code=exc.status_code,
        headers=dict(exc.headers or {}),
    ).to_response()


async def _integrity_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Turn a unique/FK violation into a 409 instead of a 500.

    A race between two concurrent signups for the same email loses the
    application-level uniqueness check and lands here; the database constraint
    is the real arbiter, so honour it as a conflict.
    """
    assert isinstance(exc, IntegrityError)  # noqa: S101

    detail = str(getattr(exc.orig, "detail", "") or exc.orig or "")
    # The constraint name, logged as its own field.
    #
    # `exc_info` alone was not enough: a 409 from this handler said only "database
    # integrity violation", and working out *which* constraint fired meant reading the
    # model, the migration, and every write in the request. asyncpg carries the name on
    # the exception, so there is no reason to make anyone guess.
    constraint = getattr(exc.orig, "constraint_name", None) or getattr(exc.orig, "constraint", None)
    log.warning(
        "database integrity violation",
        extra={
            "path": request.url.path,
            "constraint": str(constraint) if constraint else "unknown",
            "detail": detail[:500],
        },
        exc_info=True,
    )
    if "unique" in detail.lower() or "duplicate key" in detail.lower():
        return ConflictError("A record with these details already exists").to_response()
    if "foreign key" in detail.lower():
        return ValidationError("References a record that does not exist").to_response()
    return ConflictError("The request could not be completed").to_response()


async def _sqlalchemy_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak SQL or connection strings to the client."""
    log.error("database error", extra={"path": request.url.path}, exc_info=True)
    return ServiceUnavailableError("A database error occurred").to_response()


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all. Logs everything, tells the client nothing but a request id."""
    log.critical(
        "unhandled exception",
        extra={"path": request.url.path, "method": request.method},
        exc_info=True,
    )
    return AppError("An unexpected error occurred").to_response()


def register_exception_handlers(app: FastAPI) -> None:
    """Wire every handler. Order is irrelevant; FastAPI dispatches by type."""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(IntegrityError, _integrity_error_handler)
    app.add_exception_handler(SQLAlchemyError, _sqlalchemy_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
