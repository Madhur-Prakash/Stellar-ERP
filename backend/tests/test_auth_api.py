"""Integration tests for the authentication API.

These run against real PostgreSQL and Redis through the real ASGI stack - no
mocked repositories. Auth bugs live in the interaction between layers (token
issued but session revoked, epoch bumped but token still accepted), which a
mock-based test cannot see.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.modules.audit.models import AuditAction, AuditLog, AuditSeverity
from app.modules.auth.dependencies import REFRESH_COOKIE_NAME
from app.modules.auth.models import SessionRevocationReason, UserSession
from app.modules.auth.token_store import (
    email_verification_store,
    otp_store,
    password_reset_otp_store,
)
from app.modules.organizations.models import Organization
from app.modules.users.models import User
from tests.conftest import TEST_PASSWORD


# =============================================================================
# Registration
# =============================================================================
class TestRegistration:
    async def test_creates_unverified_user(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        response = await client.post(
            f"{api}/auth/register",
            json={
                "email": "New.User@Example.COM",
                "password": TEST_PASSWORD,
                "full_name": "Nina Rao",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["email_verification_required"] is True

        # Email must be normalised to lowercase, or uniqueness is case-sensitive.
        stored = await db.scalar(select(User).where(User.email == "new.user@example.com"))
        assert stored is not None
        assert stored.email_verified_at is None
        assert stored.password_hash is not None
        assert TEST_PASSWORD not in stored.password_hash

    async def test_creates_organization_with_seeded_roles(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        response = await client.post(
            f"{api}/auth/register",
            json={
                "email": "founder@example.com",
                "password": TEST_PASSWORD,
                "full_name": "Arjun Mehta",
                "organization_name": "Mehta Exports",
            },
        )
        assert response.status_code == 201, response.text
        organization_id = response.json()["organization_id"]
        assert organization_id is not None

        from app.modules.rbac.models import Role

        roles = (
            await db.scalars(select(Role).where(Role.organization_id == organization_id))
        ).all()
        assert {role.slug for role in roles} == {
            "owner",
            "admin",
            "accountant",
            "sales",
            "viewer",
        }
        assert sum(role.is_default for role in roles) == 1

    async def test_duplicate_email_conflicts(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        response = await client.post(
            f"{api}/auth/register",
            json={
                "email": user.email,
                "password": TEST_PASSWORD,
                "full_name": "Impostor",
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "email_taken"

    async def test_duplicate_email_is_case_insensitive(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        response = await client.post(
            f"{api}/auth/register",
            json={
                "email": user.email.upper(),
                "password": TEST_PASSWORD,
                "full_name": "Impostor",
            },
        )
        assert response.status_code == 409

    async def test_weak_password_rejected_with_reasons(self, client: AsyncClient, api: str) -> None:
        response = await client.post(
            f"{api}/auth/register",
            json={
                "email": "weak@example.com",
                "password": "password123",
                "full_name": "Weak Pass",
            },
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_error"
        assert error["details"]["password"]

    async def test_rejects_both_org_and_invitation(self, client: AsyncClient, api: str) -> None:
        response = await client.post(
            f"{api}/auth/register",
            json={
                "email": "confused@example.com",
                "password": TEST_PASSWORD,
                "full_name": "Confused User",
                "organization_name": "Some Co",
                "invitation_token": "abc123",
            },
        )
        assert response.status_code == 422

    async def test_cannot_set_privileged_fields(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """Mass-assignment guard: ``extra="forbid"`` must reject unknown fields."""
        response = await client.post(
            f"{api}/auth/register",
            json={
                "email": "sneaky@example.com",
                "password": TEST_PASSWORD,
                "full_name": "Sneaky User",
                "is_superuser": True,
            },
        )
        assert response.status_code == 422


# =============================================================================
# Email verification
# =============================================================================
class TestEmailVerification:
    async def test_verifies_with_valid_token(
        self, client: AsyncClient, api: str, db: AsyncSession, unverified_user: User
    ) -> None:
        token = await email_verification_store().issue({"user_id": str(unverified_user.id)})

        response = await client.post(f"{api}/auth/verify-email", json={"token": token})
        assert response.status_code == 200, response.text

        await db.refresh(unverified_user)
        assert unverified_user.email_verified_at is not None

    async def test_token_is_single_use(
        self, client: AsyncClient, api: str, unverified_user: User
    ) -> None:
        token = await email_verification_store().issue({"user_id": str(unverified_user.id)})

        assert (
            await client.post(f"{api}/auth/verify-email", json={"token": token})
        ).status_code == 200
        second = await client.post(f"{api}/auth/verify-email", json={"token": token})
        assert second.status_code == 401

    async def test_invalid_token_rejected(self, client: AsyncClient, api: str) -> None:
        response = await client.post(f"{api}/auth/verify-email", json={"token": "not-a-real-token"})
        assert response.status_code == 401

    async def test_resend_does_not_reveal_account_existence(
        self, client: AsyncClient, api: str, unverified_user: User
    ) -> None:
        real = await client.post(
            f"{api}/auth/resend-verification", json={"email": unverified_user.email}
        )
        fake = await client.post(
            f"{api}/auth/resend-verification", json={"email": "nobody@example.com"}
        )
        assert real.status_code == fake.status_code == 200
        assert real.json() == fake.json()


# =============================================================================
# Login
# =============================================================================
class TestLogin:
    async def test_successful_login_returns_token_and_cookie(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        response = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["token_type"] == "bearer"
        assert body["access_token"]
        assert body["user"]["email"] == user.email

        # The refresh token must never appear in the body.
        assert "refresh_token" not in body

        cookie = response.cookies.get(REFRESH_COOKIE_NAME)
        assert cookie, "refresh cookie was not set"

    async def test_refresh_cookie_is_httponly_and_samesite_strict(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        """The property that makes XSS unable to steal a long-lived credential."""
        response = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        set_cookie = next(
            value
            for key, value in response.headers.multi_items()
            if key.lower() == "set-cookie" and REFRESH_COOKIE_NAME in value
        )
        assert "HttpOnly" in set_cookie
        assert "SameSite=strict" in set_cookie.replace("SameSite=Strict", "SameSite=strict")

    async def test_login_is_case_insensitive_on_email(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        response = await client.post(
            f"{api}/auth/login",
            json={"email": user.email.upper(), "password": TEST_PASSWORD},
        )
        assert response.status_code == 200

    async def test_wrong_password_rejected(self, client: AsyncClient, api: str, user: User) -> None:
        response = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": "wrong-password-here"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"

    async def test_unknown_and_wrong_password_are_indistinguishable(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        """No enumeration oracle: identical status, code, and message."""
        unknown = await client.post(
            f"{api}/auth/login",
            json={"email": "nobody@example.com", "password": "wrong-password-here"},
        )
        wrong = await client.post(
            f"{api}/auth/login",
            json={"email": user.email, "password": "wrong-password-here"},
        )
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["error"]["code"] == wrong.json()["error"]["code"]
        assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]

    async def test_unverified_email_blocked(
        self, client: AsyncClient, api: str, unverified_user: User
    ) -> None:
        response = await client.post(
            f"{api}/auth/login",
            json={"email": unverified_user.email, "password": TEST_PASSWORD},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "email_not_verified"

    async def test_disabled_account_blocked(
        self, client: AsyncClient, api: str, db: AsyncSession, user: User
    ) -> None:
        user.is_active = False
        await db.flush()

        response = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "account_disabled"

    async def test_lockout_after_repeated_failures(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        """Brute-force protection, counted per email address."""
        from app.core.config import settings

        for _ in range(settings.max_login_attempts):
            response = await client.post(
                f"{api}/auth/login",
                json={"email": user.email, "password": "wrong-password-here"},
            )
            assert response.status_code == 401

        # Even the correct password is now refused.
        locked = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        assert locked.status_code == 423
        assert locked.json()["error"]["code"] == "account_locked"

    async def test_successful_login_clears_failure_counter(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        for _ in range(2):
            await client.post(
                f"{api}/auth/login",
                json={"email": user.email, "password": "wrong-password-here"},
            )

        assert (
            await client.post(
                f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
            )
        ).status_code == 200

        from app.core.redis import RedisKey

        assert await get_redis().get(RedisKey.login_attempts(user.email)) is None


# =============================================================================
# Authenticated identity
# =============================================================================
class TestMe:
    async def test_returns_profile_and_permissions(
        self, authed_client: AsyncClient, api: str, user: User, organization: Organization
    ) -> None:
        response = await authed_client.get(f"{api}/auth/me")
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["email"] == user.email
        assert body["active_organization"]["id"] == str(organization.id)

        # The rendering settings ride along, so no screen has to fetch them or fall back to
        # a hardcoded "INR" for the first paint.
        assert body["active_organization"]["currency"] == organization.currency
        assert body["active_organization"]["timezone"] == organization.timezone
        assert (
            body["active_organization"]["fiscal_year_start_month"]
            == organization.fiscal_year_start_month
        )
        assert body["active_organization"]["is_owner"] is True
        # The owner holds every permission in the catalogue.
        from app.modules.rbac.permissions import ALL_PERMISSION_VALUES

        assert set(body["permissions"]) == ALL_PERMISSION_VALUES

    async def test_requires_authentication(self, client: AsyncClient, api: str) -> None:
        response = await client.get(f"{api}/auth/me")
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"


class TestProfileEdit:
    """`PATCH /users/me`, which the Settings profile card drives."""

    async def test_the_profile_carries_the_phone_number(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """`/auth/me` has no phone, so the form has to read this endpoint instead.

        Worth pinning: the profile card showed an always-empty phone box because it read the
        signed-in user object, which does not carry one.
        """
        saved = await authed_client.patch(
            f"{api}/users/me", json={"full_name": "Madhur Prakash", "phone": "+91 98765 43210"}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["phone"] == "+91 98765 43210"

        again = await authed_client.get(f"{api}/users/me")
        assert again.json()["phone"] == "+91 98765 43210"
        assert again.json()["full_name"] == "Madhur Prakash"

    async def test_an_empty_name_is_rejected(self, authed_client: AsyncClient, api: str) -> None:
        """The form blocks this now, but the rule lives here.

        The card used to send an empty name whenever the user object had not loaded before
        the field initialised, so "Save changes" failed with nothing on screen explaining
        why. The 422 is correct - the form was wrong.
        """
        response = await authed_client.patch(f"{api}/users/me", json={"full_name": "   "})
        assert response.status_code == 422, response.text
        assert "full_name" in response.text

    async def test_clearing_the_phone_stores_nothing_rather_than_an_empty_string(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """An emptied optional field means "remove this".

        `null` cannot express it - the service drops nulls so a partial update never blanks a
        field it omitted - so an empty string is how clearing arrives, and it has to land as
        NULL. Left as "" the column is not null, escapes every `IS NULL` check, and still
        renders blank on screen.
        """
        await authed_client.patch(f"{api}/users/me", json={"phone": "+91 98765 43210"})

        cleared = await authed_client.patch(f"{api}/users/me", json={"phone": ""})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["phone"] is None

    async def test_omitting_a_field_leaves_it_alone(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """Saving a name must not wipe the phone number beside it."""
        await authed_client.patch(
            f"{api}/users/me", json={"full_name": "First Name", "phone": "+91 98765 43210"}
        )

        renamed = await authed_client.patch(f"{api}/users/me", json={"full_name": "Second Name"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["full_name"] == "Second Name"
        assert renamed.json()["phone"] == "+91 98765 43210"

    async def test_rejects_garbage_token(self, client: AsyncClient, api: str) -> None:
        client.headers["Authorization"] = "Bearer not-a-real-jwt"
        response = await client.get(f"{api}/auth/me")
        assert response.status_code == 401


# =============================================================================
# Refresh rotation
# =============================================================================
class TestRefresh:
    async def test_rotates_and_returns_new_tokens(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        login = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        original_cookie = login.cookies[REFRESH_COOKIE_NAME]

        refreshed = await client.post(f"{api}/auth/refresh")
        assert refreshed.status_code == 200, refreshed.text

        new_cookie = refreshed.cookies.get(REFRESH_COOKIE_NAME)
        assert new_cookie and new_cookie != original_cookie, "token was not rotated"

    async def test_rotation_preserves_original_expiry(
        self, client: AsyncClient, api: str, db: AsyncSession, user: User
    ) -> None:
        """Refreshing must not let a session extend itself indefinitely."""
        await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        first = (await db.scalars(select(UserSession))).all()[0]
        original_expiry = first.expires_at

        await client.post(f"{api}/auth/refresh")

        sessions = (await db.scalars(select(UserSession))).all()
        assert len(sessions) == 2
        assert all(s.expires_at == original_expiry for s in sessions)

    async def test_reuse_of_rotated_token_revokes_lineage(
        self, client: AsyncClient, api: str, db: AsyncSession, user: User
    ) -> None:
        """The central defence against a stolen refresh token.

        Replaying an already-rotated token means two parties hold it, so the whole
        chain is revoked rather than guessing which one is legitimate.

        Critically, this asserts the *successor* token is dead too. An earlier
        version of the handler returned 401 correctly but had its revocation
        rolled back by that very exception, so the attacker's chain kept working
        while the endpoint looked correct.
        """
        login = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        stolen = login.cookies[REFRESH_COOKIE_NAME]

        rotated = await client.post(f"{api}/auth/refresh")
        assert rotated.status_code == 200
        successor = rotated.cookies[REFRESH_COOKIE_NAME]

        # Replay the old token. The cookie jar now holds the rotated token and the
        # endpoint prefers the cookie, so it must be cleared - otherwise this is
        # just a second legitimate refresh.
        client.cookies.clear()
        replay = await client.post(f"{api}/auth/refresh", json={"refresh_token": stolen})
        assert replay.status_code == 401

        # Database state is asserted here, before any further request. Each 401
        # rolls the request session back, and stacking two of them leaves it
        # unusable for direct queries.
        sessions = (
            await db.scalars(select(UserSession).where(UserSession.user_id == user.id))
        ).all()
        assert sessions, "no sessions found"
        assert all(s.revoked_at is not None for s in sessions), (
            "lineage was not fully revoked after reuse"
        )
        assert any(s.revocation_reason == SessionRevocationReason.REUSE_DETECTED for s in sessions)

        # The successor must be dead too - this is the assertion that catches a
        # rolled-back revocation. Last, because it raises and rolls back again.
        client.cookies.clear()
        after = await client.post(f"{api}/auth/refresh", json={"refresh_token": successor})
        assert after.status_code == 401, "successor token survived reuse detection"

    async def test_reuse_detection_is_audited_as_critical(
        self, client: AsyncClient, api: str, db: AsyncSession, user: User
    ) -> None:
        """The audit row must survive the 401 that reports the breach."""
        login = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        stolen = login.cookies[REFRESH_COOKIE_NAME]
        await client.post(f"{api}/auth/refresh")

        client.cookies.clear()
        await client.post(f"{api}/auth/refresh", json={"refresh_token": stolen})

        entries = (
            await db.scalars(
                select(AuditLog).where(AuditLog.action == AuditAction.SESSION_REUSE_DETECTED)
            )
        ).all()
        assert entries, "reuse detection wrote no audit row"
        assert entries[0].severity == AuditSeverity.CRITICAL

    async def test_one_login_leaves_one_live_session_however_often_it_refreshes(
        self, client: AsyncClient, api: str, db: AsyncSession, user: User
    ) -> None:
        """Rotation replaces a session; it must never add one.

        Each rotation mints a successor and revokes its predecessor, so a lineage holds
        exactly one live row no matter how many times the tab reloads. Asserted because the
        Settings screen listed six live sessions after a single sign-in - every reload was
        leaving its predecessor behind.
        """
        login = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        assert login.status_code == 200, login.text

        for _ in range(5):
            rotated = await client.post(f"{api}/auth/refresh")
            assert rotated.status_code == 200, rotated.text

        live = (
            await db.scalars(
                select(UserSession).where(
                    UserSession.user_id == user.id,
                    UserSession.revoked_at.is_(None),
                )
            )
        ).all()
        assert len(live) == 1, f"{len(live)} live sessions after one login and five refreshes"

        # And the survivor is the newest generation, not the original.
        assert live[0].generation == 5

    async def test_two_refreshes_on_one_token_cannot_both_succeed(
        self, client: AsyncClient, api: str, db: AsyncSession, user: User
    ) -> None:
        """The race that produced the duplicates.

        Two refreshes presenting the *same* token both read it as valid, both mint a
        successor, and both revoke the original - two live sessions from one login. The row
        is locked for the duration of the rotation now, so the second must serialise behind
        the first and be rejected as a replay.
        """
        login = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        token = login.cookies[REFRESH_COOKIE_NAME]

        first = await client.post(f"{api}/auth/refresh", json={"refresh_token": token})
        assert first.status_code == 200, first.text

        # Cookies cleared first, or the client simply presents the *rotated* cookie the
        # first call set and performs a second legitimate refresh - the endpoint prefers the
        # cookie over the body. Clearing them is what makes this a replay of the original
        # token rather than a fresh one.
        client.cookies.clear()
        second = await client.post(f"{api}/auth/refresh", json={"refresh_token": token})
        assert second.status_code == 401, second.text

        # Never two live sessions - reuse detection revokes the lineage, so zero is the
        # correct answer here, and the user is asked to sign in again.
        live = (
            await db.scalars(
                select(UserSession).where(
                    UserSession.user_id == user.id,
                    UserSession.revoked_at.is_(None),
                )
            )
        ).all()
        assert len(live) <= 1, f"{len(live)} live sessions after a replayed token"

    async def test_unknown_refresh_token_rejected(self, client: AsyncClient, api: str) -> None:
        response = await client.post(
            f"{api}/auth/refresh", json={"refresh_token": "totally-made-up-token"}
        )
        assert response.status_code == 401

    async def test_missing_refresh_token_rejected(self, client: AsyncClient, api: str) -> None:
        response = await client.post(f"{api}/auth/refresh")
        assert response.status_code == 401

    async def test_expired_session_rejected(
        self, client: AsyncClient, api: str, db: AsyncSession, user: User
    ) -> None:
        login = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        token = login.cookies[REFRESH_COOKIE_NAME]

        session = (await db.scalars(select(UserSession))).all()[0]
        session.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
        await db.flush()

        response = await client.post(f"{api}/auth/refresh", json={"refresh_token": token})
        assert response.status_code == 401


# =============================================================================
# Logout
# =============================================================================
class TestLogout:
    async def test_logout_revokes_session_and_access_token(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """The access token must die immediately, not at its TTL."""
        assert (await authed_client.get(f"{api}/auth/me")).status_code == 200

        assert (await authed_client.post(f"{api}/auth/logout")).status_code == 200

        after = await authed_client.get(f"{api}/auth/me")
        assert after.status_code == 401, "access token still worked after logout"

    async def test_logout_all_devices(
        self, client: AsyncClient, api: str, db: AsyncSession, user: User, organization
    ) -> None:
        first = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        second = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        second_token = second.json()["access_token"]

        client.headers["Authorization"] = f"Bearer {first.json()['access_token']}"
        response = await client.post(f"{api}/auth/logout", json={"all_devices": True})
        assert response.status_code == 200

        # The other device's token must also be dead, via the epoch bump.
        client.headers["Authorization"] = f"Bearer {second_token}"
        assert (await client.get(f"{api}/auth/me")).status_code == 401


# =============================================================================
# Password reset and change
# =============================================================================
class TestPasswordReset:
    async def test_forgot_password_never_reveals_existence(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        real = await client.post(f"{api}/auth/forgot-password", json={"email": user.email})
        fake = await client.post(
            f"{api}/auth/forgot-password", json={"email": "nobody@example.com"}
        )
        assert real.status_code == fake.status_code == 200
        assert real.json() == fake.json()

    async def test_reset_sets_new_password_and_revokes_sessions(
        self, client: AsyncClient, api: str, db: AsyncSession, user: User
    ) -> None:
        await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )

        code = await password_reset_otp_store.issue(user.email)

        new_password = "Quixotic-Ledger-Verse-77"
        response = await client.post(
            f"{api}/auth/reset-password",
            json={"email": user.email, "code": code, "new_password": new_password},
        )
        assert response.status_code == 200, response.text

        # Old password no longer works; new one does.
        client.cookies.clear()
        assert (
            await client.post(
                f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
            )
        ).status_code == 401
        assert (
            await client.post(
                f"{api}/auth/login", json={"email": user.email, "password": new_password}
            )
        ).status_code == 200

    async def test_reset_code_is_single_use(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        code = await password_reset_otp_store.issue(user.email)

        assert (
            await client.post(
                f"{api}/auth/reset-password",
                json={
                    "email": user.email,
                    "code": code,
                    "new_password": "Quixotic-Ledger-Verse-77",
                },
            )
        ).status_code == 200

        second = await client.post(
            f"{api}/auth/reset-password",
            json={"email": user.email, "code": code, "new_password": "Another-Valid-Phrase-88"},
        )
        assert second.status_code == 401

    async def test_wrong_reset_code_rejected(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        await password_reset_otp_store.issue(user.email)
        response = await client.post(
            f"{api}/auth/reset-password",
            json={
                "email": user.email,
                "code": "000000",
                "new_password": "Quixotic-Ledger-Verse-77",
            },
        )
        assert response.status_code == 401

    async def test_sign_in_code_cannot_reset_a_password(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        """The two emailed codes must not be interchangeable.

        A sign-in code is issued on an unauthenticated request to anyone who knows
        the address. If it also worked here, that request would be enough to take
        the account over - so the namespaces are separate and this must fail.
        """
        sign_in_code = await otp_store.issue(user.email)

        response = await client.post(
            f"{api}/auth/reset-password",
            json={
                "email": user.email,
                "code": sign_in_code,
                "new_password": "Quixotic-Ledger-Verse-77",
            },
        )
        assert response.status_code == 401

        # And the sign-in code still works for what it was minted for - the failed
        # attempt above must not have consumed it.
        assert (
            await client.post(
                f"{api}/auth/otp/verify", json={"email": user.email, "code": sign_in_code}
            )
        ).status_code == 200

    async def test_reset_code_brute_force_is_budgeted(self, user: User) -> None:
        """Six digits must not be guessable. Same budget as the sign-in code.

        Exercised against the store rather than the endpoint, because repeated wrong
        guesses there also trip the account lockout, which would mask whether the
        *code* itself was destroyed.
        """
        from app.core.redis import RedisKey
        from app.modules.auth.token_store import MAX_OTP_ATTEMPTS

        code = await password_reset_otp_store.issue(user.email)

        for _ in range(MAX_OTP_ATTEMPTS):
            assert await password_reset_otp_store.verify(user.email, "000000") is False

        assert await get_redis().get(RedisKey.otp("password-reset", user.email)) is None
        assert await password_reset_otp_store.verify(user.email, code) is False

    async def test_reset_enforces_password_policy(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        code = await password_reset_otp_store.issue(user.email)
        response = await client.post(
            f"{api}/auth/reset-password",
            json={"email": user.email, "code": code, "new_password": "password123"},
        )
        assert response.status_code == 422

    # -- A rejected password must not cost the user their code -----------------
    #
    # The bug these cover: the code was consumed *before* the password was validated, so a
    # correct code plus a refused password destroyed the code. The retry then reported "This
    # reset code is invalid or has expired" - blaming the part the user had got right - and
    # the freshly requested code would be burned by the next policy failure just the same.
    # Reported from the UI as "the code becomes invalid very quickly".
    async def test_a_policy_rejected_password_leaves_the_code_usable(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        code = await password_reset_otp_store.issue(user.email)

        weak = await client.post(
            f"{api}/auth/reset-password",
            json={"email": user.email, "code": code, "new_password": "password123"},
        )
        assert weak.status_code == 422

        # Same code, corrected password. This is the assertion that failed before.
        retry = await client.post(
            f"{api}/auth/reset-password",
            json={
                "email": user.email,
                "code": code,
                "new_password": "Quixotic-Ledger-Verse-77",
            },
        )
        assert retry.status_code == 200, retry.text

    async def test_reusing_the_current_password_leaves_the_code_usable(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        """The second way `_apply_new_password` refuses, and it burned the code too."""
        code = await password_reset_otp_store.issue(user.email)

        same = await client.post(
            f"{api}/auth/reset-password",
            json={"email": user.email, "code": code, "new_password": TEST_PASSWORD},
        )
        assert same.status_code == 422

        retry = await client.post(
            f"{api}/auth/reset-password",
            json={
                "email": user.email,
                "code": code,
                "new_password": "Quixotic-Ledger-Verse-77",
            },
        )
        assert retry.status_code == 200, retry.text

    async def test_policy_failures_do_not_exhaust_the_attempt_budget(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        """More policy failures than the guess budget, and the code still works.

        The attempt counter bounds *guessing*. Presenting the correct code is not a guess, so
        it must not spend budget - otherwise the brute-force limiter destroys the code of the
        one person it is not aimed at.
        """
        from app.modules.auth.token_store import MAX_OTP_ATTEMPTS

        code = await password_reset_otp_store.issue(user.email)

        for _ in range(MAX_OTP_ATTEMPTS + 2):
            rejected = await client.post(
                f"{api}/auth/reset-password",
                json={"email": user.email, "code": code, "new_password": "password123"},
            )
            assert rejected.status_code == 422

        accepted = await client.post(
            f"{api}/auth/reset-password",
            json={
                "email": user.email,
                "code": code,
                "new_password": "Quixotic-Ledger-Verse-77",
            },
        )
        assert accepted.status_code == 200, accepted.text

    async def test_wrong_guesses_still_destroy_the_code(self, user: User) -> None:
        """The refund must not become a bypass: wrong codes still spend the budget."""
        from app.core.redis import RedisKey
        from app.modules.auth.token_store import MAX_OTP_ATTEMPTS

        code = await password_reset_otp_store.issue(user.email)

        for _ in range(MAX_OTP_ATTEMPTS):
            assert await password_reset_otp_store.check(user.email, "000000") is False

        assert await get_redis().get(RedisKey.otp("password-reset", user.email)) is None
        assert await password_reset_otp_store.check(user.email, code) is False

    async def test_check_does_not_spend_the_code_but_consume_does(self, user: User) -> None:
        """The split that fixes the bug, asserted directly on the store."""
        code = await password_reset_otp_store.issue(user.email)

        assert await password_reset_otp_store.check(user.email, code) is True
        assert await password_reset_otp_store.check(user.email, code) is True

        await password_reset_otp_store.consume(user.email)
        assert await password_reset_otp_store.check(user.email, code) is False


class TestChangePassword:
    async def test_requires_correct_current_password(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        response = await authed_client.post(
            f"{api}/auth/change-password",
            json={
                "current_password": "definitely-not-it",
                "new_password": "Quixotic-Ledger-Verse-77",
            },
        )
        assert response.status_code == 401

    async def test_changes_password_and_revokes_all_sessions(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        response = await authed_client.post(
            f"{api}/auth/change-password",
            json={
                "current_password": TEST_PASSWORD,
                "new_password": "Quixotic-Ledger-Verse-77",
            },
        )
        assert response.status_code == 200, response.text

        # The caller's own token is invalidated too - a password change signs out
        # everywhere, including here.
        assert (await authed_client.get(f"{api}/auth/me")).status_code == 401

    async def test_rejects_reusing_the_same_password(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        response = await authed_client.post(
            f"{api}/auth/change-password",
            json={"current_password": TEST_PASSWORD, "new_password": TEST_PASSWORD},
        )
        assert response.status_code == 422


# =============================================================================
# Passwordless
# =============================================================================
class TestPasswordless:
    async def test_magic_link_email_points_at_the_verify_route(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The emailed link must be the one that *consumes* the token.

        `/magic-link` is the request form and ignores search parameters, so a link
        aimed there re-showed the form and signed nobody in. Asserted rather than
        eyeballed because nothing else fails when it regresses - the mail sends, the
        link opens, and the user simply never gets a session.
        """
        from app.modules.notifications import email as mailer

        captured: dict[str, str] = {}

        async def fake_send_email(**kwargs: str) -> bool:
            captured.update(kwargs)
            return True

        monkeypatch.setattr(mailer, "send_email", fake_send_email)

        assert await mailer.send_magic_link_email(
            to="someone@example.com", name="Ada", token="tok-123"
        )
        assert "/magic-link/verify?token=tok-123" in captured["text"]
        assert "/magic-link/verify?token=tok-123" in captured["html"]

    async def test_magic_link_signs_user_in(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        from app.modules.auth.token_store import magic_link_store

        token = await magic_link_store().issue({"user_id": str(user.id), "redirect_path": None})

        response = await client.post(f"{api}/auth/magic-link/verify", json={"token": token})
        assert response.status_code == 200, response.text
        assert response.json()["access_token"]

    async def test_magic_link_token_is_single_use(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        from app.modules.auth.token_store import magic_link_store

        token = await magic_link_store().issue({"user_id": str(user.id), "redirect_path": None})
        assert (
            await client.post(f"{api}/auth/magic-link/verify", json={"token": token})
        ).status_code == 200
        assert (
            await client.post(f"{api}/auth/magic-link/verify", json={"token": token})
        ).status_code == 401

    async def test_magic_link_request_is_neutral(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        real = await client.post(f"{api}/auth/magic-link", json={"email": user.email})
        fake = await client.post(f"{api}/auth/magic-link", json={"email": "nobody@example.com"})
        assert real.json() == fake.json()

    async def test_magic_link_rejects_absolute_redirect(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        """Open-redirect guard."""
        response = await client.post(
            f"{api}/auth/magic-link",
            json={"email": user.email, "redirect_path": "https://evil.test/steal"},
        )
        assert response.status_code == 422

    async def test_magic_link_rejects_protocol_relative_redirect(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        response = await client.post(
            f"{api}/auth/magic-link",
            json={"email": user.email, "redirect_path": "//evil.test/steal"},
        )
        assert response.status_code == 422

    # -------------------------------------------------------------------------
    # Device sign-in: the app sends the link but never receives it
    # -------------------------------------------------------------------------
    @pytest.fixture
    def sent_magic_links(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        """Captures what would have been emailed.

        The token cannot be read back out of Redis - only its digest is stored - so
        intercepting the send is the only way to hold the link a real user would click,
        and it also proves the endpoint put the device handle *into* that link.
        """
        from app.modules.notifications import email as mailer

        calls: list[dict[str, Any]] = []

        async def fake_send_magic_link_email(**kwargs: Any) -> bool:
            calls.append(kwargs)
            return True

        monkeypatch.setattr(mailer, "send_magic_link_email", fake_send_magic_link_email)
        return calls

    async def test_device_sign_in_completes_when_the_link_is_opened(
        self, client: AsyncClient, api: str, user: User, sent_magic_links: list[dict[str, Any]]
    ) -> None:
        """The whole point: the app that asked ends up signed in, not just the browser.

        One client stands in for two here - `started` is the desktop app, and the
        verify call is whatever browser opens the mail.
        """
        started = await client.post(f"{api}/auth/magic-link/device", json={"email": user.email})
        assert started.status_code == 200, started.text
        handle = started.json()["device_handle"]
        assert started.json()["user_code"]

        # Nothing has opened the link yet.
        pending = await client.post(
            f"{api}/auth/magic-link/device/poll", json={"device_handle": handle}
        )
        assert pending.status_code == 200
        assert pending.json()["status"] == "pending"

        # The browser opens it. The code in the mail is the one the app is showing.
        assert sent_magic_links[-1]["user_code"] == started.json()["user_code"]
        opened = await client.post(
            f"{api}/auth/magic-link/verify", json={"token": sent_magic_links[-1]["token"]}
        )
        assert opened.status_code == 200, opened.text
        # ...and the browser is told to expect nothing, rather than being signed in.
        assert opened.json()["device_approved"] is True
        assert opened.json()["user_code"] == started.json()["user_code"]
        assert "access_token" not in opened.json()

        # ...and now the app's poll returns a real session.
        claimed = await client.post(
            f"{api}/auth/magic-link/device/poll", json={"device_handle": handle}
        )
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["access_token"]
        assert claimed.json()["user"]["email"] == user.email

    async def test_device_handle_is_single_use(
        self, client: AsyncClient, api: str, user: User, sent_magic_links: list[dict[str, Any]]
    ) -> None:
        """A claimed handle must not mint a second session."""
        started = await client.post(f"{api}/auth/magic-link/device", json={"email": user.email})
        handle = started.json()["device_handle"]

        await client.post(
            f"{api}/auth/magic-link/verify", json={"token": sent_magic_links[-1]["token"]}
        )

        assert (
            await client.post(f"{api}/auth/magic-link/device/poll", json={"device_handle": handle})
        ).status_code == 200
        second = await client.post(
            f"{api}/auth/magic-link/device/poll", json={"device_handle": handle}
        )
        assert second.status_code == 401

    async def test_an_ordinary_magic_link_approves_no_device(
        self, client: AsyncClient, api: str, user: User, sent_magic_links: list[dict[str, Any]]
    ) -> None:
        """The browser flow must not carry a device handle at all."""
        await client.post(f"{api}/auth/magic-link", json={"email": user.email})
        assert sent_magic_links[-1].get("user_code") is None

    async def test_opening_an_apps_link_does_not_sign_the_browser_in(
        self, client: AsyncClient, api: str, user: User, sent_magic_links: list[dict[str, Any]]
    ) -> None:
        """Whoever requested the link is the only one who signs in.

        One click used to leave sessions on two machines - the app it was meant for and
        whatever browser happened to open the mail. Only the first was asked for.
        """
        await client.post(f"{api}/auth/magic-link/device", json={"email": user.email})
        await client.post(
            f"{api}/auth/magic-link/verify", json={"token": sent_magic_links[-1]["token"]}
        )

        # No refresh cookie was set, so the browser cannot mint an access token either.
        assert REFRESH_COOKIE_NAME not in client.cookies
        assert (await client.post(f"{api}/auth/refresh")).status_code == 401

    async def test_a_browser_requested_link_still_signs_the_browser_in(
        self, client: AsyncClient, api: str, user: User, sent_magic_links: list[dict[str, Any]]
    ) -> None:
        """The other half of the rule - the browser flow is untouched."""
        await client.post(f"{api}/auth/magic-link", json={"email": user.email})

        signed_in = await client.post(
            f"{api}/auth/magic-link/verify", json={"token": sent_magic_links[-1]["token"]}
        )
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["access_token"]
        assert REFRESH_COOKIE_NAME in client.cookies

    async def test_unknown_device_handle_is_rejected_not_left_pending(
        self, client: AsyncClient, api: str
    ) -> None:
        """Expired and pending are different answers - see poll_device_sign_in."""
        response = await client.post(
            f"{api}/auth/magic-link/device/poll", json={"device_handle": "not-a-real-handle"}
        )
        assert response.status_code == 401

    async def test_device_sign_in_is_neutral_about_unknown_addresses(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        """A handle only for real accounts would be an enumeration oracle."""
        real = await client.post(f"{api}/auth/magic-link/device", json={"email": user.email})
        fake = await client.post(
            f"{api}/auth/magic-link/device", json={"email": "nobody@example.com"}
        )

        assert real.status_code == fake.status_code == 200
        assert fake.json()["device_handle"]
        assert real.json().keys() == fake.json().keys()

        # And the unknown one can never be approved, so it stays pending.
        pending = await client.post(
            f"{api}/auth/magic-link/device/poll",
            json={"device_handle": fake.json()["device_handle"]},
        )
        assert pending.json()["status"] == "pending"

    async def test_device_sign_in_still_requires_the_second_factor(
        self,
        authed_client: AsyncClient,
        api: str,
        user: User,
        sent_magic_links: list[dict[str, Any]],
    ) -> None:
        """Opening the link is one factor. 2FA is owed in the app, not the browser.

        Without this, the device flow would be a way onto a 2FA-protected account with
        nothing but mailbox access.
        """
        import pyotp

        secret = (await authed_client.post(f"{api}/auth/2fa/setup")).json()["secret"]
        assert (
            await authed_client.post(
                f"{api}/auth/2fa/enable", json={"code": pyotp.TOTP(secret).now()}
            )
        ).status_code == 200
        authed_client.headers.pop("Authorization", None)
        authed_client.cookies.clear()

        started = await authed_client.post(
            f"{api}/auth/magic-link/device", json={"email": user.email}
        )
        handle = started.json()["device_handle"]

        # Opening the link approves the app and signs nothing in here, so the
        # browser is never asked for a second factor it does not need.
        opened = await authed_client.post(
            f"{api}/auth/magic-link/verify", json={"token": sent_magic_links[-1]["token"]}
        )
        assert opened.status_code == 200, opened.text
        assert opened.json()["device_approved"] is True

        challenged = await authed_client.post(
            f"{api}/auth/magic-link/device/poll", json={"device_handle": handle}
        )
        assert challenged.status_code == 200, challenged.text
        assert challenged.json()["two_factor_required"] is True

        # The app finishes with a TOTP code, through the ordinary endpoint.
        finished = await authed_client.post(
            f"{api}/auth/login/2fa",
            json={
                "challenge_id": challenged.json()["challenge_id"],
                "code": pyotp.TOTP(secret).now(),
            },
        )
        assert finished.status_code == 200, finished.text
        assert finished.json()["access_token"]

    async def test_device_sign_in_email_carries_the_user_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The code is the only way the reader can spot a link they did not ask for."""
        from app.modules.notifications import email as mailer

        captured: dict[str, str] = {}

        async def fake_send_email(**kwargs: str) -> bool:
            captured.update(kwargs)
            return True

        monkeypatch.setattr(mailer, "send_email", fake_send_email)

        assert await mailer.send_magic_link_email(
            to="someone@example.com", name="Ada", token="tok-123", user_code="4F2K"
        )
        assert "4F2K" in captured["text"]
        assert "4F2K" in captured["html"]
        # And the warning that makes the code actionable.
        assert "do not open the link" in captured["text"].lower()

    async def test_otp_signs_user_in(self, client: AsyncClient, api: str, user: User) -> None:
        code = await otp_store.issue(user.email)

        response = await client.post(
            f"{api}/auth/otp/verify", json={"email": user.email, "code": code}
        )
        assert response.status_code == 200, response.text

    async def test_otp_is_single_use(self, client: AsyncClient, api: str, user: User) -> None:
        code = await otp_store.issue(user.email)
        assert (
            await client.post(f"{api}/auth/otp/verify", json={"email": user.email, "code": code})
        ).status_code == 200
        assert (
            await client.post(f"{api}/auth/otp/verify", json={"email": user.email, "code": code})
        ).status_code == 401

    async def test_wrong_otp_rejected(self, client: AsyncClient, api: str, user: User) -> None:
        await otp_store.issue(user.email)
        response = await client.post(
            f"{api}/auth/otp/verify", json={"email": user.email, "code": "000000"}
        )
        assert response.status_code == 401

    async def test_otp_attempt_budget_destroys_code(self, user: User) -> None:
        """A 6-digit code must not be brute-forceable.

        Exercised against the store rather than the endpoint: repeated wrong
        guesses at ``/auth/otp/verify`` also trip the account lockout, which would
        mask whether the *code* itself was invalidated. Both defences are wanted;
        this test isolates the OTP budget, and
        ``TestLogin::test_lockout_after_repeated_failures`` covers the other.
        """
        from app.core.redis import RedisKey
        from app.modules.auth.token_store import MAX_OTP_ATTEMPTS

        code = await otp_store.issue(user.email)

        for _ in range(MAX_OTP_ATTEMPTS):
            assert await otp_store.verify(user.email, "000000") is False

        # Budget spent: the stored code is destroyed, not merely rejected.
        assert await get_redis().get(RedisKey.otp("login", user.email)) is None
        assert await otp_store.verify(user.email, code) is False

    async def test_otp_brute_force_locks_the_account(
        self, client: AsyncClient, api: str, user: User
    ) -> None:
        """Wrong OTP guesses must count toward the same lockout as passwords."""
        from app.core.config import settings

        await otp_store.issue(user.email)

        for _ in range(settings.max_login_attempts):
            await client.post(
                f"{api}/auth/otp/verify", json={"email": user.email, "code": "000000"}
            )

        locked = await client.post(
            f"{api}/auth/otp/verify", json={"email": user.email, "code": "000000"}
        )
        assert locked.status_code == 423


# =============================================================================
# Two-factor authentication
# =============================================================================
class TestTwoFactor:
    async def test_full_enrolment_and_login_flow(
        self, authed_client: AsyncClient, api: str, db: AsyncSession, user: User
    ) -> None:
        import pyotp

        setup = await authed_client.post(f"{api}/auth/2fa/setup")
        assert setup.status_code == 200, setup.text
        body = setup.json()
        assert body["secret"]
        assert body["provisioning_uri"].startswith("otpauth://totp/")
        assert body["qr_code"].startswith("data:image/png;base64,")

        # Not yet in force - enrolment is unconfirmed.
        await db.refresh(user)
        assert user.is_two_factor_enabled is False

        code = pyotp.TOTP(body["secret"]).now()
        enable = await authed_client.post(f"{api}/auth/2fa/enable", json={"code": code})
        assert enable.status_code == 200, enable.text
        recovery_codes = enable.json()["recovery_codes"]
        assert len(recovery_codes) == 10

        await db.refresh(user)
        assert user.is_two_factor_enabled is True
        # Recovery codes must be stored hashed, never in the clear.
        assert all(code not in str(user.recovery_code_hashes) for code in recovery_codes)

        # Logging in now requires the second factor.
        authed_client.headers.pop("Authorization", None)
        authed_client.cookies.clear()
        login = await authed_client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        assert login.status_code == 200
        assert login.json()["two_factor_required"] is True
        challenge_id = login.json()["challenge_id"]

        completed = await authed_client.post(
            f"{api}/auth/login/2fa",
            json={"challenge_id": challenge_id, "code": pyotp.TOTP(body["secret"]).now()},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["access_token"]

    async def test_enable_rejects_wrong_code(
        self, authed_client: AsyncClient, api: str, db: AsyncSession, user: User
    ) -> None:
        """Self-lockout guard: enrolment must prove the app works."""
        await authed_client.post(f"{api}/auth/2fa/setup")

        response = await authed_client.post(f"{api}/auth/2fa/enable", json={"code": "000000"})
        assert response.status_code == 401

        await db.refresh(user)
        assert user.is_two_factor_enabled is False

    async def test_recovery_code_works_and_is_consumed(
        self, authed_client: AsyncClient, api: str, db: AsyncSession, user: User
    ) -> None:
        import pyotp

        setup = (await authed_client.post(f"{api}/auth/2fa/setup")).json()
        enable = await authed_client.post(
            f"{api}/auth/2fa/enable", json={"code": pyotp.TOTP(setup["secret"]).now()}
        )
        recovery_code = enable.json()["recovery_codes"][0]

        authed_client.headers.pop("Authorization", None)
        authed_client.cookies.clear()
        login = await authed_client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        challenge_id = login.json()["challenge_id"]

        first = await authed_client.post(
            f"{api}/auth/login/2fa",
            json={"challenge_id": challenge_id, "code": recovery_code},
        )
        assert first.status_code == 200, first.text

        await db.refresh(user)
        assert len(user.recovery_code_hashes) == 9, "code was not consumed"

        # The same code must not work twice.
        login2 = await authed_client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        second = await authed_client.post(
            f"{api}/auth/login/2fa",
            json={
                "challenge_id": login2.json()["challenge_id"],
                "code": recovery_code,
            },
        )
        assert second.status_code == 401

    async def test_totp_code_cannot_be_replayed(
        self, authed_client: AsyncClient, api: str, user: User
    ) -> None:
        """A code stays valid ~90s, so single-use enforcement is required."""
        import pyotp

        setup = (await authed_client.post(f"{api}/auth/2fa/setup")).json()
        await authed_client.post(
            f"{api}/auth/2fa/enable", json={"code": pyotp.TOTP(setup["secret"]).now()}
        )

        authed_client.headers.pop("Authorization", None)
        authed_client.cookies.clear()

        code = pyotp.TOTP(setup["secret"]).now()

        login = await authed_client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        first = await authed_client.post(
            f"{api}/auth/login/2fa",
            json={"challenge_id": login.json()["challenge_id"], "code": code},
        )
        assert first.status_code == 200

        login2 = await authed_client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        replay = await authed_client.post(
            f"{api}/auth/login/2fa",
            json={"challenge_id": login2.json()["challenge_id"], "code": code},
        )
        assert replay.status_code == 401, "TOTP code was accepted twice"

    async def test_disable_requires_password(self, authed_client: AsyncClient, api: str) -> None:
        import pyotp

        setup = (await authed_client.post(f"{api}/auth/2fa/setup")).json()
        await authed_client.post(
            f"{api}/auth/2fa/enable", json={"code": pyotp.TOTP(setup["secret"]).now()}
        )

        wrong = await authed_client.post(
            f"{api}/auth/2fa/disable", json={"password": "not-the-password"}
        )
        assert wrong.status_code == 401

        correct = await authed_client.post(
            f"{api}/auth/2fa/disable", json={"password": TEST_PASSWORD}
        )
        assert correct.status_code == 200


# =============================================================================
# Sessions
# =============================================================================
class TestSessions:
    async def test_lists_sessions_and_flags_current(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        response = await authed_client.get(f"{api}/auth/sessions")
        assert response.status_code == 200, response.text
        sessions = response.json()
        assert len(sessions) >= 1
        assert sum(s["is_current"] for s in sessions) == 1

    async def test_cannot_revoke_another_users_session(
        self, authed_client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """Ownership check, not just existence - otherwise ids are guessable."""
        import datetime as dt2

        from app.core.security import generate_token, hash_password, hash_token

        other = User(
            email="other@example.com",
            full_name="Other Person",
            password_hash=hash_password(TEST_PASSWORD),
            email_verified_at=dt2.datetime.now(dt2.UTC),
        )
        db.add(other)
        await db.flush()

        foreign_session = UserSession(
            user_id=other.id,
            refresh_token_hash=hash_token(generate_token()),
            expires_at=dt2.datetime.now(dt2.UTC) + dt2.timedelta(days=7),
        )
        db.add(foreign_session)
        await db.flush()

        response = await authed_client.delete(f"{api}/auth/sessions/{foreign_session.id}")
        assert response.status_code == 404

        await db.refresh(foreign_session)
        assert foreign_session.revoked_at is None


# =============================================================================
# Public metadata
# =============================================================================
class TestPasswordPolicyEndpoint:
    async def test_served_without_authentication(self, client: AsyncClient, api: str) -> None:
        """The client's hints must come from the server, not be duplicated."""
        response = await client.get(f"{api}/auth/password-policy")
        assert response.status_code == 200
        body = response.json()
        assert body["min_length"] == 6
        assert body["requires_uppercase"] is True
        assert body["requires_lowercase"] is True
        assert body["requires_special"] is True
        assert body["requires_digit"] is False
        assert body["special_characters"]
        assert body["rules"]


# =============================================================================
# Error envelope and middleware
# =============================================================================
class TestErrorEnvelope:
    async def test_errors_share_one_shape(self, client: AsyncClient, api: str) -> None:
        response = await client.get(f"{api}/auth/me")
        error = response.json()["error"]
        assert set(error) >= {"code", "message"}
        assert isinstance(error["code"], str)

    async def test_request_id_is_returned_and_echoed(self, client: AsyncClient, api: str) -> None:
        response = await client.get(f"{api}/auth/password-policy")
        assert response.headers.get("X-Request-ID")

        supplied = "test-request-id-12345"
        echoed = await client.get(f"{api}/auth/password-policy", headers={"X-Request-ID": supplied})
        assert echoed.headers["X-Request-ID"] == supplied

    async def test_security_headers_present(self, client: AsyncClient, api: str) -> None:
        response = await client.get(f"{api}/auth/password-policy")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "Content-Security-Policy" in response.headers

    @pytest.mark.parametrize("path", ["/health/live", "/health/ready", "/health"])
    async def test_health_endpoints_are_public(self, client: AsyncClient, path: str) -> None:
        response = await client.get(path)
        assert response.status_code == 200, response.text
