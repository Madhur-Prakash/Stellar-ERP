"""Feedback, usage analytics, and the install-wide adoption report.

Three things are worth asserting here, and only the first is obvious.

**`POST /feedback` must work with no token at all.** The most useful report in any
product comes from somebody who could not sign in; an endpoint behind the auth wall
cannot receive it. A regression that quietly added a dependency would be invisible
in a suite where every request is authenticated.

**Usage analytics must not become a place to put customer data.** Actions come from
a closed vocabulary and context keys from an allow-list, so the tests attempt the
bad thing - an unknown action, a context key nobody allowed - and assert it is
refused or dropped rather than stored.

**The adoption report must not flatter.** It exists so a reviewer can check how many
organizations are genuinely sealing, which means `sealing` has to count organizations
that have *sealed*, not organizations that switched sealing on. The two are easy to
conflate and the difference is the whole value of the number.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.attestation.models import (
    AttestationSetting,
    Seal,
    SealCadence,
    SealStatus,
    SealTrigger,
)
from app.modules.feedback.models import Feedback, FeedbackKind, UsageEvent
from app.modules.organizations.models import Organization
from app.modules.users.models import User
from tests.conftest import TEST_PASSWORD


# =============================================================================
# Fixtures
# =============================================================================
@pytest.fixture
async def superuser(db: AsyncSession) -> User:
    from app.core.security import hash_password

    record = User(
        email=f"root-{uuid.uuid4().hex[:8]}@example.com",
        full_name="Platform Staff",
        password_hash=hash_password(TEST_PASSWORD),
        email_verified_at=dt.datetime.now(dt.UTC),
        is_superuser=True,
    )
    db.add(record)
    await db.flush()
    return record


@pytest.fixture
async def root_client(client: AsyncClient, api: str, superuser: User) -> AsyncClient:
    """Signed in as platform staff, through the real login endpoint."""
    response = await client.post(
        f"{api}/auth/login",
        json={"email": superuser.email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    return client


# =============================================================================
# Submitting
# =============================================================================
class TestSubmittingFeedback:
    async def test_anybody_can_send_feedback_without_signing_in(
        self,
        client: AsyncClient,
        api: str,
        db: AsyncSession,
    ) -> None:
        """The report worth most is the one from somebody locked out. If this ever
        needs a token, the feature has lost its reason to exist."""
        response = await client.post(
            f"{api}/feedback",
            json={
                "kind": "problem",
                "message": "The verify page rejected a bundle my bank could read.",
            },
        )
        assert response.status_code in {200, 201}, response.text

        stored = (await db.execute(select(Feedback))).scalars().all()
        assert len(stored) == 1
        assert stored[0].user_id is None
        assert stored[0].organization_id is None

    async def test_a_signed_in_report_carries_who_sent_it(
        self,
        authed_client: AsyncClient,
        api: str,
        db: AsyncSession,
        user: User,
        organization: Organization,
    ) -> None:
        """Attribution when it is available, because a follow-up question needs
        somebody to ask."""
        response = await authed_client.post(
            f"{api}/feedback",
            json={"kind": "idea", "message": "Let me export a proof from the invoice."},
        )
        assert response.status_code in {200, 201}, response.text

        stored = (await db.execute(select(Feedback))).scalars().one()
        assert stored.user_id == user.id
        assert stored.organization_id == organization.id

    async def test_an_empty_message_is_refused(self, client: AsyncClient, api: str) -> None:
        response = await client.post(f"{api}/feedback", json={"kind": "problem", "message": "   "})
        assert response.status_code == 422

    async def test_an_unknown_kind_is_refused(self, client: AsyncClient, api: str) -> None:
        """The kinds are a closed set so the inbox can be triaged. A free-form kind
        would make every rollover of the report meaningless."""
        response = await client.post(
            f"{api}/feedback",
            json={"kind": "philosophical", "message": "why is there anything at all"},
        )
        assert response.status_code == 422


# =============================================================================
# Usage analytics
# =============================================================================
class TestUsageTracking:
    async def test_a_known_action_is_recorded(
        self,
        authed_client: AsyncClient,
        api: str,
        db: AsyncSession,
    ) -> None:
        response = await authed_client.post(
            f"{api}/feedback/track",
            json={"action": "screen.trust"},
        )
        assert response.status_code in {200, 202, 204}, response.text
        events = (await db.execute(select(UsageEvent))).scalars().all()
        assert [e.action for e in events] == ["screen.trust"]

    async def test_an_unknown_action_is_not_recorded(
        self,
        authed_client: AsyncClient,
        api: str,
        db: AsyncSession,
    ) -> None:
        """An open action vocabulary is how an events table ends up holding
        whatever a client felt like sending."""
        await authed_client.post(
            f"{api}/feedback/track",
            json={"action": "customer.viewed.acme-trading-co"},
        )
        assert (await db.execute(select(UsageEvent))).scalars().all() == []

    async def test_context_keys_outside_the_allow_list_are_dropped(
        self,
        authed_client: AsyncClient,
        api: str,
        db: AsyncSession,
    ) -> None:
        """The point of the allow-list: a client that sends a customer name must not
        be able to put it in the analytics table by accident."""
        await authed_client.post(
            f"{api}/feedback/track",
            json={
                "action": "screen.trust",
                "context": {"customer_name": "Acme Trading Co", "surface": "web"},
            },
        )
        event = (await db.execute(select(UsageEvent))).scalars().one()
        assert "customer_name" not in (event.context or {})

    async def test_the_public_verifier_can_track_without_a_session(
        self,
        client: AsyncClient,
        api: str,
        db: AsyncSession,
    ) -> None:
        """`/verify` has no session and three signals worth having. Requiring auth
        would mean the one screen aimed at outsiders reports nothing."""
        response = await client.post(f"{api}/feedback/track", json={"action": "screen.verify"})
        assert response.status_code in {200, 202, 204}, response.text
        assert (await db.execute(select(UsageEvent))).scalars().one().action == "screen.verify"

    async def test_a_private_action_needs_a_session(
        self,
        client: AsyncClient,
        api: str,
        db: AsyncSession,
    ) -> None:
        """Only the three verifier signals are open. Anything else unauthenticated
        would let a stranger write rows into somebody's analytics."""
        await client.post(f"{api}/feedback/track", json={"action": "screen.trust"})
        assert (await db.execute(select(UsageEvent))).scalars().all() == []


# =============================================================================
# Reading it back
# =============================================================================
class TestTheInboxIsStaffOnly:
    @pytest.mark.parametrize(
        "path",
        ["/feedback/inbox", "/feedback/summary", "/feedback/usage", "/attestation/adoption"],
    )
    async def test_an_ordinary_member_cannot_read_install_wide_data(
        self,
        authed_client: AsyncClient,
        api: str,
        path: str,
    ) -> None:
        """Every one of these crosses organization boundaries. An owner of one
        organization has no claim on another's rows, however senior they are."""
        response = await authed_client.get(f"{api}{path}")
        assert response.status_code == 403, f"{path} -> {response.status_code}"

    @pytest.mark.parametrize(
        "path",
        ["/feedback/inbox", "/feedback/summary", "/feedback/usage", "/attestation/adoption"],
    )
    async def test_platform_staff_can(self, root_client: AsyncClient, api: str, path: str) -> None:
        response = await root_client.get(f"{api}{path}")
        assert response.status_code == 200, f"{path} -> {response.text}"


# =============================================================================
# Adoption
# =============================================================================
class TestAdoption:
    """The report a reviewer reads to check the claim "N organizations are sealing"."""

    @staticmethod
    async def _seal(
        db: AsyncSession,
        organization: Organization,
        *,
        seq: int,
        status: SealStatus,
        tx_hash: str,
    ) -> Seal:
        now = dt.datetime.now(dt.UTC)
        seal = Seal(
            organization_id=organization.id,
            seq=seq,
            merkle_root=f"{seq:064x}",
            prev_root="00" * 32,
            entry_count=3,
            debit_minor=Decimal(150_000),
            # Advanced per seal, because `uq_seal_org_last_leaf` correctly refuses
            # two live seals claiming the same leaf range.
            first_leaf_seq=(seq - 1) * 3 + 1,
            last_leaf_seq=seq * 3,
            covered_from=now - dt.timedelta(days=1),
            covered_to=now,
            entry_date_from=(now - dt.timedelta(days=1)).date(),
            entry_date_to=now.date(),
            status=status,
            trigger=SealTrigger.MANUAL,
            network="testnet",
            contract_id="CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR",
            tx_hash=tx_hash,
            sealed_at=now if status is SealStatus.CONFIRMED else None,
            attempts=1,
        )
        db.add(seal)
        await db.flush()
        return seal

    @staticmethod
    async def _setting(db: AsyncSession, organization: Organization, *, enabled: bool) -> None:
        db.add(
            AttestationSetting(
                organization_id=organization.id,
                enabled=enabled,
                org_namespace=uuid.uuid4().hex + uuid.uuid4().hex,
                contract_id="CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR",
                network="testnet",
                signer_public_key="G" + "A" * 55,
                signer_secret_encrypted="encrypted-and-never-returned",
                cadence=SealCadence.DAILY,
                registered_at=dt.datetime.now(dt.UTC),
            )
        )
        await db.flush()

    async def test_an_organization_that_only_switched_it_on_is_not_counted_as_sealing(
        self,
        root_client: AsyncClient,
        api: str,
        db: AsyncSession,
        organization: Organization,
    ) -> None:
        """The number a reviewer reads must mean what it says. Counting configured
        organizations as sealing organizations is the flattering arithmetic this
        endpoint exists to avoid."""
        await self._setting(db, organization, enabled=True)

        body = (await root_client.get(f"{api}/attestation/adoption")).json()
        assert len(body["organizations"]) == 1
        assert body["sealing"] == 0
        assert body["total_seals"] == 0

    async def test_a_confirmed_seal_makes_it_count(
        self,
        root_client: AsyncClient,
        api: str,
        db: AsyncSession,
        organization: Organization,
    ) -> None:
        await self._setting(db, organization, enabled=True)
        await self._seal(db, organization, seq=1, status=SealStatus.CONFIRMED, tx_hash="a" * 64)

        body = (await root_client.get(f"{api}/attestation/adoption")).json()
        row = body["organizations"][0]
        assert body["sealing"] == 1
        assert body["total_seals"] == 1
        assert body["total_entries_sealed"] == 3
        assert row["seals"] == 1
        assert row["entries_sealed"] == 3
        assert row["head_seq"] == 1
        assert row["head_tx_hash"] == "a" * 64
        assert row["organization_name"] == organization.name

    async def test_an_unconfirmed_seal_does_not_count(
        self,
        root_client: AsyncClient,
        api: str,
        db: AsyncSession,
        organization: Organization,
    ) -> None:
        """A submitted seal may still be in flight, or may have failed. Counting it
        would inflate exactly the figure this endpoint makes checkable."""
        await self._setting(db, organization, enabled=True)
        await self._seal(db, organization, seq=1, status=SealStatus.SUBMITTED, tx_hash="b" * 64)

        body = (await root_client.get(f"{api}/attestation/adoption")).json()
        assert body["sealing"] == 0
        assert body["organizations"][0]["seals"] == 0
        assert body["organizations"][0]["head_tx_hash"] is None

    async def test_the_head_transaction_is_the_latest_one(
        self,
        root_client: AsyncClient,
        api: str,
        db: AsyncSession,
        organization: Organization,
    ) -> None:
        """`max(tx_hash)` would return whichever hash sorted highest, which is a
        different seal from the newest one in almost every case."""
        await self._setting(db, organization, enabled=True)
        await self._seal(db, organization, seq=1, status=SealStatus.CONFIRMED, tx_hash="f" * 64)
        await self._seal(db, organization, seq=2, status=SealStatus.CONFIRMED, tx_hash="0" * 64)

        row = (await root_client.get(f"{api}/attestation/adoption")).json()["organizations"][0]
        assert row["head_seq"] == 2
        assert row["head_tx_hash"] == "0" * 64

    async def test_the_signer_secret_is_never_returned(
        self,
        root_client: AsyncClient,
        api: str,
        db: AsyncSession,
        organization: Organization,
    ) -> None:
        """It lives in the same row this endpoint selects from, so the absence is
        asserted rather than assumed."""
        await self._setting(db, organization, enabled=True)
        response = await root_client.get(f"{api}/attestation/adoption")
        assert "encrypted-and-never-returned" not in response.text
        assert "signer_secret" not in response.text
        assert response.json()["organizations"][0]["signer_public_key"] == "G" + "A" * 55

    async def test_a_lifetime_control_total_crosses_the_wire_as_a_string(
        self,
        root_client: AsyncClient,
        api: str,
        db: AsyncSession,
        organization: Organization,
    ) -> None:
        """A count of paise over a business's lifetime outgrows a double, and
        JavaScript has no other numeric type."""
        await self._setting(db, organization, enabled=True)
        await self._seal(db, organization, seq=1, status=SealStatus.CONFIRMED, tx_hash="c" * 64)
        row = (await root_client.get(f"{api}/attestation/adoption")).json()["organizations"][0]
        assert row["debit_minor"] == "150000"
        assert isinstance(row["debit_minor"], str)


class TestFeedbackKindsAreClosed:
    def test_every_kind_the_widget_offers_exists_in_the_enum(self) -> None:
        """The web and desktop widgets send these four strings, hard-coded in
        `FeedbackWidget.tsx`. A rename here that missed a client would 422 every
        report from it, silently - and the report nobody receives is the one that
        would have told you."""
        for kind in ("problem", "idea", "question", "praise"):
            assert FeedbackKind(kind)
