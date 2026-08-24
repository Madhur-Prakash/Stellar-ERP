"""Authentication and authorization dependencies.

The per-request cost of authenticating is deliberately budgeted:

1. **Decode and verify the JWT** - no I/O.
2. **One Redis round trip** (pipelined) checking the user's token epoch and
   whether this specific session was revoked. This is what makes revocation
   effective within milliseconds instead of within the token's TTL.
3. **One indexed primary-key lookup** for the user row, so a deactivated or
   deleted account cannot keep acting on a still-valid token.

Permissions are read from the token, not the database - they were embedded at
issue time, so authorization itself costs nothing. Staleness is bounded by the
15-minute access-token TTL, and anything that must take effect immediately
(role change, suspension) bumps the epoch.

Usage::

    @router.get("/invoices")
    async def list_invoices(
        user: CurrentUser,
        _: Annotated[None, Depends(require_permission(Permission.INVOICE_READ))],
    ) -> list[InvoiceRead]: ...
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.exceptions import (
    AccountDisabledError,
    AuthenticationError,
    EmailNotVerifiedError,
    InvalidTokenError,
    PermissionDeniedError,
)
from app.core.logging import get_logger, set_log_context
from app.core.redis import RedisKey, get_redis
from app.core.security import decode_access_token
from app.db.session import get_db
from app.modules.auth.models import UserSession
from app.modules.auth.repository import SessionRepository
from app.modules.auth.service import AuthService
from app.modules.organizations.clock import organization_today
from app.modules.rbac.permissions import Permission
from app.modules.users.models import User
from app.modules.users.repository import UserRepository

log = get_logger(__name__)

#: ``auto_error=False`` so a missing header produces our own error envelope
#: rather than Starlette's bare ``{"detail": "Not authenticated"}``.
bearer_scheme = HTTPBearer(auto_error=False, description="Access token")

#: Name of the refresh-token cookie. HttpOnly, so JavaScript cannot read it.
REFRESH_COOKIE_NAME = "personalerp_refresh"


# =============================================================================
# Building blocks
# =============================================================================
def get_request_context(request: Request) -> RequestContext:
    """Caller origin, for audit rows and device history."""
    return RequestContext.from_request(request)


RequestCtx = Annotated[RequestContext, Depends(get_request_context)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_auth_service(session: DbSession) -> AuthService:
    return AuthService(session)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_token_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict[str, Any]:
    """Verify the bearer token and return its claims."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Not authenticated")
    return decode_access_token(credentials.credentials)


TokenClaims = Annotated[dict[str, Any], Depends(get_token_claims)]


async def _assert_token_still_valid(claims: dict[str, Any]) -> None:
    """Reject tokens invalidated after issue.

    Both checks go out in one pipeline - two sequential round trips would double
    the Redis latency on the hot path of every request.
    """
    user_id = claims["sub"]
    session_id = claims.get("sid", "")

    pipe = get_redis().pipeline()
    pipe.get(RedisKey.user_token_epoch(user_id))
    pipe.exists(RedisKey.revoked_session(str(session_id)))
    stored_epoch, session_revoked = await pipe.execute()

    # A token minted before the epoch was bumped is stale - password change,
    # "sign out everywhere", role change, or deactivation.
    if int(stored_epoch or 0) != int(claims.get("epoch", 0)):
        log.info(
            "token rejected: stale epoch",
            extra={"user_id": user_id, "token_epoch": claims.get("epoch")},
        )
        raise InvalidTokenError("Session is no longer valid. Please sign in again.")

    if session_revoked:
        log.info("token rejected: session revoked", extra={"session_id": str(session_id)})
        raise InvalidTokenError("This session was signed out.")


# =============================================================================
# The current principal
# =============================================================================
async def get_current_user(
    claims: TokenClaims,
    session: DbSession,
    request: Request,
) -> User:
    """Resolve the authenticated user.

    Also binds the caller's identity into the logifyx context, so every log line
    emitted downstream in this request carries ``user_id`` and ``org_id`` without
    any handler having to pass them along.
    """
    await _assert_token_still_valid(claims)

    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise InvalidTokenError("Malformed token subject") from exc

    user = await UserRepository(session).get(user_id)
    if user is None:
        # Valid signature, missing user: the account was deleted after issue.
        raise InvalidTokenError("Account no longer exists")
    if not user.can_authenticate:
        raise AccountDisabledError()

    organization_id = claims.get("org")
    set_log_context(user_id=str(user.id), org_id=organization_id)

    # Stashed for handlers and middleware that need them without re-deriving.
    request.state.user = user
    request.state.claims = claims

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_verified_user(user: CurrentUser) -> User:
    """A user who has confirmed their email.

    Applied to endpoints that act on business data. Unverified accounts can still
    reach profile and verification endpoints so they are able to recover.
    """
    if not user.is_email_verified:
        raise EmailNotVerifiedError()
    return user


VerifiedUser = Annotated[User, Depends(get_verified_user)]


async def get_current_session(claims: TokenClaims, session: DbSession) -> UserSession:
    """Load the session row backing this token.

    Only for endpoints that genuinely need it (logout, org switch, session list).
    Most requests should not pay for this query.
    """
    session_id = claims.get("sid")
    if not session_id:
        raise InvalidTokenError("Token has no session")

    try:
        user_session = await SessionRepository(session).get(uuid.UUID(session_id))
    except ValueError as exc:
        raise InvalidTokenError("Malformed session id") from exc

    if user_session is None or not user_session.is_valid():
        raise InvalidTokenError("Session is no longer valid. Please sign in again.")

    return user_session


CurrentSession = Annotated[UserSession, Depends(get_current_session)]


# =============================================================================
# Organization scope
# =============================================================================
async def get_active_organization_id(claims: TokenClaims) -> uuid.UUID:
    """The organization this request operates in, from the token.

    Trustworthy because the token is signed: a client cannot point itself at an
    organization it does not belong to, since the claim was written server-side
    after checking membership.
    """
    organization_id = claims.get("org")
    if not organization_id:
        raise PermissionDeniedError(
            message="No active organization. Create or join one to continue.",
            code="no_active_organization",
        )
    try:
        return uuid.UUID(organization_id)
    except ValueError as exc:
        raise InvalidTokenError("Malformed organization claim") from exc


ActiveOrganizationId = Annotated[uuid.UUID, Depends(get_active_organization_id)]


async def get_token_permissions(claims: TokenClaims) -> frozenset[str]:
    return frozenset(claims.get("perms") or [])


TokenPermissions = Annotated[frozenset[str], Depends(get_token_permissions)]


# =============================================================================
# Authorization
# =============================================================================
def require_permission(
    *required: Permission | str,
    require_all: bool = True,
) -> Callable[..., Awaitable[None]]:
    """Build a dependency enforcing one or more permissions.

    ``require_all=True`` (the default) demands every listed permission;
    ``False`` accepts any one of them. Defaulting to AND is the safe direction -
    a misread of the call site then denies access rather than granting it.

    Depends on :func:`get_current_user` rather than the raw claims so that
    account status is re-checked and the log context is bound even on endpoints
    that never touch the user object.
    """
    expected = tuple(str(permission) for permission in required)

    async def _check(
        user: CurrentUser,
        permissions: TokenPermissions,
        organization_id: ActiveOrganizationId,
    ) -> None:
        held = permissions
        satisfied = (
            all(permission in held for permission in expected)
            if require_all
            else any(permission in held for permission in expected)
        )

        if not satisfied:
            missing = [p for p in expected if p not in held]
            log.warning(
                "permission denied",
                extra={
                    "user_id": str(user.id),
                    "organization_id": str(organization_id),
                    "required": list(expected),
                    "missing": missing,
                },
            )
            raise PermissionDeniedError(
                permission=missing[0] if missing else expected[0],
                details={"required": list(expected), "missing": missing},
            )

    return _check


def require_superuser() -> Callable[..., Awaitable[None]]:
    """Platform-staff-only gate. Independent of organization membership."""

    async def _check(user: CurrentUser) -> None:
        if not user.is_superuser:
            log.warning("superuser endpoint denied", extra={"user_id": str(user.id)})
            raise PermissionDeniedError(
                message="This action requires platform administrator access"
            )

    return _check


# =============================================================================
# Optional authentication
# =============================================================================
async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: DbSession,
) -> User | None:
    """Resolve the user if a valid token is present, else ``None``.

    For endpoints that behave differently when signed in but do not require it.
    Any token problem yields ``None`` rather than an error - the caller opted into
    "might be anonymous".
    """
    if credentials is None or not credentials.credentials:
        return None

    try:
        claims = decode_access_token(credentials.credentials)
        await _assert_token_still_valid(claims)
        user = await UserRepository(session).get(uuid.UUID(claims["sub"]))
    except Exception:
        return None

    if user is None or not user.can_authenticate:
        return None

    set_log_context(user_id=str(user.id), org_id=claims.get("org"))
    return user


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


# =============================================================================
# The organization's calendar
# =============================================================================
async def get_organization_today(
    organization_id: ActiveOrganizationId, session: DbSession
) -> dt.date:
    """Today, in the organization's own timezone.

    Not ``dt.date.today()``, which is *the server's* today. At 00:30 in Asia/Kolkata a
    server running in UTC still calls it yesterday, so a balance sheet "as at today" would
    omit the whole of the current day for the first five and a half hours of it - and on
    1 April it moves the financial year boundary.

    The analytics module already resolved dates this way and documented why; this is the
    same rule made available to every other router rather than restated in each.
    """
    return await organization_today(session, organization_id)


#: Today by the organization's clock. Use in place of ``dt.date.today()`` anywhere the
#: answer is shown to a user or decides which period something falls into.
OrganizationToday = Annotated[dt.date, Depends(get_organization_today)]
