"""Cards and transfers - the two things that must not be merely plausible.

Two very different risks, and the tests are split along them.

**A card must never leave a number behind.** Storing a Primary Account Number is the one
mistake here with a compliance consequence rather than an accounting one, so the first
group proves the reduction is total: what goes in is a number, what exists afterwards is a
scheme and four digits, and no response, column, or error message carries anything else.

**A card and a transfer must not lie about the books.** A credit card is a liability, so it
must not turn up inside "cash"; a transfer moves the organization's own money, so it must
not turn up in the P&L. Both errors balance perfectly and look entirely plausible on
screen, which is exactly why they are asserted against the reports rather than against the
endpoint's own response.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounting.service import ChartOfAccountsService
from app.modules.billing.cards import (
    detect_network,
    inspect_card_number,
    normalise_card_number,
    passes_luhn,
)
from app.modules.billing.models import CardNetwork
from app.modules.organizations.models import Organization

pytestmark = pytest.mark.integration

D = Decimal

#: Every scheme's published test number. These are the ones issuers hand out precisely so
#: they can appear in a test file - none is a real account, and each passes Luhn, which is
#: what makes them useful for checking the arithmetic rather than just the prefix table.
VISA = "4111111111111111"
MASTERCARD = "5555555555554444"
AMEX = "378282246310005"
DISCOVER = "6011111111111117"
#: RuPay does not publish one, so this is a valid-Luhn number in its 6521 range. It exists
#: to pin the prefix ordering: 65 is also Discover, and the longer RuPay prefix must win.
#: The final 7 is a computed check digit, not a chosen one - the twelve zeros contribute
#: nothing, 6 doubles to 12 and digit-sums to 3, and 3 + 5 + 4 + 1 = 13 needs a 7 to reach
#: a multiple of ten. A guessed digit here is the one thing this file cannot afford, since
#: it would make the Luhn test assert its own input.
RUPAY = "6521000000000007"


@pytest.fixture
async def books(db: AsyncSession, organization: Organization) -> Organization:
    await ChartOfAccountsService(db).seed_defaults(organization.id)
    organization.fiscal_year_start_month = 4
    organization.timezone = "Asia/Kolkata"
    await db.flush()
    return organization


async def add_card(
    client: AsyncClient,
    api: str,
    *,
    label: str = "Business card",
    kind: str = "credit",
    number: str = VISA,
    holder_name: str | None = None,
    bank_account_id: str | None = None,
) -> dict:
    body: dict = {"label": label, "kind": kind, "card_number": number}
    if holder_name is not None:
        body["holder_name"] = holder_name
    if bank_account_id is not None:
        body["bank_account_id"] = bank_account_id
    response = await client.post(f"{api}/billing/cards", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def money_accounts(client: AsyncClient, api: str) -> list[dict]:
    options = (await client.get(f"{api}/billing/options")).json()
    return list(options["money_accounts"])


# ---------------------------------------------------------------------------
# Reading a number, and forgetting it
# ---------------------------------------------------------------------------
class TestCardNumberHandling:
    """The pure functions, without a database in the way."""

    def test_luhn_accepts_every_published_test_number(self) -> None:
        for number in (VISA, MASTERCARD, AMEX, DISCOVER, RUPAY):
            assert passes_luhn(number), number

    def test_luhn_catches_a_single_digit_typo(self) -> None:
        # The entire class of mistake somebody makes copying digits off a card.
        assert not passes_luhn("4111111111111112")

    def test_luhn_catches_a_transposition(self) -> None:
        assert not passes_luhn("5555555555544454")

    def test_separators_are_accepted(self) -> None:
        assert normalise_card_number("4111 1111 1111 1111") == VISA
        assert normalise_card_number("4111-1111-1111-1111") == VISA

    def test_networks_are_recognised(self) -> None:
        assert detect_network(VISA) is CardNetwork.VISA
        assert detect_network(MASTERCARD) is CardNetwork.MASTERCARD
        assert detect_network(AMEX) is CardNetwork.AMEX
        assert detect_network(DISCOVER) is CardNetwork.DISCOVER

    def test_rupay_wins_over_discover_on_a_shared_range(self) -> None:
        """``6521`` is RuPay and ``65`` is Discover, so the longer prefix has to win.

        Cosmetic if it is wrong, but showing an Indian shopkeeper "Discover" for their
        RuPay card reads as software that does not know where it is.
        """
        assert detect_network(RUPAY) is CardNetwork.RUPAY

    def test_an_unrecognised_range_is_other_rather_than_a_guess(self) -> None:
        # Valid Luhn, no scheme this table knows. The card still works; the software
        # simply does not claim to know whose it is.
        assert detect_network("9999999999999995") is CardNetwork.OTHER

    def test_inspection_keeps_only_the_network_and_last_four(self) -> None:
        identity = inspect_card_number("4111 1111 1111 1111")
        assert identity is not None
        assert identity.last4 == "1111"
        assert identity.network is CardNetwork.VISA
        assert identity.checksum_ok is True
        # The returned object has nowhere to read a number back out of. Asserted rather
        # than assumed, because a field added later would silently reintroduce one.
        # `checksum_ok` is a verdict *about* the number, not a piece of it.
        assert not hasattr(identity, "card_number")
        assert set(type(identity).__slots__) == {"network", "last4", "checksum_ok"}

    def test_the_wrong_shape_is_rejected_rather_than_stored(self) -> None:
        # Length and charset only. Twelve to nineteen digits and nothing else is a card
        # number; outside that there is nothing sensible to store.
        for value in ("", "abcd", "4111", "41111111111111119999999"):
            assert inspect_card_number(value) is None

    def test_a_failed_check_digit_is_reported_apart_from_a_bad_shape(self) -> None:
        """Both are refused, but the caller can tell which happened.

        "Twelve to nineteen digits" and "those digits do not check out" are different
        mistakes with different fixes, so they come back separately - one as `None`, the
        other on `checksum_ok` - and the API turns each into its own message.
        """
        identity = inspect_card_number("4111111111111112")
        assert identity is not None
        assert identity.checksum_ok is False
        assert identity.last4 == "1112"


# ---------------------------------------------------------------------------
# Nothing keeps the number
# ---------------------------------------------------------------------------
class TestNoCardNumberIsPersisted:
    async def test_the_table_has_no_column_that_could_hold_one(
        self, db: AsyncSession, books: Organization
    ) -> None:
        """A structural guarantee, stronger than a rule saying not to store it.

        Checked against the live schema rather than the model, so a column added by a
        migration alone would still be caught.
        """
        columns = {
            row[0]
            for row in (
                await db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'payment_card'"
                    )
                )
            ).all()
        }
        assert "last4" in columns, "sanity: the table exists and was found"
        for forbidden in ("card_number", "number", "pan", "cvv", "expiry"):
            assert forbidden not in columns

    async def test_the_response_carries_no_number(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        card = await add_card(authed_client, api, number=VISA)
        assert card["last4"] == "1111"
        assert card["network"] == "visa"
        # The full number must not appear anywhere in the payload, under any key.
        assert VISA not in str(card)

    async def test_a_rejected_number_is_not_echoed_back(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The failure path is where a leak would actually happen.

        A 422 that quoted the submitted value would put a card number in an error body and
        very likely in a client-side log, which is worse than storing it deliberately
        because nobody would think to look.
        """
        bad = "4111111111111112"  # valid shape, wrong check digit
        response = await authed_client.post(
            f"{api}/billing/cards",
            json={"label": "Typo", "kind": "credit", "card_number": bad},
        )
        assert response.status_code == 422, response.text
        assert bad not in response.text
        assert bad[:6] not in response.text

    async def test_a_failed_check_digit_is_refused(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Luhn decides valid from invalid, so a mistyped number does not get stored.

        The last four digits are how this card is recognised on an entry months later, and a
        wrong label defeats the point of keeping one.
        """
        response = await authed_client.post(
            f"{api}/billing/cards",
            json={
                "label": "Typo",
                "kind": "credit",
                "card_number": "4111111111111112",
            },
        )
        assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# A credit card is a liability, not cash
# ---------------------------------------------------------------------------
class TestCreditCardIsALiability:
    async def test_it_gets_a_liability_account_not_an_asset(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        card = await add_card(authed_client, api, label="Amex", number=AMEX)

        accounts = (await authed_client.get(f"{api}/accounts")).json()
        account = next(a for a in accounts if a["id"] == card["account_id"])
        assert account["account_type"] == "liability"
        assert account["subtype"] == "other_current_liability"

    async def test_it_stays_out_of_the_dashboard_cash_figure(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The error this guards is the reason cards are not modelled as bank accounts.

        Spending on a card would otherwise *increase* reported cash, because the posting
        credits the card account and the dashboard would count it as money held. The
        figure would be wrong and would look completely ordinary.
        """
        card = await add_card(authed_client, api, number=VISA)
        card_account = next(
            a for a in await money_accounts(authed_client, api) if a["card_id"] == card["id"]
        )
        assert card_account["kind"] == "credit_card"

        before = (await authed_client.get(f"{api}/analytics/dashboard")).json()["cash"]

        response = await authed_client.post(
            f"{api}/billing",
            json={
                "direction": "out",
                "amount": "5000",
                "description": "Laptop on the card",
                "party": "Croma",
                "money_account_id": card_account["id"],
            },
        )
        assert response.status_code == 201, response.text

        after = (await authed_client.get(f"{api}/analytics/dashboard")).json()
        assert D(after["cash"]) == D(before), "a card charge must not move cash"
        # It is a real expense, though - that half must still work.
        assert D(after["expenses"]["current"]) == D("5000")

    async def test_spending_on_it_increases_what_is_owed(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        card = await add_card(authed_client, api, number=MASTERCARD)
        card_account = next(
            a for a in await money_accounts(authed_client, api) if a["card_id"] == card["id"]
        )

        await authed_client.post(
            f"{api}/billing",
            json={
                "direction": "out",
                "amount": "1200",
                "description": "Fuel",
                "party": "Indian Oil",
                "money_account_id": card_account["id"],
            },
        )

        sheet = (await authed_client.get(f"{api}/reports/balance-sheet")).json()
        assert sheet["is_balanced"] is True
        assert D(sheet["total_liabilities"]) >= D("1200")

    async def test_a_card_charge_lands_in_the_general_journal(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Not the bank book: no bank was involved, and that book answers a question."""
        card = await add_card(authed_client, api, number=VISA)
        card_account = next(
            a for a in await money_accounts(authed_client, api) if a["card_id"] == card["id"]
        )
        entry = (
            await authed_client.post(
                f"{api}/billing",
                json={
                    "direction": "out",
                    "amount": "300",
                    "description": "Stationery",
                    "party": "Shop",
                    "money_account_id": card_account["id"],
                },
            )
        ).json()
        assert entry["entry_number"].startswith("JV-")


# ---------------------------------------------------------------------------
# A debit card is not an account
# ---------------------------------------------------------------------------
class TestDebitCardSharesItsBankAccount:
    async def test_it_creates_no_second_account(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Otherwise the same money would be counted twice on the balance sheet."""
        bank = next(a for a in await money_accounts(authed_client, api) if a["kind"] == "bank")
        before = len((await authed_client.get(f"{api}/accounts")).json())

        card = await add_card(
            authed_client,
            api,
            label="HDFC debit",
            kind="debit",
            number=VISA,
            bank_account_id=bank["id"],
        )

        after = len((await authed_client.get(f"{api}/accounts")).json())
        assert after == before, "a debit card must not create a ledger account"
        assert card["account_id"] == bank["id"]

    async def test_it_is_offered_as_a_way_of_paying_from_that_account(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        bank = next(a for a in await money_accounts(authed_client, api) if a["kind"] == "bank")
        card = await add_card(
            authed_client,
            api,
            label="HDFC debit",
            kind="debit",
            number=VISA,
            bank_account_id=bank["id"],
        )

        accounts = await money_accounts(authed_client, api)
        entry = next(a for a in accounts if a.get("card_id") == card["id"])
        # Same ledger account, so a posting is identical either way - but it reads as the
        # card, which is how the person paying thinks of it.
        assert entry["id"] == bank["id"]
        assert entry["kind"] == "bank"
        assert entry["card_last4"] == "1111"

    async def test_it_requires_a_bank_account(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        response = await authed_client.post(
            f"{api}/billing/cards",
            json={"label": "Orphan", "kind": "debit", "card_number": MASTERCARD},
        )
        assert response.status_code == 422, response.text
        assert "bank_account_id" in response.text


# ---------------------------------------------------------------------------
# Duplicates and archiving
# ---------------------------------------------------------------------------
class TestCardLifecycle:
    async def test_the_same_card_twice_is_refused(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        await add_card(authed_client, api, label="First", number=VISA)
        response = await authed_client.post(
            f"{api}/billing/cards",
            json={"label": "Again", "kind": "credit", "card_number": VISA},
        )
        assert response.status_code == 409, response.text
        assert "1111" in response.text

    async def test_archiving_hides_it_from_the_picker_but_keeps_it(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        card = await add_card(authed_client, api, number=VISA)

        archived = await authed_client.post(f"{api}/billing/cards/{card['id']}/archive")
        assert archived.status_code == 200, archived.text
        assert archived.json()["is_active"] is False

        assert all(a.get("card_id") != card["id"] for a in await money_accounts(authed_client, api))
        listed = (await authed_client.get(f"{api}/billing/cards?include_archived=true")).json()
        assert any(c["id"] == card["id"] for c in listed)

        restored = await authed_client.post(f"{api}/billing/cards/{card['id']}/restore")
        assert restored.json()["is_active"] is True
        assert any(a.get("card_id") == card["id"] for a in await money_accounts(authed_client, api))


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------
class TestTransfers:
    async def test_cash_to_bank_leaves_total_cash_unchanged(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The defining property. If this figure moves, the transfer invented money."""
        accounts = await money_accounts(authed_client, api)
        cash = next(a for a in accounts if a["kind"] == "cash")
        bank = next(a for a in accounts if a["kind"] == "bank")

        # Something to move, first.
        await authed_client.post(
            f"{api}/billing",
            json={
                "direction": "in",
                "amount": "10000",
                "description": "Counter sales",
                "party": "Walk-in",
                "money_account_id": cash["id"],
            },
        )
        before = (await authed_client.get(f"{api}/analytics/dashboard")).json()["cash"]

        response = await authed_client.post(
            f"{api}/billing/transfers",
            json={
                "from_account_id": cash["id"],
                "to_account_id": bank["id"],
                "amount": "4000",
                "description": "Banked the till",
            },
        )
        assert response.status_code == 201, response.text

        after = (await authed_client.get(f"{api}/analytics/dashboard")).json()["cash"]
        assert D(after) == D(before)

    async def test_it_does_not_touch_the_profit_and_loss(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The error here inflates income and expenses equally, so profit stays right and
        every other figure on the statement is wrong. Nothing on screen would look odd."""
        accounts = await money_accounts(authed_client, api)
        cash = next(a for a in accounts if a["kind"] == "cash")
        bank = next(a for a in accounts if a["kind"] == "bank")

        await authed_client.post(
            f"{api}/billing/transfers",
            json={
                "from_account_id": bank["id"],
                "to_account_id": cash["id"],
                "amount": "2500",
            },
        )

        dashboard = (await authed_client.get(f"{api}/analytics/dashboard")).json()
        assert D(dashboard["revenue"]["current"]) == D("0")
        assert D(dashboard["expenses"]["current"]) == D("0")

    async def test_it_stays_out_of_the_day_book(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The day book reconstructs a direction and a category from an entry's shape, and
        a transfer has neither. Listing one would invent a category that does not exist."""
        accounts = await money_accounts(authed_client, api)
        cash = next(a for a in accounts if a["kind"] == "cash")
        bank = next(a for a in accounts if a["kind"] == "bank")

        await authed_client.post(
            f"{api}/billing/transfers",
            json={
                "from_account_id": cash["id"],
                "to_account_id": bank["id"],
                "amount": "1000",
            },
        )

        entries = (await authed_client.get(f"{api}/billing")).json()
        assert entries["items"] == []
        summary = (await authed_client.get(f"{api}/billing/summary")).json()
        assert D(summary["money_in"]) == D("0")
        assert D(summary["money_out"]) == D("0")

    async def test_it_still_balances_and_reaches_the_trial_balance(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        accounts = await money_accounts(authed_client, api)
        cash = next(a for a in accounts if a["kind"] == "cash")
        bank = next(a for a in accounts if a["kind"] == "bank")

        transfer = (
            await authed_client.post(
                f"{api}/billing/transfers",
                json={
                    "from_account_id": bank["id"],
                    "to_account_id": cash["id"],
                    "amount": "750",
                },
            )
        ).json()
        assert transfer["entry_number"] is not None

        balance = (await authed_client.get(f"{api}/reports/trial-balance")).json()
        assert balance["is_balanced"] is True

    async def test_paying_a_credit_card_off_reduces_what_is_owed_and_spends_cash(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The case that proves one rule covers all of them.

        Only one line is cash-equivalent here, so cash genuinely goes out - unlike a
        cash-to-bank move, where both are and the net is nil.
        """
        card = await add_card(authed_client, api, number=VISA)
        accounts = await money_accounts(authed_client, api)
        bank = next(a for a in accounts if a["kind"] == "bank")
        card_account = next(a for a in accounts if a.get("card_id") == card["id"])

        # Fund the bank, then spend on the card so there is a balance to settle.
        await authed_client.post(
            f"{api}/billing",
            json={
                "direction": "in",
                "amount": "20000",
                "description": "Client payment",
                "party": "Acme",
                "money_account_id": bank["id"],
            },
        )
        await authed_client.post(
            f"{api}/billing",
            json={
                "direction": "out",
                "amount": "3000",
                "description": "Supplies on the card",
                "party": "Shop",
                "money_account_id": card_account["id"],
            },
        )

        owed_before = D(
            (await authed_client.get(f"{api}/reports/balance-sheet")).json()["total_liabilities"]
        )
        cash_before = D((await authed_client.get(f"{api}/analytics/dashboard")).json()["cash"])

        response = await authed_client.post(
            f"{api}/billing/transfers",
            json={
                "from_account_id": bank["id"],
                "to_account_id": card_account["id"],
                "amount": "3000",
                "description": "Card bill",
            },
        )
        assert response.status_code == 201, response.text

        sheet = (await authed_client.get(f"{api}/reports/balance-sheet")).json()
        dashboard = (await authed_client.get(f"{api}/analytics/dashboard")).json()

        assert D(sheet["total_liabilities"]) == owed_before - D("3000")
        assert D(dashboard["cash"]) == cash_before - D("3000")
        assert sheet["is_balanced"] is True

    async def test_the_same_account_on_both_sides_is_refused(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        cash = next(a for a in await money_accounts(authed_client, api) if a["kind"] == "cash")
        response = await authed_client.post(
            f"{api}/billing/transfers",
            json={
                "from_account_id": cash["id"],
                "to_account_id": cash["id"],
                "amount": "100",
            },
        )
        assert response.status_code == 422, response.text

    async def test_a_category_cannot_be_a_transfer_endpoint(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Otherwise a transfer becomes an untracked way to post to the P&L."""
        options = (await authed_client.get(f"{api}/billing/options")).json()
        category = options["categories"][0]
        cash = next(a for a in options["money_accounts"] if a["kind"] == "cash")

        response = await authed_client.post(
            f"{api}/billing/transfers",
            json={
                "from_account_id": cash["id"],
                "to_account_id": category["id"],
                "amount": "100",
            },
        )
        assert response.status_code == 422, response.text

    async def test_a_negative_amount_is_refused(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        accounts = await money_accounts(authed_client, api)
        cash = next(a for a in accounts if a["kind"] == "cash")
        bank = next(a for a in accounts if a["kind"] == "bank")
        response = await authed_client.post(
            f"{api}/billing/transfers",
            json={
                "from_account_id": cash["id"],
                "to_account_id": bank["id"],
                "amount": "-500",
            },
        )
        assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Bank details - the facts a person needs and the ledger does not
# ---------------------------------------------------------------------------
class TestBankDetails:
    """Which bank, whose name, which number.

    The mirror image of `TestNoCardNumberIsPersisted`, and deliberately so: here the number
    *is* kept, because it is what you quote to be paid and reconcile against. What these
    prove is that keeping it does not mean leaving it lying around in plaintext.
    """

    async def test_an_account_can_be_created_with_its_details(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        response = await authed_client.post(
            f"{api}/billing/money-accounts",
            json={
                "name": "HDFC Current",
                "kind": "bank",
                "bank_name": "HDFC Bank",
                "holder_name": "Jhon Doe",
                "account_number": "50100123454321",
            },
        )
        assert response.status_code == 201, response.text
        created = response.json()
        assert created["bank_name"] == "HDFC Bank"
        assert created["holder_name"] == "Jhon Doe"
        # The tail comes back; the whole number does not, on this route.
        assert created["account_number_last4"] == "4321"
        assert "account_number" not in created

    async def test_the_number_is_not_readable_in_the_table(
        self, authed_client: AsyncClient, api: str, db: AsyncSession, books: Organization
    ) -> None:
        """Encrypted at rest, like a TOTP secret.

        Read straight out of the column rather than through the service, because the point
        is what a stolen dump would contain.
        """
        number = "50100123454321"
        await authed_client.post(
            f"{api}/billing/money-accounts",
            json={"name": "HDFC Current", "kind": "bank", "account_number": number},
        )

        stored = (
            await db.execute(text("SELECT account_number_encrypted FROM bank_account_detail"))
        ).scalar_one()

        assert stored is not None
        assert number not in stored
        # Fernet tokens are versioned and base64; this is a cheap shape check that we are
        # looking at a ciphertext rather than at something merely rearranged.
        assert stored.startswith("gAAAAA")

    async def test_the_full_number_comes_back_from_its_own_route(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Kept in order to be quoted, so it must survive the round trip intact."""
        created = (
            await authed_client.post(
                f"{api}/billing/money-accounts",
                json={
                    "name": "HDFC Current",
                    "kind": "bank",
                    "account_number": "50100123454321",
                },
            )
        ).json()

        details = (
            await authed_client.get(f"{api}/billing/money-accounts/{created['id']}/details")
        ).json()
        assert details["account_number"] == "50100123454321"
        assert details["account_number_last4"] == "4321"

    async def test_spaces_and_dashes_are_stripped(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Two spellings of one account must compare equal, so the digits are stored."""
        created = (
            await authed_client.post(
                f"{api}/billing/money-accounts",
                json={
                    "name": "HDFC Current",
                    "kind": "bank",
                    "account_number": "5010 0123-45 4321",
                },
            )
        ).json()

        details = (
            await authed_client.get(f"{api}/billing/money-accounts/{created['id']}/details")
        ).json()
        assert details["account_number"] == "50100123454321"

    async def test_letters_are_rejected(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        response = await authed_client.post(
            f"{api}/billing/money-accounts",
            json={"name": "Nonsense", "kind": "bank", "account_number": "50100ABC4321"},
        )
        assert response.status_code == 422, response.text

    async def test_details_are_optional(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """A first account must not be blocked on paperwork."""
        response = await authed_client.post(
            f"{api}/billing/money-accounts", json={"name": "Petty cash", "kind": "cash"}
        )
        assert response.status_code == 201, response.text
        assert response.json()["bank_name"] is None
        assert response.json()["account_number_last4"] is None

    async def test_a_cash_box_gets_no_detail_row(
        self, authed_client: AsyncClient, api: str, db: AsyncSession, books: Organization
    ) -> None:
        """Cash has no bank, no number and no holder, so there is nothing to store.

        Asserted because an all-null row would force every later read to tell "no details"
        from "a row of nothings".
        """
        await authed_client.post(
            f"{api}/billing/money-accounts",
            json={
                "name": "Till two",
                "kind": "cash",
                "bank_name": "Ignored Bank",
                "account_number": "50100123454321",
            },
        )
        count = (await db.execute(text("SELECT count(*) FROM bank_account_detail"))).scalar_one()
        assert count == 0

    async def test_the_seeded_bank_account_can_be_filled_in_afterwards(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The account most organizations actually use exists before anyone names its bank.

        Without the update route this one - "Primary Bank Account", created by the chart
        template - would be the only account that could never carry its own details.
        """
        accounts = await money_accounts(authed_client, api)
        bank = next(a for a in accounts if a["kind"] == "bank")

        response = await authed_client.put(
            f"{api}/billing/money-accounts/{bank['id']}/details",
            json={
                "bank_name": "State Bank of India",
                "holder_name": "Jhon Doe",
                "account_number": "30987654321098",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["account_number_last4"] == "1098"

        # And it shows up on the picker payload the forms are built from.
        refreshed = await money_accounts(authed_client, api)
        updated = next(a for a in refreshed if a["id"] == bank["id"])
        assert updated["bank_name"] == "State Bank of India"
        assert updated["account_number_last4"] == "1098"

    async def test_a_blank_number_clears_one_entered_by_mistake(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """`PUT` replaces the whole set, which is how a wrong number gets removed."""
        accounts = await money_accounts(authed_client, api)
        bank = next(a for a in accounts if a["kind"] == "bank")
        url = f"{api}/billing/money-accounts/{bank['id']}/details"

        await authed_client.put(
            url, json={"bank_name": "HDFC Bank", "account_number": "50100123454321"}
        )
        cleared = (await authed_client.put(url, json={"bank_name": "HDFC Bank"})).json()

        assert cleared["bank_name"] == "HDFC Bank"
        assert cleared["account_number"] is None
        assert cleared["account_number_last4"] is None

    async def test_details_cannot_be_hung_on_a_revenue_account(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """ "Which bank is Sales Revenue at" is not a question, so it is refused."""
        options = (await authed_client.get(f"{api}/billing/options")).json()
        category = options["categories"][0]

        response = await authed_client.put(
            f"{api}/billing/money-accounts/{category['id']}/details",
            json={"bank_name": "HDFC Bank"},
        )
        assert response.status_code == 422, response.text

    async def test_another_organizations_account_is_not_writable(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Tenant isolation, on a route that takes an account id from the caller."""
        import uuid as _uuid

        response = await authed_client.put(
            f"{api}/billing/money-accounts/{_uuid.uuid4()}/details",
            json={"bank_name": "HDFC Bank"},
        )
        assert response.status_code == 404, response.text


class TestCardHolderName:
    async def test_the_name_on_the_card_is_kept(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        card = await add_card(authed_client, api, holder_name="Jhon Doe")
        assert card["holder_name"] == "Jhon Doe"

        listed = (await authed_client.get(f"{api}/billing/cards")).json()
        assert listed[0]["holder_name"] == "Jhon Doe"

    async def test_it_is_optional(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """On a sole proprietor's own card it is simply their own name."""
        card = await add_card(authed_client, api)
        assert card["holder_name"] is None

    async def test_a_blank_name_is_not_stored_as_whitespace(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        card = await add_card(authed_client, api, holder_name="   ")
        assert card["holder_name"] is None


class TestArchivingAnAccount:
    """Stop offering an account without losing the entries that point at it.

    The rule worth pinning is the one a client cannot guess: a **seeded** account cannot be
    archived, because later modules post to "Cash on Hand" and "Primary Bank Account" by
    role. The API says so with `can_archive` rather than leaving the client to re-derive it.
    """

    async def test_a_user_added_account_can_be_archived(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        created = (
            await authed_client.post(
                f"{api}/billing/money-accounts",
                json={"name": "UPI wallet", "kind": "bank"},
            )
        ).json()
        assert created["can_archive"] is True

        response = await authed_client.post(f"{api}/billing/money-accounts/{created['id']}/archive")
        assert response.status_code == 200, response.text
        assert response.json()["is_active"] is False

    async def test_an_archived_account_leaves_the_picker(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The whole point. A picker offering a closed account posts to a closed account."""
        created = (
            await authed_client.post(
                f"{api}/billing/money-accounts",
                json={"name": "Old current account", "kind": "bank"},
            )
        ).json()
        await authed_client.post(f"{api}/billing/money-accounts/{created['id']}/archive")

        offered = await money_accounts(authed_client, api)
        assert all(a["id"] != created["id"] for a in offered)

        # But it is still there when the accounts screen asks for it.
        listed = (
            await authed_client.get(
                f"{api}/billing/money-accounts", params={"include_archived": True}
            )
        ).json()
        archived = next(a for a in listed if a["id"] == created["id"])
        assert archived["is_active"] is False

    async def test_it_can_be_restored(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        created = (
            await authed_client.post(
                f"{api}/billing/money-accounts",
                json={"name": "Seasonal float", "kind": "cash"},
            )
        ).json()
        await authed_client.post(f"{api}/billing/money-accounts/{created['id']}/archive")

        restored = (
            await authed_client.post(f"{api}/billing/money-accounts/{created['id']}/restore")
        ).json()
        assert restored["is_active"] is True

        offered = await money_accounts(authed_client, api)
        assert any(a["id"] == created["id"] for a in offered)

    async def test_a_seeded_account_says_it_cannot_be_archived(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """`can_archive` is false, so a client never offers the button."""
        offered = await money_accounts(authed_client, api)
        seeded = [a for a in offered if not a["card_id"]]
        assert seeded, "sanity: the chart template seeded some money accounts"
        assert all(a["can_archive"] is False for a in seeded)

    async def test_and_the_server_refuses_if_asked_anyway(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The flag is advice for the UI; this is the rule being enforced."""
        offered = await money_accounts(authed_client, api)
        seeded = next(a for a in offered if not a["card_id"])

        response = await authed_client.post(f"{api}/billing/money-accounts/{seeded['id']}/archive")
        assert response.status_code == 422, response.text

    async def test_archiving_keeps_the_entries_that_used_it(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The reason it is archived rather than deleted."""
        created = (
            await authed_client.post(
                f"{api}/billing/money-accounts",
                json={"name": "Closing branch account", "kind": "bank"},
            )
        ).json()

        posted = await authed_client.post(
            f"{api}/billing",
            json={
                "direction": "out",
                "amount": "250.00",
                "description": "Branch rent",
                "party": "Landlord",
                "money_account_id": created["id"],
            },
        )
        assert posted.status_code == 201, posted.text

        await authed_client.post(f"{api}/billing/money-accounts/{created['id']}/archive")

        # The day book still shows it, named as before.
        entries = (await authed_client.get(f"{api}/billing")).json()["items"]
        assert any(e["money_account_name"] == "Closing branch account" for e in entries)

    async def test_a_revenue_account_cannot_be_archived_from_here(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """This route is for places money sits, not for the whole chart of accounts."""
        options = (await authed_client.get(f"{api}/billing/options")).json()
        category = options["categories"][0]

        response = await authed_client.post(
            f"{api}/billing/money-accounts/{category['id']}/archive"
        )
        assert response.status_code == 422, response.text
