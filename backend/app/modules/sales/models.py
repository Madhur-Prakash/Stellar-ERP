"""Sales - customers, leads, quotations, orders, invoices, payments.

The document chain is **quotation → sales order → invoice → payment**, and each
step links back to its predecessor without requiring it. A shop that just raises
invoices never touches quotations; a business that quotes first gets full
traceability. Making the chain optional rather than mandatory is what lets one
schema serve both.

**Money is stored, not recomputed on read.** Totals are written when a document is
saved. A five-year-old invoice must show the figures the customer actually
received, and re-deriving them would silently restate history the moment a tax
rate, rounding rule, or price changes. The arithmetic lives in
:mod:`app.modules.tax.gst` and its results are persisted here.

**Posting to the ledger is a separate, explicit act.** A draft invoice has no
accounting effect. Posting it creates the journal entry - debit receivables,
credit revenue, credit tax - and stores that entry's id on the invoice. From then
on the invoice is immutable, for the same reason the entry is: it is a statutory
record, and correction means a credit note, not an edit.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, OrgScopedMixin, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import ZERO, CurrencyCode, LedgerDate, Money, Quantity, Rate, enum_column
from app.modules.tax.gst import TaxTreatment

if TYPE_CHECKING:
    from app.modules.accounting.models import JournalEntry
    from app.modules.organizations.models import Organization
    from app.modules.users.models import User


# =============================================================================
# Enumerations
# =============================================================================
class LeadStatus(StrEnum):
    """Pipeline stages. Deliberately short - a small business does not run a
    fourteen-stage enterprise funnel, and every extra stage is one more thing
    nobody updates."""

    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    PROPOSAL_SENT = "proposal_sent"
    WON = "won"
    LOST = "lost"

    @property
    def is_open(self) -> bool:
        return self not in (LeadStatus.WON, LeadStatus.LOST)


class QuotationStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    #: Turned into a sales order. Terminal, so a quote cannot be converted twice.
    CONVERTED = "converted"


class SalesOrderStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    PARTIALLY_INVOICED = "partially_invoiced"
    INVOICED = "invoiced"
    CANCELLED = "cancelled"


class InvoiceStatus(StrEnum):
    """An invoice's life.

    ``DRAFT`` has no accounting effect. Everything from ``POSTED`` onward does, and
    is therefore immutable - payment moves it between paid states, and a mistake is
    corrected with a credit note.
    """

    DRAFT = "draft"
    POSTED = "posted"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    #: Reversed in the ledger. Retained, never deleted.
    CANCELLED = "cancelled"

    @property
    def is_posted(self) -> bool:
        return self in (
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.PAID,
        )

    @property
    def is_editable(self) -> bool:
        return self is InvoiceStatus.DRAFT


class PaymentMethod(StrEnum):
    CASH = "cash"
    BANK_TRANSFER = "bank_transfer"
    CHEQUE = "cheque"
    UPI = "upi"
    CARD = "card"
    OTHER = "other"

    @property
    def is_cash(self) -> bool:
        """Whether it lands in cash rather than bank - decides which ledger
        account the payment posts against."""
        return self is PaymentMethod.CASH


# =============================================================================
# Customers
# =============================================================================
class Customer(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, SoftDeleteMixin):
    """A party you invoice.

    Soft-deleted: a customer named on a posted invoice can never truly disappear
    without orphaning the document.
    """

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False, index=True)
    legal_name: Mapped[str | None] = mapped_column(String(250))

    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    website: Mapped[str | None] = mapped_column(String(255))
    contact_person: Mapped[str | None] = mapped_column(String(200))

    # --- Tax identity ---
    gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    pan: Mapped[str | None] = mapped_column(String(10))
    #: First two digits of the GSTIN. Denormalised because it decides CGST/SGST
    #: versus IGST on every line of every invoice - deriving it per line would
    #: mean parsing the GSTIN thousands of times per report.
    state_code: Mapped[str | None] = mapped_column(String(2))
    is_tax_exempt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- Billing address ---
    billing_address_line1: Mapped[str | None] = mapped_column(String(255))
    billing_address_line2: Mapped[str | None] = mapped_column(String(255))
    billing_city: Mapped[str | None] = mapped_column(String(100))
    billing_state: Mapped[str | None] = mapped_column(String(100))
    billing_postal_code: Mapped[str | None] = mapped_column(String(20))
    billing_country: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")

    #: Null means "same as billing", which is the common case.
    shipping_address_line1: Mapped[str | None] = mapped_column(String(255))
    shipping_address_line2: Mapped[str | None] = mapped_column(String(255))
    shipping_city: Mapped[str | None] = mapped_column(String(100))
    shipping_state: Mapped[str | None] = mapped_column(String(100))
    shipping_postal_code: Mapped[str | None] = mapped_column(String(20))
    shipping_country: Mapped[str | None] = mapped_column(String(2))

    # --- Commercial terms ---
    #: Days until an invoice falls due. Drives the due date and the ageing report.
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    credit_limit: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    currency: Mapped[CurrencyCode] = mapped_column(nullable=False, default="INR")
    default_discount_percent: Mapped[Rate] = mapped_column(nullable=False, default=ZERO)

    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped[Organization] = relationship()
    invoices: Mapped[list[Invoice]] = relationship(back_populates="customer")

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_customer_org_code"),
        Index("ix_customer_org_name", "organization_id", "name"),
        CheckConstraint("payment_terms_days >= 0", name="terms_non_negative"),
        CheckConstraint("credit_limit >= 0", name="credit_limit_non_negative"),
    )

    @property
    def has_shipping_address(self) -> bool:
        return self.shipping_address_line1 is not None

    def due_date_for(self, invoice_date: dt.date) -> dt.date:
        return invoice_date + dt.timedelta(days=self.payment_terms_days)


# =============================================================================
# Leads
# =============================================================================
class Lead(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, SoftDeleteMixin):
    """A prospect, before they become a customer.

    Separate from :class:`Customer` on purpose. A lead has no billing identity, no
    credit terms, and frequently no valid GSTIN; folding the two together would
    mean every customer query filtering out half-formed records, and every
    required customer field becoming nullable.
    """

    name: Mapped[str] = mapped_column(String(250), nullable=False, index=True)
    company: Mapped[str | None] = mapped_column(String(250))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(32))

    status: Mapped[LeadStatus] = mapped_column(
        enum_column(LeadStatus, length=20), nullable=False, default=LeadStatus.NEW, index=True
    )
    source: Mapped[str | None] = mapped_column(String(100))
    #: Expected deal value, for pipeline weighting.
    estimated_value: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    expected_close_date: Mapped[LedgerDate | None] = mapped_column(default=None)

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)
    last_contacted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    #: Set when the lead is converted. Keeps the origin of a customer traceable,
    #: which is what makes "which source produced revenue?" answerable.
    converted_customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customer.id", ondelete="SET NULL")
    )
    converted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    lost_reason: Mapped[str | None] = mapped_column(String(500))

    organization: Mapped[Organization] = relationship()
    owner: Mapped[User | None] = relationship()
    converted_customer: Mapped[Customer | None] = relationship()

    __table_args__ = (
        Index("ix_lead_org_status", "organization_id", "status"),
        CheckConstraint("estimated_value >= 0", name="value_non_negative"),
    )

    @property
    def is_converted(self) -> bool:
        return self.converted_customer_id is not None


# =============================================================================
# Shared line behaviour
# =============================================================================
class SalesLineMixin:
    """Columns common to every sales document line.

    A mixin rather than one polymorphic ``document_line`` table: a shared table
    would need a nullable foreign key per document type and a discriminator, and
    every query would carry a filter that the database cannot use to enforce
    anything. Separate tables keep each foreign key non-nullable and each
    constraint real.

    Every derived figure is stored, because these are the numbers that were printed.
    """

    line_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    #: Harmonised System / Service Accounting Code, required on GST invoices.
    hsn_code: Mapped[str | None] = mapped_column(String(10))

    quantity: Mapped[Quantity] = mapped_column(nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20))
    unit_price: Mapped[Money] = mapped_column(nullable=False)

    discount_percent: Mapped[Rate] = mapped_column(nullable=False, default=ZERO)
    discount_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    tax_rate: Mapped[Rate] = mapped_column(nullable=False, default=ZERO)
    #: The tax split as computed at save time. Stored, not derived, so a rate
    #: change tomorrow cannot restate a document issued today.
    cgst_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    sgst_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    igst_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    #: quantity x unit_price, before discount.
    gross_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    #: gross - discount. The base tax was applied to.
    taxable_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    tax_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    line_total: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    @property
    def tax_split_total(self) -> Decimal:
        return self.cgst_amount + self.sgst_amount + self.igst_amount


def _line_constraints(prefix: str) -> tuple[CheckConstraint, ...]:
    """Value constraints shared by every line table.

    Generated rather than repeated so a rule cannot be tightened on invoices and
    silently left loose on quotations.
    """
    return (
        CheckConstraint("quantity > 0", name=f"{prefix}_quantity_positive"),
        CheckConstraint("unit_price >= 0", name=f"{prefix}_price_non_negative"),
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 100", name=f"{prefix}_tax_rate_range"),
        CheckConstraint("discount_amount >= 0", name=f"{prefix}_discount_non_negative"),
        # The tax split must reconstitute the stored tax total, or the printed
        # document does not add up.
        CheckConstraint(
            "cgst_amount + sgst_amount + igst_amount = tax_amount",
            name=f"{prefix}_tax_split_reconciles",
        ),
        CheckConstraint(
            "taxable_amount + tax_amount = line_total", name=f"{prefix}_total_reconciles"
        ),
    )


class SalesDocumentMixin:
    """Header totals common to quotations, orders, and invoices."""

    subtotal: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    discount_total: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    taxable_total: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    cgst_total: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    sgst_total: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    igst_total: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    tax_total: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    round_off: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    grand_total: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    currency: Mapped[CurrencyCode] = mapped_column(nullable=False, default="INR")
    tax_treatment: Mapped[TaxTreatment] = mapped_column(
        enum_column(TaxTreatment, length=20),
        nullable=False,
        default=TaxTreatment.INTRA_STATE,
    )

    notes: Mapped[str | None] = mapped_column(Text)
    terms: Mapped[str | None] = mapped_column(Text)


def _document_constraints(prefix: str) -> tuple[CheckConstraint, ...]:
    return (
        CheckConstraint("grand_total >= 0", name=f"{prefix}_total_non_negative"),
        CheckConstraint(
            "cgst_total + sgst_total + igst_total = tax_total",
            name=f"{prefix}_tax_split_reconciles",
        ),
        CheckConstraint(
            "taxable_total + tax_total + round_off = grand_total",
            name=f"{prefix}_total_reconciles",
        ),
    )


# =============================================================================
# Quotations
# =============================================================================
class Quotation(
    Base, UUIDPrimaryKeyMixin, OrgScopedMixin, SalesDocumentMixin, TimestampMixin, SoftDeleteMixin
):
    quotation_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customer.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lead.id", ondelete="SET NULL"))

    quotation_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)
    valid_until: Mapped[LedgerDate | None] = mapped_column(default=None)

    status: Mapped[QuotationStatus] = mapped_column(
        enum_column(QuotationStatus, length=20),
        nullable=False,
        default=QuotationStatus.DRAFT,
        index=True,
    )
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship()
    customer: Mapped[Customer] = relationship()
    lead: Mapped[Lead | None] = relationship()
    lines: Mapped[list[QuotationLine]] = relationship(
        back_populates="quotation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="QuotationLine.line_number",
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "quotation_number", name="uq_quotation_org_number"),
        *_document_constraints("quotation"),
    )

    def is_expired(self, on: dt.date) -> bool:
        if self.valid_until is None:
            return False
        return on > self.valid_until


class QuotationLine(Base, UUIDPrimaryKeyMixin, SalesLineMixin, TimestampMixin):
    quotation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quotation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quotation: Mapped[Quotation] = relationship(back_populates="lines")

    __table_args__ = _line_constraints("quotation_line")


# =============================================================================
# Sales orders
# =============================================================================
class SalesOrder(
    Base, UUIDPrimaryKeyMixin, OrgScopedMixin, SalesDocumentMixin, TimestampMixin, SoftDeleteMixin
):
    order_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customer.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quotation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("quotation.id", ondelete="SET NULL")
    )

    order_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)
    expected_delivery_date: Mapped[LedgerDate | None] = mapped_column(default=None)
    #: The customer's own PO number, which they will quote when paying.
    customer_reference: Mapped[str | None] = mapped_column(String(100))

    status: Mapped[SalesOrderStatus] = mapped_column(
        enum_column(SalesOrderStatus, length=25),
        nullable=False,
        default=SalesOrderStatus.DRAFT,
        index=True,
    )
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    #: How much of the order has been invoiced. Maintained as invoices are raised
    #: so partial fulfilment is visible without summing child invoices per read.
    invoiced_total: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship()
    customer: Mapped[Customer] = relationship()
    quotation: Mapped[Quotation | None] = relationship()
    lines: Mapped[list[SalesOrderLine]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SalesOrderLine.line_number",
    )
    invoices: Mapped[list[Invoice]] = relationship(back_populates="sales_order")

    __table_args__ = (
        UniqueConstraint("organization_id", "order_number", name="uq_sales_order_org_number"),
        CheckConstraint("invoiced_total >= 0", name="invoiced_non_negative"),
        *_document_constraints("sales_order"),
    )

    @property
    def uninvoiced_total(self) -> Decimal:
        return self.grand_total - self.invoiced_total


class SalesOrderLine(Base, UUIDPrimaryKeyMixin, SalesLineMixin, TimestampMixin):
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_order.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order: Mapped[SalesOrder] = relationship(back_populates="lines")

    __table_args__ = _line_constraints("sales_order_line")


# =============================================================================
# Invoices
# =============================================================================
class Invoice(
    Base, UUIDPrimaryKeyMixin, OrgScopedMixin, SalesDocumentMixin, TimestampMixin, SoftDeleteMixin
):
    """A tax invoice.

    Once posted this is a statutory document. It is never edited and never deleted:
    a mistake is corrected by cancelling (which reverses the ledger entry) or by a
    credit note.
    """

    invoice_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customer.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sales_order.id", ondelete="SET NULL")
    )

    invoice_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)
    due_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)

    status: Mapped[InvoiceStatus] = mapped_column(
        enum_column(InvoiceStatus, length=25),
        nullable=False,
        default=InvoiceStatus.DRAFT,
        index=True,
    )

    #: How much has been received. Denormalised because "what is outstanding?" is
    #: the single most-asked question of this table, and summing allocations on
    #: every read would make the ageing report quadratic.
    paid_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    # --- Ledger linkage ---
    #: The journal entry this invoice posted. Null while it is a draft.
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="RESTRICT")
    )
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    #: The reversing entry created when the invoice was cancelled.
    reversal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="RESTRICT")
    )

    #: Snapshot of the buyer's GSTIN as it was when issued. A customer who later
    #: corrects their GSTIN must not retroactively alter a filed invoice.
    customer_gstin: Mapped[str | None] = mapped_column(String(15))
    place_of_supply: Mapped[str | None] = mapped_column(String(2))

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship()
    customer: Mapped[Customer] = relationship(back_populates="invoices")
    sales_order: Mapped[SalesOrder | None] = relationship(back_populates="invoices")
    journal_entry: Mapped[JournalEntry | None] = relationship(foreign_keys=[journal_entry_id])
    reversal_entry: Mapped[JournalEntry | None] = relationship(foreign_keys=[reversal_entry_id])
    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="InvoiceLine.line_number",
    )
    allocations: Mapped[list[PaymentAllocation]] = relationship(
        back_populates="invoice", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "invoice_number", name="uq_invoice_org_number"),
        CheckConstraint("paid_amount >= 0", name="paid_non_negative"),
        # Overpayment must be refused, not silently absorbed.
        CheckConstraint("paid_amount <= grand_total", name="paid_within_total"),
        CheckConstraint("due_date >= invoice_date", name="due_after_issue"),
        # A posted invoice must carry its ledger entry, and a draft must not.
        CheckConstraint(
            "(status = 'draft' AND journal_entry_id IS NULL) "
            "OR (status <> 'draft' AND journal_entry_id IS NOT NULL)",
            name="posted_has_journal_entry",
        ),
        *_document_constraints("invoice"),
        Index("ix_invoice_org_status_due", "organization_id", "status", "due_date"),
    )

    @property
    def outstanding(self) -> Decimal:
        return self.grand_total - self.paid_amount

    @property
    def is_fully_paid(self) -> bool:
        return self.paid_amount >= self.grand_total

    def is_overdue(self, on: dt.date) -> bool:
        if not self.status.is_posted or self.is_fully_paid:
            return False
        return on > self.due_date

    def days_overdue(self, on: dt.date) -> int:
        if not self.is_overdue(on):
            return 0
        return (on - self.due_date).days


class InvoiceLine(Base, UUIDPrimaryKeyMixin, SalesLineMixin, TimestampMixin):
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoice.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Which revenue account this line credits. Lets a business split revenue by
    #: product line without a separate item master, which Stage 4 will add.
    revenue_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="RESTRICT")
    )

    invoice: Mapped[Invoice] = relationship(back_populates="lines")

    __table_args__ = _line_constraints("invoice_line")


# =============================================================================
# Payments
# =============================================================================
class Payment(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, SoftDeleteMixin):
    """Money received from a customer.

    Modelled independently of the invoice, then **allocated** to one or more of
    them. Customers pay round numbers against several invoices, pay on account
    before invoicing, and part-pay; a payment welded to a single invoice cannot
    represent any of that.
    """

    payment_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customer.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    payment_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)
    amount: Mapped[Money] = mapped_column(nullable=False)
    #: Not yet applied to an invoice - a payment on account.
    unallocated_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    method: Mapped[PaymentMethod] = mapped_column(
        enum_column(PaymentMethod, length=20),
        nullable=False,
        default=PaymentMethod.BANK_TRANSFER,
    )
    reference: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[CurrencyCode] = mapped_column(nullable=False, default="INR")
    notes: Mapped[str | None] = mapped_column(Text)

    #: Which account the money landed in - cash or a specific bank account.
    deposit_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="RESTRICT")
    )
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="RESTRICT")
    )

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship()
    customer: Mapped[Customer] = relationship()
    allocations: Mapped[list[PaymentAllocation]] = relationship(
        back_populates="payment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "payment_number", name="uq_payment_org_number"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "unallocated_amount >= 0 AND unallocated_amount <= amount",
            name="unallocated_within_amount",
        ),
    )

    @property
    def allocated_amount(self) -> Decimal:
        return self.amount - self.unallocated_amount


class PaymentAllocation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """How much of one payment settles one invoice.

    The join that makes many-to-many settlement possible: one payment across
    several invoices, and one invoice settled by several payments.
    """

    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoice.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Money] = mapped_column(nullable=False)

    payment: Mapped[Payment] = relationship(back_populates="allocations")
    invoice: Mapped[Invoice] = relationship(back_populates="allocations")

    __table_args__ = (
        # One row per (payment, invoice) pair; a larger settlement raises `amount`
        # rather than adding a second row, so the pair stays the natural key.
        UniqueConstraint("payment_id", "invoice_id", name="uq_allocation_payment_invoice"),
        CheckConstraint("amount > 0", name="allocation_amount_positive"),
    )
