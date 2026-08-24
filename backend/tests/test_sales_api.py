"""Sales and accounting HTTP surface.

This file exists because of a gap it would have caught: `with_computed` validated
the ORM object *before* overlaying computed fields, so every response helper using
it raised `ValidationError` on a required computed field. Sales and accounting
routers had 10 such call sites and **no test ever executed one** - the module tests
called services directly. The bug surfaced only when purchasing got API tests.

So: every response-assembly helper in sales and accounting is now exercised over
HTTP at least once.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounting.service import ChartOfAccountsService, FiscalCalendarService
from app.modules.organizations.models import Organization

pytestmark = pytest.mark.integration

D = Decimal


def money(value: str) -> Decimal:
    """Money crosses the wire as a decimal string; compare numerically."""
    return Decimal(value)


@pytest.fixture
async def ready_books(db: AsyncSession, organization: Organization) -> Organization:
    await ChartOfAccountsService(db).seed_defaults(organization.id)
    await FiscalCalendarService(db).ensure_year_for(organization.id, fiscal_year_start_month=4)
    organization.gstin = "27AABCU9603R1ZM"
    await db.flush()
    return organization


class TestOrderToCashCycle:
    async def test_full_cycle_over_the_api(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """Customer → quotation → order → invoice → payment, then the identities."""
        # --- Customer -------------------------------------------------------
        response = await authed_client.post(
            f"{api}/customers", json={"name": "Kirana Retail", "gstin": "27AAAAA0000A1Z5"}
        )
        assert response.status_code == 201, response.text
        customer = response.json()
        assert customer["state_code"] == "27"

        line = {
            "description": "Widget",
            "quantity": "10",
            "unit_price": "100",
            "tax_rate": "18",
        }

        # --- Quotation ------------------------------------------------------
        response = await authed_client.post(
            f"{api}/quotations", json={"customer_id": customer["id"], "lines": [line]}
        )
        assert response.status_code == 201, response.text
        quotation = response.json()
        assert money(quotation["grand_total"]) == D("1180.0000")
        assert money(quotation["cgst_total"]) == D("90.0000")
        assert quotation["customer_name"] == "Kirana Retail"

        sent = await authed_client.post(f"{api}/quotations/{quotation['id']}/send")
        assert sent.status_code == 200, sent.text
        assert sent.json()["status"] == "sent"

        accepted = await authed_client.post(f"{api}/quotations/{quotation['id']}/accept")
        assert accepted.json()["status"] == "accepted"

        # --- Order from the quotation ---------------------------------------
        response = await authed_client.post(f"{api}/sales-orders/from-quotation/{quotation['id']}")
        assert response.status_code == 201, response.text
        order = response.json()
        assert money(order["grand_total"]) == money(quotation["grand_total"])
        assert money(order["uninvoiced_total"]) == D("1180.0000")

        confirmed = await authed_client.post(f"{api}/sales-orders/{order['id']}/confirm")
        assert confirmed.json()["status"] == "confirmed"

        # The quotation is terminal now.
        again = await authed_client.post(f"{api}/sales-orders/from-quotation/{quotation['id']}")
        assert again.status_code == 409

        # --- Invoice --------------------------------------------------------
        response = await authed_client.post(
            f"{api}/invoices",
            json={
                "customer_id": customer["id"],
                "sales_order_id": order["id"],
                "lines": [line],
                "post": True,
            },
        )
        assert response.status_code == 201, response.text
        invoice = response.json()
        assert invoice["status"] == "posted"
        assert invoice["journal_entry_id"] is not None
        assert money(invoice["outstanding"]) == D("1180.0000")
        assert invoice["is_overdue"] is False

        # A posted invoice is immutable.
        edit = await authed_client.patch(
            f"{api}/invoices/{invoice['id']}", json={"notes": "tampering"}
        )
        assert edit.status_code == 422
        assert "statutory record" in edit.json()["error"]["message"]

        # --- Payment --------------------------------------------------------
        response = await authed_client.post(
            f"{api}/payments",
            json={
                "customer_id": customer["id"],
                "amount": "1180",
                "allocations": [{"invoice_id": invoice["id"], "amount": "1180"}],
            },
        )
        assert response.status_code == 201, response.text
        payment = response.json()
        assert money(payment["unallocated_amount"]) == D("0")
        assert payment["allocations"][0]["invoice_number"] == invoice["invoice_number"]

        paid = await authed_client.get(f"{api}/invoices/{invoice['id']}")
        assert paid.json()["status"] == "paid"

        # --- Identities -----------------------------------------------------
        tb = await authed_client.get(f"{api}/reports/trial-balance")
        assert tb.json()["is_balanced"] is True
        bs = await authed_client.get(f"{api}/reports/balance-sheet")
        assert bs.json()["is_balanced"] is True


class TestSalesReports:
    async def test_ageing_and_summary(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        customer = (await authed_client.post(f"{api}/customers", json={"name": "C"})).json()
        await authed_client.post(
            f"{api}/invoices",
            json={
                "customer_id": customer["id"],
                "lines": [
                    {"description": "X", "quantity": "1", "unit_price": "1000", "tax_rate": "0"}
                ],
                "post": True,
            },
        )

        ageing = await authed_client.get(f"{api}/invoices/ageing")
        assert ageing.status_code == 200, ageing.text
        assert money(ageing.json()["total_outstanding"]) == D("1000.0000")

        summary = await authed_client.get(
            f"{api}/invoices/summary",
            params={"from_date": "2000-01-01", "to_date": "2100-01-01"},
        )
        assert summary.status_code == 200, summary.text
        assert summary.json()["invoice_count"] == 1

    async def test_customer_statement(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        customer = (
            await authed_client.post(f"{api}/customers", json={"name": "C", "credit_limit": "5000"})
        ).json()
        await authed_client.post(
            f"{api}/invoices",
            json={
                "customer_id": customer["id"],
                "lines": [
                    {"description": "X", "quantity": "1", "unit_price": "2000", "tax_rate": "0"}
                ],
                "post": True,
            },
        )
        response = await authed_client.get(f"{api}/customers/{customer['id']}/statement")
        assert response.status_code == 200, response.text
        body = response.json()
        assert money(body["total_outstanding"]) == D("2000.0000")
        assert money(body["credit_available"]) == D("3000.0000")

    async def test_lead_pipeline(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        created = await authed_client.post(
            f"{api}/leads", json={"name": "Prospect", "estimated_value": "50000"}
        )
        assert created.status_code == 201, created.text

        pipeline = await authed_client.get(f"{api}/leads/pipeline")
        assert pipeline.status_code == 200, pipeline.text
        assert money(pipeline.json()["new"]) == D("50000.0000")

        convert = await authed_client.post(f"{api}/leads/{created.json()['id']}/convert", json={})
        assert convert.status_code == 201, convert.text
        assert convert.json()["name"] == "Prospect"


class TestAccountingApi:
    """Exercises the accounting router's `with_computed` helpers."""

    async def test_chart_of_accounts_with_balances(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        response = await authed_client.get(f"{api}/accounts")
        assert response.status_code == 200, response.text
        accounts = response.json()

        # Asserted by shape rather than by an exact count. `len(accounts) == 53` was here
        # and had to be edited every time the template gained a category - a maintenance
        # tax that never caught a defect, because a wrong *number* of accounts is not a
        # failure mode. Every system role being resolvable is.
        keys = {a["system_key"] for a in accounts if a["system_key"]}
        assert {
            "cash",
            "bank",
            "accounts_receivable",
            "accounts_payable",
            "inventory",
            "sales_revenue",
            "cost_of_goods_sold",
            "gst_input",
            "gst_output",
            "grni",
            "retained_earnings",
            "owner_capital",
            "rounding",
        } <= keys

        cash = next(a for a in accounts if a["system_key"] == "cash")
        assert cash["normal_balance"] == "debit"
        assert cash["is_postable"] is True

        # Groups exist and are not postable; leaves are.
        assert any(a["is_group"] for a in accounts)
        assert all(not a["is_postable"] for a in accounts if a["is_group"])

    async def test_journal_entry_round_trip(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        accounts = (await authed_client.get(f"{api}/accounts")).json()
        cash = next(a for a in accounts if a["system_key"] == "cash")
        capital = next(a for a in accounts if a["system_key"] == "owner_capital")
        journals = (await authed_client.get(f"{api}/journals")).json()
        general = next(j for j in journals if j["journal_type"] == "general")

        response = await authed_client.post(
            f"{api}/journal-entries",
            json={
                "journal_id": general["id"],
                "entry_date": "2026-07-29",
                "narration": "Opening capital",
                "post": True,
                "lines": [
                    {"account_id": cash["id"], "debit": "100000"},
                    {"account_id": capital["id"], "credit": "100000"},
                ],
            },
        )
        assert response.status_code == 201, response.text
        entry = response.json()
        assert entry["status"] == "posted"
        assert entry["entry_number"] is not None
        assert entry["journal_code"] == "GEN"
        assert len(entry["lines"]) == 2
        assert entry["lines"][0]["account_code"] == cash["code"]

        # Reversal.
        reversal = await authed_client.post(f"{api}/journal-entries/{entry['id']}/reverse", json={})
        assert reversal.status_code == 200, reversal.text
        assert reversal.json()["reverses_id"] == entry["id"]

    async def test_unbalanced_entry_is_rejected(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        accounts = (await authed_client.get(f"{api}/accounts")).json()
        cash = next(a for a in accounts if a["system_key"] == "cash")
        capital = next(a for a in accounts if a["system_key"] == "owner_capital")
        journals = (await authed_client.get(f"{api}/journals")).json()

        response = await authed_client.post(
            f"{api}/journal-entries",
            json={
                "journal_id": journals[0]["id"],
                "entry_date": "2026-07-29",
                "narration": "Lopsided",
                "lines": [
                    {"account_id": cash["id"], "debit": "100"},
                    {"account_id": capital["id"], "credit": "90"},
                ],
            },
        )
        assert response.status_code == 422
        assert "does not balance" in response.text

    async def test_account_ledger_endpoint(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        accounts = (await authed_client.get(f"{api}/accounts")).json()
        cash = next(a for a in accounts if a["system_key"] == "cash")

        response = await authed_client.get(
            f"{api}/accounts/{cash['id']}/ledger",
            params={"from_date": "2000-01-01", "to_date": "2100-01-01"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["account"]["code"] == cash["code"]

    async def test_fiscal_years_and_roles_endpoints(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """Covers the calendar and RBAC `with_computed` helpers too."""
        years = await authed_client.get(f"{api}/fiscal-years")
        assert years.status_code == 200, years.text
        assert len(years.json()[0]["periods"]) == 12

        roles = await authed_client.get(f"{api}/roles")
        assert roles.status_code == 200, roles.text
        assert any(r["slug"] == "owner" for r in roles.json())
