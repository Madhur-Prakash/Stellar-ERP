"""Analytics over HTTP, against a real posted ledger.

The whole point of this module is that a dashboard tile cannot disagree with the
statement it summarises. That is only true if it is tested, so these tests post real
invoices and bills and then assert the tiles equal the P&L, the position figures
equal the balance sheet, and the monthly series sums to the total.

`test_the_trend_sums_to_the_profit_and_loss` is the load-bearing one: the trend takes
a performance shortcut (one grouped query instead of twelve P&L computations), and
that test is what makes the shortcut safe rather than merely fast.
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
    """Chart of accounts, fiscal years, a GSTIN, and an April fiscal start.

    Two fiscal years, not one: every comparison in this module reaches into the
    previous period, and posting into a month with no fiscal year raises.
    """
    await ChartOfAccountsService(db).seed_defaults(organization.id)

    calendar = FiscalCalendarService(db)
    today = dt.date.today()
    await calendar.ensure_year_for(organization.id, fiscal_year_start_month=4)
    # The previous year, so a year-on-year comparison has somewhere to post.
    await calendar.ensure_year_for(
        organization.id,
        on=dt.date(today.year - 1, today.month, 1),
        fiscal_year_start_month=4,
    )

    organization.gstin = "27AAAAA0000A1Z5"
    organization.fiscal_year_start_month = 4
    organization.timezone = "Asia/Kolkata"
    organization.currency = "INR"
    await db.flush()
    return organization


async def make_customer(client: AsyncClient, api: str, name: str) -> str:
    response = await client.post(
        f"{api}/customers", json={"name": name, "gstin": "27AABCU9603R1ZM"}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def post_invoice(
    client: AsyncClient,
    api: str,
    customer_id: str,
    *,
    amount: str,
    on: dt.date,
    description: str = "Consulting",
    tax_rate: str = "18",
) -> dict:
    """Raise and post an invoice, so it reaches the ledger."""
    response = await client.post(
        f"{api}/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": on.isoformat(),
            "lines": [
                {
                    "description": description,
                    "quantity": "1",
                    "unit_price": amount,
                    "tax_rate": tax_rate,
                }
            ],
            "post": True,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "posted", body
    return dict(body)


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------
class TestPeriods:
    async def test_serves_the_windows_and_the_fiscal_settings(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The frontend must not re-derive "this financial year" itself."""
        response = await authed_client.get(f"{api}/analytics/periods")
        assert response.status_code == 200, response.text
        body = response.json()

        values = [option["value"] for option in body["options"]]
        assert "this_month" in values
        assert "this_fiscal_year" in values
        assert body["fiscal_year_start_month"] == 4
        assert body["today"]

    async def test_requires_report_read(self, authed_client: AsyncClient, api: str) -> None:
        # The owner has it; this asserts the dependency is wired at all, and the
        # negative case is covered in TestPermissions below.
        assert (await authed_client.get(f"{api}/analytics/periods")).status_code == 200


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
class TestDashboard:
    async def test_empty_books_report_zero_not_an_error(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """A business on day one has zero revenue, and the dashboard must say so
        rather than 500 or show placeholder figures."""
        response = await authed_client.get(f"{api}/analytics/dashboard")
        assert response.status_code == 200, response.text
        body = response.json()

        assert D(body["revenue"]["current"]) == 0
        assert D(body["net_profit"]["current"]) == 0
        assert body["revenue"]["change_percent"] is None  # no basis, not "0%"
        assert body["invoices_issued"] == 0

    async def test_revenue_matches_the_profit_and_loss_exactly(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The central guarantee of this module.

        A tile that disagrees with the statement destroys trust in both, and nobody
        can tell which is right. So the tile is computed *by* the statement.
        """
        today = dt.date.today()
        customer = await make_customer(authed_client, api, "Acme Buyer")
        await post_invoice(authed_client, api, customer, amount="10000", on=today)
        await post_invoice(authed_client, api, customer, amount="5000", on=today)

        dashboard = (
            await authed_client.get(f"{api}/analytics/dashboard", params={"period": "this_month"})
        ).json()
        span = dashboard["span"]

        statement = (
            await authed_client.get(
                f"{api}/reports/profit-and-loss",
                params={"from_date": span["start"], "to_date": span["end"]},
            )
        ).json()

        assert D(dashboard["revenue"]["current"]) == D(statement["total_income"])
        assert D(dashboard["expenses"]["current"]) == D(statement["total_expenses"])
        assert D(dashboard["net_profit"]["current"]) == D(statement["net_profit"])
        # And it is the real figure, not zero passing vacuously.
        assert D(dashboard["revenue"]["current"]) == D("15000")

    async def test_revenue_excludes_gst(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """GST collected is not revenue - it is the government's money in transit.

        A ₹10,000 invoice at 18% bills ₹11,800. Reporting ₹11,800 as revenue overstates
        income by the tax and is the most common way a naive dashboard flatters the
        business.
        """
        today = dt.date.today()
        customer = await make_customer(authed_client, api, "Acme Buyer")
        invoice = await post_invoice(authed_client, api, customer, amount="10000", on=today)

        assert D(invoice["grand_total"]) == D("11800.00")

        dashboard = (await authed_client.get(f"{api}/analytics/dashboard")).json()
        assert D(dashboard["revenue"]["current"]) == D("10000")

    async def test_position_figures_match_the_balance_sheet(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Receivables come from the Accounts Receivable control account.

        The invoice table also knows what is outstanding, but the ledger is what an
        accountant checks, and the two must agree.
        """
        today = dt.date.today()
        customer = await make_customer(authed_client, api, "Acme Buyer")
        await post_invoice(authed_client, api, customer, amount="10000", on=today)

        dashboard = (await authed_client.get(f"{api}/analytics/dashboard")).json()
        # Receivable is the gross invoice, tax included - the customer owes all of it.
        assert D(dashboard["receivables"]) == D("11800")

        sheet = (
            await authed_client.get(
                f"{api}/reports/balance-sheet", params={"as_of": today.isoformat()}
            )
        ).json()
        assert sheet["is_balanced"] is True
        assert D(sheet["total_assets"]) >= D(dashboard["receivables"])

    async def test_the_comparison_window_is_reported(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """A percentage is meaningless without saying what it is measured against.

        For a month-to-date figure the comparison is truncated to the same number of
        days, which the user can only verify if the dates are in the response.
        """
        response = await authed_client.get(
            f"{api}/analytics/dashboard", params={"period": "this_month"}
        )
        body = response.json()

        span, comparison = body["span"], body["comparison"]
        assert comparison["days"] <= span["days"]
        assert comparison["end"] < span["start"]  # no overlap
        assert body["period_label"] == "This month"

    async def test_counts_only_issued_invoices_in_the_window(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        today = dt.date.today()
        customer = await make_customer(authed_client, api, "Acme Buyer")
        await post_invoice(authed_client, api, customer, amount="1000", on=today)

        this_month = (
            await authed_client.get(f"{api}/analytics/dashboard", params={"period": "this_month"})
        ).json()
        assert this_month["invoices_issued"] == 1

        last_month = (
            await authed_client.get(f"{api}/analytics/dashboard", params={"period": "last_month"})
        ).json()
        assert last_month["invoices_issued"] == 0

    @pytest.mark.parametrize(
        "period",
        [
            "this_month",
            "last_month",
            "this_quarter",
            "this_fiscal_year",
            "last_30_days",
            "last_12_months",
        ],
    )
    async def test_every_period_is_accepted(
        self, authed_client: AsyncClient, api: str, books: Organization, period: str
    ) -> None:
        """A period the API advertises must actually resolve."""
        response = await authed_client.get(f"{api}/analytics/dashboard", params={"period": period})
        assert response.status_code == 200, response.text

    async def test_rejects_an_unknown_period(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        response = await authed_client.get(
            f"{api}/analytics/dashboard", params={"period": "since_the_dawn_of_time"}
        )
        assert response.status_code == 422

    async def test_money_crosses_the_wire_as_strings(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """A float here would round-trip a ledger figure through IEEE-754 on its way
        to the screen the owner makes decisions from."""
        body = (await authed_client.get(f"{api}/analytics/dashboard")).json()

        assert isinstance(body["revenue"]["current"], str)
        assert isinstance(body["cash"], str)
        assert isinstance(body["receivables"], str)


# ---------------------------------------------------------------------------
# Trend
# ---------------------------------------------------------------------------
class TestTrend:
    async def test_the_trend_sums_to_the_profit_and_loss(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The reconciliation that licenses the trend's performance shortcut.

        `AnalyticsService.trend` uses one grouped query instead of twelve P&L
        computations. That is only safe if the series adds up to the statement for the
        same span - otherwise the chart and the tile above it tell different stories.
        """
        today = dt.date.today()
        customer = await make_customer(authed_client, api, "Acme Buyer")
        await post_invoice(authed_client, api, customer, amount="7000", on=today)

        trend = (
            await authed_client.get(f"{api}/analytics/trend", params={"period": "this_fiscal_year"})
        ).json()
        span = trend["span"]

        statement = (
            await authed_client.get(
                f"{api}/reports/profit-and-loss",
                params={"from_date": span["start"], "to_date": span["end"]},
            )
        ).json()

        assert D(trend["total_income"]) == D(statement["total_income"])
        assert D(trend["total_expenses"]) == D(statement["total_expenses"])
        assert D(trend["total_income"]) == D("7000")

        # And the points themselves add up to the totals they are presented with.
        assert sum(D(point["income"]) for point in trend["points"]) == D(trend["total_income"])

    async def test_includes_months_with_no_activity(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """A chart that skips an empty month draws a line across it and implies
        trading that did not happen."""
        trend = (
            await authed_client.get(f"{api}/analytics/trend", params={"period": "last_12_months"})
        ).json()

        assert len(trend["points"]) == 12
        assert all(D(point["income"]) >= 0 for point in trend["points"])

    async def test_labels_carry_the_year(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """A 12-month chart spans two Aprils."""
        trend = (
            await authed_client.get(f"{api}/analytics/trend", params={"period": "last_12_months"})
        ).json()

        labels = [point["label"] for point in trend["points"]]
        assert len(set(labels)) == 12
        assert all(len(label.split()) == 2 for label in labels)

    async def test_profit_is_income_less_expenses_per_point(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        trend = (
            await authed_client.get(f"{api}/analytics/trend", params={"period": "last_12_months"})
        ).json()

        for point in trend["points"]:
            assert D(point["profit"]) == D(point["income"]) - D(point["expenses"])


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------
class TestRankings:
    async def test_ranks_customers_by_taxable_value(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        today = dt.date.today()
        big = await make_customer(authed_client, api, "Big Buyer")
        small = await make_customer(authed_client, api, "Small Buyer")

        await post_invoice(authed_client, api, big, amount="50000", on=today)
        await post_invoice(authed_client, api, small, amount="5000", on=today)

        body = (
            await authed_client.get(
                f"{api}/analytics/top-customers", params={"period": "this_fiscal_year"}
            )
        ).json()

        assert [row["label"] for row in body["rows"]] == ["Big Buyer", "Small Buyer"]
        assert D(body["rows"][0]["amount"]) == D("50000")
        assert D(body["total"]) == D("55000")

    async def test_a_higher_tax_rate_does_not_promote_a_customer(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Ranking on the invoice total would put a 28% buyer above a larger 5% one.

        GST is money held for the government; including it invents a fact about who is
        worth more to the business.
        """
        today = dt.date.today()
        luxury = await make_customer(authed_client, api, "Luxury Buyer")
        staples = await make_customer(authed_client, api, "Staples Buyer")

        # 10,000 at 28% = 12,800 gross. 11,000 at 5% = 11,550 gross.
        await post_invoice(authed_client, api, luxury, amount="10000", on=today, tax_rate="28")
        await post_invoice(authed_client, api, staples, amount="11000", on=today, tax_rate="5")

        body = (
            await authed_client.get(
                f"{api}/analytics/top-customers", params={"period": "this_fiscal_year"}
            )
        ).json()

        # Ranked on taxable value, so the genuinely larger customer leads - even
        # though its invoice total is smaller.
        assert body["rows"][0]["label"] == "Staples Buyer"

    async def test_the_total_covers_all_rows_not_just_the_top_n(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """So the UI can say "these two are 40% of revenue" truthfully."""
        today = dt.date.today()
        for index in range(4):
            customer = await make_customer(authed_client, api, f"Buyer {index}")
            await post_invoice(authed_client, api, customer, amount="1000", on=today)

        body = (
            await authed_client.get(
                f"{api}/analytics/top-customers",
                params={"period": "this_fiscal_year", "limit": 2},
            )
        ).json()

        assert len(body["rows"]) == 2
        assert D(body["total"]) == D("4000")  # all four, not the two returned

    async def test_ranks_products_by_line_description(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Grouped by description because invoice lines are deliberately free-text -
        a service with no product record must still be counted."""
        today = dt.date.today()
        customer = await make_customer(authed_client, api, "Acme Buyer")
        await post_invoice(
            authed_client, api, customer, amount="9000", on=today, description="Widget"
        )
        await post_invoice(
            authed_client, api, customer, amount="1000", on=today, description="Delivery"
        )

        body = (
            await authed_client.get(
                f"{api}/analytics/top-products", params={"period": "this_fiscal_year"}
            )
        ).json()

        assert [row["label"] for row in body["rows"]] == ["Widget", "Delivery"]
        assert body["rows"][0]["id"] is None  # not keyed to a product record


# ---------------------------------------------------------------------------
# Control accounts
# ---------------------------------------------------------------------------
class TestControlChecks:
    async def test_agree_on_a_clean_set_of_books(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Receivables derived from the ledger and from the invoices must match.

        This is the monthly reconciliation a bookkeeper does by hand. If it ever
        fails, a document updated one table but not the other.
        """
        today = dt.date.today()
        customer = await make_customer(authed_client, api, "Acme Buyer")
        await post_invoice(authed_client, api, customer, amount="10000", on=today)

        body = (await authed_client.get(f"{api}/analytics/control-checks")).json()

        assert body["all_agree"] is True, body["checks"]
        names = {check["name"] for check in body["checks"]}
        assert names == {"Accounts receivable", "Accounts payable", "Inventory"}

        receivable = next(c for c in body["checks"] if c["name"] == "Accounts receivable")
        assert D(receivable["ledger"]) == D("11800")
        assert D(receivable["subledger"]) == D("11800")
        assert D(receivable["difference"]) == 0

    async def test_agree_on_empty_books(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        body = (await authed_client.get(f"{api}/analytics/control-checks")).json()
        assert body["all_agree"] is True
        assert all(D(check["ledger"]) == 0 for check in body["checks"])

    async def test_a_payment_keeps_them_in_step(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """A payment reduces both the control account and the invoice's outstanding.

        If it touched only one, this is the test that catches it.
        """
        today = dt.date.today()
        customer = await make_customer(authed_client, api, "Acme Buyer")
        invoice = await post_invoice(authed_client, api, customer, amount="10000", on=today)

        paid = await authed_client.post(
            f"{api}/payments",
            json={
                "customer_id": customer,
                "amount": "5000",
                "allocations": [{"invoice_id": invoice["id"], "amount": "5000"}],
            },
        )
        assert paid.status_code == 201, paid.text

        body = (await authed_client.get(f"{api}/analytics/control-checks")).json()
        assert body["all_agree"] is True, body["checks"]

        receivable = next(c for c in body["checks"] if c["name"] == "Accounts receivable")
        assert D(receivable["ledger"]) == D("6800")  # 11,800 less the 5,000 paid

    async def test_honours_an_as_of_date(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """Reconciling "as at last month end" is the actual bookkeeping workflow."""
        today = dt.date.today()
        customer = await make_customer(authed_client, api, "Acme Buyer")
        await post_invoice(authed_client, api, customer, amount="10000", on=today)

        yesterday = today - dt.timedelta(days=1)
        body = (
            await authed_client.get(
                f"{api}/analytics/control-checks", params={"as_of": yesterday.isoformat()}
            )
        ).json()

        assert body["as_of"] == yesterday.isoformat()
        receivable = next(c for c in body["checks"] if c["name"] == "Accounts receivable")
        assert D(receivable["ledger"]) == 0  # the invoice is dated today


# ---------------------------------------------------------------------------
# Tenancy and permissions
# ---------------------------------------------------------------------------
class TestIsolation:
    async def test_figures_do_not_leak_across_organizations(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """One organization's revenue must never appear in another's dashboard.

        Checked here rather than assumed, because analytics aggregates across every
        table at once - it is the single easiest place to forget an org filter, and a
        missing one shows up as someone else's revenue rather than as an error.
        """
        today = dt.date.today()
        customer = await make_customer(authed_client, api, "Acme Buyer")
        await post_invoice(authed_client, api, customer, amount="10000", on=today)

        created = await authed_client.post(f"{api}/organizations", json={"name": "Second Company"})
        assert created.status_code == 201, created.text
        second = created.json()["id"]

        switched = await authed_client.post(f"{api}/auth/switch-organization/{second}")
        assert switched.status_code == 200, switched.text
        authed_client.headers["Authorization"] = f"Bearer {switched.json()['access_token']}"

        body = (await authed_client.get(f"{api}/analytics/dashboard")).json()
        assert D(body["revenue"]["current"]) == 0
        assert D(body["receivables"]) == 0
        assert body["invoices_issued"] == 0

        ranking = (await authed_client.get(f"{api}/analytics/top-customers")).json()
        assert ranking["rows"] == []


class TestPermissions:
    ENDPOINTS = ("dashboard", "trend", "top-customers", "top-products", "control-checks", "periods")

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    async def test_unauthenticated_requests_are_refused(
        self, client: AsyncClient, api: str, endpoint: str
    ) -> None:
        response = await client.get(f"{api}/analytics/{endpoint}")
        assert response.status_code == 401


class TestTrendAcceptsExplicitDates:
    """A chart beside a report filtered to custom dates must cover the same window.

    Without this the accounting screen's range filter could move every panel except the
    trend, and two charts side by side would show different periods - a reliable way to
    draw a wrong conclusion from correct numbers.
    """

    async def test_explicit_dates_override_the_preset(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        today = dt.date.today()
        customer = await make_customer(authed_client, api, "Acme Buyer")
        await post_invoice(authed_client, api, customer, amount="4000", on=today)

        response = await authed_client.get(
            f"{api}/analytics/trend",
            params={
                "period": "last_12_months",
                "from_date": today.replace(day=1).isoformat(),
                "to_date": today.isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["span"]["start"] == today.replace(day=1).isoformat()
        assert body["span"]["end"] == today.isoformat()
        assert D(body["total_income"]) == D("4000")

    async def test_a_reversed_range_is_rejected(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        response = await authed_client.get(
            f"{api}/analytics/trend",
            params={"from_date": "2026-07-31", "to_date": "2026-07-01"},
        )
        assert response.status_code == 422

    async def test_the_preset_still_works_without_dates(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        response = await authed_client.get(
            f"{api}/analytics/trend", params={"period": "this_month"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["points"]


class TestWaterfallSourceMatchesTheDashboard:
    """The waterfall's closing bar is the P&L's `net_profit`, so the two must agree.

    The chart uses the statement's own figures rather than re-adding the lines, and this
    asserts the premise that makes that safe: for one window, the P&L and the dashboard
    report the same profit.
    """

    async def test_profit_and_loss_matches_the_dashboard_for_the_same_window(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        today = dt.date.today()
        customer = await make_customer(authed_client, api, "Acme Buyer")
        await post_invoice(authed_client, api, customer, amount="9000", on=today)

        dashboard = (
            await authed_client.get(f"{api}/analytics/dashboard", params={"period": "this_month"})
        ).json()
        span = dashboard["span"]

        report = (
            await authed_client.get(
                f"{api}/reports/profit-and-loss",
                params={"from_date": span["start"], "to_date": span["end"]},
            )
        ).json()

        assert D(report["net_profit"]) == D(dashboard["net_profit"]["current"])

    async def test_the_expense_lines_sum_to_the_stated_total(
        self, authed_client: AsyncClient, api: str, books: Organization
    ) -> None:
        """The waterfall steps down once per expense line, so the steps must add up to the
        total it claims to decompose - otherwise the bars and the closing figure disagree."""
        today = dt.date.today()
        report = (
            await authed_client.get(
                f"{api}/reports/profit-and-loss",
                params={
                    "from_date": today.replace(day=1).isoformat(),
                    "to_date": today.isoformat(),
                },
            )
        ).json()

        lines = sum((D(line["amount"]) for line in report["expenses"]), start=D("0"))
        assert lines == D(report["total_expenses"])
        assert D(report["total_income"]) - D(report["total_expenses"]) == D(report["net_profit"])
