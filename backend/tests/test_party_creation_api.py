"""Customer and supplier creation, using exactly the payloads the UI sends.

These exist because of a real bug. The frontend shares one form between customers and
suppliers - they ask for the same six fields - but the two request schemas name the
city differently: a customer has a billing address and a shipping one, so its field is
`billing_city`, while a supplier has a single address and uses `city`. Both schemas are
`extra="forbid"`, so the shared form sent `city` for a customer and got a 422.

TypeScript could not catch it: the form builds its body with conditional spreads
(`...(city ? { city } : {})`), and excess-property checking does not see through a
spread. So the contract is pinned here instead, in the only place that actually
executes it.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

#: The fields the shared `PartyFormModal` collects, with the city key left out -
#: each test adds the one its endpoint expects.
FORM_COMMON: dict[str, Any] = {
    "name": "Sharma Enterprises",
    "gstin": "27AABCU9603R1ZM",
    "email": "accounts@example.com",
    "phone": "022 2345 6789",
    "payment_terms_days": 30,
}


class TestCustomerCreation:
    async def test_accepts_the_form_payload(self, authed_client: AsyncClient, api: str) -> None:
        """`billing_city`, not `city` - this is the 422 the shared form used to cause."""
        response = await authed_client.post(
            f"{api}/customers", json={**FORM_COMMON, "billing_city": "Mumbai"}
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["name"] == "Sharma Enterprises"
        assert body["gstin"] == "27AABCU9603R1ZM"
        assert body["billing_city"] == "Mumbai"
        # Derived from the GSTIN's first two digits, which is what decides the GST split.
        assert body["state_code"] == "27"
        # Generated, so the form does not have to ask for one.
        assert body["code"]

    async def test_rejects_the_supplier_spelling_of_city(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """The exact bug, asserted so the shared form cannot regress into it."""
        response = await authed_client.post(
            f"{api}/customers", json={**FORM_COMMON, "city": "Mumbai"}
        )
        assert response.status_code == 422

    async def test_a_name_alone_is_enough(self, authed_client: AsyncClient, api: str) -> None:
        """Asking a shopkeeper for a GSTIN and a credit limit before they can raise
        their first invoice is how software gets abandoned at step one."""
        response = await authed_client.post(f"{api}/customers", json={"name": "Corner Shop"})
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["name"] == "Corner Shop"
        assert body["gstin"] is None
        assert body["payment_terms_days"] == 30  # a sane default, not zero

    async def test_appears_immediately_in_the_list_the_dropdown_reads(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """The invoice composer selects the new customer straight after creating it,
        so it has to be visible on the very next read."""
        created = await authed_client.post(f"{api}/customers", json={"name": "Corner Shop"})
        assert created.status_code == 201, created.text

        listing = await authed_client.get(f"{api}/customers", params={"page_size": 200})
        assert listing.status_code == 200
        ids = [row["id"] for row in listing.json()["items"]]
        assert created.json()["id"] in ids

    async def test_can_be_invoiced_right_away(self, authed_client: AsyncClient, api: str) -> None:
        """The workflow the missing form was blocking, end to end.

        Creating a customer is only useful if an invoice can then be raised against
        it - which is what the user was trying to do when the dropdown was empty.
        """
        customer = await authed_client.post(
            f"{api}/customers", json={"name": "Sharma Enterprises", "gstin": "27AABCU9603R1ZM"}
        )
        assert customer.status_code == 201, customer.text

        invoice = await authed_client.post(
            f"{api}/invoices",
            json={
                "customer_id": customer.json()["id"],
                "lines": [
                    {
                        "description": "wdsac",
                        "quantity": "1",
                        "unit_price": "1000",
                        "tax_rate": "18",
                    }
                ],
            },
        )
        assert invoice.status_code == 201, invoice.text
        assert invoice.json()["grand_total"] == "1180.0000"


class TestSupplierCreation:
    async def test_accepts_the_form_payload(self, authed_client: AsyncClient, api: str) -> None:
        """`city`, not `billing_city` - the mirror image of the customer case."""
        response = await authed_client.post(
            f"{api}/suppliers", json={**FORM_COMMON, "city": "Mumbai"}
        )
        assert response.status_code == 201, response.text
        body = response.json()

        assert body["name"] == "Sharma Enterprises"
        assert body["city"] == "Mumbai"
        assert body["state_code"] == "27"
        assert body["code"]

    async def test_rejects_the_customer_spelling_of_city(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        response = await authed_client.post(
            f"{api}/suppliers", json={**FORM_COMMON, "billing_city": "Mumbai"}
        )
        assert response.status_code == 422

    async def test_a_name_alone_is_enough(self, authed_client: AsyncClient, api: str) -> None:
        response = await authed_client.post(f"{api}/suppliers", json={"name": "Local Wholesaler"})
        assert response.status_code == 201, response.text
        assert response.json()["gstin"] is None

    async def test_unblocks_confirming_a_scanned_document(
        self, authed_client: AsyncClient, api: str
    ) -> None:
        """The OCR confirm form has the same empty-dropdown dead end.

        A scanned invoice almost always names a supplier who is not on file yet - that
        is the normal case for a first invoice from someone, so creating one inline has
        to work.
        """
        created = await authed_client.post(
            f"{api}/suppliers",
            json={"name": "Mumbai Wholesale Traders", "gstin": "27AABCU9603R1ZM"},
        )
        assert created.status_code == 201, created.text

        listing = await authed_client.get(f"{api}/suppliers", params={"page_size": 200})
        ids = [row["id"] for row in listing.json()["items"]]
        assert created.json()["id"] in ids
