"""Auth request/response contracts.

Response-shape decisions that matter:

* **Refresh tokens are not in any response body.** They travel in an
  ``HttpOnly; Secure; SameSite=Strict`` cookie, unreachable from JavaScript, so
  an XSS bug cannot exfiltrate long-lived credentials. Access tokens *are*
  returned in the body and held in memory only - never ``localStorage``.
* **Several endpoints return the same message whether or not the account
  exists.** Password reset, magic link, and OTP request all answer "if an account
  exists, we have sent a link". Anything more specific is an enumeration oracle.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.core.schemas import (
    BaseSchema,
    Email,
    IpAddress,
    NameStr,
    PasswordStr,
    ResponseSchema,
    ShortStr,
)
from app.modules.auth.models import LoginMethod

#: A 6-digit TOTP or email OTP. Spaces and hyphens are stripped before
#: validation because authenticator apps display codes as "123 456".
OtpCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=12)]


# =============================================================================
# Registration
# =============================================================================
class RegisterRequest(BaseSchema):
    email: Email
    password: PasswordStr
    full_name: NameStr
    #: Optional: creating an organization during signup makes the common
    #: "I am a new company" path one request instead of two.
    organization_name: ShortStr | None = None
    #: Set when arriving from an invitation link; joins that org instead.
    invitation_token: str | None = None

    @model_validator(mode="after")
    def _reject_conflicting_intent(self) -> RegisterRequest:
        if self.organization_name and self.invitation_token:
            raise ValueError("Provide either organization_name or invitation_token, not both")
        return self


class RegisterResponse(ResponseSchema):
    user_id: uuid.UUID
    email: str
    email_verification_required: bool
    organization_id: uuid.UUID | None = None
    message: str


# =============================================================================
# Login
# =============================================================================
class LoginRequest(BaseSchema):
    email: Email
    password: PasswordStr
    #: Extends the refresh token's lifetime on trusted devices.
    remember_me: bool = False


class TokenResponse(ResponseSchema):
    """Successful authentication.

    The refresh token is absent by design - see the module docstring.
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")
    expires_at: dt.datetime
    session_id: uuid.UUID
    user: AuthenticatedUser
    #: Present when the password was accepted but must be changed before use.
    must_change_password: bool = False


class TwoFactorChallengeResponse(ResponseSchema):
    """Password accepted; a TOTP code is still required."""

    challenge_id: str
    two_factor_required: Literal[True] = True
    message: str = "Enter the code from your authenticator app"


class TwoFactorLoginRequest(BaseSchema):
    challenge_id: str
    code: OtpCode
    remember_me: bool = False


class RefreshRequest(BaseSchema):
    """Body for the refresh endpoint.

    The token normally arrives in the cookie; this field is the fallback for
    non-browser clients (mobile, CLI) that have no cookie jar.
    """

    refresh_token: str | None = None


class LogoutRequest(BaseSchema):
    #: Revoke every session, not just this one. Used after a suspected breach.
    all_devices: bool = False


# =============================================================================
# Email verification
# =============================================================================
class VerifyEmailRequest(BaseSchema):
    token: str


class ResendVerificationRequest(BaseSchema):
    email: Email


# =============================================================================
# Password reset / change
# =============================================================================
class ForgotPasswordRequest(BaseSchema):
    email: Email


class ResetPasswordRequest(BaseSchema):
    #: The address the code was sent to. Needed because a code carries no identity
    #: of its own - it is looked up under the address that requested it.
    email: Email
    code: OtpCode
    new_password: PasswordStr


class ChangePasswordRequest(BaseSchema):
    #: Required even though the caller is authenticated: it proves the person at
    #: the keyboard is the account owner and not someone using a borrowed session.
    current_password: PasswordStr
    new_password: PasswordStr

    @model_validator(mode="after")
    def _reject_unchanged(self) -> ChangePasswordRequest:
        if self.current_password == self.new_password:
            raise ValueError("New password must be different from the current one")
        return self


# =============================================================================
# Passwordless
# =============================================================================
class MagicLinkRequest(BaseSchema):
    email: Email
    #: Where to land after authenticating. Validated against an allow-list
    #: server-side - an unchecked redirect target is an open-redirect bug.
    redirect_path: str | None = None

    @field_validator("redirect_path")
    @classmethod
    def _must_be_relative(cls, value: str | None) -> str | None:
        """Reject absolute URLs and protocol-relative paths outright."""
        if value is None:
            return None
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("redirect_path must be a relative path beginning with '/'")
        return value


class MagicLinkVerifyRequest(BaseSchema):
    token: str


class MagicLinkDeviceApprovedResponse(ResponseSchema):
    """The opened link belonged to an app. Nothing is signed in here.

    Returned instead of tokens so that one click cannot leave sessions on two
    machines: the client that *asked* for the link is the one that gets signed in.
    """

    device_approved: Literal[True] = True
    #: The code the waiting app is showing, so the reader can confirm it matches.
    user_code: str
    message: str = "Your app is signing in now. You can close this tab."


class DeviceSignInRequest(BaseSchema):
    """Start a sign-in from a client that cannot receive the emailed link."""

    email: Email


class DeviceSignInResponse(ResponseSchema):
    """What the requesting client needs to wait for its own sign-in.

    Returned whether or not the address has an account: a handle withheld for unknown
    addresses would be an enumeration oracle.
    """

    #: The secret to poll with. Belongs in memory for the life of the screen - it is a
    #: credential, and it is never emailed, logged, or put in a URL.
    device_handle: str
    #: Shown to the user so they can check the email refers to *this* device.
    user_code: str
    expires_in_seconds: int
    poll_interval_seconds: int


class DeviceSignInPollRequest(BaseSchema):
    device_handle: str


class DeviceSignInPendingResponse(ResponseSchema):
    """The link has not been opened yet. Keep polling."""

    status: Literal["pending"] = "pending"
    message: str = "Waiting for the link in your email to be opened"


class OtpRequestBody(BaseSchema):
    email: Email


class OtpVerifyRequest(BaseSchema):
    email: Email
    code: OtpCode


# =============================================================================
# Two-factor enrolment
# =============================================================================
class TwoFactorSetupResponse(ResponseSchema):
    """Enrolment payload.

    The secret is returned once, at setup, for manual entry when a QR code cannot
    be scanned. It is never retrievable again.
    """

    secret: str
    provisioning_uri: str
    qr_code: str = Field(description="PNG data: URI for the QR code")


class TwoFactorEnableRequest(BaseSchema):
    """Confirms enrolment by proving the app produces valid codes.

    Without this proof, a misconfigured authenticator would lock the user out on
    their next sign-in.
    """

    code: OtpCode


class TwoFactorEnableResponse(ResponseSchema):
    enabled: bool
    recovery_codes: list[str] = Field(description="Shown exactly once - store them securely")


class TwoFactorDisableRequest(BaseSchema):
    #: Re-authentication for a security-downgrading action.
    password: PasswordStr


class RecoveryCodesResponse(ResponseSchema):
    recovery_codes: list[str]


# =============================================================================
# Sessions / device history
# =============================================================================
class SessionRead(ResponseSchema):
    id: uuid.UUID
    ip_address: IpAddress | None
    user_agent: str | None
    device_label: str | None
    device_type: str | None
    login_method: LoginMethod
    created_at: dt.datetime
    last_used_at: dt.datetime | None
    expires_at: dt.datetime
    #: Flagged so the UI can label "This device" and avoid offering to revoke it.
    is_current: bool = False


# =============================================================================
# The authenticated principal
# =============================================================================
class OrganizationSummary(ResponseSchema):
    """An org as seen from the user's own membership."""

    id: uuid.UUID
    name: str
    slug: str
    logo_url: str | None = None
    role_name: str
    role_slug: str
    is_owner: bool

    #: How this organization's figures and dates are to be rendered.
    #:
    #: Carried on the session payload rather than fetched per screen: every amount and
    #: every date in the app needs them, so a separate request would mean either a second
    #: round trip before the first paint or a hardcoded "INR" standing in until it lands -
    #: and a figure that renders in the wrong currency for a moment is worse than one that
    #: renders late.
    currency: str
    timezone: str
    fiscal_year_start_month: int


class AuthenticatedUser(ResponseSchema):
    """The ``/auth/me`` payload - everything the client needs to render a shell.

    Permissions are included so the UI can hide actions the user cannot perform.
    That is presentation only: the server re-checks every permission on every
    request, because a hidden button is not access control.
    """

    id: uuid.UUID
    email: str
    full_name: str
    avatar_url: str | None = None
    initials: str
    is_email_verified: bool
    is_two_factor_enabled: bool
    is_superuser: bool
    locale: str
    timezone: str
    theme: str
    last_login_at: dt.datetime | None = None

    active_organization: OrganizationSummary | None = None
    organizations: list[OrganizationSummary] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class PasswordPolicyResponse(ResponseSchema):
    """The enforced policy, so client-side hints cannot drift from the server.

    Mirrors :func:`app.modules.auth.password_policy.describe_policy` exactly -
    if a knob is added there, it belongs here too.
    """

    min_length: int
    max_length: int
    requires_uppercase: bool
    requires_lowercase: bool
    requires_special: bool
    requires_digit: bool
    #: The literal set of accepted special characters, so the client can show
    #: them rather than inventing its own list.
    special_characters: str
    rules: list[str]
