"""Every new organization must get books it can write to.

This exists because of a real bug that reached a user. `POST /organizations` seeded the
chart of accounts and the fiscal year; the **registration** path, written before
accounting existed, seeded only roles and was never updated. So anyone who signed up
with an organization name got an organization with no chart, and the first thing they
saw on the billing screen was "No income accounts exist yet" above two empty dropdowns
with no way forward.

Both paths now call `provision_books`. These tests assert both, because the failure mode
is silent - the organization is created successfully and looks fine until the moment
someone tries to record money.

The second class covers the repair path: organizations that already exist without a
chart are healed when the billing screen is opened, so the fix does not require a
migration or anything from the user.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounting.models import Account, FiscalYear, Journal
from app.modules.organizations.models import (
    MemberStatus,
    Organization,
    OrganizationMember,
)
from app.modules.rbac.permissions import SystemRole
from app.modules.rbac.repository import RoleRepository
from app.modules.users.models import User
from tests.conftest import TEST_DOMAIN, TEST_PASSWORD

pytestmark = pytest.mark.integration


async def count_accounts(db: AsyncSession, organization_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(Account)
            .where(Account.organization_id == organization_id)
        )
    ).scalar_one()


async def count_journals(db: AsyncSession, organization_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(Journal)
            .where(Journal.organization_id == organization_id)
        )
    ).scalar_one()


async def count_years(db: AsyncSession, organization_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(FiscalYear)
            .where(FiscalYear.organization_id == organization_id)
        )
    ).scalar_one()


class TestRegistrationProvisionsBooks:
    """The path that was broken."""

    async def test_registering_with_an_organization_seeds_the_chart(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """The regression. Roles alone are not working books."""
        email = f"newowner-{uuid.uuid4().hex[:8]}@{TEST_DOMAIN}"
        response = await client.post(
            f"{api}/auth/register",
            json={
                "email": email,
                "password": TEST_PASSWORD,
                "full_name": "New Owner",
                "organization_name": "Fresh Shop",
            },
        )
        assert response.status_code == 201, response.text

        organization_id = (
            await db.execute(select(Organization.id).where(Organization.name == "Fresh Shop"))
        ).scalar_one()

        assert await count_accounts(db, organization_id) > 0, "no chart of accounts"
        assert await count_journals(db, organization_id) > 0, "no journals"
        assert await count_years(db, organization_id) > 0, "no fiscal year"

    async def test_the_seeded_chart_has_what_billing_needs(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """Specifically: income, expense, and cash accounts.

        A chart with no income accounts is what produced the error the user saw, so it
        is asserted directly rather than inferred from a row count.
        """
        email = f"newowner-{uuid.uuid4().hex[:8]}@{TEST_DOMAIN}"
        registered = await client.post(
            f"{api}/auth/register",
            json={
                "email": email,
                "password": TEST_PASSWORD,
                "full_name": "New Owner",
                "organization_name": "Second Shop",
            },
        )
        assert registered.status_code == 201, registered.text

        organization_id = (
            await db.execute(select(Organization.id).where(Organization.name == "Second Shop"))
        ).scalar_one()

        accounts = (
            (
                await db.execute(
                    select(Account).where(
                        Account.organization_id == organization_id,
                        Account.is_group.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )

        types = {account.account_type.value for account in accounts}
        assert "income" in types
        assert "expense" in types
        assert any(account.subtype.is_cash_equivalent for account in accounts)

    async def test_a_registered_owner_can_record_money_immediately(
        self, client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """The end-to-end version of the bug report.

        Register, sign in, open billing, record ₹10. This is exactly the sequence that
        previously ended in "No income accounts exist yet".
        """
        email = f"newowner-{uuid.uuid4().hex[:8]}@{TEST_DOMAIN}"
        registered = await client.post(
            f"{api}/auth/register",
            json={
                "email": email,
                "password": TEST_PASSWORD,
                "full_name": "New Owner",
                "organization_name": "Third Shop",
            },
        )
        assert registered.status_code == 201, registered.text

        # Verified directly rather than through the emailed link: login correctly
        # refuses an unverified address, and that flow has its own tests. What is
        # under test here is whether registration left behind usable books.
        owner = (await db.execute(select(User).where(User.email == email))).scalar_one()
        owner.email_verified_at = dt.datetime.now(dt.UTC)
        await db.flush()

        signed_in = await client.post(
            f"{api}/auth/login", json={"email": email, "password": TEST_PASSWORD}
        )
        assert signed_in.status_code == 200, signed_in.text
        client.headers["Authorization"] = f"Bearer {signed_in.json()['access_token']}"

        options = await client.get(f"{api}/billing/options")
        assert options.status_code == 200, options.text
        body = options.json()
        assert [c for c in body["categories"] if c["direction"] == "in"], "no income categories"
        assert body["money_accounts"], "nowhere for the money to land"

        recorded = await client.post(
            f"{api}/billing",
            json={
                "direction": "in",
                "amount": "10",
                "description": "asx",
                "party": "A customer",
            },
        )
        assert recorded.status_code == 201, recorded.text
        assert recorded.json()["entry_number"]

    async def test_creating_an_organization_later_also_seeds_books(
        self, authed_client: AsyncClient, api: str, db: AsyncSession
    ) -> None:
        """The path that was already correct - asserted so the shared helper cannot
        regress it while fixing the other one."""
        created = await authed_client.post(f"{api}/organizations", json={"name": "Another Co"})
        assert created.status_code == 201, created.text

        organization_id = uuid.UUID(created.json()["id"])
        assert await count_accounts(db, organization_id) > 0
        assert await count_years(db, organization_id) > 0


class TestBillingRepairsExistingOrganizations:
    """Organizations created before the fix have no chart. They must heal themselves.

    Otherwise the fix would only help new signups, and every existing account would
    stay stuck on the same error with no route out short of a data migration.
    """

    @pytest.fixture
    async def bookless_org(self, db: AsyncSession, user: User) -> Organization:
        """An organization with roles and a member but no chart - exactly the state the
        old registration path left behind."""
        organization = Organization(name="Bookless Shop", slug=f"bookless-{uuid.uuid4().hex[:6]}")
        db.add(organization)
        await db.flush()

        seeded = await RoleRepository(db).seed_system_roles(organization.id)
        db.add(
            OrganizationMember(
                organization_id=organization.id,
                user_id=user.id,
                role_id=seeded[SystemRole.OWNER].id,
                is_owner=True,
                status=MemberStatus.ACTIVE,
                joined_at=dt.datetime.now(dt.UTC),
            )
        )
        user.last_organization_id = organization.id
        await db.flush()

        assert await count_accounts(db, organization.id) == 0
        return organization

    async def test_opening_the_form_seeds_the_chart(
        self,
        client: AsyncClient,
        api: str,
        db: AsyncSession,
        user: User,
        bookless_org: Organization,
    ) -> None:
        await db.commit()

        signed_in = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        assert signed_in.status_code == 200, signed_in.text
        client.headers["Authorization"] = f"Bearer {signed_in.json()['access_token']}"

        response = await client.get(f"{api}/billing/options")
        assert response.status_code == 200, response.text
        body = response.json()

        assert [c for c in body["categories"] if c["direction"] == "in"]
        assert [c for c in body["categories"] if c["direction"] == "out"]
        assert body["money_accounts"]

    async def test_and_then_money_can_be_recorded(
        self,
        client: AsyncClient,
        api: str,
        db: AsyncSession,
        user: User,
        bookless_org: Organization,
    ) -> None:
        await db.commit()

        signed_in = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        client.headers["Authorization"] = f"Bearer {signed_in.json()['access_token']}"

        recorded = await client.post(
            f"{api}/billing",
            json={
                "direction": "out",
                "amount": "1500",
                "description": "Rent",
                "party": "The landlord",
            },
        )
        assert recorded.status_code == 201, recorded.text

        dashboard = await client.get(f"{api}/analytics/dashboard")
        assert dashboard.status_code == 200
        assert dashboard.json()["expenses"]["current"] not in (None, "")

    async def test_seeding_is_idempotent(
        self,
        client: AsyncClient,
        api: str,
        db: AsyncSession,
        user: User,
        bookless_org: Organization,
    ) -> None:
        """Three calls, one chart. `seed_defaults` skips when any account exists, so
        repeated requests must not duplicate codes or half-rebuild it."""
        await db.commit()

        signed_in = await client.post(
            f"{api}/auth/login", json={"email": user.email, "password": TEST_PASSWORD}
        )
        client.headers["Authorization"] = f"Bearer {signed_in.json()['access_token']}"

        first = await client.get(f"{api}/billing/options")
        assert first.status_code == 200
        count_after_first = len(first.json()["categories"])

        for _ in range(2):
            again = await client.get(f"{api}/billing/options")
            assert again.status_code == 200
            assert len(again.json()["categories"]) == count_after_first
