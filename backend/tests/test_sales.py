"""Sales integration tests.

The assertions that matter are the ones tying sales back to the ledger. An invoice
is not a record that resembles accounting - issuing one *is* accounting, and if the
posting is wrong the trial balance stops balancing. So the tests check the ledger
after each sales action, not just the sales tables.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from app.modules.accounting.coa_template import SystemAccount
from app.modules.accounting.models import EntryStatus
from app.modules.accounting.reports import ReportingService
from app.modules.accounting.repository import AccountRepository
from app.modules.accounting.service import ChartOfAccountsService, FiscalCalendarService
from app.modules.organizations.models import Organization
from app.modules.sales.invoicing import InvoiceService, PaymentService
from app.modules.sales.models import (
    InvoiceStatus,
    PaymentMethod,
    QuotationStatus,
    SalesOrderStatus,
)
from app.modules.sales.schemas import (
    AllocatePaymentRequest,
    CustomerCreate,
    InvoiceCreate,
    InvoiceUpdate,
    LeadConvert,
    LeadCreate,
    PaymentAllocationInput,
    PaymentCreate,
    QuotationCreate,
    SalesLineInput,
    SalesOrderCreate,
)
from app.modules.sales.service import (
    CustomerService,
    LeadService,
    QuotationService,
    SalesOrderService,
)
from app.modules.tax.gst import TaxTreatment
from app.modules.users.models import User

pytestmark = pytest.mark.integration

TODAY = dt.date.today()
D = Decimal


# =============================================================================
# Fixtures
# =============================================================================
@pytest.fixture
async def books(db: AsyncSession, organization: Organization) -> Organization:
    """Chart of accounts, journals, and the current fiscal year."""
    await ChartOfAccountsService(db).seed_defaults(organization.id)
    await FiscalCalendarService(db).ensure_year_for(organization.id, fiscal_year_start_month=4)
    # The seller's own GSTIN decides intra- vs inter-state on every invoice.
    organization.gstin = "27AABCU9603R1ZM"  # Maharashtra
    await db.flush()
    return organization


@pytest.fixture
async def customer(db: AsyncSession, books: Organization, user: User):
    """A Maharashtra customer - same state as the seller, so CGST + SGST."""
    return await CustomerService(db).create(
        books.id,
        CustomerCreate(
            name="Kirana Retail",
            gstin="27AAAAA0000A1Z5",
            payment_terms_days=30,
            billing_city="Mumbai",
        ),
        user,
    )


@pytest.fixture
async def outstate_customer(db: AsyncSession, books: Organization, user: User):
    """A Karnataka customer - different state, so IGST."""
    return await CustomerService(db).create(
        books.id,
        CustomerCreate(name="Bengaluru Traders", gstin="29AAAAA0000A1Z5"),
        user,
    )


def line(amount: str, rate: str = "18", qty: str = "1") -> SalesLineInput:
    return SalesLineInput(
        description="Widget", quantity=D(qty), unit_price=D(amount), tax_rate=D(rate)
    )


# =============================================================================
# Customers and leads
# =============================================================================
class TestCustomers:
    async def test_state_code_derived_from_gstin(self, customer) -> None:
        """The GSTIN's first two digits decide the tax split on every invoice."""
        assert customer.state_code == "27"

    async def test_code_generated_when_absent(self, customer) -> None:
        assert customer.code.startswith("CUST-")

    async def test_duplicate_code_refused(
        self, db: AsyncSession, books: Organization, user: User
    ) -> None:
        await CustomerService(db).create(
            books.id, CustomerCreate(code="ACME", name="Acme One"), user
        )
        with pytest.raises(ConflictError, match="already in use"):
            await CustomerService(db).create(
                books.id, CustomerCreate(code="ACME", name="Acme Two"), user
            )

    async def test_due_date_uses_payment_terms(self, customer) -> None:
        assert customer.due_date_for(TODAY) == TODAY + dt.timedelta(days=30)

    async def test_cannot_delete_a_customer_with_invoices(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")]),
            user,
        )
        with pytest.raises(BusinessRuleError, match="cannot be deleted"):
            await CustomerService(db).delete(books.id, customer.id, user)

    async def test_cross_tenant_customer_is_not_found(
        self, db: AsyncSession, books: Organization, user: User
    ) -> None:
        other = Organization(name="Rival", slug=f"rival-{uuid.uuid4().hex[:6]}")
        db.add(other)
        await db.flush()
        foreign = await CustomerService(db).create(
            other.id, CustomerCreate(name="Their Customer"), user
        )
        with pytest.raises(NotFoundError):
            await CustomerService(db).get(books.id, foreign.id)


class TestLeads:
    async def test_convert_creates_a_customer_and_links_back(
        self, db: AsyncSession, books: Organization, user: User
    ) -> None:
        """The lead is kept, so the revenue source stays traceable."""
        lead = await LeadService(db).create(
            books.id,
            LeadCreate(name="Jhon Doe", company="Sharma Textiles", source="referral"),
            user,
        )
        customer = await LeadService(db).convert(books.id, lead.id, LeadConvert(), user)

        assert customer.name == "Sharma Textiles"  # company preferred over contact name
        assert lead.converted_customer_id == customer.id
        assert lead.status == "won"

    async def test_cannot_convert_twice(
        self, db: AsyncSession, books: Organization, user: User
    ) -> None:
        lead = await LeadService(db).create(books.id, LeadCreate(name="Once Only"), user)
        await LeadService(db).convert(books.id, lead.id, LeadConvert(), user)
        with pytest.raises(ConflictError, match="already been converted"):
            await LeadService(db).convert(books.id, lead.id, LeadConvert(), user)


# =============================================================================
# Tax treatment resolution against real parties
# =============================================================================
class TestTaxTreatment:
    async def test_same_state_splits_into_cgst_and_sgst(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")]),
            user,
        )
        assert invoice.tax_treatment == TaxTreatment.INTRA_STATE
        assert invoice.cgst_total == D("90.0000")
        assert invoice.sgst_total == D("90.0000")
        assert invoice.igst_total == 0
        assert invoice.grand_total == D("1180.0000")

    async def test_different_state_is_all_igst(
        self, db: AsyncSession, books: Organization, user: User, outstate_customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=outstate_customer.id, lines=[line("1000")]),
            user,
        )
        assert invoice.tax_treatment == TaxTreatment.INTER_STATE
        assert invoice.igst_total == D("180.0000")
        assert invoice.cgst_total == 0
        assert invoice.grand_total == D("1180.0000")

    async def test_client_cannot_dictate_totals(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        """The line schema has no total field, so a caller cannot invoice ₹1000
        and book ₹1 of revenue."""
        assert "line_total" not in SalesLineInput.model_fields
        assert "taxable_amount" not in SalesLineInput.model_fields
        assert "tax_amount" not in SalesLineInput.model_fields


# =============================================================================
# The document chain
# =============================================================================
class TestDocumentChain:
    async def test_quotation_to_order_carries_the_figures(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        quotation = await QuotationService(db).create(
            books.id,
            QuotationCreate(customer_id=customer.id, lines=[line("5000"), line("2500", "12")]),
            user,
        )
        order = await SalesOrderService(db).from_quotation(books.id, quotation.id, user)

        assert order.grand_total == quotation.grand_total
        assert order.tax_total == quotation.tax_total
        assert len(order.lines) == len(quotation.lines)
        assert order.quotation_id == quotation.id
        assert quotation.status is QuotationStatus.CONVERTED

    async def test_a_quotation_cannot_be_converted_twice(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        quotation = await QuotationService(db).create(
            books.id, QuotationCreate(customer_id=customer.id, lines=[line("1000")]), user
        )
        await SalesOrderService(db).from_quotation(books.id, quotation.id, user)
        with pytest.raises(ConflictError, match="already been converted"):
            await SalesOrderService(db).from_quotation(books.id, quotation.id, user)

    async def test_rejected_quotation_cannot_become_an_order(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        quotation = await QuotationService(db).create(
            books.id, QuotationCreate(customer_id=customer.id, lines=[line("1000")]), user
        )
        await QuotationService(db).respond(books.id, quotation.id, accepted=False, actor=user)
        with pytest.raises(BusinessRuleError, match="rejected quotation"):
            await SalesOrderService(db).from_quotation(books.id, quotation.id, user)

    async def test_invoicing_an_order_advances_its_status(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        order = await SalesOrderService(db).create(
            books.id,
            SalesOrderCreate(customer_id=customer.id, lines=[line("1000")]),
            user,
        )
        await SalesOrderService(db).confirm(books.id, order.id, user)

        await InvoiceService(db).create(
            books.id,
            InvoiceCreate(
                customer_id=customer.id, sales_order_id=order.id, lines=[line("1000")], post=True
            ),
            user,
        )
        refreshed = await SalesOrderService(db).get(books.id, order.id)
        assert refreshed.status is SalesOrderStatus.INVOICED
        assert refreshed.invoiced_total == D("1180.0000")
        assert refreshed.uninvoiced_total == 0

    async def test_cannot_cancel_an_invoiced_order(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        order = await SalesOrderService(db).create(
            books.id, SalesOrderCreate(customer_id=customer.id, lines=[line("1000")]), user
        )
        await InvoiceService(db).create(
            books.id,
            InvoiceCreate(
                customer_id=customer.id, sales_order_id=order.id, lines=[line("1000")], post=True
            ),
            user,
        )
        with pytest.raises(BusinessRuleError, match="has been invoiced"):
            await SalesOrderService(db).cancel(books.id, order.id, user)


# =============================================================================
# Invoice posting - the ledger boundary
# =============================================================================
class TestInvoicePosting:
    async def test_posting_produces_correct_double_entry(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        """Debit receivables 1180; credit revenue 1000, CGST 90, SGST 90."""
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")], post=True),
            user,
        )
        assert invoice.status is InvoiceStatus.POSTED
        assert invoice.journal_entry_id is not None

        from app.modules.accounting.service import PostingService

        entry = await PostingService(db).get_entry(books.id, invoice.journal_entry_id)
        assert entry.status is EntryStatus.POSTED
        assert entry.total_debit == entry.total_credit == D("1180.0000")
        assert entry.source_type == "invoice"
        assert entry.source_id == invoice.id

        accounts = AccountRepository(db)
        receivable = await accounts.get_by_system_key(books.id, SystemAccount.ACCOUNTS_RECEIVABLE)
        revenue = await accounts.get_by_system_key(books.id, SystemAccount.SALES_REVENUE)
        gst = await accounts.get_by_system_key(books.id, SystemAccount.GST_OUTPUT)
        assert receivable and revenue and gst

        by_account = {line.account_id: line for line in entry.lines}
        assert by_account[receivable.id].debit == D("1180.0000")
        assert by_account[revenue.id].credit == D("1000.0000")
        # CGST and SGST are separate lines against the same account, summing to 180.
        gst_lines = [line for line in entry.lines if line.account_id == gst.id]
        assert sum(line.credit for line in gst_lines) == D("180.0000")

    async def test_trial_balance_still_balances_after_invoicing(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        """The property that must survive every sales action."""
        for amount in ("1000", "2500.55", "99.99"):
            await InvoiceService(db).create(
                books.id,
                InvoiceCreate(customer_id=customer.id, lines=[line(amount)], post=True),
                user,
            )
        tb = await ReportingService(db).trial_balance(books.id, as_of=TODAY)
        assert tb.is_balanced, f"{tb.total_debit} != {tb.total_credit}"

    async def test_rounded_invoice_still_balances(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        """Rounding to the whole rupee needs its own ledger line, or the entry
        cannot balance and the post fails outright."""
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(
                customer_id=customer.id,
                lines=[line("333.33", "18", "3")],
                round_to_whole=True,
                post=True,
            ),
            user,
        )
        assert invoice.round_off != 0
        assert invoice.grand_total == invoice.grand_total.to_integral_value()

        tb = await ReportingService(db).trial_balance(books.id, as_of=TODAY)
        assert tb.is_balanced

    async def test_draft_has_no_ledger_effect(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        await InvoiceService(db).create(
            books.id, InvoiceCreate(customer_id=customer.id, lines=[line("1000")]), user
        )
        tb = await ReportingService(db).trial_balance(books.id, as_of=TODAY)
        assert tb.total_debit == 0

    async def test_posted_invoice_cannot_be_edited(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")], post=True),
            user,
        )
        with pytest.raises(BusinessRuleError, match="statutory record"):
            await InvoiceService(db).update(
                books.id, invoice.id, InvoiceUpdate(notes="tampering"), user
            )

    async def test_cannot_post_twice(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")], post=True),
            user,
        )
        with pytest.raises(ConflictError, match="already posted"):
            await InvoiceService(db).post(books.id, invoice.id, user)

    async def test_revenue_is_grouped_by_account(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        """A five-line invoice against one revenue account makes one credit line -
        the ledger records the accounting effect, not the invoice layout."""
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(
                customer_id=customer.id,
                lines=[line("100"), line("200"), line("300"), line("400"), line("500")],
                post=True,
            ),
            user,
        )
        from app.modules.accounting.service import PostingService

        assert invoice.journal_entry_id
        entry = await PostingService(db).get_entry(books.id, invoice.journal_entry_id)
        revenue = await AccountRepository(db).get_by_system_key(
            books.id, SystemAccount.SALES_REVENUE
        )
        assert revenue
        revenue_lines = [line for line in entry.lines if line.account_id == revenue.id]
        assert len(revenue_lines) == 1
        assert revenue_lines[0].credit == D("1500.0000")


class TestInvoiceCancellation:
    async def test_cancelling_reverses_the_ledger_entry(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")], post=True),
            user,
        )
        await InvoiceService(db).cancel(books.id, invoice.id, reason="Duplicate", actor=user)

        assert invoice.status is InvoiceStatus.CANCELLED
        assert invoice.reversal_entry_id is not None

        # Net ledger effect is nil, so the books are back where they started.
        tb = await ReportingService(db).trial_balance(books.id, as_of=TODAY)
        assert tb.is_balanced
        assert tb.total_debit == 0

    async def test_cannot_cancel_an_invoice_with_payments(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")], post=True),
            user,
        )
        await PaymentService(db).record(
            books.id,
            PaymentCreate(
                customer_id=customer.id,
                amount=D("500"),
                allocations=[PaymentAllocationInput(invoice_id=invoice.id, amount=D("500"))],
            ),
            user,
        )
        with pytest.raises(BusinessRuleError, match="has payments"):
            await InvoiceService(db).cancel(books.id, invoice.id, reason="oops", actor=user)

    async def test_posted_invoice_cannot_be_deleted(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")], post=True),
            user,
        )
        with pytest.raises(BusinessRuleError, match="Only drafts"):
            await InvoiceService(db).delete_draft(books.id, invoice.id, user)


# =============================================================================
# Payments
# =============================================================================
class TestPayments:
    async def test_receipt_posts_bank_and_clears_receivables(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")], post=True),
            user,
        )
        payment = await PaymentService(db).record(
            books.id,
            PaymentCreate(
                customer_id=customer.id,
                amount=D("1180"),
                method=PaymentMethod.BANK_TRANSFER,
                allocations=[PaymentAllocationInput(invoice_id=invoice.id, amount=D("1180"))],
            ),
            user,
        )
        assert payment.journal_entry_id is not None
        assert payment.unallocated_amount == 0

        refreshed = await InvoiceService(db).get(books.id, invoice.id)
        assert refreshed.status is InvoiceStatus.PAID
        assert refreshed.outstanding == 0

        # Receivables net to zero; cash is up by the receipt.
        accounts = AccountRepository(db)
        receivable = await accounts.get_by_system_key(books.id, SystemAccount.ACCOUNTS_RECEIVABLE)
        bank = await accounts.get_by_system_key(books.id, SystemAccount.BANK)
        assert receivable and bank
        reporting = ReportingService(db)
        ar = await reporting.accounts.balance_for(receivable.id, to_date=TODAY)
        bank_balance = await reporting.accounts.balance_for(bank.id, to_date=TODAY)
        assert ar.total_debit - ar.total_credit == 0
        assert bank_balance.total_debit - bank_balance.total_credit == D("1180.0000")

        tb = await reporting.trial_balance(books.id, as_of=TODAY)
        assert tb.is_balanced

    async def test_partial_payment_marks_partially_paid(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")], post=True),
            user,
        )
        await PaymentService(db).record(
            books.id,
            PaymentCreate(
                customer_id=customer.id,
                amount=D("500"),
                allocations=[PaymentAllocationInput(invoice_id=invoice.id, amount=D("500"))],
            ),
            user,
        )
        refreshed = await InvoiceService(db).get(books.id, invoice.id)
        assert refreshed.status is InvoiceStatus.PARTIALLY_PAID
        assert refreshed.outstanding == D("680.0000")

    async def test_payment_on_account_stays_unallocated(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        """Customers pay before being invoiced; that must be representable."""
        payment = await PaymentService(db).record(
            books.id,
            PaymentCreate(customer_id=customer.id, amount=D("5000")),
            user,
        )
        assert payment.unallocated_amount == D("5000.0000")
        assert payment.allocated_amount == 0
        # Still posted, so the cash is on the books.
        assert payment.journal_entry_id is not None

    async def test_cannot_over_allocate_a_payment(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")], post=True),
            user,
        )
        payment = await PaymentService(db).record(
            books.id, PaymentCreate(customer_id=customer.id, amount=D("100")), user
        )
        with pytest.raises(BusinessRuleError, match="unallocated"):
            await PaymentService(db).allocate(
                books.id,
                payment.id,
                AllocatePaymentRequest(
                    allocations=[PaymentAllocationInput(invoice_id=invoice.id, amount=D("500"))]
                ),
                user,
            )

    async def test_cannot_allocate_more_than_an_invoice_owes(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("100")], post=True),
            user,
        )
        payment = await PaymentService(db).record(
            books.id, PaymentCreate(customer_id=customer.id, amount=D("5000")), user
        )
        with pytest.raises(BusinessRuleError, match="outstanding"):
            await PaymentService(db).allocate(
                books.id,
                payment.id,
                AllocatePaymentRequest(
                    allocations=[PaymentAllocationInput(invoice_id=invoice.id, amount=D("5000"))]
                ),
                user,
            )

    async def test_cannot_allocate_to_another_customers_invoice(
        self, db: AsyncSession, books: Organization, user: User, customer, outstate_customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=outstate_customer.id, lines=[line("1000")], post=True),
            user,
        )
        payment = await PaymentService(db).record(
            books.id, PaymentCreate(customer_id=customer.id, amount=D("1180")), user
        )
        with pytest.raises(ValidationError, match="different customer"):
            await PaymentService(db).allocate(
                books.id,
                payment.id,
                AllocatePaymentRequest(
                    allocations=[PaymentAllocationInput(invoice_id=invoice.id, amount=D("1180"))]
                ),
                user,
            )

    async def test_cannot_pay_a_draft_invoice(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        invoice = await InvoiceService(db).create(
            books.id, InvoiceCreate(customer_id=customer.id, lines=[line("1000")]), user
        )
        payment = await PaymentService(db).record(
            books.id, PaymentCreate(customer_id=customer.id, amount=D("1180")), user
        )
        with pytest.raises(BusinessRuleError, match="only a"):
            await PaymentService(db).allocate(
                books.id,
                payment.id,
                AllocatePaymentRequest(
                    allocations=[PaymentAllocationInput(invoice_id=invoice.id, amount=D("1180"))]
                ),
                user,
            )

    async def test_auto_allocate_applies_oldest_first(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        """The convention every AR clerk uses."""
        old = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(
                customer_id=customer.id,
                invoice_date=TODAY - dt.timedelta(days=60),
                lines=[line("1000")],
                post=True,
            ),
            user,
        )
        new = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")], post=True),
            user,
        )
        payment = await PaymentService(db).record(
            books.id, PaymentCreate(customer_id=customer.id, amount=D("1180")), user
        )
        await PaymentService(db).auto_allocate(books.id, payment.id, user)

        assert (await InvoiceService(db).get(books.id, old.id)).status is InvoiceStatus.PAID
        assert (await InvoiceService(db).get(books.id, new.id)).outstanding == D("1180.0000")

    async def test_one_payment_across_several_invoices(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        first = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("1000")], post=True),
            user,
        )
        second = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("2000")], post=True),
            user,
        )
        payment = await PaymentService(db).record(
            books.id,
            PaymentCreate(
                customer_id=customer.id,
                amount=D("3540"),
                allocations=[
                    PaymentAllocationInput(invoice_id=first.id, amount=D("1180")),
                    PaymentAllocationInput(invoice_id=second.id, amount=D("2360")),
                ],
            ),
            user,
        )
        assert payment.unallocated_amount == 0

        # Re-fetched: `record()` returns the constructed row, whose `allocations`
        # relationship is unloaded. `get()` eager-loads it, which is what the
        # router does too.
        loaded = await PaymentService(db).get(books.id, payment.id)
        assert len(loaded.allocations) == 2
        assert {a.amount for a in loaded.allocations} == {D("1180.0000"), D("2360.0000")}

        assert (await InvoiceService(db).get(books.id, first.id)).status is InvoiceStatus.PAID
        assert (await InvoiceService(db).get(books.id, second.id)).status is InvoiceStatus.PAID

    async def test_cash_payment_hits_the_cash_account(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        payment = await PaymentService(db).record(
            books.id,
            PaymentCreate(customer_id=customer.id, amount=D("500"), method=PaymentMethod.CASH),
            user,
        )
        cash = await AccountRepository(db).get_by_system_key(books.id, SystemAccount.CASH)
        assert cash
        assert payment.deposit_account_id == cash.id


# =============================================================================
# Receivables ageing
# =============================================================================
class TestAgeing:
    async def test_buckets_by_days_overdue(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        from app.modules.sales.repository import InvoiceRepository

        # Due 45 days ago -> the 31-60 bucket.
        await InvoiceService(db).create(
            books.id,
            InvoiceCreate(
                customer_id=customer.id,
                invoice_date=TODAY - dt.timedelta(days=75),
                due_date=TODAY - dt.timedelta(days=45),
                lines=[line("1000")],
                post=True,
            ),
            user,
        )
        # Not yet due -> Current.
        await InvoiceService(db).create(
            books.id,
            InvoiceCreate(customer_id=customer.id, lines=[line("2000")], post=True),
            user,
        )

        rows = await InvoiceRepository(db).ageing(books.id, as_of=TODAY)
        by_label = {label: (amount, count) for label, amount, count in rows}

        assert by_label["31-60 days"] == (D("1180.0000"), 1)
        assert by_label["Current"] == (D("2360.0000"), 1)
        assert by_label["90+ days"][1] == 0

    async def test_paid_invoices_are_excluded(
        self, db: AsyncSession, books: Organization, user: User, customer
    ) -> None:
        from app.modules.sales.repository import InvoiceRepository

        invoice = await InvoiceService(db).create(
            books.id,
            InvoiceCreate(
                customer_id=customer.id,
                invoice_date=TODAY - dt.timedelta(days=60),
                due_date=TODAY - dt.timedelta(days=30),
                lines=[line("1000")],
                post=True,
            ),
            user,
        )
        await PaymentService(db).record(
            books.id,
            PaymentCreate(
                customer_id=customer.id,
                amount=D("1180"),
                allocations=[PaymentAllocationInput(invoice_id=invoice.id, amount=D("1180"))],
            ),
            user,
        )
        rows = await InvoiceRepository(db).ageing(books.id, as_of=TODAY)
        assert sum(amount for _, amount, _ in rows) == 0
