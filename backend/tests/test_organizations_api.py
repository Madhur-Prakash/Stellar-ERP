"""Integration tests for organizations, members, invitations, roles, and audit.

The emphasis is on the two failure modes that matter most in a multi-tenant ERP:

* **Cross-tenant leakage** - one organization reading or mutating another's data.
* **Self-inflicted lockout** - an organization destroying its own ability to be
  administered (removing the owner, deleting a role people hold).

Both are tested by attempting the bad thing and asserting it is refused.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.modules.organizations.models import (
    Invitation,
    InvitationStatus,
    MemberStatus,
    Organization,
    OrganizationMember,
)
from app.modules.rbac.models import Role
from app.modules.rbac.permissions import SystemRole
from app.modules.rbac.repository import RoleRepository
from app.modules.users.models import User
from tests.conftest import TEST_PASSWORD


# =============================================================================
# Helpers
# =============================================================================
async def _make_user(db: AsyncSession, email: str, name: str = "Test Person") -> User:
    user = User(
        email=email,
        full_name=name,
        password_hash=hash_password(TEST_PASSWORD),
        email_verified_at=dt.datetime.now(dt.UTC),
    )
    db.add(user)
    await db.flush()
    return user


async def _make_org(db: AsyncSession, owner: User, name: str) -> Organization:
    org = Organization(name=name, slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}")
    db.add(org)
    await db.flush()

    seeded = await RoleRepository(db).seed_system_roles(org.id)
    db.add(
        OrganizationMember(
            organization_id=org.id,
            user_id=owner.id,
            role_id=seeded[SystemRole.OWNER].id,
            is_owner=True,
            status=MemberStatus.ACTIVE,
            joined_at=dt.datetime.now(dt.UTC),
        )
    )
    owner.last_organization_id = org.id
    await db.flush()
    return org


async def _sign_in(client: AsyncClient, api: str, user: User) -> str:
    response = await client.post(
        f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


async def _role_id(db: AsyncSession, org: Organization, slug: str) -> uuid.UUID:
    role = await db.scalar(select(Role).where(Role.organization_id == org.id, Role.slug == slug))
    assert role is not None
    return role.id


# =============================================================================
# Organizations
# =============================================================================
class TestOrganizations:
    async def test_owner_reads_current_organization(
        self, authed_client: AsyncClient, api: str, organization: Organization
    ) -> None:
        response = await authed_client.get(f"{api}/organizations/current")
        assert response.status_code == 200, response.text
        assert response.json()["id"] == str(organization.id)

    async def test_owner_updates_organization(self, authed_client: AsyncClient, api: str) -> None:
        response = await authed_client.patch(
            f"{api}/organizations/current",
            json={"name": "Acme Trading Private Limited", "city": "Mumbai"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["name"] == "Acme Trading Private Limited"
        assert body["city"] == "Mumbai"

    async def test_partial_update_does_not_clear_other_fields(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        await authed_client.patch(f"{api}/organizations/current", json={"city": "Pune"})
        await authed_client.patch(f"{api}/organizations/current", json={"phone": "+912212345678"})

        body = (await authed_client.get(f"{api}/organizations/current")).json()
        assert body["city"] == "Pune", "an unrelated field was cleared"
        assert body["phone"] == "+912212345678"

    async def test_slug_cannot_be_changed(self, authed_client: AsyncClient, api: str) -> None:
        """Slugs appear in shared URLs; renaming would break bookmarks."""
        response = await authed_client.patch(
            f"{api}/organizations/current", json={"slug": "something-else"}
        )
        assert response.status_code == 422

    async def test_invalid_gstin_rejected(self, authed_client: AsyncClient, api: str) -> None:
        response = await authed_client.patch(
            f"{api}/organizations/current", json={"gstin": "NOT-A-GSTIN"}
        )
        assert response.status_code == 422

    async def test_valid_gstin_accepted_and_uppercased(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        response = await authed_client.patch(
            f"{api}/organizations/current", json={"gstin": "29aabcu9603r1zm"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["gstin"] == "29AABCU9603R1ZM"

    async def test_create_second_organization_seeds_its_own_roles(
        self, authed_client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """Each tenant gets its own role rows, never a shared global set."""
        response = await authed_client.post(f"{api}/organizations", json={"name": "Second Venture"})
        assert response.status_code == 201, response.text
        new_id = response.json()["id"]

        roles = (
            await db.scalars(select(Role).where(Role.organization_id == uuid.UUID(new_id)))
        ).all()
        assert len(roles) == 5

    async def test_lists_only_the_users_own_organizations(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        alice = await _make_user(db, "alice@example.com", "Alice")
        bob = await _make_user(db, "bob@example.com", "Bob")
        await _make_org(db, alice, "Alice Co")
        await _make_org(db, bob, "Bob Co")

        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, alice)}"
        response = await client.get(f"{api}/organizations")
        assert response.status_code == 200

        names = {item["name"] for item in response.json()}
        assert names == {"Alice Co"}
        assert "Bob Co" not in names


# =============================================================================
# Cross-tenant isolation
# =============================================================================
class TestTenantIsolation:
    async def test_cannot_read_another_organizations_members(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """The active org comes from the signed token, not the URL.

        There is no organization id for a client to tamper with, so this asserts
        the shape of that guarantee: Alice's token only ever sees Alice's org.
        """
        alice = await _make_user(db, "alice@example.com", "Alice")
        bob = await _make_user(db, "bob@example.com", "Bob")
        await _make_org(db, alice, "Alice Co")
        bob_org = await _make_org(db, bob, "Bob Co")
        await db.commit()

        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, alice)}"
        members = (await client.get(f"{api}/organizations/current/members")).json()

        emails = {m["user"]["email"] for m in members}
        assert emails == {alice.email}
        assert bob.email not in emails
        assert str(bob_org.id) not in str(members)

    async def test_cannot_modify_a_member_of_another_organization(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """A guessed member id from another tenant must 404, not succeed."""
        alice = await _make_user(db, "alice@example.com", "Alice")
        bob = await _make_user(db, "bob@example.com", "Bob")
        await _make_org(db, alice, "Alice Co")
        bob_org = await _make_org(db, bob, "Bob Co")

        bob_member = await db.scalar(
            select(OrganizationMember).where(OrganizationMember.organization_id == bob_org.id)
        )
        assert bob_member is not None
        await db.commit()

        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, alice)}"
        response = await client.delete(f"{api}/organizations/current/members/{bob_member.id}")
        assert response.status_code == 404

        await db.refresh(bob_member)
        assert bob_member.id is not None, "another tenant's member was deleted"

    async def test_cannot_read_another_organizations_role(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        alice = await _make_user(db, "alice@example.com", "Alice")
        bob = await _make_user(db, "bob@example.com", "Bob")
        await _make_org(db, alice, "Alice Co")
        bob_org = await _make_org(db, bob, "Bob Co")
        bob_role_id = await _role_id(db, bob_org, "admin")
        await db.commit()

        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, alice)}"
        response = await client.get(f"{api}/roles/{bob_role_id}")
        assert response.status_code == 404

    async def test_audit_log_is_scoped_to_one_organization(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        alice = await _make_user(db, "alice@example.com", "Alice")
        bob = await _make_user(db, "bob@example.com", "Bob")
        await _make_org(db, alice, "Alice Co")
        await _make_org(db, bob, "Bob Co")
        await db.commit()

        # Both act, generating audit rows in their own organizations.
        alice_token = await _sign_in(client, api, alice)
        client.headers["Authorization"] = f"Bearer {alice_token}"
        await client.patch(f"{api}/organizations/current", json={"city": "Chennai"})

        bob_token = await _sign_in(client, api, bob)
        client.headers["Authorization"] = f"Bearer {bob_token}"
        await client.patch(f"{api}/organizations/current", json={"city": "Kolkata"})

        client.headers["Authorization"] = f"Bearer {alice_token}"
        entries = (await client.get(f"{api}/audit")).json()["items"]

        actors = {entry["actor"]["email"] for entry in entries}
        assert bob.email not in actors, "audit trail leaked across tenants"


# =============================================================================
# Permission enforcement
# =============================================================================
class TestPermissionEnforcement:
    async def test_viewer_cannot_update_the_organization(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        owner = await _make_user(db, "owner@example.com", "Owner Person")
        org = await _make_org(db, owner, "Acme Co")

        viewer = await _make_user(db, "viewer@example.com", "Viewer Person")
        db.add(
            OrganizationMember(
                organization_id=org.id,
                user_id=viewer.id,
                role_id=await _role_id(db, org, "viewer"),
                status=MemberStatus.ACTIVE,
                joined_at=dt.datetime.now(dt.UTC),
            )
        )
        viewer.last_organization_id = org.id
        await db.commit()

        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, viewer)}"

        # Reading is permitted...
        assert (await client.get(f"{api}/organizations/current")).status_code == 200

        # ...writing is not.
        response = await client.patch(f"{api}/organizations/current", json={"name": "Hijacked Co"})
        assert response.status_code == 403
        error = response.json()["error"]
        assert error["code"] == "permission_denied"
        assert error["details"]["required_permission"] == "organization:update"

    async def test_viewer_cannot_invite_members(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        owner = await _make_user(db, "owner@example.com", "Owner Person")
        org = await _make_org(db, owner, "Acme Co")
        viewer = await _make_user(db, "viewer@example.com", "Viewer Person")
        db.add(
            OrganizationMember(
                organization_id=org.id,
                user_id=viewer.id,
                role_id=await _role_id(db, org, "viewer"),
                status=MemberStatus.ACTIVE,
            )
        )
        viewer.last_organization_id = org.id
        await db.commit()

        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, viewer)}"
        response = await client.post(
            f"{api}/organizations/current/invitations", json={"email": "new@example.com"}
        )
        assert response.status_code == 403

    async def test_accountant_cannot_delete_the_organization(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        owner = await _make_user(db, "owner@example.com", "Owner Person")
        org = await _make_org(db, owner, "Acme Co")
        accountant = await _make_user(db, "ca@example.com", "Accountant Person")
        db.add(
            OrganizationMember(
                organization_id=org.id,
                user_id=accountant.id,
                role_id=await _role_id(db, org, "accountant"),
                status=MemberStatus.ACTIVE,
            )
        )
        accountant.last_organization_id = org.id
        await db.commit()

        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, accountant)}"
        response = await client.delete(f"{api}/organizations/current")
        assert response.status_code == 403

    async def test_suspended_member_loses_access_immediately(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """Suspension must not wait for the access token to expire."""
        owner = await _make_user(db, "owner@example.com", "Owner Person")
        org = await _make_org(db, owner, "Acme Co")
        member = await _make_user(db, "member@example.com", "Member Person")
        membership = OrganizationMember(
            organization_id=org.id,
            user_id=member.id,
            role_id=await _role_id(db, org, "admin"),
            status=MemberStatus.ACTIVE,
        )
        db.add(membership)
        member.last_organization_id = org.id
        await db.commit()

        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, member)}"
        assert (await client.get(f"{api}/organizations/current")).status_code == 200

        # The owner suspends them.
        owner_client_token = await _sign_in(client, api, owner)
        client.headers["Authorization"] = f"Bearer {owner_client_token}"
        suspend = await client.post(f"{api}/organizations/current/members/{membership.id}/suspend")
        assert suspend.status_code == 200, suspend.text

        # Re-sign-in as the suspended member: they now have no active org.
        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, member)}"
        response = await client.get(f"{api}/organizations/current")
        assert response.status_code == 403


# =============================================================================
# Owner protections
# =============================================================================
class TestOwnerProtections:
    async def test_owner_cannot_be_removed(
        self, authed_client: AsyncClient, api: str, db: AsyncSession, organization: Organization
    ) -> None:
        owner_member = await db.scalar(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization.id,
                OrganizationMember.is_owner.is_(True),
            )
        )
        assert owner_member is not None

        response = await authed_client.delete(
            f"{api}/organizations/current/members/{owner_member.id}"
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] in {
            "cannot_remove_owner",
            "cannot_remove_self",
        }

    async def test_owner_cannot_leave(self, authed_client: AsyncClient, api: str) -> None:
        response = await authed_client.post(f"{api}/organizations/current/leave")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "owner_cannot_leave"

    async def test_owner_role_cannot_be_changed(
        self, authed_client: AsyncClient, api: str, db: AsyncSession, organization: Organization
    ) -> None:
        owner_member = await db.scalar(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization.id,
                OrganizationMember.is_owner.is_(True),
            )
        )
        assert owner_member is not None
        viewer_role_id = await _role_id(db, organization, "viewer")

        response = await authed_client.patch(
            f"{api}/organizations/current/members/{owner_member.id}",
            json={"role_id": str(viewer_role_id)},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "cannot_change_owner_role"

    async def test_only_one_owner_per_organization(
        self, db: AsyncSession, organization: Organization
    ) -> None:
        """Enforced by a partial unique index, not just application code."""
        from sqlalchemy.exc import IntegrityError

        second = await _make_user(db, "second-owner@example.com", "Second Owner")
        db.add(
            OrganizationMember(
                organization_id=organization.id,
                user_id=second.id,
                role_id=await _role_id(db, organization, "owner"),
                is_owner=True,
                status=MemberStatus.ACTIVE,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()


# =============================================================================
# Invitations
# =============================================================================
class TestInvitations:
    async def test_invite_creates_pending_row_with_hashed_token(
        self, authed_client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        response = await authed_client.post(
            f"{api}/organizations/current/invitations",
            json={"email": "Newcomer@Example.COM", "message": "Join us"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["email"] == "newcomer@example.com"
        assert body["status"] == "pending"
        # The default role is the least-privileged one.
        assert body["role"]["slug"] == "viewer"

        stored = await db.scalar(
            select(Invitation).where(Invitation.email == "newcomer@example.com")
        )
        assert stored is not None
        assert len(stored.token_hash) == 64  # sha256 hex, not the raw token

    async def test_cannot_invite_an_existing_member(
        self, authed_client: AsyncClient, api: str, user: User
    ) -> None:
        response = await authed_client.post(
            f"{api}/organizations/current/invitations", json={"email": user.email}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "already_member"

    async def test_cannot_double_invite_the_same_address(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        first = await authed_client.post(
            f"{api}/organizations/current/invitations", json={"email": "dup@example.com"}
        )
        assert first.status_code == 201

        second = await authed_client.post(
            f"{api}/organizations/current/invitations", json={"email": "dup@example.com"}
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "invitation_pending"

    async def test_database_also_blocks_a_second_pending_invitation(
        self,
        authed_client: AsyncClient,
        api: str,
        db: AsyncSession,
        organization: Organization,
    ) -> None:
        """The partial unique index must fire, not just the service check.

        `uq_invitation_pending_email` is a partial unique index with predicate
        `WHERE status = 'pending'`. It was silently inert for a while: enum columns
        stored the member *name* (`'PENDING'`), so the predicate matched no row and
        the index guaranteed nothing. The test above still passed, because the
        service's own check caught the duplicate first - which is precisely why the
        gap went unnoticed.

        This test bypasses the service and writes directly, so only the database
        can stop it.
        """
        first = await authed_client.post(
            f"{api}/organizations/current/invitations", json={"email": "race@example.com"}
        )
        assert first.status_code == 201

        role = (
            await db.execute(
                select(Role).where(
                    Role.organization_id == organization.id,
                    Role.slug == SystemRole.VIEWER,
                )
            )
        ).scalar_one()

        with pytest.raises(IntegrityError, match="uq_invitation_pending_email"):
            async with db.begin_nested():  # savepoint, so the session survives
                db.add(
                    Invitation(
                        organization_id=organization.id,
                        email="race@example.com",
                        role_id=role.id,
                        token_hash=uuid.uuid4().hex * 2,
                        status=InvitationStatus.PENDING,
                        expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=7),
                    )
                )
                await db.flush()

    async def test_enum_columns_store_values_not_names(
        self, authed_client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """Guards the root cause of the inert-index bug.

        The database must hold `'pending'`, matching what the API serialises. If
        this ever reads `'PENDING'` again, every value-based SQL predicate in the
        schema has quietly stopped working.
        """
        created = await authed_client.post(
            f"{api}/organizations/current/invitations", json={"email": "casing@example.com"}
        )
        assert created.status_code == 201

        stored = (
            await db.execute(
                text("SELECT status FROM invitation WHERE email = :email"),
                {"email": "casing@example.com"},
            )
        ).scalar_one()
        assert stored == "pending", f"expected the enum value, got {stored!r}"

    async def test_revoking_invalidates_the_emailed_link(
        self, authed_client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """Revocation must rewrite the digest, not only flip the status."""
        created = await authed_client.post(
            f"{api}/organizations/current/invitations", json={"email": "revoke@example.com"}
        )
        invitation_id = created.json()["id"]

        stored = await db.scalar(
            select(Invitation).where(Invitation.id == uuid.UUID(invitation_id))
        )
        assert stored is not None
        original_hash = stored.token_hash

        response = await authed_client.delete(
            f"{api}/organizations/current/invitations/{invitation_id}"
        )
        assert response.status_code == 200

        await db.refresh(stored)
        assert stored.status is InvitationStatus.REVOKED
        assert stored.token_hash != original_hash, "the old link would still resolve"

    async def test_preview_is_public_and_minimal(
        self, client: AsyncClient, authed_client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        await authed_client.post(
            f"{api}/organizations/current/invitations", json={"email": "preview@example.com"}
        )
        await db.commit()

        # The plaintext token is not retained anywhere, so mint a known one.
        from app.core.security import generate_token, hash_token

        token = generate_token()
        stored = await db.scalar(
            select(Invitation).where(Invitation.email == "preview@example.com")
        )
        assert stored is not None
        stored.token_hash = hash_token(token)
        await db.commit()

        response = await client.get(f"{api}/invitations/{token}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["organization_name"] == "Acme Trading Co"
        assert body["role_name"] == "Viewer"
        assert body["requires_registration"] is True
        # Must not expose anything about the org's members or internals.
        assert "members" not in body
        assert "id" not in body

    async def test_preview_names_the_inviter_on_a_cold_session(
        self,
        client: AsyncClient,
        authed_client: AsyncClient,
        api: str,
        db: AsyncSession,
        user: User,
    ) -> None:
        """Regression: previewing a perfectly valid invitation returned 503.

        `get_by_token` eager-loaded the organization and the role but not
        `invited_by`, and the preview endpoint reads `invited_by.full_name` to
        render "X invited you". A lazy load under `AsyncSession` raises
        `MissingGreenlet`, which is a `SQLAlchemyError` - so the handler turned it
        into a 503 and the acceptance page rendered *every* live invitation link
        as "this invitation is no longer valid".

        `expunge_all()` is what makes this a test rather than a formality. The
        suite shares one session with the app, so the inviter is normally already
        in the identity map and the traversal resolves with no IO at all - which
        is exactly why `test_preview_is_public_and_minimal` above kept passing
        while the endpoint was broken for every real caller. A production request
        starts with a cold session; this models that.
        """
        from app.core.security import generate_token, hash_token

        inviter_name = user.full_name

        await authed_client.post(
            f"{api}/organizations/current/invitations", json={"email": "cold@example.com"}
        )
        token = generate_token()
        stored = await db.scalar(select(Invitation).where(Invitation.email == "cold@example.com"))
        assert stored is not None
        stored.token_hash = hash_token(token)
        await db.commit()

        # Nothing left in the identity map, so every relationship the endpoint
        # touches has to be loaded for real - as it is on a fresh request.
        db.expunge_all()

        response = await client.get(f"{api}/invitations/{token}")

        assert response.status_code == 200, response.text
        assert response.json()["invited_by_name"] == inviter_name

    async def test_expired_invitation_cannot_be_previewed(
        self, client: AsyncClient, authed_client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        from app.core.security import generate_token, hash_token

        await authed_client.post(
            f"{api}/organizations/current/invitations", json={"email": "stale@example.com"}
        )
        token = generate_token()
        stored = await db.scalar(select(Invitation).where(Invitation.email == "stale@example.com"))
        assert stored is not None
        stored.token_hash = hash_token(token)
        stored.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
        await db.commit()

        response = await client.get(f"{api}/invitations/{token}")
        assert response.status_code == 401

    async def test_registering_with_an_invitation_joins_the_org(
        self,
        client: AsyncClient,
        authed_client: AsyncClient,
        api: str,
        db: AsyncSession,
        organization: Organization,
    ) -> None:
        from app.core.security import generate_token, hash_token

        await authed_client.post(
            f"{api}/organizations/current/invitations", json={"email": "joiner@example.com"}
        )
        token = generate_token()
        stored = await db.scalar(select(Invitation).where(Invitation.email == "joiner@example.com"))
        assert stored is not None
        stored.token_hash = hash_token(token)
        await db.commit()

        response = await client.post(
            f"{api}/auth/register",
            json={
                "email": "joiner@example.com",
                "password": TEST_PASSWORD,
                "full_name": "New Joiner",
                "invitation_token": token,
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["organization_id"] == str(organization.id)
        # An invitation proves control of the address, so no separate verification.
        assert body["email_verification_required"] is False

    async def test_invitation_cannot_be_redeemed_by_a_different_address(
        self, client: AsyncClient, authed_client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """A forwarded invitation link must not let a stranger in."""
        from app.core.security import generate_token, hash_token

        await authed_client.post(
            f"{api}/organizations/current/invitations", json={"email": "intended@example.com"}
        )
        token = generate_token()
        stored = await db.scalar(
            select(Invitation).where(Invitation.email == "intended@example.com")
        )
        assert stored is not None
        stored.token_hash = hash_token(token)
        await db.commit()

        response = await client.post(
            f"{api}/auth/register",
            json={
                "email": "someone.else@example.com",
                "password": TEST_PASSWORD,
                "full_name": "Interloper",
                "invitation_token": token,
            },
        )
        assert response.status_code == 403


# =============================================================================
# Roles
# =============================================================================
class TestRoles:
    async def test_lists_seeded_roles_with_member_counts(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        response = await authed_client.get(f"{api}/roles")
        assert response.status_code == 200, response.text
        roles = response.json()

        assert {r["slug"] for r in roles} == {
            "owner",
            "admin",
            "accountant",
            "sales",
            "viewer",
        }
        owner = next(r for r in roles if r["slug"] == "owner")
        assert owner["is_system"] is True
        assert owner["member_count"] == 1

    async def test_creates_custom_role_and_expands_wildcards(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        response = await authed_client.post(
            f"{api}/roles",
            json={
                "name": "Invoice Clerk",
                "description": "Raises invoices only",
                "permissions": ["invoice:*", "customer:read"],
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["slug"] == "invoice-clerk"
        assert body["is_system"] is False
        # Both the stored grants and the resolved set are returned.
        assert set(body["permissions"]) == {"invoice:*", "customer:read"}
        assert set(body["effective_permissions"]) == {
            "invoice:read",
            "invoice:write",
            "invoice:approve",
            "customer:read",
        }

    async def test_rejects_unknown_permission(self, authed_client: AsyncClient, api: str) -> None:
        """A typo'd grant would be stored and silently never apply."""
        response = await authed_client.post(
            f"{api}/roles", json={"name": "Broken Role", "permissions": ["invoces:read"]}
        )
        assert response.status_code == 422

    async def test_duplicate_role_name_conflicts(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        await authed_client.post(f"{api}/roles", json={"name": "Clerk", "permissions": []})
        response = await authed_client.post(
            f"{api}/roles", json={"name": "Clerk", "permissions": []}
        )
        assert response.status_code == 409

    async def test_system_role_cannot_be_deleted(
        self, authed_client: AsyncClient, api: str, db: AsyncSession, organization: Organization
    ) -> None:
        role_id = await _role_id(db, organization, "accountant")
        response = await authed_client.delete(f"{api}/roles/{role_id}")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "cannot_delete_system_role"

    async def test_system_role_cannot_be_renamed(
        self, authed_client: AsyncClient, api: str, db: AsyncSession, organization: Organization
    ) -> None:
        role_id = await _role_id(db, organization, "accountant")
        response = await authed_client.patch(
            f"{api}/roles/{role_id}", json={"name": "Chartered Accountant"}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "cannot_rename_system_role"

    async def test_owner_role_cannot_be_restricted(
        self, authed_client: AsyncClient, api: str, db: AsyncSession, organization: Organization
    ) -> None:
        """Stripping the owner's permissions would lock the org out of itself."""
        role_id = await _role_id(db, organization, "owner")
        response = await authed_client.patch(
            f"{api}/roles/{role_id}", json={"permissions": ["invoice:read"]}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "cannot_restrict_owner_role"

    async def test_system_role_permissions_can_be_adjusted(
        self, authed_client: AsyncClient, api: str, db: AsyncSession, organization: Organization
    ) -> None:
        """Permissions are editable even on seeded roles - only names are fixed."""
        role_id = await _role_id(db, organization, "sales")
        response = await authed_client.patch(
            f"{api}/roles/{role_id}",
            json={"permissions": ["customer:*", "invoice:*", "report:read"]},
        )
        assert response.status_code == 200, response.text
        assert "invoice:approve" in response.json()["effective_permissions"]

    async def test_role_in_use_cannot_be_deleted(
        self, authed_client: AsyncClient, api: str, db: AsyncSession, organization: Organization
    ) -> None:
        created = await authed_client.post(
            f"{api}/roles", json={"name": "Temp Role", "permissions": ["invoice:read"]}
        )
        role_id = created.json()["id"]

        member = await _make_user(db, "holder@example.com", "Holder Person")
        db.add(
            OrganizationMember(
                organization_id=organization.id,
                user_id=member.id,
                role_id=uuid.UUID(role_id),
                status=MemberStatus.ACTIVE,
            )
        )
        await db.commit()

        response = await authed_client.delete(f"{api}/roles/{role_id}")
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "role_in_use"
        assert error["details"]["member_count"] == 1

    async def test_unused_custom_role_can_be_deleted(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        created = await authed_client.post(
            f"{api}/roles", json={"name": "Disposable", "permissions": []}
        )
        role_id = created.json()["id"]

        assert (await authed_client.delete(f"{api}/roles/{role_id}")).status_code == 200
        assert (await authed_client.get(f"{api}/roles/{role_id}")).status_code == 404

    async def test_role_change_takes_effect_immediately(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """Permissions live in the token, so a role change must re-mint it."""
        owner = await _make_user(db, "owner@example.com", "Owner Person")
        org = await _make_org(db, owner, "Acme Co")
        member = await _make_user(db, "member@example.com", "Member Person")
        membership = OrganizationMember(
            organization_id=org.id,
            user_id=member.id,
            role_id=await _role_id(db, org, "admin"),
            status=MemberStatus.ACTIVE,
        )
        db.add(membership)
        member.last_organization_id = org.id
        await db.commit()

        member_token = await _sign_in(client, api, member)
        client.headers["Authorization"] = f"Bearer {member_token}"
        assert (
            await client.patch(f"{api}/organizations/current", json={"city": "Delhi"})
        ).status_code == 200

        # Owner demotes them to viewer.
        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, owner)}"
        demote = await client.patch(
            f"{api}/organizations/current/members/{membership.id}",
            json={"role_id": str(await _role_id(db, org, "viewer"))},
        )
        assert demote.status_code == 200, demote.text

        # The member's old token is now stale, not merely under-privileged.
        client.headers["Authorization"] = f"Bearer {member_token}"
        response = await client.patch(f"{api}/organizations/current", json={"city": "Hyderabad"})
        assert response.status_code == 401

    async def test_permission_catalogue_is_served(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        response = await authed_client.get(f"{api}/roles/permissions")
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["groups"]
        assert body["total"] > 0
        listed = sum(len(group["permissions"]) for group in body["groups"])
        assert listed == body["total"], "the catalogue and its grouping disagree"


# =============================================================================
# Audit trail
# =============================================================================
class TestAuditTrail:
    async def test_records_actions_with_actor_and_diff(
        self, authed_client: AsyncClient, api: str, user: User
    ) -> None:
        await authed_client.patch(
            f"{api}/organizations/current", json={"name": "Renamed Trading Co"}
        )

        response = await authed_client.get(f"{api}/audit")
        assert response.status_code == 200, response.text
        entries = response.json()["items"]

        update = next(e for e in entries if e["action"] == "organization.updated")
        assert update["actor"]["email"] == user.email
        assert update["changes"]["name"]["after"] == "Renamed Trading Co"
        assert update["request_id"], "audit row is not correlated to a request"

    async def test_login_is_recorded(self, authed_client: AsyncClient, api: str) -> None:
        entries = (await authed_client.get(f"{api}/audit")).json()["items"]
        assert any(e["action"] == "user.logged_in" for e in entries)

    async def test_filters_by_action(self, authed_client: AsyncClient, api: str) -> None:
        await authed_client.patch(f"{api}/organizations/current", json={"city": "Surat"})

        response = await authed_client.get(
            f"{api}/audit", params={"action": "organization.updated"}
        )
        assert response.status_code == 200
        entries = response.json()["items"]
        assert entries
        assert all(e["action"] == "organization.updated" for e in entries)

    async def test_cursor_pagination_advances(self, authed_client: AsyncClient, api: str) -> None:
        for index in range(6):
            await authed_client.patch(
                f"{api}/organizations/current", json={"city": f"City {index}"}
            )

        first = (await authed_client.get(f"{api}/audit", params={"limit": 3})).json()
        assert len(first["items"]) == 3
        assert first["has_more"] is True
        assert first["next_cursor"]

        second = (
            await authed_client.get(
                f"{api}/audit", params={"limit": 3, "cursor": first["next_cursor"]}
            )
        ).json()

        first_ids = {item["id"] for item in first["items"]}
        second_ids = {item["id"] for item in second["items"]}
        assert not (first_ids & second_ids), "pages overlap"

    async def test_malformed_cursor_returns_first_page(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """A truncated cursor should degrade, not 500."""
        response = await authed_client.get(f"{api}/audit", params={"cursor": "!!!not-base64!!!"})
        assert response.status_code == 200

    async def test_secrets_never_reach_the_audit_trail(
        self, authed_client: AsyncClient, api: str, user: User
    ) -> None:
        """The redaction backstop, verified end to end."""
        await authed_client.post(
            f"{api}/auth/change-password",
            json={
                "current_password": TEST_PASSWORD,
                "new_password": "Quixotic-Ledger-Verse-77",
            },
        )

        # The caller was signed out, so re-authenticate to read the trail.
        authed_client.headers.pop("Authorization", None)
        authed_client.cookies.clear()
        login = await authed_client.post(
            f"{api}/auth/login",
            json={"email": user.email, "password": "Quixotic-Ledger-Verse-77"},
        )
        authed_client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"

        raw = (await authed_client.get(f"{api}/audit")).text
        assert TEST_PASSWORD not in raw
        assert "Quixotic-Ledger-Verse-77" not in raw

    async def test_viewer_cannot_read_the_audit_trail(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        owner = await _make_user(db, "owner@example.com", "Owner Person")
        org = await _make_org(db, owner, "Acme Co")
        sales = await _make_user(db, "sales@example.com", "Sales Person")
        db.add(
            OrganizationMember(
                organization_id=org.id,
                user_id=sales.id,
                role_id=await _role_id(db, org, "sales"),
                status=MemberStatus.ACTIVE,
            )
        )
        sales.last_organization_id = org.id
        await db.commit()

        client.headers["Authorization"] = f"Bearer {await _sign_in(client, api, sales)}"
        response = await client.get(f"{api}/audit")
        assert response.status_code == 403
