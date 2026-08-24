"""Authentication endpoints.

Routers stay thin: parse, delegate to the service, shape the response. No
business logic here, so the same rules apply identically to any future transport.

The one thing this layer genuinely owns is **cookie handling for the refresh
token**, because that is an HTTP concern the service must not know about.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Cookie, Request, Response, status

from app.core.config import settings
from app.core.exceptions import InvalidTokenError
from app.core.limiter import limiter
from app.core.logging import get_logger
from app.core.schemas import MessageResponse
from app.modules.auth.dependencies import (
    REFRESH_COOKIE_NAME,
    ActiveOrganizationId,
    AuthServiceDep,
    CurrentSession,
    CurrentUser,
    RequestCtx,
)
from app.modules.auth.password_policy import describe_policy
from app.modules.auth.schemas import (
    AuthenticatedUser,
    ChangePasswordRequest,
    DeviceSignInPendingResponse,
    DeviceSignInPollRequest,
    DeviceSignInRequest,
    DeviceSignInResponse,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    MagicLinkDeviceApprovedResponse,
    MagicLinkRequest,
    MagicLinkVerifyRequest,
    OtpRequestBody,
    OtpVerifyRequest,
    PasswordPolicyResponse,
    RecoveryCodesResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SessionRead,
    TokenResponse,
    TwoFactorChallengeResponse,
    TwoFactorDisableRequest,
    TwoFactorEnableRequest,
    TwoFactorEnableResponse,
    TwoFactorLoginRequest,
    TwoFactorSetupResponse,
    VerifyEmailRequest,
)
from app.modules.auth.service import AuthResult, DeviceSignInApproved, TwoFactorPending

log = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


# =============================================================================
# Per-endpoint rate limits
# =============================================================================
# The budgets below are the *second* limiter, declared where a reader of the endpoint
# will see them. The first is :class:`~app.core.middleware.RateLimitMiddleware`, which
# limits every route by pattern-matched tier and needs no annotation here. Both are
# enforced; the reasoning for having two, and why this one is not the blanket layer, is
# in :mod:`app.core.limiter`.
#
# Deliberately at or below the corresponding middleware tier, so this layer is the one
# that speaks first on the endpoints that matter. Where they differ it is because the
# endpoint has a property the tier table cannot see:
#
# * `/login` and `/login/2fa` are password and second-factor guessing. Five per minute is
#   above any human's typing speed and far below anything useful to an attacker; the
#   per-account lockout in `LoginThrottle` handles the account side, and this handles the
#   source side.
# * `/register` is account-creation spam, which costs a row and an outbound email each.
# * The reset, magic-link, OTP and resend endpoints all *send mail*. Abuse there spends
#   someone else's inbox and this deployment's sending reputation, and the sending domain
#   is the asset that does not recover quickly.
# * `/refresh` rotates a long-lived credential and writes a session row. A client needs
#   one call per 15-minute access token; twenty a minute is three orders of magnitude of
#   headroom and still bounds a token-churning loop.
#
# `/auth/magic-link/device/poll` is deliberately absent: it is called every two seconds
# by design from the desktop sign-in screen.
#
# **The values come from configuration, not from here.** They were literals in this file,
# which meant loosening a limit for one deployment required editing and redeploying Python -
# and, worse, that the number a 429 was actually enforcing appeared nowhere an operator would
# look. `RATE_LIMIT_AUTH_STRICT=5/minute` in `.env` next to a hard-coded `3/minute` here is
# how "/forgot-password says 429 after three tries" becomes a mystery.
#
# Aliased rather than used inline at each decorator so the mapping from endpoint group to
# setting stays visible in one place, and so the names below still read as intent
# (`MAIL_SENDING_LIMIT`) rather than as plumbing.
#
# **Bound at import time**, because that is when a decorator is applied. Changing these needs
# a restart, exactly as the literals did - see the note on
# :attr:`app.core.config.Settings.rate_limit_login`.
LOGIN_LIMIT = settings.rate_limit_login
REGISTER_LIMIT = settings.rate_limit_register
MAIL_SENDING_LIMIT = settings.rate_limit_mail_sending
TOKEN_EXCHANGE_LIMIT = settings.rate_limit_token_exchange


# =============================================================================
# Refresh cookie
# =============================================================================
def _set_refresh_cookie(response: Response, result: AuthResult) -> None:
    """Attach the refresh token as a hardened cookie.

    Each flag earns its place:

    * ``httponly`` - unreachable from JavaScript, so XSS cannot exfiltrate a
      long-lived credential. This is the single most important one.
    * ``secure`` - HTTPS only. Relaxed in local development, where there is no
      TLS and the cookie would otherwise never be set.
    * ``samesite`` - ``strict`` while the app and the API are one site, which is what
      makes CSRF against the refresh endpoint infeasible. It cannot stay ``strict`` when
      they are not: a browser withholds a strict cookie from *every* cross-site request,
      so an SPA on one host calling an API on another would sign in successfully and then
      find no session on the next page load, because ``/auth/refresh`` is exactly the
      request the cookie never reaches. Derived rather than hardcoded - see
      :attr:`~app.core.config.Settings.refresh_cookie_samesite` for the derivation and
      for what replaces ``strict`` as the CSRF control when it has to be relaxed.
    * ``path`` - scoped to the auth routes, so it is not attached to every API
      call that has no use for it.

    **Both functions must agree on every attribute.** A browser matches a deletion to a
    stored cookie by name, domain and path, and rejects a ``Set-Cookie`` whose
    ``SameSite=None`` lacks ``Secure`` - so a clear that disagreed with the set would
    silently leave the credential in place on sign-out.
    """
    # Measured from now, not from the access token's expiry - the two have
    # entirely different lifetimes.
    max_age = int((result.refresh_expires_at - dt.datetime.now(dt.UTC)).total_seconds())

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=result.refresh_token,
        max_age=max(max_age, 0),
        httponly=True,
        secure=settings.cookie_is_secure,
        samesite=settings.refresh_cookie_samesite,
        path=f"{settings.api_v1_prefix}/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=f"{settings.api_v1_prefix}/auth",
        httponly=True,
        secure=settings.cookie_is_secure,
        samesite=settings.refresh_cookie_samesite,
    )


# =============================================================================
# Registration & verification
# =============================================================================
@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
@limiter.limit(REGISTER_LIMIT)
async def register(
    request: Request,
    data: RegisterRequest,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> RegisterResponse:
    user, organization_id = await service.register(data, ctx)
    return RegisterResponse(
        user_id=user.id,
        email=user.email,
        email_verification_required=not user.is_email_verified,
        organization_id=organization_id,
        message=(
            "Account created. Check your email to verify your address."
            if not user.is_email_verified
            else "Account created."
        ),
    )


@router.post("/verify-email", response_model=MessageResponse, summary="Verify an email address")
async def verify_email(
    data: VerifyEmailRequest,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> MessageResponse:
    await service.verify_email(data.token, ctx)
    return MessageResponse(message="Email verified. You can now sign in.")


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    summary="Resend the verification email",
)
@limiter.limit(MAIL_SENDING_LIMIT)
async def resend_verification(
    request: Request,
    data: ResendVerificationRequest,
    service: AuthServiceDep,
) -> MessageResponse:
    return MessageResponse(message=await service.resend_verification(data.email))


# =============================================================================
# Sign in
# =============================================================================
@router.post(
    "/login",
    response_model=TokenResponse | TwoFactorChallengeResponse,
    summary="Sign in with email and password",
    responses={
        401: {"description": "Invalid credentials, or a 2FA code is required"},
        403: {"description": "Email not verified, or account disabled"},
        423: {"description": "Account temporarily locked after repeated failures"},
    },
)
@limiter.limit(LOGIN_LIMIT)
async def login(
    request: Request,
    data: LoginRequest,
    response: Response,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> TokenResponse | TwoFactorChallengeResponse:
    """Authenticate with a password.

    Returns either a token pair or - when 2FA is enabled - a challenge to be
    completed at ``/auth/login/2fa``.
    """
    result = await service.login(data.email, data.password, ctx, remember_me=data.remember_me)

    if isinstance(result, TwoFactorPending):
        return TwoFactorChallengeResponse(challenge_id=result.challenge_id)

    _set_refresh_cookie(response, result)
    return result.tokens


@router.post(
    "/login/2fa",
    response_model=TokenResponse,
    summary="Complete sign-in with a two-factor code",
)
@limiter.limit(LOGIN_LIMIT)
async def login_two_factor(
    request: Request,
    data: TwoFactorLoginRequest,
    response: Response,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> TokenResponse:
    """Accepts either a TOTP code or an unused recovery code."""
    result = await service.complete_two_factor(data.challenge_id, data.code, ctx)
    _set_refresh_cookie(response, result)
    return result.tokens


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new access token",
)
@limiter.limit(TOKEN_EXCHANGE_LIMIT)
async def refresh(
    request: Request,
    response: Response,
    service: AuthServiceDep,
    ctx: RequestCtx,
    refresh_cookie: Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)] = None,
    body: Annotated[RefreshRequest | None, Body()] = None,
) -> TokenResponse:
    """Rotate the refresh token and mint a fresh access token.

    Prefers the cookie; falls back to the request body for non-browser clients
    that have no cookie jar.
    """
    token = refresh_cookie or (body.refresh_token if body else None)
    if not token:
        raise InvalidTokenError("No refresh token supplied")

    result = await service.refresh(token, ctx)
    _set_refresh_cookie(response, result)
    return result.tokens


@router.post("/logout", response_model=MessageResponse, summary="Sign out")
async def logout(
    response: Response,
    user: CurrentUser,
    session: CurrentSession,
    service: AuthServiceDep,
    ctx: RequestCtx,
    data: Annotated[LogoutRequest, Body()] = LogoutRequest(),
) -> MessageResponse:
    count = await service.logout(user, session.id, ctx, all_devices=data.all_devices)
    _clear_refresh_cookie(response)

    return MessageResponse(
        message=f"Signed out of {count} devices." if data.all_devices else "Signed out."
    )


# =============================================================================
# Passwordless
# =============================================================================
@router.post(
    "/magic-link",
    response_model=MessageResponse,
    summary="Request a passwordless sign-in link",
)
@limiter.limit(MAIL_SENDING_LIMIT)
async def request_magic_link(
    request: Request,
    data: MagicLinkRequest,
    service: AuthServiceDep,
) -> MessageResponse:
    message = await service.request_magic_link(data.email, data.redirect_path)
    return MessageResponse(message=message)


@router.post(
    "/magic-link/verify",
    response_model=TokenResponse | MagicLinkDeviceApprovedResponse,
    summary="Sign in with a magic link token",
)
async def verify_magic_link(
    data: MagicLinkVerifyRequest,
    response: Response,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> TokenResponse | MagicLinkDeviceApprovedResponse:
    """Signs in *this* client, or approves the app that asked for the link.

    Which one depends on where the link came from - see
    :meth:`~app.modules.auth.service.AuthService.verify_magic_link`. A link an app
    requested approves that app and leaves this client signed out, so opening it in a
    browser does not create a second session nobody asked for.
    """
    result = await service.verify_magic_link(data.token, ctx)

    if isinstance(result, DeviceSignInApproved):
        return MagicLinkDeviceApprovedResponse(user_code=result.user_code)

    _set_refresh_cookie(response, result)
    return result.tokens


@router.post(
    "/magic-link/device",
    response_model=DeviceSignInResponse,
    summary="Start a sign-in that completes on this device",
)
async def start_device_sign_in(
    data: DeviceSignInRequest,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> DeviceSignInResponse:
    """Send a magic link and return a handle to poll while it is opened.

    For clients that cannot receive the link themselves - the desktop app sends it,
    but it opens in a browser. Poll :func:`poll_device_sign_in` with the handle.
    """
    opened = await service.open_device_sign_in(data.email, ctx)
    return DeviceSignInResponse(
        device_handle=opened.handle,
        user_code=opened.user_code,
        expires_in_seconds=opened.expires_in_seconds,
        poll_interval_seconds=opened.poll_interval_seconds,
    )


@router.post(
    "/magic-link/device/poll",
    response_model=TokenResponse | TwoFactorChallengeResponse | DeviceSignInPendingResponse,
    summary="Claim the session once the emailed link has been opened",
    responses={
        401: {"description": "The handle is unknown or has expired - start again"},
    },
)
async def poll_device_sign_in(
    data: DeviceSignInPollRequest,
    response: Response,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> TokenResponse | TwoFactorChallengeResponse | DeviceSignInPendingResponse:
    """Three answers: still waiting, a 2FA challenge, or the tokens.

    The session is established in *this* request, so it records this client's IP,
    user agent and device label rather than the browser's.
    """
    result = await service.poll_device_sign_in(data.device_handle, ctx)

    if result is None:
        return DeviceSignInPendingResponse()
    if isinstance(result, TwoFactorPending):
        return TwoFactorChallengeResponse(challenge_id=result.challenge_id)

    _set_refresh_cookie(response, result)
    return result.tokens


@router.post("/otp", response_model=MessageResponse, summary="Request an email sign-in code")
@limiter.limit(MAIL_SENDING_LIMIT)
async def request_otp(
    request: Request, data: OtpRequestBody, service: AuthServiceDep
) -> MessageResponse:
    return MessageResponse(message=await service.request_otp(data.email))


@router.post("/otp/verify", response_model=TokenResponse, summary="Sign in with an email code")
@limiter.limit(LOGIN_LIMIT)
async def verify_otp(
    request: Request,
    data: OtpVerifyRequest,
    response: Response,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> TokenResponse:
    result = await service.verify_otp(data.email, data.code, ctx)
    _set_refresh_cookie(response, result)
    return result.tokens


# =============================================================================
# Password management
# =============================================================================
@router.get(
    "/password-policy",
    response_model=PasswordPolicyResponse,
    summary="The enforced password policy",
)
async def password_policy() -> PasswordPolicyResponse:
    """Served so the client's hints cannot drift from server enforcement."""
    return PasswordPolicyResponse.model_validate(describe_policy())


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    summary="Request a password reset code by email",
)
@limiter.limit(MAIL_SENDING_LIMIT)
async def forgot_password(
    request: Request,
    data: ForgotPasswordRequest,
    service: AuthServiceDep,
) -> MessageResponse:
    """Always reports the same message, whether or not the account exists."""
    return MessageResponse(message=await service.forgot_password(data.email))


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Set a new password using an emailed reset code",
)
@limiter.limit(MAIL_SENDING_LIMIT)
async def reset_password(
    request: Request,
    data: ResetPasswordRequest,
    response: Response,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> MessageResponse:
    await service.reset_password(data.email, data.code, data.new_password, ctx)
    # Every session was revoked, so any refresh cookie in this browser is dead.
    _clear_refresh_cookie(response)
    return MessageResponse(
        message="Password updated. Sign in with your new password.",
        detail="All other sessions were signed out.",
    )


@router.post("/change-password", response_model=MessageResponse, summary="Change your password")
async def change_password(
    data: ChangePasswordRequest,
    response: Response,
    user: CurrentUser,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> MessageResponse:
    await service.change_password(user, data.current_password, data.new_password, ctx)
    _clear_refresh_cookie(response)
    return MessageResponse(
        message="Password changed.",
        detail="All sessions were signed out. Please sign in again.",
    )


# =============================================================================
# Two-factor authentication
# =============================================================================
@router.post(
    "/2fa/setup",
    response_model=TwoFactorSetupResponse,
    summary="Begin two-factor enrolment",
)
async def begin_two_factor_setup(
    user: CurrentUser,
    service: AuthServiceDep,
) -> TwoFactorSetupResponse:
    """Generate a secret and QR code. 2FA is not active until confirmed."""
    secret, provisioning_uri, qr_code = await service.begin_two_factor_setup(user)
    return TwoFactorSetupResponse(secret=secret, provisioning_uri=provisioning_uri, qr_code=qr_code)


@router.post(
    "/2fa/enable",
    response_model=TwoFactorEnableResponse,
    summary="Confirm two-factor enrolment",
)
async def enable_two_factor(
    data: TwoFactorEnableRequest,
    user: CurrentUser,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> TwoFactorEnableResponse:
    """Recovery codes are returned once and never again."""
    codes = await service.enable_two_factor(user, data.code, ctx)
    return TwoFactorEnableResponse(enabled=True, recovery_codes=codes)


@router.post(
    "/2fa/disable",
    response_model=MessageResponse,
    summary="Turn off two-factor authentication",
)
async def disable_two_factor(
    data: TwoFactorDisableRequest,
    user: CurrentUser,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> MessageResponse:
    await service.disable_two_factor(user, data.password, ctx)
    return MessageResponse(message="Two-factor authentication disabled.")


@router.post(
    "/2fa/recovery-codes",
    response_model=RecoveryCodesResponse,
    summary="Regenerate recovery codes",
)
async def regenerate_recovery_codes(
    data: TwoFactorDisableRequest,
    user: CurrentUser,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> RecoveryCodesResponse:
    """Invalidates all previously issued codes."""
    codes = await service.regenerate_recovery_codes(user, data.password, ctx)
    return RecoveryCodesResponse(recovery_codes=codes)


# =============================================================================
# Session & identity
# =============================================================================
@router.get("/me", response_model=AuthenticatedUser, summary="The current user")
async def me(
    request: Request,
    user: CurrentUser,
    service: AuthServiceDep,
) -> AuthenticatedUser:
    """Everything the client needs to render the app shell."""
    claims = getattr(request.state, "claims", {})
    organization_id = claims.get("org")
    return await service.build_authenticated_user(
        user, uuid.UUID(organization_id) if organization_id else None
    )


@router.get("/sessions", response_model=list[SessionRead], summary="List active sessions")
async def list_sessions(
    user: CurrentUser,
    session: CurrentSession,
    service: AuthServiceDep,
) -> list[SessionRead]:
    """Device history. The current session is flagged ``is_current``."""
    return await service.list_sessions(user, session.id)


@router.delete(
    "/sessions/{session_id}",
    response_model=MessageResponse,
    summary="Revoke a session",
)
async def revoke_session(
    session_id: uuid.UUID,
    user: CurrentUser,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> MessageResponse:
    await service.revoke_session(user, session_id, ctx)
    return MessageResponse(message="Session revoked.")


@router.post(
    "/switch-organization/{organization_id}",
    response_model=TokenResponse,
    summary="Switch the active organization",
)
async def switch_organization(
    organization_id: uuid.UUID,
    user: CurrentUser,
    session: CurrentSession,
    service: AuthServiceDep,
    ctx: RequestCtx,
) -> TokenResponse:
    """Re-mints the access token, since permissions are organization-specific."""
    return await service.switch_organization(user, session, organization_id, ctx)


@router.get(
    "/permissions",
    response_model=list[str],
    summary="The caller's permissions in the active organization",
)
async def my_permissions(
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    service: AuthServiceDep,
) -> list[str]:
    """Resolved live from the database rather than read off the token.

    Deliberate: this is the endpoint a client polls after a role change, so it
    must reflect current state, not what was true when the token was issued.
    """
    return sorted(await service.effective_permissions(user, organization_id))
