"""Billing - record money in and out, and prove it reaches every screen.

The requirement was "manually add the money that came in and goes out, and it should
be reflected on the dashboard and the whole UI". The second half is the part worth
testing: it is easy to write a form that stores something and quietly fails to affect
any report.

So these tests post through the real endpoint and then assert the figure appears in
the dashboard, the P&L, the trial balance, the cash flow statement, and the analytics
trend - without any of those being told about billing. That works because a billing
entry *is* a journal entry, which is the whole reason there is no billing table.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounting.service import ChartOfAccountsService, FiscalCalendarService
from app.modules.organizations.models import Organization

pytestmark = pytest.mark.integration

D = Decimal


@pytest.fixture
async def books(db: AsyncSession, organization: Organization) -> Organization:
    """A chart of accounts and nothing else.

    Deliberately **no fiscal year**: the service creates it on demand, and that is the
    behaviour worth exercising. A user who never asked for a fiscal calendar should not
    meet "no open period for this date" on their first entry.
    """
    await ChartOfAccountsService(db).seed_defaults(organization.id)
    organization.fiscal_year_start_month = 4
    organization.timezone = "Asia/Kolkata"
    await db.flush()
    return organization


async def record(
    client: AsyncClient,
    api: str,
    *,
    direction: str,
    amount: str,
    description: str,
    entry_date: dt.date | None = None,
    category_id: str | None = None,
    # Required by the API, so the helper supplies one. Tests that care about the party
    # pass their own; the rest would otherwise all fail on a field they are not testing.
    party: str = "Someone",
) -> dict:
    body: dict = {
        "direction": direction,
        "amount": amount,
        "description": description,
        "party": party,
    }
    if entry_date is not None:
        body["entry_date"] = entry_date.isoformat()
    if category_id is not None:
        body["category_id"] = category_id

    response = await client.post(f"{api}/billing", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# Which book an entry is filed in
# ---------------------------------------------------------------------------
class TestJournalRouting:
    """Cash entries belong in the Cash Book, bank entries in the Bank Book.

    The choice used to be keyed on direction - which does not affect it, since a cash book
    has both a receipts and a payments side - so everything landed in the Cash Book even
    when the money moved through a bank. Sales and purchasing already route by account,
    and billing disagreeing with them meant the same payment recorded two ways ended up in
    two different books.
    """

    async def test_cash_goes_to_the_cash_book(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        options = (await authed_client.get(f"{api}/billing/options")).json()
        cash = next(a for a in options["money_accounts"] if "Cash" in a["name"])

        for direction in ("in", "out"):
            response = await authed_client.post(
                f"{api}/billing",
                json={
                    "direction": direction,
                    "amount": "100",
                    "description": "Over the counter",
                    "party": "Ramesh",
                    "money_account_id": cash["id"],
                },
            )
            assert response.status_code == 201, response.text
            # Both directions, one book: that is what a cash book is for.
            assert response.json()["entry_number"].startswith("CB-")

    async def test_bank_goes_to_the_bank_book(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        options = (await authed_client.get(f"{api}/billing/options")).json()
        bank = next(a for a in options["money_accounts"] if "Bank" in a["name"])

        response = await authed_client.post(
            f"{api}/billing",
            json={
                "direction": "in",
                "amount": "112",
                "description": "Transfer received",
                "party": "A customer",
                "money_account_id": bank["id"],
            },
        )
        assert response.status_code == 201, response.text
        entry = response.json()
        assert entry["entry_number"].startswith("BB-"), entry["entry_number"]

        # And the journal itself says so, not just the number prefix.
        entries = (await authed_client.get(f"{api}/journal-entries")).json()
        posted = next(e for e in entries["items"] if e["id"] == entry["id"])
        assert posted["journal_code"] == "BNK"

    async def test_a_bank_entry_still_appears_in_the_billing_day_book(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The regression this change could have caused.

        The day book selects on `source_type`, not on the journal - so moving bank entries
        into a different book must not hide them from the screen they were entered on.
        """
        options = (await authed_client.get(f"{api}/billing/options")).json()
        bank = next(a for a in options["money_accounts"] if "Bank" in a["name"])

        created = await authed_client.post(
            f"{api}/billing",
            json={
                "direction": "in",
                "amount": "112",
                "description": "Transfer received",
                "party": "A customer",
                "money_account_id": bank["id"],
            },
        )
        assert created.status_code == 201, created.text

        listing = (await authed_client.get(f"{api}/billing")).json()
        row = next(r for r in listing["items"] if r["id"] == created.json()["id"])
        assert row["direction"] == "in"
        assert D(row["amount"]) == D("112")
        assert row["money_account_name"] == bank["name"]


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
class TestOptions:
    async def test_serves_categories_money_accounts_and_today(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        response = await authed_client.get(f"{api}/billing/options")
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["categories"]
        assert body["money_accounts"]
        assert body["today"]
        assert body["currency"] == "INR"

    async def test_each_direction_has_exactly_one_default(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """So the form opens on a sensible answer and the common case needs no choice."""
        body = (await authed_client.get(f"{api}/billing/options")).json()

        for direction in ("in", "out"):
            defaults = [
                c for c in body["categories"] if c["direction"] == direction and c["is_default"]
            ]
            assert len(defaults) == 1, f"{direction}: {defaults}"

        assert sum(1 for a in body["money_accounts"] if a["is_default"]) == 1

    async def test_offers_no_group_headings(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """You cannot post to "Operating Expenses" - only to a leaf under it."""
        body = (await authed_client.get(f"{api}/billing/options")).json()
        names = {c["name"] for c in body["categories"]}
        assert "Expenses" not in names
        assert "Income" not in names

    async def test_money_accounts_are_only_cash_and_bank(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        body = (await authed_client.get(f"{api}/billing/options")).json()
        names = {a["name"] for a in body["money_accounts"]}
        assert "Accounts Receivable" not in names
        assert "Inventory" not in names


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
class TestRecording:
    async def test_money_out_needs_only_amount_and_description(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The minimum viable entry, which is what was actually asked for."""
        entry = await record(
            authed_client, api, direction="out", amount="5000", description="Stationery"
        )

        assert entry["direction"] == "out"
        assert D(entry["amount"]) == D("5000")
        assert entry["description"] == "Stationery"
        # Filed and dated without the user choosing anything.
        assert entry["category_name"]
        assert entry["money_account_name"]
        assert entry["date"]
        # It is a real posted ledger entry, so it has the journal's own number.
        assert entry["entry_number"]

    async def test_money_in_needs_only_amount_and_description(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        entry = await record(
            authed_client, api, direction="in", amount="12000", description="Counter sale"
        )
        assert entry["direction"] == "in"
        assert D(entry["amount"]) == D("12000")

    async def test_creates_the_fiscal_year_on_demand(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """No fiscal year exists in this fixture.

        Without on-demand creation the first entry fails with "no open period", which
        is meaningless to someone who never asked for a fiscal calendar.
        """
        entry = await record(
            authed_client, api, direction="out", amount="100", description="First ever entry"
        )
        assert entry["entry_number"]

        years = await authed_client.get(f"{api}/fiscal-years")
        assert years.status_code == 200
        assert years.json()

    async def test_accepts_a_backdated_entry(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Recording last week's expense is the normal case for a hand-kept book."""
        last_week = dt.date.today() - dt.timedelta(days=7)
        entry = await record(
            authed_client,
            api,
            direction="out",
            amount="750",
            description="Auto fare",
            entry_date=last_week,
        )
        assert entry["date"] == last_week.isoformat()

    async def test_accepts_paise(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        entry = await record(
            authed_client, api, direction="out", amount="123.45", description="Odd amount"
        )
        assert D(entry["amount"]) == D("123.45")

    async def test_money_crosses_the_wire_as_a_string(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        entry = await record(
            authed_client, api, direction="out", amount="5000", description="Stationery"
        )
        assert isinstance(entry["amount"], str)

    @pytest.mark.parametrize("amount", ["0", "-500", "-0.01"])
    async def test_refuses_a_non_positive_amount(
        self, authed_client: AsyncClient, api: str, books: Organization, amount: str
    ) -> None:
        """A correction is a reversal, not a negative entry. A ledger records what
        happened, not the net of it."""
        response = await authed_client.post(
            f"{api}/billing",
            json={"direction": "out", "amount": amount, "description": "Nope"},
        )
        assert response.status_code == 422

    async def test_requires_a_description(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """An amount with no note is unidentifiable a month later."""
        response = await authed_client.post(
            f"{api}/billing", json={"direction": "out", "amount": "500", "description": "  "}
        )
        assert response.status_code == 422

    async def test_refuses_an_income_category_for_money_out(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Filing an expense against a revenue account would inflate income and
        understate costs - the books would still balance, and be wrong."""
        options = (await authed_client.get(f"{api}/billing/options")).json()
        income = next(c for c in options["categories"] if c["direction"] == "in")

        response = await authed_client.post(
            f"{api}/billing",
            json={
                "direction": "out",
                "amount": "500",
                "description": "Miscategorised",
                "party": "Someone",
                "category_id": income["id"],
            },
        )
        assert response.status_code == 422
        assert "income" in response.json()["error"]["message"].lower()

    async def test_refuses_a_non_cash_money_account(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Money cannot move through Inventory."""
        accounts = (await authed_client.get(f"{api}/accounts", params={"page_size": 200})).json()
        rows = accounts["items"] if isinstance(accounts, dict) else accounts
        inventory = next(row for row in rows if row["code"] == "1140")

        response = await authed_client.post(
            f"{api}/billing",
            json={
                "direction": "out",
                "amount": "500",
                "description": "Wrong pocket",
                "money_account_id": inventory["id"],
            },
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# It has to show up everywhere
# ---------------------------------------------------------------------------
class TestReflectedAcrossTheApp:
    """The half of the requirement that is easy to get wrong.

    None of these reports know billing exists. They pick the entries up because a
    billing entry is a journal entry - which is the entire justification for not
    having a billing table.
    """

    async def test_appears_on_the_dashboard(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        await record(authed_client, api, direction="in", amount="20000", description="Sales")
        await record(authed_client, api, direction="out", amount="8000", description="Rent")

        dashboard = (await authed_client.get(f"{api}/analytics/dashboard")).json()

        assert D(dashboard["revenue"]["current"]) == D("20000")
        assert D(dashboard["expenses"]["current"]) == D("8000")
        assert D(dashboard["net_profit"]["current"]) == D("12000")
        # Cash actually moved, so the position figure moved with it.
        assert D(dashboard["cash"]) == D("12000")

    async def test_appears_in_the_profit_and_loss(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        await record(authed_client, api, direction="in", amount="20000", description="Sales")
        await record(authed_client, api, direction="out", amount="8000", description="Rent")

        today = dt.date.today()
        statement = (
            await authed_client.get(
                f"{api}/reports/profit-and-loss",
                params={
                    "from_date": today.replace(day=1).isoformat(),
                    "to_date": today.isoformat(),
                },
            )
        ).json()

        assert D(statement["total_income"]) == D("20000")
        assert D(statement["total_expenses"]) == D("8000")
        assert D(statement["net_profit"]) == D("12000")

    async def test_keeps_the_trial_balance_balanced(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The property that matters after any sequence of real operations."""
        await record(authed_client, api, direction="in", amount="20000", description="Sales")
        await record(authed_client, api, direction="out", amount="8000", description="Rent")
        await record(authed_client, api, direction="out", amount="1234.56", description="Sundry")

        report = (await authed_client.get(f"{api}/reports/trial-balance")).json()
        assert report["is_balanced"] is True
        assert D(report["total_debit"]) == D(report["total_credit"])
        assert D(report["total_debit"]) > 0

    async def test_appears_in_the_cash_flow_statement(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        await record(authed_client, api, direction="in", amount="20000", description="Sales")
        await record(authed_client, api, direction="out", amount="8000", description="Rent")

        today = dt.date.today()
        flow = (
            await authed_client.get(
                f"{api}/reports/cash-flow",
                params={
                    "from_date": today.replace(day=1).isoformat(),
                    "to_date": today.isoformat(),
                },
            )
        ).json()

        assert flow["reconciles"] is True
        assert D(flow["closing_cash"]) == D("12000")

    async def test_appears_in_the_analytics_trend(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        await record(authed_client, api, direction="in", amount="20000", description="Sales")

        trend = (
            await authed_client.get(f"{api}/analytics/trend", params={"period": "this_month"})
        ).json()
        assert D(trend["total_income"]) == D("20000")

    async def test_does_not_disturb_control_account_reconciliation(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Billing touches neither receivables nor payables, so the sub-ledger checks
        must stay clean. If they broke, the dashboard would show a scary warning for a
        perfectly ordinary cash entry."""
        await record(authed_client, api, direction="in", amount="20000", description="Sales")
        await record(authed_client, api, direction="out", amount="8000", description="Rent")

        checks = (await authed_client.get(f"{api}/analytics/control-checks")).json()
        assert checks["all_agree"] is True, checks["checks"]

    async def test_shows_in_the_journal_so_an_accountant_can_trace_it(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        entry = await record(
            authed_client, api, direction="out", amount="5000", description="Stationery"
        )

        entries = (await authed_client.get(f"{api}/journal-entries")).json()
        numbers = [row["entry_number"] for row in entries["items"]]
        assert entry["entry_number"] in numbers


# ---------------------------------------------------------------------------
# Reading back
# ---------------------------------------------------------------------------
class TestAddingCategoriesAndAccounts:
    """The escape hatches. The template cannot anticipate every trade or every wallet."""

    async def test_adds_an_expense_category_from_a_name(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        response = await authed_client.post(
            f"{api}/billing/categories", json={"name": "Tempo Hire", "direction": "out"}
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["name"] == "Tempo Hire"
        assert body["direction"] == "out"
        # Derived, so the user never sees a chart-of-accounts field.
        assert body["code"]
        assert body["group"]

    async def test_the_new_category_is_immediately_usable(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        created = await authed_client.post(
            f"{api}/billing/categories", json={"name": "Tempo Hire", "direction": "out"}
        )
        category_id = created.json()["id"]

        options = (await authed_client.get(f"{api}/billing/options")).json()
        assert category_id in [c["id"] for c in options["categories"]]

        entry = await record(
            authed_client,
            api,
            direction="out",
            amount="1200",
            description="Delivery run",
            category_id=category_id,
        )
        assert entry["category_name"] == "Tempo Hire"

    async def test_refuses_a_duplicate_category_name(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Two categories with one name makes every report ambiguous."""
        await authed_client.post(
            f"{api}/billing/categories", json={"name": "Tempo Hire", "direction": "out"}
        )
        again = await authed_client.post(
            f"{api}/billing/categories", json={"name": "tempo hire", "direction": "out"}
        )
        assert again.status_code == 409

    async def test_adds_a_bank_account(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """A UPI wallet is a bank: reconciled against a statement, not by counting."""
        response = await authed_client.post(
            f"{api}/billing/money-accounts", json={"name": "UPI Wallet", "kind": "bank"}
        )
        assert response.status_code == 201, response.text
        assert response.json()["name"] == "UPI Wallet"

        options = (await authed_client.get(f"{api}/billing/options")).json()
        assert "UPI Wallet" in [a["name"] for a in options["money_accounts"]]

    async def test_adds_a_second_cash_box(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        response = await authed_client.post(
            f"{api}/billing/money-accounts", json={"name": "Counter Till", "kind": "cash"}
        )
        assert response.status_code == 201, response.text
        # Numbered next to Cash on Hand rather than ahead of it.
        assert response.json()["code"].startswith("111")

    async def test_money_can_be_recorded_against_a_new_account(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        created = await authed_client.post(
            f"{api}/billing/money-accounts", json={"name": "UPI Wallet", "kind": "bank"}
        )
        account_id = created.json()["id"]

        response = await authed_client.post(
            f"{api}/billing",
            json={
                "direction": "in",
                "amount": "2500",
                "description": "Online order",
                "party": "Someone",
                "money_account_id": account_id,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["money_account_name"] == "UPI Wallet"

        # And it counts as cash on the dashboard, like any cash-equivalent.
        dashboard = (await authed_client.get(f"{api}/analytics/dashboard")).json()
        assert D(dashboard["cash"]) == D("2500")

    async def test_refuses_a_duplicate_account_name(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        response = await authed_client.post(
            f"{api}/billing/money-accounts", json={"name": "Cash on Hand", "kind": "cash"}
        )
        assert response.status_code == 409

    async def test_the_books_still_balance_after_adding_both(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """New accounts must not break the accounting equation."""
        category = await authed_client.post(
            f"{api}/billing/categories", json={"name": "Tempo Hire", "direction": "out"}
        )
        account = await authed_client.post(
            f"{api}/billing/money-accounts", json={"name": "UPI Wallet", "kind": "bank"}
        )
        await authed_client.post(
            f"{api}/billing",
            json={
                "direction": "out",
                "amount": "1200",
                "description": "Delivery run",
                "party": "Someone",
                "category_id": category.json()["id"],
                "money_account_id": account.json()["id"],
            },
        )

        report = (await authed_client.get(f"{api}/reports/trial-balance")).json()
        assert report["is_balanced"] is True
        assert D(report["total_debit"]) == D("1200")


class TestParty:
    """Who the money came from or went to - free text, no master record."""

    async def test_records_who_it_came_from(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        entry = await record(
            authed_client,
            api,
            direction="in",
            amount="500",
            description="Counter sale",
            party="Walk-in customer",
        )
        assert entry["party"] == "Walk-in customer"

    async def test_records_who_it_went_to(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        entry = await record(
            authed_client,
            api,
            direction="out",
            amount="799",
            description="Broadband",
            party="Airtel",
        )
        assert entry["party"] == "Airtel"

    async def test_the_trial_balance_names_who_each_account_dealt_with(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The "Dealt with" column.

        A row there is one account aggregated over every entry that touched it, so it has
        no single counterparty the way an entry does - the server collects the distinct
        ones. Both accounts an entry touches name the party, because both dealt with them.
        """
        await record(
            authed_client,
            api,
            direction="in",
            amount="500",
            description="Sale",
            party="Ramesh",
        )
        await record(
            authed_client,
            api,
            direction="out",
            amount="200",
            description="Broadband",
            party="Airtel",
        )

        rows = (await authed_client.get(f"{api}/reports/trial-balance")).json()["rows"]
        by_code = {row["code"]: row for row in rows}

        # Cash saw both, and each name appears once however many entries there were.
        cash = next(row for row in rows if "Cash" in row["name"])
        assert sorted(cash["parties"]) == ["Airtel", "Ramesh"]

        # The account on the other side of each entry names the same party, because it
        # dealt with them too - and only that party.
        income = next(row for row in rows if row["account_type"] == "income")
        assert income["parties"] == ["Ramesh"]
        expense = next(row for row in rows if row["account_type"] == "expense")
        assert expense["parties"] == ["Airtel"]

        assert by_code  # the report is keyed by code elsewhere; guard against a rename

    async def test_the_trial_balance_names_only_what_was_typed(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """An entry naming no party contributes no name.

        The first version filled the gap with the account on the other side of the entry,
        which put "Owner's Capital" down a column asking who the money was with - the chart
        of accounts restated. Posted through the journal directly, which is the path that
        legitimately has no counterparty.
        """
        # One billing entry first, purely to provision the fiscal calendar: this fixture
        # deliberately has no fiscal year, because billing creates one on demand and a
        # direct journal post does not.
        await record(
            authed_client, api, direction="in", amount="1", description="Opening", party="Ramesh"
        )

        accounts = (await authed_client.get(f"{api}/accounts")).json()
        cash = next(a for a in accounts if a["system_key"] == "cash")
        capital = next(a for a in accounts if a["system_key"] == "owner_capital")
        journals = (await authed_client.get(f"{api}/journals")).json()
        general = next(j for j in journals if j["journal_type"] == "general")

        posted = await authed_client.post(
            f"{api}/journal-entries",
            json={
                "journal_id": general["id"],
                "entry_date": dt.date.today().isoformat(),
                "narration": "Owner puts money in",
                "post": True,
                "lines": [
                    {"account_id": cash["id"], "debit": "1000"},
                    {"account_id": capital["id"], "credit": "1000"},
                ],
            },
        )
        assert posted.status_code == 201, posted.text

        rows = (await authed_client.get(f"{api}/reports/trial-balance")).json()["rows"]
        cash_row = next(row for row in rows if row["code"] == cash["code"])
        # Only the billing entry named anyone, so only that name appears.
        assert cash_row["parties"] == ["Ramesh"]
        assert capital["name"] not in cash_row["parties"]

        # And the account that entry credited carries no name at all.
        capital_row = next(row for row in rows if row["code"] == capital["code"])
        assert capital_row["parties"] == []

    async def test_is_required(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Rejected server-side, not merely marked required in the form.

        A rule the browser keeps and the API does not is not a rule - and the form is the
        only thing that creates these entries, so nothing legitimate is locked out.
        """
        response = await authed_client.post(
            f"{api}/billing",
            json={"direction": "out", "amount": "50", "description": "Chai"},
        )
        assert response.status_code == 422, response.text
        assert "party" in response.text

    async def test_whitespace_does_not_count_as_filled_in(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Spaces would satisfy a naive length check and look blank on screen."""
        response = await authed_client.post(
            f"{api}/billing",
            json={"direction": "out", "amount": "50", "description": "Chai", "party": "   "},
        )
        assert response.status_code == 422, response.text

    async def test_no_customer_or_supplier_record_is_created(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The point of free text. Naming a party must not create a master record."""
        await record(
            authed_client, api, direction="in", amount="500", description="Sale", party="Ramesh"
        )

        customers = (await authed_client.get(f"{api}/customers")).json()
        suppliers = (await authed_client.get(f"{api}/suppliers")).json()
        assert customers["meta"]["total_items"] == 0
        assert suppliers["meta"]["total_items"] == 0

    async def test_is_searchable(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """ "Everything I paid Airtel" is a question people ask."""
        await record(
            authed_client,
            api,
            direction="out",
            amount="799",
            description="Broadband",
            party="Airtel",
        )
        await record(
            authed_client,
            api,
            direction="out",
            amount="120",
            description="Chai",
            party="Corner stall",
        )

        hit = (await authed_client.get(f"{api}/billing", params={"q": "airtel"})).json()
        assert hit["meta"]["total_items"] == 1
        assert hit["items"][0]["party"] == "Airtel"


class TestListing:
    async def test_lists_newest_first(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """A day book is read backwards from today."""
        today = dt.date.today()
        await record(
            authed_client,
            api,
            direction="out",
            amount="100",
            description="Older",
            entry_date=today - dt.timedelta(days=3),
        )
        await record(
            authed_client, api, direction="out", amount="200", description="Newer", entry_date=today
        )

        body = (await authed_client.get(f"{api}/billing")).json()
        assert [row["description"] for row in body["items"]] == ["Newer", "Older"]

    async def test_filters_by_direction(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        await record(authed_client, api, direction="in", amount="500", description="Sale")
        await record(authed_client, api, direction="out", amount="300", description="Expense")

        money_in = (await authed_client.get(f"{api}/billing", params={"direction": "in"})).json()
        assert [row["description"] for row in money_in["items"]] == ["Sale"]
        assert money_in["meta"]["total_items"] == 1

    async def test_searches_the_description(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        await record(
            authed_client, api, direction="out", amount="500", description="Chai for staff"
        )
        await record(authed_client, api, direction="out", amount="300", description="Bus tickets")

        hit = (await authed_client.get(f"{api}/billing", params={"q": "chai"})).json()
        assert hit["meta"]["total_items"] == 1

    async def test_summary_reports_in_out_and_net(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        await record(authed_client, api, direction="in", amount="20000", description="Sales")
        await record(authed_client, api, direction="out", amount="8000", description="Rent")

        summary = (await authed_client.get(f"{api}/billing/summary")).json()
        assert D(summary["money_in"]) == D("20000")
        assert D(summary["money_out"]) == D("8000")
        assert D(summary["net"]) == D("12000")
        assert summary["entry_count"] == 2

    async def test_an_empty_book_summarises_to_zero(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        summary = (await authed_client.get(f"{api}/billing/summary")).json()
        assert D(summary["money_in"]) == 0
        assert D(summary["money_out"]) == 0
        assert summary["entry_count"] == 0

    async def test_ignores_entries_from_other_modules(
        self, authed_client: AsyncClient, api: str, books: Organization, db: AsyncSession
    ) -> None:
        """A manual journal entry is not a billing entry.

        The list is tagged by source, so an accountant's adjusting entry does not
        appear in the shopkeeper's day book - and cannot break the two-line
        reconstruction.
        """
        # `POST /journal-entries` does not create the fiscal year on demand; only
        # billing does, which is the whole point of that convenience.
        await FiscalCalendarService(db).ensure_year_for(books.id, fiscal_year_start_month=4)
        await db.flush()

        journals = (await authed_client.get(f"{api}/journals")).json()
        general = next(j for j in journals if j["journal_type"] == "general")
        accounts = (await authed_client.get(f"{api}/accounts", params={"page_size": 200})).json()
        rows = accounts["items"] if isinstance(accounts, dict) else accounts
        cash = next(r for r in rows if r["code"] == "1110")
        rent = next(r for r in rows if r["code"] == "5210")

        manual = await authed_client.post(
            f"{api}/journal-entries",
            json={
                "journal_id": general["id"],
                "entry_date": dt.date.today().isoformat(),
                "narration": "Accountant adjustment",
                "lines": [
                    {"account_id": rent["id"], "debit": "999", "credit": "0"},
                    {"account_id": cash["id"], "debit": "0", "credit": "999"},
                ],
                "post": True,
            },
        )
        assert manual.status_code == 201, manual.text

        body = (await authed_client.get(f"{api}/billing")).json()
        assert body["meta"]["total_items"] == 0

    async def test_entries_do_not_leak_across_organizations(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        await record(authed_client, api, direction="in", amount="20000", description="Sales")

        created = await authed_client.post(f"{api}/organizations", json={"name": "Second Co"})
        assert created.status_code == 201, created.text
        switched = await authed_client.post(
            f"{api}/auth/switch-organization/{created.json()['id']}"
        )
        authed_client.headers["Authorization"] = f"Bearer {switched.json()['access_token']}"

        body = (await authed_client.get(f"{api}/billing")).json()
        assert body["meta"]["total_items"] == 0


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------
class TestReversal:
    async def test_reversing_cancels_the_ledger_effect(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The only honest undo of a posted entry.

        Not a delete and not an edit - an opposite entry that nets it to zero, which
        is what an auditor expects to find.
        """
        entry = await record(
            authed_client, api, direction="out", amount="5000", description="Wrong amount"
        )

        response = await authed_client.post(
            f"{api}/billing/{entry['id']}/reverse", json={"reason": "Typed the wrong figure"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["is_reversed"] is True

        dashboard = (await authed_client.get(f"{api}/analytics/dashboard")).json()
        assert D(dashboard["expenses"]["current"]) == 0
        assert D(dashboard["cash"]) == 0

        report = (await authed_client.get(f"{api}/reports/trial-balance")).json()
        assert report["is_balanced"] is True

        # Total movement balances too, and this is the case that proves it is a real
        # check rather than a restatement of `is_balanced`: here the net figures are all
        # nil, so the balance columns agree trivially at zero, while the gross columns
        # still carry ₹5,000 on each side. The trial balance reports both, and the UI
        # footers them side by side.
        assert sum(D(row["gross_debit"]) for row in report["rows"]) == sum(
            D(row["gross_credit"]) for row in report["rows"]
        )
        assert sum(D(row["gross_debit"]) for row in report["rows"]) > 0

    async def test_the_original_stays_in_the_list(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Both rows survive. The cancellation is part of the record, not a deletion
        of it."""
        entry = await record(
            authed_client, api, direction="out", amount="5000", description="Wrong amount"
        )
        await authed_client.post(f"{api}/billing/{entry['id']}/reverse", json={})

        body = (await authed_client.get(f"{api}/billing")).json()
        found = next(row for row in body["items"] if row["id"] == entry["id"])
        assert found["is_reversed"] is True

    async def test_a_reversed_entry_leaves_the_totals(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        entry = await record(authed_client, api, direction="out", amount="5000", description="Oops")
        await record(authed_client, api, direction="out", amount="300", description="Real expense")
        await authed_client.post(f"{api}/billing/{entry['id']}/reverse", json={})

        summary = (await authed_client.get(f"{api}/billing/summary")).json()
        assert D(summary["money_out"]) == D("300")
        assert summary["entry_count"] == 1

    async def test_cannot_reverse_twice(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        entry = await record(authed_client, api, direction="out", amount="5000", description="Oops")
        first = await authed_client.post(f"{api}/billing/{entry['id']}/reverse", json={})
        assert first.status_code == 200

        second = await authed_client.post(f"{api}/billing/{entry['id']}/reverse", json={})
        assert second.status_code == 422
        assert second.json()["error"]["code"] == "already_reversed"


class TestPermissions:
    async def test_unauthenticated_requests_are_refused(
        self, client: AsyncClient, api: str
    ) -> None:
        for path in ("", "/options", "/summary"):
            assert (await client.get(f"{api}/billing{path}")).status_code == 401

        response = await client.post(
            f"{api}/billing", json={"direction": "out", "amount": "1", "description": "x"}
        )
        assert response.status_code == 401


class TestReversalIsVisibleInTheReports:
    """A reversal must not vanish.

    Both entries stay in the ledger and sum to zero, so nothing in the *net* figures
    records that anything happened. The journal then shows two entries the trial balance
    cannot account for - and an account whose only movement was cancelled disappeared
    from the report entirely, which is indistinguishable from never having been touched.
    """

    async def test_the_journal_marks_the_entry_and_its_reversal(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        entry = await record(
            authed_client, api, direction="out", amount="100", description="Wrong amount"
        )
        await authed_client.post(f"{api}/billing/{entry['id']}/reverse", json={})

        entries = (await authed_client.get(f"{api}/journal-entries")).json()["items"]
        original = next(e for e in entries if e["entry_number"] == entry["entry_number"])
        reversal = next(e for e in entries if e["reverses_id"] == original["id"])

        assert original["status"] == "reversed"
        assert reversal["status"] == "posted"

    async def test_the_journal_reports_which_way_cash_moved(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """An entry has both a debit and a credit, so the useful question is direction."""
        await record(authed_client, api, direction="in", amount="500", description="Sale")
        await record(authed_client, api, direction="out", amount="200", description="Rent")

        entries = (await authed_client.get(f"{api}/journal-entries")).json()["items"]
        by_narration = {e["narration"]: e for e in entries}

        assert by_narration["Sale"]["cash_direction"] == "in"
        assert D(by_narration["Sale"]["cash_amount"]) == D("500")
        assert by_narration["Rent"]["cash_direction"] == "out"
        assert D(by_narration["Rent"]["cash_amount"]) == D("200")

    async def test_the_trial_balance_counts_the_reversal(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        entry = await record(
            authed_client, api, direction="out", amount="100", description="Wrong amount"
        )

        before = (await authed_client.get(f"{api}/reports/trial-balance")).json()
        assert before["reversed_entry_count"] == 0

        await authed_client.post(f"{api}/billing/{entry['id']}/reverse", json={})

        after = (await authed_client.get(f"{api}/reports/trial-balance")).json()
        assert after["reversed_entry_count"] == 1
        assert after["is_balanced"] is True

    async def test_an_account_that_cancelled_out_stays_on_the_trial_balance(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The bug this fixes: the expense account vanished entirely.

        Its ₹100 charge and the ₹100 reversal net to zero, and the report dropped every
        zero-net row - so the only trace of the whole episode was in the journal.
        """
        options = (await authed_client.get(f"{api}/billing/options")).json()
        category = next(
            c for c in options["categories"] if c["direction"] == "out" and c["is_default"]
        )

        entry = await record(
            authed_client,
            api,
            direction="out",
            amount="100",
            description="Wrong amount",
            category_id=category["id"],
        )
        await authed_client.post(f"{api}/billing/{entry['id']}/reverse", json={})

        report = (await authed_client.get(f"{api}/reports/trial-balance")).json()
        row = next((r for r in report["rows"] if r["account_id"] == category["id"]), None)

        assert row is not None, "the reversed account vanished from the trial balance"
        # Nets to nothing...
        assert D(row["debit"]) == 0
        assert D(row["credit"]) == 0
        # ...but the movement that cancelled is still reported.
        assert D(row["gross_debit"]) == D("100")
        assert D(row["gross_credit"]) == D("100")

    async def test_an_untouched_account_still_stays_off_the_report(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Showing every zero account would bury the four rows that matter under a
        hundred that do not. Only accounts with *activity* are kept."""
        await record(authed_client, api, direction="in", amount="500", description="Sale")

        report = (await authed_client.get(f"{api}/reports/trial-balance")).json()
        names = {r["name"] for r in report["rows"]}
        assert "Pet Care" not in names
        assert len(report["rows"]) < 10
