"""Authentication business logic.

This is the module where the security posture of the product actually lives, so
the reasoning is documented at each decision rather than left implicit.

Recurring themes:

**No account enumeration.** Registration, password reset, magic link, and OTP all
respond identically whether or not the address exists. Login always burns a hash
cycle, so a missing account and a wrong password take the same time. An attacker
should not be able to use this API to discover who has an account.

**Refresh token rotation with reuse detection.** Every refresh mints a new token
and revokes the old one. If a token that was already rotated is presented again,
one of two parties is replaying a stolen credential - and we cannot tell which -
so the entire session lineage is revoked and the event is audited as critical.

**Sessions and access tokens are revoked together.** Sessions live in PostgreSQL;
access tokens are stateless JWTs. Revoking only the session leaves valid access
tokens for up to their full TTL, so every revocation path also bumps the user's
Redis token epoch, which invalidates outstanding JWTs immediately.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext
from app.core.exceptions import (
    AccountDisabledError,
    AccountLockedError,
    ConflictError,
    EmailNotVerifiedError,
    InvalidCredentialsError,
    InvalidTokenError,
    NotFoundError,
    PermissionDeniedError,
    TwoFactorRequiredError,
    ValidationError,
)
from app.core.logging import get_logger, set_log_context
from app.core.schemas import with_computed
from app.core.security import (
    create_access_token,
    decrypt_secret,
    dummy_password_verify,
    encrypt_secret,
    generate_recovery_codes,
    generate_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_password,
)
from app.modules.accounting.service import provision_books
from app.modules.audit.models import AuditAction, AuditSeverity
from app.modules.audit.service import AuditService
from app.modules.auth import totp
from app.modules.auth.models import (
    LoginMethod,
    SessionRevocationReason,
    UserSession,
)
from app.modules.auth.password_policy import PasswordPolicyError, validate_password
from app.modules.auth.repository import SessionRepository
from app.modules.auth.schemas import (
    AuthenticatedUser,
    OrganizationSummary,
    RegisterRequest,
    SessionRead,
    TokenResponse,
)
from app.modules.auth.token_store import (
    device_sign_ins,
    email_verification_store,
    login_throttle,
    magic_link_store,
    otp_store,
    password_reset_otp_store,
    revoked_sessions,
    token_epochs,
    two_factor_challenges,
)
from app.modules.notifications import email as mailer
from app.modules.organizations.models import (
    InvitationStatus,
    MemberStatus,
    Organization,
    OrganizationMember,
)
from app.modules.organizations.repository import (
    InvitationRepository,
    MemberRepository,
    OrganizationRepository,
)
from app.modules.rbac.permissions import SystemRole, expand_grants
from app.modules.rbac.repository import RoleRepository
from app.modules.users.models import User
from app.modules.users.repository import UserRepository

log = get_logger(__name__)

#: Identical response for every passwordless/reset request, whatever the outcome.
_NEUTRAL_EMAIL_MESSAGE = "If an account exists for that address, we have sent an email."

#: How often a client waiting on a device sign-in should ask again.
#:
#: Served to the client rather than hard-coded in it, so the cadence can be changed
#: without shipping a new app build. Two seconds is short enough to feel immediate
#: after the link is clicked and slow enough that the poll stays well inside the
#: default rate-limit budget.
_DEVICE_POLL_INTERVAL_SECONDS = 2


@dataclass(slots=True)
class AuthResult:
    """A completed authentication.

    The refresh token is returned separately from :class:`TokenResponse` because
    it must be set as an HttpOnly cookie by the router, never serialised into the
    response body.
    """

    tokens: TokenResponse
    refresh_token: str
    refresh_expires_at: dt.datetime


@dataclass(slots=True)
class TwoFactorPending:
    """Password verified; awaiting a TOTP code."""

    challenge_id: str


@dataclass(slots=True)
class DeviceSignInApproved:
    """The link belonged to an app, so nothing was signed in here.

    Carries the app's ``user_code`` back so the approval page can show it: the person
    who just clicked can see whether it matches the app they were looking at, which is
    the last point at which a link they did not ask for is still noticeable.
    """

    user_code: str


@dataclass(slots=True)
class DeviceSignInOpened:
    """A sign-in the requesting client must now poll for.

    ``handle`` is the secret it polls with; ``user_code`` is the string it shows the
    user to compare against the email.
    """

    handle: str
    user_code: str
    expires_in_seconds: int
    poll_interval_seconds: int


class AuthService:
    """Registration, sign-in, session lifecycle, and 2FA."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.sessions = SessionRepository(session)
        self.organizations = OrganizationRepository(session)
        self.members = MemberRepository(session)
        self.roles = RoleRepository(session)
        self.invitations = InvitationRepository(session)
        self.audit = AuditService(session)

    # =========================================================================
    # Registration
    # =========================================================================
    async def register(
        self, data: RegisterRequest, ctx: RequestContext
    ) -> tuple[User, uuid.UUID | None]:
        """Create an account, optionally with an organization or via invitation.

        Returns ``(user, organization_id)``.

        A duplicate email *does* return a 409 here, unlike the reset flows. The
        alternative - pretending to succeed - leaves the user staring at a
        verification email that never arrives, with no way to recover. Signup
        endpoints are rate-limited instead, which is the appropriate control.
        """
        email = data.email.strip().lower()

        try:
            validate_password(password=data.password, email=email, full_name=data.full_name)
        except PasswordPolicyError as exc:
            raise ValidationError(
                "Password does not meet requirements",
                details={"password": exc.problems},
            ) from exc

        if await self.users.email_exists(email):
            raise ConflictError(
                "An account with this email already exists",
                code="email_taken",
                details={"field": "email"},
            )

        user = User(
            email=email,
            full_name=data.full_name.strip(),
            password_hash=hash_password(data.password),
            password_changed_at=dt.datetime.now(dt.UTC),
        )
        await self.users.add(user)

        organization_id: uuid.UUID | None = None

        if data.invitation_token:
            # Joining an existing org: the invitation already proves control of
            # the address, so verification is granted implicitly.
            member = await self._redeem_invitation(data.invitation_token, user)
            organization_id = member.organization_id
            user.email_verified_at = dt.datetime.now(dt.UTC)
            user.last_organization_id = organization_id
            await self.session.flush()
        elif data.organization_name:
            organization = await self._provision_organization(data.organization_name, user)
            organization_id = organization.id

        await self.audit.record(
            AuditAction.USER_REGISTERED,
            actor=user,
            organization_id=organization_id,
            resource_type="user",
            resource_id=user.id,
            summary=f"{user.email} registered",
            context={"via_invitation": bool(data.invitation_token)},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

        if not user.is_email_verified:
            await self._dispatch_verification_email(user)

        log.info(
            "user registered",
            extra={
                "user_id": str(user.id),
                "has_organization": organization_id is not None,
                "via_invitation": bool(data.invitation_token),
            },
        )
        return user, organization_id

    async def _provision_organization(self, name: str, owner: User) -> Organization:
        """Create an organization, give it working books, and make the creator owner.

        The books used to be missing here. This path predates accounting, and when the
        chart of accounts arrived only `POST /organizations` was updated - so anyone who
        signed up with an organization name got an organization with no chart and no
        fiscal year, and the billing screen greeted them with "no income accounts exist
        yet". Both paths now go through `provision_books`.
        """
        organization = Organization(
            name=name.strip(),
            slug=await self.organizations.generate_unique_slug(name),
        )
        await self.organizations.add(organization)

        seeded = await self.roles.seed_system_roles(organization.id)

        await self.members.add(
            OrganizationMember(
                organization_id=organization.id,
                user_id=owner.id,
                role_id=seeded[SystemRole.OWNER].id,
                is_owner=True,
                status=MemberStatus.ACTIVE,
                joined_at=dt.datetime.now(dt.UTC),
            )
        )

        await provision_books(
            self.session,
            organization.id,
            fiscal_year_start_month=organization.fiscal_year_start_month,
        )

        owner.last_organization_id = organization.id
        await self.session.flush()

        await self.audit.record(
            AuditAction.ORG_CREATED,
            actor=owner,
            organization_id=organization.id,
            resource_type="organization",
            resource_id=organization.id,
            summary=f"Created organization {organization.name}",
        )
        return organization

    async def _redeem_invitation(self, token: str, user: User) -> OrganizationMember:
        """Consume an invitation and create the membership.

        The invited address must match the registering one; otherwise a forwarded
        invitation link would let anybody into the organization.
        """
        invitation = await self.invitations.get_by_token(token)
        if invitation is None:
            raise InvalidTokenError("This invitation link is not valid")
        if not invitation.is_redeemable:
            raise InvalidTokenError("This invitation has expired or already been used")
        if invitation.email.strip().lower() != user.email:
            raise PermissionDeniedError(
                message="This invitation was sent to a different email address"
            )

        member = await self.members.add(
            OrganizationMember(
                organization_id=invitation.organization_id,
                user_id=user.id,
                role_id=invitation.role_id,
                status=MemberStatus.ACTIVE,
                joined_at=dt.datetime.now(dt.UTC),
                invited_by_id=invitation.invited_by_id,
            )
        )

        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = dt.datetime.now(dt.UTC)
        invitation.accepted_by_id = user.id
        await self.session.flush()

        await self.audit.record(
            AuditAction.MEMBER_JOINED,
            actor=user,
            organization_id=invitation.organization_id,
            resource_type="member",
            resource_id=member.id,
            summary=f"{user.email} joined via invitation",
        )
        return member

    # =========================================================================
    # Email verification
    # =========================================================================
    async def _dispatch_verification_email(self, user: User) -> None:
        token = await email_verification_store().issue({"user_id": str(user.id)})
        await mailer.send_verification_email(to=user.email, name=user.full_name, token=token)

    async def verify_email(self, token: str, ctx: RequestContext) -> User:
        payload = await email_verification_store().consume(token)
        if payload is None:
            raise InvalidTokenError("This verification link is invalid or has expired")

        user = await self.users.get(uuid.UUID(payload["user_id"]))
        if user is None:
            raise NotFoundError("User")

        # Idempotent: clicking the link twice should confirm, not error. The
        # token is already consumed, so this only helps a genuine double-click.
        if not user.is_email_verified:
            user.email_verified_at = dt.datetime.now(dt.UTC)
            await self.session.flush()

            await self.audit.record(
                AuditAction.USER_EMAIL_VERIFIED,
                actor=user,
                resource_type="user",
                resource_id=user.id,
                summary=f"{user.email} verified their email",
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
            )

        return user

    async def resend_verification(self, email: str) -> str:
        """Re-send verification. Always reports the neutral message."""
        user = await self.users.get_by_email(email)
        if user is not None and not user.is_email_verified and user.can_authenticate:
            await self._dispatch_verification_email(user)
        return _NEUTRAL_EMAIL_MESSAGE

    # =========================================================================
    # Password login
    # =========================================================================
    async def login(
        self, email: str, password: str, ctx: RequestContext, *, remember_me: bool = False
    ) -> AuthResult | TwoFactorPending:
        """Authenticate with email and password.

        Ordering here is security-relevant. The lockout check comes first, so a
        locked account costs no hashing. The user lookup is followed by
        :func:`dummy_password_verify` on a miss, equalising response time. Only
        after the password is confirmed do we check verification and 2FA - a
        pre-password check would reveal which addresses are registered.
        """
        email = email.strip().lower()

        if (lock_seconds := await login_throttle.is_locked(email)) > 0:
            raise AccountLockedError(
                f"Too many failed attempts. Try again in {lock_seconds // 60 + 1} minutes.",
                details={"retry_after_seconds": lock_seconds},
            )

        user = await self.users.get_by_email(email)

        if user is None or not user.has_password:
            # Equalise timing against the real-user path before failing.
            dummy_password_verify()
            await self._record_failed_login(email, ctx, reason="unknown_account")
            raise InvalidCredentialsError()

        if not verify_password(password, user.password_hash or ""):
            await self._record_failed_login(email, ctx, reason="bad_password", user=user)
            raise InvalidCredentialsError()

        if not user.can_authenticate:
            raise AccountDisabledError()

        # Correct password: transparently upgrade a hash made with older,
        # cheaper Argon2 parameters.
        if password_needs_rehash(user.password_hash or ""):
            user.password_hash = hash_password(password)
            await self.session.flush()
            log.info("password hash upgraded", extra={"user_id": str(user.id)})

        await login_throttle.reset(email)

        if user.is_two_factor_enabled:
            challenge_id = await two_factor_challenges.create(
                user.id, {"remember_me": remember_me, "method": LoginMethod.PASSWORD.value}
            )
            log.info("2fa challenge issued", extra={"user_id": str(user.id)})
            return TwoFactorPending(challenge_id=challenge_id)

        if not user.is_email_verified:
            raise EmailNotVerifiedError()

        return await self._establish_session(
            user, ctx, method=LoginMethod.PASSWORD, remember_me=remember_me
        )

    async def _record_failed_login(
        self,
        email: str,
        ctx: RequestContext,
        *,
        reason: str,
        user: User | None = None,
    ) -> None:
        attempts, lockout = await login_throttle.record_failure(email)

        await self.audit.record(
            AuditAction.USER_LOCKED_OUT if lockout else AuditAction.USER_LOGIN_FAILED,
            actor=user,
            resource_type="user",
            resource_id=user.id if user else None,
            summary=f"Failed sign-in for {email}",
            context={"reason": reason, "attempts": attempts},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        log.warning(
            "login failed",
            extra={"email": email, "reason": reason, "attempts": attempts},
        )

    # =========================================================================
    # Two-factor login
    # =========================================================================
    async def complete_two_factor(
        self, challenge_id: str, code: str, ctx: RequestContext
    ) -> AuthResult:
        """Finish a 2FA login with a TOTP code or a recovery code."""
        payload = await two_factor_challenges.resolve(challenge_id)
        if payload is None:
            raise InvalidTokenError("This challenge has expired. Sign in again.")

        user = await self.users.get(uuid.UUID(payload["user_id"]))
        if user is None or not user.can_authenticate:
            raise InvalidCredentialsError()

        remember_me = bool(payload.get("remember_me"))
        accepted_via_recovery = False

        if await self._consume_totp_code(user, code):
            pass
        elif await self._consume_recovery_code(user, code):
            accepted_via_recovery = True
        else:
            await self.audit.record(
                AuditAction.TWO_FACTOR_CHALLENGE_FAILED,
                actor=user,
                resource_type="user",
                resource_id=user.id,
                summary="Incorrect two-factor code",
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
            )
            # Count 2FA failures toward the same lockout budget as passwords -
            # otherwise the second factor is brute-forceable at leisure.
            await login_throttle.record_failure(user.email)
            raise InvalidCredentialsError("Incorrect or expired code")

        await two_factor_challenges.discard(challenge_id)
        await login_throttle.reset(user.email)

        if accepted_via_recovery:
            await self.audit.record(
                AuditAction.TWO_FACTOR_RECOVERY_CODE_USED,
                actor=user,
                resource_type="user",
                resource_id=user.id,
                summary="Signed in with a recovery code",
                context={"remaining_codes": len(user.recovery_code_hashes)},
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
            )

        if not user.is_email_verified:
            raise EmailNotVerifiedError()

        return await self._establish_session(
            user, ctx, method=LoginMethod.PASSWORD, remember_me=remember_me
        )

    async def _consume_totp_code(self, user: User, code: str) -> bool:
        """Verify a TOTP code and burn it against replay."""
        if user.totp_secret is None:
            return False

        secret = decrypt_secret(user.totp_secret)
        if not totp.verify_code(secret, code):
            return False

        # Valid, but possibly already used within its window.
        if not await two_factor_challenges.burn_code(
            user.id, code.strip(), totp.replay_ttl_seconds()
        ):
            log.warning("totp code replay rejected", extra={"user_id": str(user.id)})
            return False

        return True

    async def _consume_recovery_code(self, user: User, code: str) -> bool:
        """Verify and permanently consume a single-use recovery code.

        Codes are stored as Argon2 hashes, so every stored hash must be tried;
        there is no index to look one up by.
        """
        candidate = totp.normalise_recovery_code(code)
        if not candidate or not user.recovery_code_hashes:
            return False

        for stored in list(user.recovery_code_hashes):
            if verify_password(candidate, stored):
                # Reassign rather than mutate in place: SQLAlchemy does not track
                # in-place mutation of a plain JSONB list, and the removal would
                # never be persisted - leaving the code reusable forever.
                user.recovery_code_hashes = [h for h in user.recovery_code_hashes if h != stored]
                await self.session.flush()
                return True

        return False

    # =========================================================================
    # Passwordless: magic link
    # =========================================================================
    async def request_magic_link(self, email: str, redirect_path: str | None = None) -> str:
        user = await self.users.get_by_email(email)
        if user is not None and user.can_authenticate:
            token = await magic_link_store().issue(
                {"user_id": str(user.id), "redirect_path": redirect_path}
            )
            await mailer.send_magic_link_email(to=user.email, name=user.full_name, token=token)
            await self.audit.record(
                AuditAction.USER_MAGIC_LINK_REQUESTED,
                actor=user,
                resource_type="user",
                resource_id=user.id,
                summary="Requested a sign-in link",
            )
        return _NEUTRAL_EMAIL_MESSAGE

    async def verify_magic_link(
        self, token: str, ctx: RequestContext
    ) -> AuthResult | DeviceSignInApproved:
        """Consume a sign-in link.

        **Whichever client asked for the link is the one that gets signed in**, and the
        token itself records which that was: a link requested by an app carries a device
        handle, a link requested from a browser does not. So a browser that merely
        *opens* an app's link approves the app and signs nobody in locally - which is
        what stops one click from creating two sessions on two machines, only one of
        which the user was thinking about.
        """
        payload = await magic_link_store().consume(token)
        if payload is None:
            raise InvalidTokenError("This sign-in link is invalid or has expired")

        user = await self.users.get(uuid.UUID(payload["user_id"]))
        if user is None or not user.can_authenticate:
            raise InvalidCredentialsError()

        # Clicking a link delivered to the address proves control of it.
        if not user.is_email_verified:
            user.email_verified_at = dt.datetime.now(dt.UTC)
            await self.session.flush()

        # An app is waiting on this click. Approve it and stop - no session here.
        #
        # 2FA is deliberately not checked on this path: nothing is being signed in yet.
        # Approval only authorises the app to *claim* a session, and the claim is
        # independently gated by 2FA in `poll_device_sign_in`, so the app still needs
        # both factors. Checking here as well would force a 2FA user to complete 2FA in
        # the browser to sign in somewhere else entirely.
        device_handle_digest = payload.get("device_handle_digest")
        if isinstance(device_handle_digest, str) and device_handle_digest:
            record = await device_sign_ins.read_by_digest(device_handle_digest)
            if record is None or not await device_sign_ins.approve(device_handle_digest, user.id):
                raise InvalidTokenError(
                    "The app that asked for this link stopped waiting. Request a new one."
                )

            log.info("device sign-in approved", extra={"user_id": str(user.id)})
            await self.audit.record(
                AuditAction.USER_MAGIC_LINK_REQUESTED,
                actor=user,
                resource_type="user",
                resource_id=user.id,
                summary="Approved a sign-in link for an app",
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
            )
            return DeviceSignInApproved(user_code=record.user_code)

        if user.is_two_factor_enabled:
            # A magic link is one factor; 2FA still applies. Skipping it here
            # would make email a bypass for the second factor.
            challenge_id = await two_factor_challenges.create(
                user.id, {"method": LoginMethod.MAGIC_LINK.value}
            )
            raise TwoFactorRequiredError(challenge_id)

        return await self._establish_session(user, ctx, method=LoginMethod.MAGIC_LINK)

    # =========================================================================
    # Passwordless: device sign-in
    #
    # For a client that can *send* the magic link but never receives it. The desktop
    # app sends one, the link opens in a browser, and without this the app would sit
    # there signed out while the browser got the session - which is exactly what it
    # used to do.
    #
    # The app holds a secret handle and polls; opening the link approves the handle;
    # the next poll claims a session established in the *app's* own request context,
    # so its device history, IP and user agent are the app's rather than the
    # browser's. See :class:`~app.modules.auth.token_store.DeviceSignInStore`.
    # =========================================================================
    async def open_device_sign_in(self, email: str, ctx: RequestContext) -> DeviceSignInOpened:
        """Start a device sign-in and mail the link.

        Returns a handle whether or not the address has an account - the neutrality
        rule applies here as everywhere else, and a response that only carried a
        handle for real accounts would be an enumeration oracle. For an address with
        no account, nothing can ever approve the record and the app polls until it
        expires.
        """
        handle, user_code = await device_sign_ins.open(email)

        user = await self.users.get_by_email(email)
        if user is not None and user.can_authenticate:
            token = await magic_link_store().issue(
                {
                    "user_id": str(user.id),
                    "redirect_path": None,
                    # The digest, so the approving request never holds anything
                    # pollable - see DeviceSignInStore.
                    "device_handle_digest": hash_token(handle),
                }
            )
            await mailer.send_magic_link_email(
                to=user.email,
                name=user.full_name,
                token=token,
                user_code=user_code,
                device_label=ctx.device_label,
            )
            await self.audit.record(
                AuditAction.USER_MAGIC_LINK_REQUESTED,
                actor=user,
                resource_type="user",
                resource_id=user.id,
                summary="Requested a sign-in link for an app",
            )

        return DeviceSignInOpened(
            handle=handle,
            user_code=user_code,
            expires_in_seconds=settings.magic_link_ttl_minutes * 60,
            poll_interval_seconds=_DEVICE_POLL_INTERVAL_SECONDS,
        )

    async def poll_device_sign_in(
        self, handle: str, ctx: RequestContext
    ) -> AuthResult | TwoFactorPending | None:
        """Claim the session once the link has been opened. ``None`` = still waiting.

        Raises rather than returning ``None`` when the handle is unknown: expired and
        pending are different answers, and a client that cannot tell them apart either
        polls a dead handle forever or gives up on a live one.
        """
        record = await device_sign_ins.read(handle)
        if record is None:
            raise InvalidTokenError("This sign-in request has expired. Start again.")

        if record.user_id is None:
            return None

        user = await self.users.get(uuid.UUID(record.user_id))
        if user is None or not user.can_authenticate:
            # Deactivated between approval and this poll.
            await device_sign_ins.close(handle)
            raise InvalidCredentialsError()

        # Destroyed before the session is handed over, so a replayed handle cannot
        # mint a second one. A crash between here and the response costs the user a
        # restart of the flow, which is the right way round.
        await device_sign_ins.close(handle)

        if user.is_two_factor_enabled:
            # The link proved control of the mailbox; the code is still owed, and it
            # is owed *in the app* - which is where the session is being created.
            challenge_id = await two_factor_challenges.create(
                user.id, {"method": LoginMethod.MAGIC_LINK.value}
            )
            return TwoFactorPending(challenge_id=challenge_id)

        return await self._establish_session(user, ctx, method=LoginMethod.MAGIC_LINK)

    # =========================================================================
    # Passwordless: email OTP
    # =========================================================================
    async def request_otp(self, email: str) -> str:
        user = await self.users.get_by_email(email)
        if user is not None and user.can_authenticate:
            code = await otp_store.issue(user.email)
            await mailer.send_otp_email(to=user.email, name=user.full_name, code=code)
            await self.audit.record(
                AuditAction.USER_OTP_REQUESTED,
                actor=user,
                resource_type="user",
                resource_id=user.id,
                summary="Requested a sign-in code",
            )
        return _NEUTRAL_EMAIL_MESSAGE

    async def verify_otp(self, email: str, code: str, ctx: RequestContext) -> AuthResult:
        email = email.strip().lower()

        if (lock_seconds := await login_throttle.is_locked(email)) > 0:
            raise AccountLockedError(
                f"Too many failed attempts. Try again in {lock_seconds // 60 + 1} minutes.",
                details={"retry_after_seconds": lock_seconds},
            )

        if not await otp_store.verify(email, code):
            await self._record_failed_login(email, ctx, reason="bad_otp")
            raise InvalidCredentialsError("Incorrect or expired code")

        user = await self.users.get_by_email(email)
        if user is None or not user.can_authenticate:
            raise InvalidCredentialsError()

        if not user.is_email_verified:
            user.email_verified_at = dt.datetime.now(dt.UTC)
            await self.session.flush()

        await login_throttle.reset(email)

        if user.is_two_factor_enabled:
            challenge_id = await two_factor_challenges.create(
                user.id, {"method": LoginMethod.OTP.value}
            )
            raise TwoFactorRequiredError(challenge_id)

        return await self._establish_session(user, ctx, method=LoginMethod.OTP)

    # =========================================================================
    # Password reset / change
    # =========================================================================
    async def forgot_password(self, email: str) -> str:
        """Mail a reset code. Always answers neutrally, account or not.

        A code rather than a link, unlike signing in. A magic link is a bearer
        credential in an inbox and that is an accepted trade for a session that 2FA
        still gates; the same prefetch against a reset link hands over the account
        permanently, so this one never leaves the browser that asked for it.
        """
        user = await self.users.get_by_email(email)

        if user is not None and user.can_authenticate:
            code = await password_reset_otp_store.issue(user.email)
            await mailer.send_password_reset_email(to=user.email, name=user.full_name, code=code)
            await self.audit.record(
                AuditAction.USER_PASSWORD_RESET_REQUESTED,
                actor=user,
                resource_type="user",
                resource_id=user.id,
                summary="Requested a password reset code",
            )
        else:
            log.info("password reset requested for unknown address", extra={"email": email})

        return _NEUTRAL_EMAIL_MESSAGE

    async def reset_password(
        self, email: str, code: str, new_password: str, ctx: RequestContext
    ) -> User:
        """Set a new password against a code mailed by :meth:`forgot_password`.

        The code is the whole proof, so everything protecting it matters: it is
        purpose-scoped (a sign-in code cannot be used here), single-use, and backed
        by an attempt budget that destroys it after
        :data:`~app.modules.auth.token_store.MAX_OTP_ATTEMPTS` wrong guesses.

        The lockout is checked *and* recorded here, unlike the magic-link flow. Six
        digits is guessable in a way a 32-byte token is not, so without it an attacker
        gets a fresh budget of five for every code they force the account to send
        itself.
        """
        email = email.strip().lower()

        if (lock_seconds := await login_throttle.is_locked(email)) > 0:
            raise AccountLockedError(
                f"Too many failed attempts. Try again in {lock_seconds // 60 + 1} minutes.",
                details={"retry_after_seconds": lock_seconds},
            )

        if not await password_reset_otp_store.check(email, code):
            await self._record_failed_login(email, ctx, reason="bad_password_reset_code")
            raise InvalidTokenError("This reset code is invalid or has expired")

        user = await self.users.get_by_email(email)
        if user is None:
            # Only reachable if the account was deleted between the two requests -
            # the code was checked, so the address existed when it was issued.
            raise NotFoundError("User")

        await login_throttle.reset(email)
        await self._apply_new_password(user, new_password, ctx, reason="reset")

        # Spent only now, with every check that could reject the request behind us. A raise
        # above leaves the code live for the corrected retry, which is what a single-use code
        # should mean: used once *successfully*, not consumed by the attempt.
        await password_reset_otp_store.consume(email)

        await self.audit.record(
            AuditAction.USER_PASSWORD_RESET_COMPLETED,
            actor=user,
            resource_type="user",
            resource_id=user.id,
            summary="Completed a password reset",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return user

    async def change_password(
        self, user: User, current_password: str, new_password: str, ctx: RequestContext
    ) -> User:
        if not user.has_password or not verify_password(current_password, user.password_hash or ""):
            raise InvalidCredentialsError("Current password is incorrect")

        await self._apply_new_password(user, new_password, ctx, reason="change")

        await self.audit.record(
            AuditAction.USER_PASSWORD_CHANGED,
            actor=user,
            resource_type="user",
            resource_id=user.id,
            summary="Changed their password",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return user

    async def _apply_new_password(
        self, user: User, new_password: str, ctx: RequestContext, *, reason: str
    ) -> None:
        """Set a new password and invalidate everything issued under the old one.

        Revoking all sessions is the whole point of a password change: if the
        password was changed *because* it was compromised, leaving the attacker's
        session alive defeats the exercise. Bumping the token epoch kills their
        access token too, rather than waiting out its TTL.
        """
        try:
            validate_password(password=new_password, email=user.email, full_name=user.full_name)
        except PasswordPolicyError as exc:
            raise ValidationError(
                "Password does not meet requirements",
                details={"password": exc.problems},
            ) from exc

        if user.has_password and verify_password(new_password, user.password_hash or ""):
            raise ValidationError(
                "New password must be different from your current password",
                details={"field": "new_password"},
            )

        user.password_hash = hash_password(new_password)
        user.password_changed_at = dt.datetime.now(dt.UTC)
        user.must_change_password = False
        await self.session.flush()

        revoked = await self.sessions.revoke_all_for_user(
            user.id, SessionRevocationReason.PASSWORD_CHANGED
        )
        await token_epochs.bump(user.id)

        await mailer.send_password_changed_email(to=user.email, name=user.full_name)

        log.info(
            "password updated",
            extra={"user_id": str(user.id), "reason": reason, "sessions_revoked": revoked},
        )

    # =========================================================================
    # Two-factor enrolment
    # =========================================================================
    async def begin_two_factor_setup(self, user: User) -> tuple[str, str, str]:
        """Generate and store a TOTP secret, returning enrolment material.

        The secret is written now but ``totp_enabled_at`` stays null, so 2FA is
        not yet in force - see :attr:`User.is_two_factor_enabled`. A user who
        abandons setup is not locked out.
        """
        if user.is_two_factor_enabled:
            raise ConflictError(
                "Two-factor authentication is already enabled",
                code="two_factor_already_enabled",
            )

        secret = totp.generate_secret()
        user.totp_secret = encrypt_secret(secret)
        user.totp_enabled_at = None
        await self.session.flush()

        provisioning_uri = totp.build_provisioning_uri(secret, email=user.email)
        return secret, provisioning_uri, totp.build_qr_code_data_uri(provisioning_uri)

    async def enable_two_factor(self, user: User, code: str, ctx: RequestContext) -> list[str]:
        """Confirm enrolment and issue recovery codes.

        Requiring a valid code first is what prevents self-lockout from a
        mis-scanned QR. Recovery codes are returned once and stored only as
        hashes - the same reasoning as passwords.
        """
        if user.totp_secret is None:
            raise ConflictError("Start two-factor setup first", code="two_factor_not_started")
        if user.is_two_factor_enabled:
            raise ConflictError(
                "Two-factor authentication is already enabled",
                code="two_factor_already_enabled",
            )

        if not totp.verify_code(decrypt_secret(user.totp_secret), code):
            raise InvalidCredentialsError("That code is not valid. Try the next one.")

        codes = generate_recovery_codes()
        user.recovery_code_hashes = [
            hash_password(totp.normalise_recovery_code(code)) for code in codes
        ]
        user.totp_enabled_at = dt.datetime.now(dt.UTC)
        await self.session.flush()

        await self.audit.record(
            AuditAction.TWO_FACTOR_ENABLED,
            actor=user,
            resource_type="user",
            resource_id=user.id,
            summary="Enabled two-factor authentication",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return codes

    async def disable_two_factor(self, user: User, password: str, ctx: RequestContext) -> None:
        """Turn 2FA off, re-verifying the password first.

        This weakens the account, so a hijacked session alone must not be enough
        to do it.
        """
        if not user.is_two_factor_enabled:
            raise ConflictError(
                "Two-factor authentication is not enabled", code="two_factor_not_enabled"
            )
        if not user.has_password or not verify_password(password, user.password_hash or ""):
            raise InvalidCredentialsError("Password is incorrect")

        user.totp_secret = None
        user.totp_enabled_at = None
        user.recovery_code_hashes = []
        await self.session.flush()

        await self.audit.record(
            AuditAction.TWO_FACTOR_DISABLED,
            actor=user,
            resource_type="user",
            resource_id=user.id,
            summary="Disabled two-factor authentication",
            severity=AuditSeverity.WARNING,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

    async def regenerate_recovery_codes(
        self, user: User, password: str, ctx: RequestContext
    ) -> list[str]:
        if not user.is_two_factor_enabled:
            raise ConflictError(
                "Two-factor authentication is not enabled", code="two_factor_not_enabled"
            )
        if not user.has_password or not verify_password(password, user.password_hash or ""):
            raise InvalidCredentialsError("Password is incorrect")

        codes = generate_recovery_codes()
        # Replacing the list invalidates every previously issued code, which is
        # the expected behaviour when a user believes the old set leaked.
        user.recovery_code_hashes = [
            hash_password(totp.normalise_recovery_code(code)) for code in codes
        ]
        await self.session.flush()

        await self.audit.record(
            AuditAction.TWO_FACTOR_RECOVERY_CODES_REGENERATED,
            actor=user,
            resource_type="user",
            resource_id=user.id,
            summary="Regenerated recovery codes",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return codes

    # =========================================================================
    # Sessions
    # =========================================================================
    async def _establish_session(
        self,
        user: User,
        ctx: RequestContext,
        *,
        method: LoginMethod,
        remember_me: bool = False,
        generation: int = 0,
    ) -> AuthResult:
        """Create a session and mint the first token pair."""
        refresh_token = generate_token()
        ttl_days = settings.refresh_token_ttl_days if remember_me else 7
        refresh_expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=ttl_days)

        active_org_id = await self._resolve_active_organization(user)

        session = await self.sessions.create(
            user_id=user.id,
            refresh_token=refresh_token,
            expires_at=refresh_expires_at,
            organization_id=active_org_id,
            login_method=method,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
            device_label=ctx.device_label,
            device_type=ctx.device_type,
            generation=generation,
        )

        await self.users.touch_last_login(user)

        await self.audit.record(
            AuditAction.USER_LOGGED_IN,
            actor=user,
            organization_id=active_org_id,
            resource_type="session",
            resource_id=session.id,
            summary=f"Signed in via {method.value}",
            context={"device": ctx.device_label, "method": method.value},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

        tokens = await self._issue_access_token(user, session)
        log.info(
            "session established",
            extra={
                "user_id": str(user.id),
                "session_id": str(session.id),
                "method": method.value,
            },
        )
        return AuthResult(
            tokens=tokens,
            refresh_token=refresh_token,
            refresh_expires_at=refresh_expires_at,
        )

    async def _resolve_active_organization(self, user: User) -> uuid.UUID | None:
        """Pick which org the session opens in.

        Prefers the user's last active org, but only if the membership is still
        valid - a removed or suspended member must not resume there.
        """
        memberships = await self.users.active_memberships(user.id)
        if not memberships:
            return None

        if user.last_organization_id is not None:
            for membership in memberships:
                if membership.organization_id == user.last_organization_id:
                    return membership.organization_id

        return memberships[0].organization_id

    async def _issue_access_token(self, user: User, session: UserSession) -> TokenResponse:
        """Mint an access token carrying the caller's permissions."""
        permissions = await self._permissions_for(user, session.organization_id)
        epoch = await token_epochs.current(user.id)

        token, _jti, expires_at = create_access_token(
            user_id=user.id,
            session_id=session.id,
            organization_id=session.organization_id,
            permissions=sorted(permissions),
            epoch=epoch,
        )

        return TokenResponse(
            access_token=token,
            expires_in=settings.access_token_ttl_minutes * 60,
            expires_at=expires_at,
            session_id=session.id,
            user=await self.build_authenticated_user(user, session.organization_id),
            must_change_password=user.must_change_password,
        )

    async def effective_permissions(
        self, user: User, organization_id: uuid.UUID | None
    ) -> frozenset[str]:
        """Public wrapper over :meth:`_permissions_for`.

        Resolves permissions live from the database. Callers that need current
        state after a role change use this rather than reading the token claim,
        which is only as fresh as the token itself.
        """
        return await self._permissions_for(user, organization_id)

    async def _permissions_for(
        self, user: User, organization_id: uuid.UUID | None
    ) -> frozenset[str]:
        """Resolve the effective permission set for a user in an organization.

        Role grants are expanded, then per-member ``deny`` overrides subtract from
        the result. Deny wins over grant - the safe direction, and it makes
        "everything except approving invoices" expressible without cloning a role.
        """
        if organization_id is None:
            return frozenset()

        membership = await self.members.get_membership(organization_id, user.id)
        if membership is None or not membership.is_active:
            return frozenset()

        granted = set(expand_grants(membership.role.permissions))

        overrides: dict[str, Any] = membership.permission_overrides or {}
        if allow := overrides.get("allow"):
            granted.update(expand_grants(list(allow)))
        if deny := overrides.get("deny"):
            granted.difference_update(expand_grants(list(deny)))

        return frozenset(granted)

    async def refresh(self, refresh_token: str, ctx: RequestContext) -> AuthResult:
        """Rotate a refresh token, with reuse detection.

        The stolen-token problem: an attacker who copies a refresh token can
        refresh forever, and we cannot distinguish them from the real user. The
        mitigation is that rotation makes reuse *detectable* - the first party to
        refresh invalidates the other's copy, so the second use of an already
        rotated token is a reliable signal that two parties hold it. Response:
        revoke the whole lineage and force a fresh sign-in.
        """
        session = await self.sessions.get_by_refresh_token(refresh_token)
        if session is None:
            raise InvalidTokenError("Session not recognised. Please sign in again.")

        if session.is_revoked:
            if session.rotated_to_id is not None:
                # A rotated token replayed - treat as compromise.
                await self._handle_refresh_reuse(session, ctx)
            raise InvalidTokenError("Session expired. Please sign in again.")

        if session.is_expired():
            await self.sessions.revoke(session, SessionRevocationReason.LOGOUT)
            raise InvalidTokenError("Session expired. Please sign in again.")

        user = await self.users.get(session.user_id)
        if user is None or not user.can_authenticate:
            await self.sessions.revoke(session, SessionRevocationReason.ACCOUNT_DISABLED)
            raise AccountDisabledError()

        new_refresh_token = generate_token()
        # Preserve the original expiry: rotation must not let a session extend
        # itself indefinitely by refreshing.
        successor = await self.sessions.create(
            user_id=user.id,
            refresh_token=new_refresh_token,
            expires_at=session.expires_at,
            organization_id=session.organization_id,
            login_method=session.login_method,
            ip_address=ctx.ip_address or session.ip_address,
            user_agent=ctx.user_agent or session.user_agent,
            device_label=ctx.device_label or session.device_label,
            device_type=ctx.device_type or session.device_type,
            generation=session.generation + 1,
        )
        await self.sessions.revoke(
            session, SessionRevocationReason.ROTATED, rotated_to=successor.id
        )

        tokens = await self._issue_access_token(user, successor)
        return AuthResult(
            tokens=tokens,
            refresh_token=new_refresh_token,
            refresh_expires_at=successor.expires_at,
        )

    async def _handle_refresh_reuse(self, session: UserSession, ctx: RequestContext) -> None:
        """Revoke a compromised session lineage and commit it immediately.

        **The explicit commit is the whole point of this method, and the one
        place a service is allowed to call it.**

        The caller raises :class:`InvalidTokenError` the moment this returns, and
        :func:`app.db.session.get_db` rolls back on any exception. Without
        committing here, the revocation and its audit row would be discarded by
        the very 401 that reports the breach - the endpoint would *look* correct
        while leaving the stolen token's lineage fully usable and recording
        nothing. That is a silent security failure, and exactly the bug this
        comment exists to prevent someone from reintroducing.

        The later ``rollback()`` in ``get_db`` is harmless: the transaction is
        already closed, so it is a no-op.
        """
        revoked = await self.sessions.revoke_lineage(
            session, SessionRevocationReason.REUSE_DETECTED
        )
        actor = await self.users.get(session.user_id)

        await self.audit.record(
            AuditAction.SESSION_REUSE_DETECTED,
            actor=actor,
            organization_id=session.organization_id,
            resource_type="session",
            resource_id=session.id,
            summary="Refresh token reuse detected - session lineage revoked",
            severity=AuditSeverity.CRITICAL,
            context={"sessions_revoked": revoked, "generation": session.generation},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

        await self.session.commit()

        # Redis, so unaffected by any rollback. Bumped after the commit so the
        # two stores cannot disagree if the commit fails.
        await token_epochs.bump(session.user_id)

        log.critical(
            "refresh token reuse detected",
            extra={
                "user_id": str(session.user_id),
                "session_id": str(session.id),
                "sessions_revoked": revoked,
            },
        )

    async def logout(
        self, user: User, session_id: uuid.UUID, ctx: RequestContext, *, all_devices: bool
    ) -> int:
        """End the current session, or every session for the user."""
        if all_devices:
            count = await self.sessions.revoke_all_for_user(
                user.id, SessionRevocationReason.LOGOUT_ALL
            )
            await token_epochs.bump(user.id)
            await self.audit.record(
                AuditAction.SESSION_ALL_REVOKED,
                actor=user,
                resource_type="user",
                resource_id=user.id,
                summary=f"Signed out of {count} devices",
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
            )
            return count

        session = await self.sessions.get(session_id)
        if session is not None and session.user_id == user.id:
            await self.sessions.revoke(session, SessionRevocationReason.LOGOUT)
        # Kill the already-issued access token too, rather than leaving it live
        # for the remainder of its TTL.
        await revoked_sessions.revoke(session_id)

        await self.audit.record(
            AuditAction.USER_LOGGED_OUT,
            actor=user,
            resource_type="session",
            resource_id=session_id,
            summary="Signed out",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return 1

    async def list_sessions(self, user: User, current_session_id: uuid.UUID) -> list[SessionRead]:
        sessions = await self.sessions.list_active(user.id)
        return [
            with_computed(SessionRead, session, is_current=session.id == current_session_id)
            for session in sessions
        ]

    async def revoke_session(self, user: User, session_id: uuid.UUID, ctx: RequestContext) -> None:
        session = await self.sessions.get(session_id)
        # Ownership check, not just existence: one user must not be able to
        # terminate another's session by guessing an id.
        if session is None or session.user_id != user.id:
            raise NotFoundError("Session")

        await self.sessions.revoke(session, SessionRevocationReason.ADMIN_REVOKED)
        await revoked_sessions.revoke(session.id)

        await self.audit.record(
            AuditAction.SESSION_REVOKED,
            actor=user,
            resource_type="session",
            resource_id=session.id,
            summary=f"Revoked session on {session.device_label or 'unknown device'}",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

    # =========================================================================
    # Organization switching
    # =========================================================================
    async def switch_organization(
        self, user: User, session: UserSession, organization_id: uuid.UUID, ctx: RequestContext
    ) -> TokenResponse:
        """Point the session at a different organization and re-mint the token.

        A new access token is required, not optional: permissions are embedded in
        the token and differ per organization.
        """
        membership = await self.members.get_membership(organization_id, user.id)
        if membership is None:
            raise NotFoundError("Organization")
        if not membership.is_active:
            raise PermissionDeniedError(
                message="Your access to this organization has been suspended"
            )

        await self.sessions.set_organization(session, organization_id)
        user.last_organization_id = organization_id
        await self.session.flush()

        await self.audit.record(
            AuditAction.ORG_SWITCHED,
            actor=user,
            organization_id=organization_id,
            resource_type="organization",
            resource_id=organization_id,
            summary="Switched organization",
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return await self._issue_access_token(user, session)

    # =========================================================================
    # Principal assembly
    # =========================================================================
    async def build_authenticated_user(
        self, user: User, active_organization_id: uuid.UUID | None
    ) -> AuthenticatedUser:
        """Assemble the ``/auth/me`` payload."""
        memberships = await self.users.active_memberships(user.id)

        summaries = [
            OrganizationSummary(
                id=membership.organization.id,
                name=membership.organization.name,
                slug=membership.organization.slug,
                logo_url=membership.organization.logo_url,
                role_name=membership.role.name,
                role_slug=membership.role.slug,
                is_owner=membership.is_owner,
                currency=membership.organization.currency,
                timezone=membership.organization.timezone,
                fiscal_year_start_month=membership.organization.fiscal_year_start_month,
            )
            for membership in memberships
        ]
        active = next((s for s in summaries if s.id == active_organization_id), None)
        permissions = await self._permissions_for(user, active_organization_id)

        return AuthenticatedUser(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            avatar_url=user.avatar_url,
            initials=user.initials,
            is_email_verified=user.is_email_verified,
            is_two_factor_enabled=user.is_two_factor_enabled,
            is_superuser=user.is_superuser,
            locale=user.locale,
            timezone=user.timezone,
            theme=user.theme,
            last_login_at=user.last_login_at,
            active_organization=active,
            organizations=summaries,
            permissions=sorted(permissions),
        )

    async def bind_log_context(self, user: User, organization_id: uuid.UUID | None) -> None:
        """Attach the caller's identity to the logifyx context for this request."""
        set_log_context(
            user_id=str(user.id),
            org_id=str(organization_id) if organization_id else None,
        )
