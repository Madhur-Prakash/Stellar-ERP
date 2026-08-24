"""Purchasing and inventory integration tests.

The assertions that matter tie stock to the ledger. Three identities must hold at
all times, and each is checked after real operations rather than in isolation:

1. **Trial balance balances** - after every receipt, bill, payment, and sale.
2. **Stock value equals the Inventory account balance** - the physical and the
   financial view of the same goods must agree, or one of them is lying.
3. **StockLevel equals the replayed movement history** - the denormalised position
   is only safe if it provably matches the append-only log it derives from.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, ConflictError, ValidationError
from app.modules.accounting.coa_template import SystemAccount
from app.modules.accounting.reports import ReportingService
from app.modules.accounting.repository import AccountRepository
from app.modules.accounting.service import ChartOfAccountsService, FiscalCalendarService
from app.modules.organizations.models import Organization
from app.modules.purchasing.models import (
    BillStatus,
    GoodsReceiptStatus,
    ProductKind,
    PurchaseOrderStatus,
    StockLevel,
    StockMovement,
)
from app.modules.purchasing.receiving import (
    BillService,
    GoodsReceiptService,
    SupplierPaymentService,
)
from app.modules.purchasing.schemas import (
    BillCreate,
    GoodsReceiptCreate,
    GoodsReceiptLineInput,
    ProductCreate,
    PurchaseLineInput,
    PurchaseOrderCreate,
    SupplierAllocationInput,
    SupplierCreate,
    SupplierPaymentCreate,
    WarehouseCreate,
)
from app.modules.purchasing.service import (
    ProductService,
    PurchaseOrderService,
    SupplierService,
    WarehouseService,
)
from app.modules.purchasing.stock import StockService
from app.modules.users.models import User

pytestmark = pytest.mark.integration

TODAY = dt.date.today()
D = Decimal


# =============================================================================
# Fixtures
# =============================================================================
@pytest.fixture
async def books(db: AsyncSession, organization: Organization) -> Organization:
    await ChartOfAccountsService(db).seed_defaults(organization.id)
    await FiscalCalendarService(db).ensure_year_for(organization.id, fiscal_year_start_month=4)
    organization.gstin = "27AABCU9603R1ZM"  # Maharashtra
    await db.flush()
    return organization


@pytest.fixture
async def supplier(db: AsyncSession, books: Organization, user: User):
    """Same state as the buyer, so CGST + SGST."""
    return await SupplierService(db).create(
        books.id,
        SupplierCreate(name="Mumbai Wholesale", gstin="27AAAAA0000A1Z5", payment_terms_days=30),
        user,
    )


@pytest.fixture
async def product(db: AsyncSession, books: Organization, user: User):
    return await ProductService(db).create(
        books.id,
        ProductCreate(
            sku="WIDGET-1",
            name="Widget",
            barcode="8901234567890",
            unit="NOS",
            tax_rate=D("18"),
            purchase_price=D("100"),
            sale_price=D("150"),
            reorder_level=D("10"),
        ),
        user,
    )


async def total_stock_value(db: AsyncSession, organization_id: uuid.UUID) -> Decimal:
    """Sum the authoritative `stock_value`, not `quantity * average_cost`.

    The derived product cannot reconcile: an average of 8,000/150 = 53.333333…
    truncates, and 175 x 51.428571 comes back as 8,999.9999 against a ledger holding
    exactly 9,000. That mismatch is what drove making the total authoritative.
    """
    row = (
        await db.execute(
            select(func.coalesce(func.sum(StockLevel.stock_value), 0)).where(
                StockLevel.organization_id == organization_id
            )
        )
    ).scalar_one()
    return Decimal(row).quantize(D("0.0001"))


async def derived_stock_value(db: AsyncSession, organization_id: uuid.UUID) -> Decimal:
    """The naive `quantity * average_cost`, kept only to demonstrate the drift."""
    row = (
        await db.execute(
            select(func.coalesce(func.sum(StockLevel.quantity * StockLevel.average_cost), 0)).where(
                StockLevel.organization_id == organization_id
            )
        )
    ).scalar_one()
    return Decimal(row).quantize(D("0.0001"))


async def inventory_balance(db: AsyncSession, organization_id: uuid.UUID) -> Decimal:
    account = await AccountRepository(db).get_by_system_key(
        organization_id, SystemAccount.INVENTORY
    )
    assert account is not None
    balance = await ReportingService(db).accounts.balance_for(account.id, to_date=TODAY)
    return (balance.total_debit - balance.total_credit).quantize(D("0.0001"))


# =============================================================================
# Masters
# =============================================================================
class TestMasters:
    async def test_supplier_state_derived_from_gstin(self, supplier) -> None:
        assert supplier.state_code == "27"

    async def test_supplier_code_generated(self, supplier) -> None:
        assert supplier.code.startswith("SUP-")

    async def test_product_barcode_lookup(
        self, db: AsyncSession, books: Organization, product
    ) -> None:
        """The point of indexing barcode: a scan is one point lookup."""
        found = await ProductService(db).by_barcode(books.id, "8901234567890")
        assert found.id == product.id

    async def test_duplicate_barcode_refused(
        self, db: AsyncSession, books: Organization, user: User, product
    ) -> None:
        with pytest.raises(ConflictError, match="Barcode"):
            await ProductService(db).create(
                books.id,
                ProductCreate(sku="OTHER", name="Other", barcode="8901234567890"),
                user,
            )

    async def test_default_warehouse_created_on_demand(
        self, db: AsyncSession, books: Organization
    ) -> None:
        """A single-location business never has to think about warehouses."""
        warehouse = await StockService(db).default_warehouse(books.id)
        assert warehouse.is_default
        assert warehouse.code == "MAIN"
        # Idempotent.
        again = await StockService(db).default_warehouse(books.id)
        assert again.id == warehouse.id

    async def test_new_default_warehouse_demotes_the_old_one(
        self, db: AsyncSession, books: Organization, user: User
    ) -> None:
        """The partial unique index permits only one default."""
        await StockService(db).default_warehouse(books.id)
        second = await WarehouseService(db).create(
            books.id, WarehouseCreate(code="WH2", name="Second", is_default=True), user
        )
        assert second.is_default
        defaults = [w for w in await WarehouseService(db).paginate(books.id) if w.is_default]
        assert len(defaults) == 1


# =============================================================================
# Goods receipt → ledger
# =============================================================================
class TestGoodsReceipt:
    async def test_posting_moves_stock_and_accrues(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        """Debit Inventory, credit Goods Received Not Invoiced.

        No tax here: input GST is not claimable until the supplier's invoice arrives.
        """
        receipt = await GoodsReceiptService(db).create(
            books.id,
            GoodsReceiptCreate(
                supplier_id=supplier.id,
                lines=[
                    GoodsReceiptLineInput(
                        product_id=product.id, quantity=D("100"), unit_cost=D("50")
                    )
                ],
                post=True,
            ),
            user,
        )
        assert receipt.status is GoodsReceiptStatus.POSTED
        assert receipt.journal_entry_id is not None
        assert receipt.total_cost == D("5000.0000")

        level = await StockService(db).level_for(books.id, product.id)
        assert level is not None
        assert level.quantity == D("100.0000")
        assert level.average_cost == D("50.000000")

        accounts = AccountRepository(db)
        inventory = await accounts.get_by_system_key(books.id, SystemAccount.INVENTORY)
        grni = await accounts.get_by_system_key(books.id, SystemAccount.GOODS_RECEIVED_NOT_INVOICED)
        assert inventory and grni

        from app.modules.accounting.service import PostingService

        entry = await PostingService(db).get_entry(books.id, receipt.journal_entry_id)
        by_account = {line.account_id: line for line in entry.lines}
        assert by_account[inventory.id].debit == D("5000.0000")
        assert by_account[grni.id].credit == D("5000.0000")

        assert (await ReportingService(db).trial_balance(books.id, as_of=TODAY)).is_balanced

    async def test_stock_value_matches_the_inventory_account(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        """The physical and financial views of the same goods must agree."""
        for quantity, cost in [("100", "50"), ("50", "60"), ("25", "40")]:
            await GoodsReceiptService(db).create(
                books.id,
                GoodsReceiptCreate(
                    supplier_id=supplier.id,
                    lines=[
                        GoodsReceiptLineInput(
                            product_id=product.id, quantity=D(quantity), unit_cost=D(cost)
                        )
                    ],
                    post=True,
                ),
                user,
            )

        assert await total_stock_value(db, books.id) == await inventory_balance(db, books.id)

    async def test_derived_average_cost_would_not_reconcile(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        """Pins *why* total value is stored rather than derived.

        These three receipts total exactly 9,000. The average works out to
        8,000/150 = 53.333333… which truncates, so `quantity * average_cost` comes
        back as 8,999.9999 - a gap against the ledger that grows with every
        movement and cannot be explained to an auditor.

        If this test ever fails because the two now agree, the rounding assumption
        changed and the stored-total design could be revisited.
        """
        for quantity, cost in [("100", "50"), ("50", "60"), ("25", "40")]:
            await GoodsReceiptService(db).create(
                books.id,
                GoodsReceiptCreate(
                    supplier_id=supplier.id,
                    lines=[
                        GoodsReceiptLineInput(
                            product_id=product.id, quantity=D(quantity), unit_cost=D(cost)
                        )
                    ],
                    post=True,
                ),
                user,
            )

        ledger = await inventory_balance(db, books.id)
        assert await total_stock_value(db, books.id) == ledger, "stored total must reconcile"
        assert await derived_stock_value(db, books.id) != ledger, (
            "if this now reconciles, the precision assumption changed"
        )

    async def test_rejected_units_do_not_enter_stock(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        """Damaged goods are recorded for a supplier claim but were never received."""
        await GoodsReceiptService(db).create(
            books.id,
            GoodsReceiptCreate(
                supplier_id=supplier.id,
                lines=[
                    GoodsReceiptLineInput(
                        product_id=product.id,
                        quantity=D("100"),
                        unit_cost=D("50"),
                        rejected_quantity=D("10"),
                    )
                ],
                post=True,
            ),
            user,
        )
        level = await StockService(db).level_for(books.id, product.id)
        assert level is not None
        assert level.quantity == D("90.0000")
        assert await inventory_balance(db, books.id) == D("4500.0000")

    async def test_service_products_cannot_be_received(
        self, db: AsyncSession, books: Organization, user: User, supplier
    ) -> None:
        service = await ProductService(db).create(
            books.id,
            ProductCreate(sku="CONSULT", name="Consulting", kind=ProductKind.SERVICE),
            user,
        )
        with pytest.raises(BusinessRuleError, match="cannot be received into stock"):
            await GoodsReceiptService(db).create(
                books.id,
                GoodsReceiptCreate(
                    supplier_id=supplier.id,
                    lines=[
                        GoodsReceiptLineInput(
                            product_id=service.id, quantity=D("1"), unit_cost=D("5000")
                        )
                    ],
                ),
                user,
            )

    async def test_cancelling_reverses_stock_and_accrual(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        receipt = await GoodsReceiptService(db).create(
            books.id,
            GoodsReceiptCreate(
                supplier_id=supplier.id,
                lines=[
                    GoodsReceiptLineInput(
                        product_id=product.id, quantity=D("10"), unit_cost=D("100")
                    )
                ],
                post=True,
            ),
            user,
        )
        await GoodsReceiptService(db).cancel(books.id, receipt.id, reason="Wrong goods", actor=user)

        level = await StockService(db).level_for(books.id, product.id)
        assert level is not None
        assert level.quantity == 0
        assert await inventory_balance(db, books.id) == 0
        assert (await ReportingService(db).trial_balance(books.id, as_of=TODAY)).is_balanced


# =============================================================================
# Bill → ledger
# =============================================================================
class TestBills:
    async def test_bill_after_receipt_clears_the_accrual(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        """Debit GRNI + GST Input, credit Accounts Payable.

        Inventory is untouched - the goods were already capitalised at receipt.
        Debiting it again would double-count the cost.
        """
        receipt = await GoodsReceiptService(db).create(
            books.id,
            GoodsReceiptCreate(
                supplier_id=supplier.id,
                lines=[
                    GoodsReceiptLineInput(
                        product_id=product.id, quantity=D("100"), unit_cost=D("50")
                    )
                ],
                post=True,
            ),
            user,
        )
        bill = await BillService(db).create(
            books.id,
            BillCreate(
                supplier_id=supplier.id,
                goods_receipt_id=receipt.id,
                supplier_invoice_number="INV-A-1",
                lines=[
                    PurchaseLineInput(
                        product_id=product.id,
                        description="Widget",
                        quantity=D("100"),
                        unit_price=D("50"),
                        tax_rate=D("18"),
                    )
                ],
                post=True,
            ),
            user,
        )
        assert bill.status is BillStatus.POSTED
        assert bill.grand_total == D("5900.0000")  # 5000 + 18%
        assert bill.cgst_total == D("450.0000")
        assert bill.sgst_total == D("450.0000")

        accounts = AccountRepository(db)
        grni = await accounts.get_by_system_key(books.id, SystemAccount.GOODS_RECEIVED_NOT_INVOICED)
        payable = await accounts.get_by_system_key(books.id, SystemAccount.ACCOUNTS_PAYABLE)
        gst_input = await accounts.get_by_system_key(books.id, SystemAccount.GST_INPUT)
        assert grni and payable and gst_input

        reporting = ReportingService(db)
        # The accrual is fully cleared.
        grni_balance = await reporting.accounts.balance_for(grni.id, to_date=TODAY)
        assert grni_balance.total_debit - grni_balance.total_credit == 0
        # Input GST is claimable.
        gst_balance = await reporting.accounts.balance_for(gst_input.id, to_date=TODAY)
        assert gst_balance.total_debit - gst_balance.total_credit == D("900.0000")
        # Inventory still holds only the receipt value.
        assert await inventory_balance(db, books.id) == D("5000.0000")

        assert (await reporting.trial_balance(books.id, as_of=TODAY)).is_balanced

    async def test_bill_without_receipt_debits_inventory_directly(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        """Cash-and-carry: no receipt, so the cost capitalises on the bill."""
        await BillService(db).create(
            books.id,
            BillCreate(
                supplier_id=supplier.id,
                lines=[
                    PurchaseLineInput(
                        product_id=product.id,
                        description="Widget",
                        quantity=D("10"),
                        unit_price=D("100"),
                        tax_rate=D("18"),
                    )
                ],
                post=True,
            ),
            user,
        )
        assert await inventory_balance(db, books.id) == D("1000.0000")
        assert (await ReportingService(db).trial_balance(books.id, as_of=TODAY)).is_balanced

    async def test_duplicate_supplier_invoice_refused(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        """The most expensive AP error: paying the same invoice twice."""
        payload = BillCreate(
            supplier_id=supplier.id,
            supplier_invoice_number="DUP-1",
            lines=[
                PurchaseLineInput(
                    description="Widget", quantity=D("1"), unit_price=D("100"), tax_rate=D("18")
                )
            ],
        )
        await BillService(db).create(books.id, payload, user)
        with pytest.raises(ConflictError, match="already entered"):
            await BillService(db).create(books.id, payload, user)

    async def test_cancelling_a_bill_reverses_the_entry(
        self, db: AsyncSession, books: Organization, user: User, supplier
    ) -> None:
        bill = await BillService(db).create(
            books.id,
            BillCreate(
                supplier_id=supplier.id,
                lines=[
                    PurchaseLineInput(
                        description="Rent", quantity=D("1"), unit_price=D("10000"), tax_rate=D("18")
                    )
                ],
                post=True,
            ),
            user,
        )
        await BillService(db).cancel(books.id, bill.id, reason="Duplicate", actor=user)
        assert bill.status is BillStatus.CANCELLED
        assert bill.reversal_entry_id is not None
        assert (await ReportingService(db).trial_balance(books.id, as_of=TODAY)).is_balanced


# =============================================================================
# Supplier payments
# =============================================================================
class TestSupplierPayments:
    async def test_payment_clears_payables(
        self, db: AsyncSession, books: Organization, user: User, supplier
    ) -> None:
        bill = await BillService(db).create(
            books.id,
            BillCreate(
                supplier_id=supplier.id,
                lines=[
                    PurchaseLineInput(
                        description="Stock", quantity=D("1"), unit_price=D("1000"), tax_rate=D("18")
                    )
                ],
                post=True,
            ),
            user,
        )
        payment = await SupplierPaymentService(db).pay(
            books.id,
            SupplierPaymentCreate(
                supplier_id=supplier.id,
                amount=D("1180"),
                allocations=[SupplierAllocationInput(bill_id=bill.id, amount=D("1180"))],
            ),
            user,
        )
        assert payment.unallocated_amount == 0
        refreshed = await BillService(db).get(books.id, bill.id)
        assert refreshed.status is BillStatus.PAID
        assert refreshed.outstanding == 0

        payable = await AccountRepository(db).get_by_system_key(
            books.id, SystemAccount.ACCOUNTS_PAYABLE
        )
        assert payable
        balance = await ReportingService(db).accounts.balance_for(payable.id, to_date=TODAY)
        assert balance.total_credit - balance.total_debit == 0
        assert (await ReportingService(db).trial_balance(books.id, as_of=TODAY)).is_balanced

    async def test_cannot_overpay_a_bill(
        self, db: AsyncSession, books: Organization, user: User, supplier
    ) -> None:
        bill = await BillService(db).create(
            books.id,
            BillCreate(
                supplier_id=supplier.id,
                lines=[
                    PurchaseLineInput(
                        description="Stock", quantity=D("1"), unit_price=D("100"), tax_rate=D("0")
                    )
                ],
                post=True,
            ),
            user,
        )
        with pytest.raises(BusinessRuleError, match="outstanding"):
            await SupplierPaymentService(db).pay(
                books.id,
                SupplierPaymentCreate(
                    supplier_id=supplier.id,
                    amount=D("500"),
                    allocations=[SupplierAllocationInput(bill_id=bill.id, amount=D("500"))],
                ),
                user,
            )

    async def test_cannot_pay_a_draft_bill(
        self, db: AsyncSession, books: Organization, user: User, supplier
    ) -> None:
        bill = await BillService(db).create(
            books.id,
            BillCreate(
                supplier_id=supplier.id,
                lines=[
                    PurchaseLineInput(
                        description="Stock", quantity=D("1"), unit_price=D("100"), tax_rate=D("0")
                    )
                ],
            ),
            user,
        )
        with pytest.raises(BusinessRuleError, match="only a posted bill"):
            await SupplierPaymentService(db).pay(
                books.id,
                SupplierPaymentCreate(
                    supplier_id=supplier.id,
                    amount=D("100"),
                    allocations=[SupplierAllocationInput(bill_id=bill.id, amount=D("100"))],
                ),
                user,
            )


# =============================================================================
# Purchase orders
# =============================================================================
class TestPurchaseOrders:
    async def test_receipt_advances_the_order(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        order = await PurchaseOrderService(db).create(
            books.id,
            PurchaseOrderCreate(
                supplier_id=supplier.id,
                lines=[
                    PurchaseLineInput(
                        product_id=product.id,
                        description="Widget",
                        quantity=D("100"),
                        unit_price=D("50"),
                        tax_rate=D("18"),
                    )
                ],
            ),
            user,
        )
        await PurchaseOrderService(db).approve(books.id, order.id, user)

        # Partial delivery.
        await GoodsReceiptService(db).create(
            books.id,
            GoodsReceiptCreate(
                supplier_id=supplier.id,
                purchase_order_id=order.id,
                lines=[
                    GoodsReceiptLineInput(
                        product_id=product.id,
                        purchase_order_line_id=order.lines[0].id,
                        quantity=D("60"),
                        unit_cost=D("50"),
                    )
                ],
                post=True,
            ),
            user,
        )
        refreshed = await PurchaseOrderService(db).get(books.id, order.id)
        assert refreshed.status is PurchaseOrderStatus.PARTIALLY_RECEIVED
        assert refreshed.lines[0].received_quantity == D("60.0000")
        assert refreshed.lines[0].outstanding_quantity == D("40.0000")

        # The rest.
        await GoodsReceiptService(db).create(
            books.id,
            GoodsReceiptCreate(
                supplier_id=supplier.id,
                purchase_order_id=order.id,
                lines=[
                    GoodsReceiptLineInput(
                        product_id=product.id,
                        purchase_order_line_id=order.lines[0].id,
                        quantity=D("40"),
                        unit_cost=D("50"),
                    )
                ],
                post=True,
            ),
            user,
        )
        final = await PurchaseOrderService(db).get(books.id, order.id)
        assert final.status is PurchaseOrderStatus.RECEIVED

    async def test_cannot_cancel_a_received_order(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        order = await PurchaseOrderService(db).create(
            books.id,
            PurchaseOrderCreate(
                supplier_id=supplier.id,
                lines=[
                    PurchaseLineInput(
                        product_id=product.id,
                        description="Widget",
                        quantity=D("10"),
                        unit_price=D("50"),
                    )
                ],
            ),
            user,
        )
        await GoodsReceiptService(db).create(
            books.id,
            GoodsReceiptCreate(
                supplier_id=supplier.id,
                purchase_order_id=order.id,
                lines=[
                    GoodsReceiptLineInput(
                        product_id=product.id,
                        purchase_order_line_id=order.lines[0].id,
                        quantity=D("10"),
                        unit_cost=D("50"),
                    )
                ],
                post=True,
            ),
            user,
        )
        with pytest.raises(BusinessRuleError, match="Goods have been received"):
            await PurchaseOrderService(db).cancel(books.id, order.id, user)


# =============================================================================
# Stock operations
# =============================================================================
class TestStockOperations:
    async def test_stock_level_matches_replayed_movements(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        """The denormalised position must equal the append-only log.

        This is the guarantee that makes caching the level safe at all.
        """
        for quantity, cost in [("100", "50"), ("50", "70"), ("30", "45")]:
            await GoodsReceiptService(db).create(
                books.id,
                GoodsReceiptCreate(
                    supplier_id=supplier.id,
                    lines=[
                        GoodsReceiptLineInput(
                            product_id=product.id, quantity=D(quantity), unit_cost=D(cost)
                        )
                    ],
                    post=True,
                ),
                user,
            )
        await StockService(db).adjust(
            books.id,
            product=product,
            quantity_delta=D("-5"),
            movement_date=TODAY,
            reason="Breakage",
            actor=user,
        )

        replayed = (
            await db.execute(
                select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
                    StockMovement.product_id == product.id
                )
            )
        ).scalar_one()
        level = await StockService(db).level_for(books.id, product.id)
        assert level is not None
        assert level.quantity == Decimal(replayed)

    async def test_over_issue_is_refused(
        self, db: AsyncSession, books: Organization, user: User, product
    ) -> None:
        """Negative stock makes the weighted average undefined."""
        with pytest.raises(BusinessRuleError, match=r"only 0 on hand|Cannot issue"):
            await StockService(db).issue_for_sale(
                books.id,
                product=product,
                quantity=D("1"),
                movement_date=TODAY,
                actor=user,
            )

    async def test_issue_for_sale_posts_cogs(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        """Debit COGS, credit Inventory - at the weighted average, not the last price."""
        for quantity, cost in [("100", "40"), ("100", "60")]:
            await GoodsReceiptService(db).create(
                books.id,
                GoodsReceiptCreate(
                    supplier_id=supplier.id,
                    lines=[
                        GoodsReceiptLineInput(
                            product_id=product.id, quantity=D(quantity), unit_cost=D(cost)
                        )
                    ],
                    post=True,
                ),
                user,
            )
        # Average is 50.
        movement, cogs = await StockService(db).issue_for_sale(
            books.id,
            product=product,
            quantity=D("50"),
            movement_date=TODAY,
            actor=user,
            reference="INV-1",
        )
        assert cogs == D("2500.0000")  # 50 x 50, not 50 x 60
        assert movement.journal_entry_id is not None

        accounts = AccountRepository(db)
        cogs_account = await accounts.get_by_system_key(books.id, SystemAccount.COST_OF_GOODS_SOLD)
        assert cogs_account
        balance = await ReportingService(db).accounts.balance_for(cogs_account.id, to_date=TODAY)
        assert balance.total_debit - balance.total_credit == D("2500.0000")

        # Stock and ledger still agree after the issue.
        assert await total_stock_value(db, books.id) == await inventory_balance(db, books.id)
        assert (await ReportingService(db).trial_balance(books.id, as_of=TODAY)).is_balanced

    async def test_transfer_conserves_value_and_posts_nothing(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        """A transfer is not an economic event - the goods are still owned."""
        stock = StockService(db)
        main = await stock.default_warehouse(books.id)
        second = await WarehouseService(db).create(
            books.id, WarehouseCreate(code="WH2", name="Second"), user
        )

        await GoodsReceiptService(db).create(
            books.id,
            GoodsReceiptCreate(
                supplier_id=supplier.id,
                warehouse_id=main.id,
                lines=[
                    GoodsReceiptLineInput(
                        product_id=product.id, quantity=D("100"), unit_cost=D("50")
                    )
                ],
                post=True,
            ),
            user,
        )
        before = await inventory_balance(db, books.id)

        outbound, inbound = await stock.transfer(
            books.id,
            product=product,
            quantity=D("40"),
            from_warehouse_id=main.id,
            to_warehouse_id=second.id,
            movement_date=TODAY,
            actor=user,
        )
        assert outbound.journal_entry_id is None, "a transfer must not post to the ledger"
        assert inbound.journal_entry_id is None

        assert await inventory_balance(db, books.id) == before
        assert await total_stock_value(db, books.id) == before

        source_level = await stock.level_for(books.id, product.id, main.id)
        dest_level = await stock.level_for(books.id, product.id, second.id)
        assert source_level and dest_level
        assert source_level.quantity == D("60.0000")
        assert dest_level.quantity == D("40.0000")
        # Cost carried across, so value is conserved.
        assert dest_level.average_cost == D("50.000000")

    async def test_transfer_to_the_same_warehouse_is_refused(
        self, db: AsyncSession, books: Organization, user: User, product
    ) -> None:
        main = await StockService(db).default_warehouse(books.id)
        with pytest.raises(ValidationError, match="must differ"):
            await StockService(db).transfer(
                books.id,
                product=product,
                quantity=D("1"),
                from_warehouse_id=main.id,
                to_warehouse_id=main.id,
                movement_date=TODAY,
                actor=user,
            )

    async def test_adjustment_writes_off_value(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        await GoodsReceiptService(db).create(
            books.id,
            GoodsReceiptCreate(
                supplier_id=supplier.id,
                lines=[
                    GoodsReceiptLineInput(
                        product_id=product.id, quantity=D("100"), unit_cost=D("50")
                    )
                ],
                post=True,
            ),
            user,
        )
        await StockService(db).adjust(
            books.id,
            product=product,
            quantity_delta=D("-10"),
            movement_date=TODAY,
            reason="Stock take shortfall",
            actor=user,
        )
        assert await inventory_balance(db, books.id) == D("4500.0000")
        assert await total_stock_value(db, books.id) == await inventory_balance(db, books.id)
        assert (await ReportingService(db).trial_balance(books.id, as_of=TODAY)).is_balanced

    async def test_movements_record_the_position_after(
        self, db: AsyncSession, books: Organization, user: User, supplier, product
    ) -> None:
        """A stock card stays auditable after the average moves on."""
        await GoodsReceiptService(db).create(
            books.id,
            GoodsReceiptCreate(
                supplier_id=supplier.id,
                lines=[
                    GoodsReceiptLineInput(
                        product_id=product.id, quantity=D("10"), unit_cost=D("10")
                    )
                ],
                post=True,
            ),
            user,
        )
        await GoodsReceiptService(db).create(
            books.id,
            GoodsReceiptCreate(
                supplier_id=supplier.id,
                lines=[
                    GoodsReceiptLineInput(
                        product_id=product.id, quantity=D("10"), unit_cost=D("20")
                    )
                ],
                post=True,
            ),
            user,
        )
        movements = (
            (
                await db.execute(
                    select(StockMovement)
                    .where(StockMovement.product_id == product.id)
                    .order_by(StockMovement.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert [m.balance_after for m in movements] == [D("10.0000"), D("20.0000")]
        assert movements[0].average_cost_after == D("10.000000")
        assert movements[1].average_cost_after == D("15.000000")
