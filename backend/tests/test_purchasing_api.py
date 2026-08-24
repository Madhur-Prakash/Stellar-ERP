"""Purchasing HTTP surface - the full purchase-to-pay cycle over the API.

The service-layer tests in ``test_purchasing.py`` prove the accounting. This file
proves the endpoints are actually reachable and wired correctly: permissions
resolve, request schemas accept what the frontend will send, response schemas
serialise without a lazy load, and the ledger still balances when the whole cycle
runs through HTTP rather than through Python calls.

One end-to-end test covers more real wiring than a dozen isolated ones, because
every step consumes an id the previous step returned.
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
    """Parse a money string from the API.

    Compared numerically rather than as a string: money crosses the wire as a
    decimal string, and its *scale* depends on provenance - a value read back from
    `NUMERIC(18,4)` serialises as `"0.0000"` while the same figure computed in
    Python (`Decimal("5900") - Decimal("5900")`) serialises as `"0"`. Both are the
    same amount, so asserting the string form is brittle by construction.
    """
    return Decimal(value)


@pytest.fixture
async def ready_books(db: AsyncSession, organization: Organization) -> Organization:
    """Chart of accounts, fiscal year, and a GSTIN so tax resolves."""
    await ChartOfAccountsService(db).seed_defaults(organization.id)
    await FiscalCalendarService(db).ensure_year_for(organization.id, fiscal_year_start_month=4)
    organization.gstin = "27AABCU9603R1ZM"
    await db.flush()
    return organization


class TestPurchaseToPayCycle:
    async def test_full_cycle_over_the_api(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """Supplier → product → PO → approve → receipt → bill → payment.

        Then assert the trial balance still balances and inventory reconciles, which
        is the property that matters after any sequence of real operations.
        """
        # --- Supplier -------------------------------------------------------
        response = await authed_client.post(
            f"{api}/suppliers",
            json={"name": "Mumbai Wholesale", "gstin": "27AAAAA0000A1Z5"},
        )
        assert response.status_code == 201, response.text
        supplier = response.json()
        assert supplier["state_code"] == "27"  # derived from the GSTIN, a string not an amount

        # --- Product --------------------------------------------------------
        response = await authed_client.post(
            f"{api}/products",
            json={
                "sku": "WIDGET-1",
                "name": "Widget",
                "barcode": "8901234567890",
                "tax_rate": "18",
                "purchase_price": "50",
                "reorder_level": "20",
            },
        )
        assert response.status_code == 201, response.text
        product = response.json()
        assert product["tracks_stock"] is True

        # Barcode lookup - the scanner path.
        scan = await authed_client.get(f"{api}/products/by-barcode/8901234567890")
        assert scan.status_code == 200
        assert scan.json()["id"] == product["id"]

        # --- Purchase order -------------------------------------------------
        response = await authed_client.post(
            f"{api}/purchase-orders",
            json={
                "supplier_id": supplier["id"],
                "lines": [
                    {
                        "product_id": product["id"],
                        "description": "Widget",
                        "quantity": "100",
                        "unit_price": "50",
                        "tax_rate": "18",
                    }
                ],
            },
        )
        assert response.status_code == 201, response.text
        order = response.json()
        assert money(order["grand_total"]) == D("5900.0000")
        assert money(order["cgst_total"]) == D("450.0000")
        assert money(order["sgst_total"]) == D("450.0000")

        approved = await authed_client.post(f"{api}/purchase-orders/{order['id']}/approve")
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"

        # --- Goods receipt --------------------------------------------------
        response = await authed_client.post(
            f"{api}/goods-receipts",
            json={
                "supplier_id": supplier["id"],
                "purchase_order_id": order["id"],
                "lines": [
                    {
                        "product_id": product["id"],
                        "purchase_order_line_id": order["lines"][0]["id"],
                        "quantity": "100",
                        "unit_cost": "50",
                    }
                ],
                "post": True,
            },
        )
        assert response.status_code == 201, response.text
        receipt = response.json()
        assert receipt["status"] == "posted"
        assert receipt["journal_entry_id"] is not None
        assert money(receipt["total_cost"]) == D("5000.0000")

        # The PO advanced to fully received.
        po_now = await authed_client.get(f"{api}/purchase-orders/{order['id']}")
        assert po_now.json()["status"] == "received"

        # Stock is on hand.
        levels = await authed_client.get(f"{api}/inventory/levels")
        assert levels.status_code == 200
        assert money(levels.json()[0]["quantity"]) == D("100.0000")
        assert money(levels.json()[0]["average_cost"]) == D("50.000000")

        # --- Bill -----------------------------------------------------------
        response = await authed_client.post(
            f"{api}/bills",
            json={
                "supplier_id": supplier["id"],
                "goods_receipt_id": receipt["id"],
                "supplier_invoice_number": "MW-2026-001",
                "lines": [
                    {
                        "product_id": product["id"],
                        "description": "Widget",
                        "quantity": "100",
                        "unit_price": "50",
                        "tax_rate": "18",
                    }
                ],
                "post": True,
            },
        )
        assert response.status_code == 201, response.text
        bill = response.json()
        assert bill["status"] == "posted"
        assert money(bill["grand_total"]) == D("5900.0000")
        assert money(bill["outstanding"]) == D("5900.0000")

        # --- Payment --------------------------------------------------------
        response = await authed_client.post(
            f"{api}/supplier-payments",
            json={
                "supplier_id": supplier["id"],
                "amount": "5900",
                "method": "bank_transfer",
                "allocations": [{"bill_id": bill["id"], "amount": "5900"}],
            },
        )
        assert response.status_code == 201, response.text
        payment = response.json()
        assert money(payment["unallocated_amount"]) == D("0.0000")
        assert len(payment["allocations"]) == 1

        bill_now = await authed_client.get(f"{api}/bills/{bill['id']}")
        assert bill_now.json()["status"] == "paid"
        assert money(bill_now.json()["outstanding"]) == D("0.0000")

        # --- The identities that must survive the whole cycle ---------------
        tb = await authed_client.get(f"{api}/reports/trial-balance")
        assert tb.status_code == 200, tb.text
        assert tb.json()["is_balanced"] is True

        valuation = await authed_client.get(f"{api}/inventory/valuation")
        assert valuation.status_code == 200
        assert money(valuation.json()["total_value"]) == D("5000.0000")

        balance_sheet = await authed_client.get(f"{api}/reports/balance-sheet")
        assert balance_sheet.json()["is_balanced"] is True


class TestInventoryEndpoints:
    async def test_reorder_report_lists_low_stock(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        supplier = (await authed_client.post(f"{api}/suppliers", json={"name": "S"})).json()
        product = (
            await authed_client.post(
                f"{api}/products",
                json={"sku": "LOW-1", "name": "Low stock item", "reorder_level": "50"},
            )
        ).json()
        await authed_client.post(
            f"{api}/goods-receipts",
            json={
                "supplier_id": supplier["id"],
                "lines": [{"product_id": product["id"], "quantity": "10", "unit_cost": "5"}],
                "post": True,
            },
        )

        response = await authed_client.get(f"{api}/products/reorder")
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) == 1
        assert rows[0]["sku"] == "LOW-1"
        assert money(rows[0]["quantity_on_hand"]) == D("10.0000")
        assert money(rows[0]["shortfall"]) == D("40.0000")

    async def test_adjustment_endpoint_writes_off_stock(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        supplier = (await authed_client.post(f"{api}/suppliers", json={"name": "S"})).json()
        product = (
            await authed_client.post(f"{api}/products", json={"sku": "ADJ-1", "name": "Item"})
        ).json()
        await authed_client.post(
            f"{api}/goods-receipts",
            json={
                "supplier_id": supplier["id"],
                "lines": [{"product_id": product["id"], "quantity": "100", "unit_cost": "10"}],
                "post": True,
            },
        )

        response = await authed_client.post(
            f"{api}/inventory/adjust",
            json={
                "product_id": product["id"],
                "quantity_delta": "-10",
                "reason": "Breakage during handling",
            },
        )
        assert response.status_code == 201, response.text
        assert money(response.json()["balance_after"]) == D("90.0000")

        tb = await authed_client.get(f"{api}/reports/trial-balance")
        assert tb.json()["is_balanced"] is True

    async def test_zero_adjustment_rejected(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        product = (
            await authed_client.post(f"{api}/products", json={"sku": "Z-1", "name": "Item"})
        ).json()
        response = await authed_client.post(
            f"{api}/inventory/adjust",
            json={"product_id": product["id"], "quantity_delta": "0", "reason": "nothing"},
        )
        assert response.status_code == 422

    async def test_transfer_endpoint_moves_stock_between_warehouses(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        supplier = (await authed_client.post(f"{api}/suppliers", json={"name": "S"})).json()
        product = (
            await authed_client.post(f"{api}/products", json={"sku": "TR-1", "name": "Item"})
        ).json()
        await authed_client.post(
            f"{api}/goods-receipts",
            json={
                "supplier_id": supplier["id"],
                "lines": [{"product_id": product["id"], "quantity": "50", "unit_cost": "20"}],
                "post": True,
            },
        )
        warehouses = (await authed_client.get(f"{api}/inventory/warehouses")).json()
        main = next(w for w in warehouses if w["is_default"])
        second = (
            await authed_client.post(
                f"{api}/inventory/warehouses", json={"code": "WH2", "name": "Second"}
            )
        ).json()

        response = await authed_client.post(
            f"{api}/inventory/transfer",
            json={
                "product_id": product["id"],
                "from_warehouse_id": main["id"],
                "to_warehouse_id": second["id"],
                "quantity": "20",
            },
        )
        assert response.status_code == 201, response.text
        movements = response.json()
        assert len(movements) == 2
        # A transfer is not an economic event.
        assert all(m["journal_entry_id"] is None for m in movements)

        tb = await authed_client.get(f"{api}/reports/trial-balance")
        assert tb.json()["is_balanced"] is True

    async def test_movement_history_is_readable(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        supplier = (await authed_client.post(f"{api}/suppliers", json={"name": "S"})).json()
        product = (
            await authed_client.post(f"{api}/products", json={"sku": "H-1", "name": "Item"})
        ).json()
        for cost in ("10", "20"):
            await authed_client.post(
                f"{api}/goods-receipts",
                json={
                    "supplier_id": supplier["id"],
                    "lines": [{"product_id": product["id"], "quantity": "10", "unit_cost": cost}],
                    "post": True,
                },
            )

        response = await authed_client.get(
            f"{api}/inventory/movements", params={"product_id": product["id"]}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta"]["total_items"] == 2
        # The stock card carries the position after each movement.
        assert {m["balance_after"] for m in body["items"]} == {"10.0000", "20.0000"}


class TestPayablesAgeing:
    async def test_ageing_buckets_unpaid_bills(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        supplier = (await authed_client.post(f"{api}/suppliers", json={"name": "S"})).json()
        await authed_client.post(
            f"{api}/bills",
            json={
                "supplier_id": supplier["id"],
                "lines": [
                    {
                        "description": "Rent",
                        "quantity": "1",
                        "unit_price": "10000",
                        "tax_rate": "0",
                    }
                ],
                "post": True,
            },
        )
        response = await authed_client.get(f"{api}/bills/ageing")
        assert response.status_code == 200, response.text
        body = response.json()
        assert money(body["total_outstanding"]) == D("10000.0000")
        # Not yet due, so it sits in Current.
        current = next(b for b in body["buckets"] if b["label"] == "Current")
        assert money(current["amount"]) == D("10000.0000")
        assert current["bill_count"] == 1


class TestPermissions:
    async def test_unauthenticated_requests_are_rejected(
        self, client: AsyncClient, api: str
    ) -> None:
        for path in ("/suppliers", "/products", "/bills", "/inventory/levels"):
            response = await client.get(f"{api}{path}")
            assert response.status_code == 401, path

    async def test_duplicate_supplier_invoice_rejected_over_http(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        supplier = (await authed_client.post(f"{api}/suppliers", json={"name": "S"})).json()
        payload = {
            "supplier_id": supplier["id"],
            "supplier_invoice_number": "SAME-1",
            "lines": [
                {"description": "Item", "quantity": "1", "unit_price": "100", "tax_rate": "0"}
            ],
        }
        first = await authed_client.post(f"{api}/bills", json=payload)
        assert first.status_code == 201

        second = await authed_client.post(f"{api}/bills", json=payload)
        assert second.status_code == 409
        assert "already entered" in second.json()["error"]["message"]


class TestInventoryWritesFromTheUi:
    """The payloads the new inventory forms send, executed against the real endpoints.

    Worth pinning because the update schema is `extra="forbid"` and the frontend type was
    `Partial<Product>` — which let read-only fields like `sku` and `quantity_on_hand`
    type-check and then 422 at runtime, the same class of bug as the customer city field.
    """

    async def test_edits_a_product(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        created = await authed_client.post(
            f"{api}/products", json={"name": "Widget", "sale_price": "100"}
        )
        assert created.status_code == 201, created.text
        product = created.json()

        response = await authed_client.patch(
            f"{api}/products/{product['id']}",
            json={
                "name": "Widget Mk II",
                "unit": "box",
                "tax_rate": "12",
                "sale_price": "150",
                "purchase_price": "90",
                "reorder_level": "5",
                "hsn_code": "8483",
            },
        )
        assert response.status_code == 200, response.text
        updated = response.json()

        assert updated["name"] == "Widget Mk II"
        assert money(updated["sale_price"]) == D("150.0000")
        # The SKU is untouched, which is why the form does not send it.
        assert updated["sku"] == product["sku"]

    async def test_refuses_to_change_the_sku(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """A code may already be printed on a label or quoted on a bill."""
        product = (await authed_client.post(f"{api}/products", json={"name": "Widget"})).json()

        response = await authed_client.patch(
            f"{api}/products/{product['id']}", json={"sku": "HIJACKED"}
        )
        assert response.status_code == 422

    async def test_archiving_hides_it_and_is_reversible(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """There is no delete. A product on a posted bill cannot be removed without
        leaving that entry pointing at nothing, so archiving is the safe equivalent."""
        product = (await authed_client.post(f"{api}/products", json={"name": "Widget"})).json()

        archived = await authed_client.patch(
            f"{api}/products/{product['id']}", json={"is_active": False}
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["is_active"] is False

        listing = await authed_client.get(f"{api}/products")
        assert product["id"] not in [row["id"] for row in listing.json()["items"]]

        restored = await authed_client.patch(
            f"{api}/products/{product['id']}", json={"is_active": True}
        )
        assert restored.json()["is_active"] is True
        again = await authed_client.get(f"{api}/products")
        assert product["id"] in [row["id"] for row in again.json()["items"]]

    async def test_adds_a_location(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        response = await authed_client.post(
            f"{api}/inventory/warehouses",
            json={"code": "GODOWN", "name": "Back godown", "is_default": False},
        )
        assert response.status_code == 201, response.text
        assert response.json()["code"] == "GODOWN"

        listing = await authed_client.get(f"{api}/inventory/warehouses")
        assert "Back godown" in [row["name"] for row in listing.json()]

    async def test_adjusts_stock_down_with_a_reason(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """The form sends a signed delta built from a direction and a positive number."""
        product = (
            await authed_client.post(
                f"{api}/products", json={"name": "Widget", "purchase_price": "50"}
            )
        ).json()
        opening = await authed_client.post(
            f"{api}/inventory/adjust",
            json={"product_id": product["id"], "quantity_delta": "10", "reason": "Opening stock"},
        )
        # Asserted, not assumed. This call failing silently is what hid a positive
        # adjustment being posted as a write-off: the test then reported "cannot issue 2,
        # 0 on hand" and read as a problem with the second call.
        assert opening.status_code == 201, opening.text
        assert money(opening.json()["balance_after"]) == D("10.0000")
        # Valued at the purchase price, having no prior average to go on.
        assert money(opening.json()["total_cost"]) == D("500.0000")

        response = await authed_client.post(
            f"{api}/inventory/adjust",
            json={
                "product_id": product["id"],
                "quantity_delta": "-2",
                "reason": "Stock take 29 July - two damaged",
            },
        )
        assert response.status_code == 201, response.text
        assert money(response.json()["balance_after"]) == D("8.0000")

        report = (await authed_client.get(f"{api}/reports/trial-balance")).json()
        assert report["is_balanced"] is True

    async def test_a_transfer_moves_stock_without_changing_its_value(
        self, authed_client: AsyncClient, api: str, ready_books: Organization
    ) -> None:
        """Nothing was bought, sold, or lost — only the location changed."""
        product = (
            await authed_client.post(
                f"{api}/products", json={"name": "Widget", "purchase_price": "50"}
            )
        ).json()
        opening = await authed_client.post(
            f"{api}/inventory/adjust",
            json={"product_id": product["id"], "quantity_delta": "10", "reason": "Opening"},
        )
        assert opening.status_code == 201, opening.text

        warehouses = (await authed_client.get(f"{api}/inventory/warehouses")).json()
        source = warehouses[0]
        destination = (
            await authed_client.post(
                f"{api}/inventory/warehouses", json={"code": "GODOWN", "name": "Back godown"}
            )
        ).json()

        before = (await authed_client.get(f"{api}/inventory/valuation")).json()

        response = await authed_client.post(
            f"{api}/inventory/transfer",
            json={
                "product_id": product["id"],
                "from_warehouse_id": source["id"],
                "to_warehouse_id": destination["id"],
                "quantity": "4",
            },
        )
        assert response.status_code == 201, response.text
        # Two movements: out of one location, into the other.
        assert len(response.json()) == 2

        after = (await authed_client.get(f"{api}/inventory/valuation")).json()
        assert money(after["total_value"]) == money(before["total_value"])
