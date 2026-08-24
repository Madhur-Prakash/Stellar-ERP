"""Sales API contracts.

Money crosses the wire as a string, as everywhere else - a JSON number is a
double in every JavaScript client.

**Line inputs carry no computed fields.** A client sends quantity, price, discount,
and tax rate; the server computes the taxable base, the CGST/SGST/IGST split, and
the totals. Accepting a client-supplied total would let a caller invoice ₹100 and
book ₹1 of revenue, and no amount of validation short of recomputing it makes that
safe - so it is simply never accepted.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from app.core.schemas import BaseSchema, Email, ResponseSchema, TimestampedSchema
from app.modules.sales.models import (
    InvoiceStatus,
    LeadStatus,
    PaymentMethod,
    QuotationStatus,
    SalesOrderStatus,
)
from app.modules.tax.gst import TaxTreatment

# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------
DocCode = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30, to_upper=True)
]
PartyName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)]
LineText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]

#: 15 characters, the statutory GSTIN format. Validated by shape, not checksum -
#: the check digit algorithm is worth adding but a malformed length is the error
#: people actually make.
Gstin = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_upper=True,
        min_length=15,
        max_length=15,
        pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[0-9A-Z]{1}[Z]{1}[0-9A-Z]{1}$",
    ),
]

Amount = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]
PositiveAmount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
Qty = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
Percent = Annotated[Decimal, Field(ge=0, le=100, max_digits=9, decimal_places=4)]


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
class CustomerCreate(BaseSchema):
    code: DocCode | None = None
    name: PartyName
    legal_name: str | None = Field(default=None, max_length=250)

    email: Email | None = None
    phone: str | None = Field(default=None, max_length=32)
    website: str | None = Field(default=None, max_length=255)
    contact_person: str | None = Field(default=None, max_length=200)

    gstin: Gstin | None = None
    pan: str | None = Field(default=None, max_length=10)
    is_tax_exempt: bool = False

    billing_address_line1: str | None = Field(default=None, max_length=255)
    billing_address_line2: str | None = Field(default=None, max_length=255)
    billing_city: str | None = Field(default=None, max_length=100)
    billing_state: str | None = Field(default=None, max_length=100)
    billing_postal_code: str | None = Field(default=None, max_length=20)
    billing_country: Annotated[
        str, StringConstraints(to_upper=True, min_length=2, max_length=2)
    ] = "IN"

    shipping_address_line1: str | None = Field(default=None, max_length=255)
    shipping_address_line2: str | None = Field(default=None, max_length=255)
    shipping_city: str | None = Field(default=None, max_length=100)
    shipping_state: str | None = Field(default=None, max_length=100)
    shipping_postal_code: str | None = Field(default=None, max_length=20)
    shipping_country: str | None = Field(default=None, max_length=2)

    payment_terms_days: int = Field(default=30, ge=0, le=365)
    credit_limit: Amount = Decimal("0")
    default_discount_percent: Percent = Decimal("0")
    notes: str | None = None


class CustomerUpdate(BaseSchema):
    name: PartyName | None = None
    legal_name: str | None = Field(default=None, max_length=250)
    email: Email | None = None
    phone: str | None = Field(default=None, max_length=32)
    contact_person: str | None = Field(default=None, max_length=200)
    gstin: Gstin | None = None
    pan: str | None = Field(default=None, max_length=10)
    is_tax_exempt: bool | None = None
    billing_address_line1: str | None = Field(default=None, max_length=255)
    billing_city: str | None = Field(default=None, max_length=100)
    billing_state: str | None = Field(default=None, max_length=100)
    billing_postal_code: str | None = Field(default=None, max_length=20)
    billing_country: str | None = Field(default=None, max_length=2)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    credit_limit: Amount | None = None
    default_discount_percent: Percent | None = None
    notes: str | None = None
    is_active: bool | None = None


class CustomerRead(TimestampedSchema):
    id: uuid.UUID
    code: str
    name: str
    legal_name: str | None
    email: str | None
    phone: str | None
    contact_person: str | None
    gstin: str | None
    state_code: str | None
    is_tax_exempt: bool
    billing_city: str | None
    billing_state: str | None
    billing_country: str
    payment_terms_days: int
    credit_limit: Decimal
    default_discount_percent: Decimal
    currency: str
    is_active: bool
    notes: str | None


class CustomerStatement(ResponseSchema):
    """Outstanding position for one customer."""

    customer: CustomerRead
    invoice_count: int
    total_invoiced: Decimal
    total_paid: Decimal
    total_outstanding: Decimal
    overdue_amount: Decimal
    credit_limit: Decimal
    #: Negative when the customer is over their limit.
    credit_available: Decimal


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------
class LeadCreate(BaseSchema):
    name: PartyName
    company: str | None = Field(default=None, max_length=250)
    email: Email | None = None
    phone: str | None = Field(default=None, max_length=32)
    source: str | None = Field(default=None, max_length=100)
    estimated_value: Amount = Decimal("0")
    expected_close_date: dt.date | None = None
    owner_id: uuid.UUID | None = None
    notes: str | None = None


class LeadUpdate(BaseSchema):
    name: PartyName | None = None
    company: str | None = Field(default=None, max_length=250)
    email: Email | None = None
    phone: str | None = Field(default=None, max_length=32)
    status: LeadStatus | None = None
    source: str | None = Field(default=None, max_length=100)
    estimated_value: Amount | None = None
    expected_close_date: dt.date | None = None
    owner_id: uuid.UUID | None = None
    notes: str | None = None
    lost_reason: str | None = Field(default=None, max_length=500)


class LeadRead(TimestampedSchema):
    id: uuid.UUID
    name: str
    company: str | None
    email: str | None
    phone: str | None
    status: LeadStatus
    source: str | None
    estimated_value: Decimal
    expected_close_date: dt.date | None
    owner_id: uuid.UUID | None
    notes: str | None
    converted_customer_id: uuid.UUID | None
    converted_at: dt.datetime | None
    lost_reason: str | None
    last_contacted_at: dt.datetime | None


class LeadConvert(BaseSchema):
    """Turn a lead into a customer.

    Fields left unset are copied from the lead, so the common case is an empty body.
    """

    code: DocCode | None = None
    name: PartyName | None = None
    gstin: Gstin | None = None
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)


# ---------------------------------------------------------------------------
# Document lines
# ---------------------------------------------------------------------------
class SalesLineInput(BaseSchema):
    """One line as submitted by a client.

    Note what is absent: no taxable amount, no tax split, no line total. Those are
    computed server-side - see the module docstring.
    """

    description: LineText
    hsn_code: str | None = Field(default=None, max_length=10)
    quantity: Qty
    unit: str | None = Field(default=None, max_length=20)
    unit_price: Amount
    discount_percent: Percent = Decimal("0")
    discount_amount: Amount | None = None
    tax_rate: Percent = Decimal("0")
    revenue_account_id: uuid.UUID | None = None


class SalesLineRead(ResponseSchema):
    id: uuid.UUID
    line_number: int
    description: str
    hsn_code: str | None
    quantity: Decimal
    unit: str | None
    unit_price: Decimal
    discount_percent: Decimal
    discount_amount: Decimal
    tax_rate: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    gross_amount: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal
    line_total: Decimal


class DocumentTotalsRead(ResponseSchema):
    """The header totals every sales document shares."""

    subtotal: Decimal
    discount_total: Decimal
    taxable_total: Decimal
    cgst_total: Decimal
    sgst_total: Decimal
    igst_total: Decimal
    tax_total: Decimal
    round_off: Decimal
    grand_total: Decimal
    currency: str
    tax_treatment: TaxTreatment


# ---------------------------------------------------------------------------
# Quotations
# ---------------------------------------------------------------------------
class QuotationCreate(BaseSchema):
    customer_id: uuid.UUID
    lead_id: uuid.UUID | None = None
    quotation_date: dt.date | None = None
    valid_until: dt.date | None = None
    lines: list[SalesLineInput] = Field(min_length=1)
    notes: str | None = None
    terms: str | None = None
    round_to_whole: bool = False

    @model_validator(mode="after")
    def _validity_after_issue(self) -> Self:
        if self.valid_until and self.quotation_date and self.valid_until < self.quotation_date:
            raise ValueError("valid_until cannot be before quotation_date")
        return self


class QuotationUpdate(BaseSchema):
    quotation_date: dt.date | None = None
    valid_until: dt.date | None = None
    lines: list[SalesLineInput] | None = Field(default=None, min_length=1)
    notes: str | None = None
    terms: str | None = None
    round_to_whole: bool | None = None


class QuotationRead(TimestampedSchema, DocumentTotalsRead):
    id: uuid.UUID
    quotation_number: str
    customer_id: uuid.UUID
    customer_name: str
    lead_id: uuid.UUID | None
    quotation_date: dt.date
    valid_until: dt.date | None
    status: QuotationStatus
    sent_at: dt.datetime | None
    responded_at: dt.datetime | None
    notes: str | None
    terms: str | None
    is_expired: bool
    lines: list[SalesLineRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Sales orders
# ---------------------------------------------------------------------------
class SalesOrderCreate(BaseSchema):
    customer_id: uuid.UUID
    quotation_id: uuid.UUID | None = None
    order_date: dt.date | None = None
    expected_delivery_date: dt.date | None = None
    customer_reference: str | None = Field(default=None, max_length=100)
    lines: list[SalesLineInput] = Field(min_length=1)
    notes: str | None = None
    terms: str | None = None
    round_to_whole: bool = False


class SalesOrderRead(TimestampedSchema, DocumentTotalsRead):
    id: uuid.UUID
    order_number: str
    customer_id: uuid.UUID
    customer_name: str
    quotation_id: uuid.UUID | None
    order_date: dt.date
    expected_delivery_date: dt.date | None
    customer_reference: str | None
    status: SalesOrderStatus
    confirmed_at: dt.datetime | None
    cancelled_at: dt.datetime | None
    invoiced_total: Decimal
    uninvoiced_total: Decimal
    notes: str | None
    terms: str | None
    lines: list[SalesLineRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
class InvoiceCreate(BaseSchema):
    customer_id: uuid.UUID
    sales_order_id: uuid.UUID | None = None
    invoice_date: dt.date | None = None
    #: Defaults to `invoice_date + customer.payment_terms_days`.
    due_date: dt.date | None = None
    lines: list[SalesLineInput] = Field(min_length=1)
    notes: str | None = None
    terms: str | None = None
    round_to_whole: bool = False
    #: Post to the ledger immediately instead of saving a draft.
    post: bool = False

    @model_validator(mode="after")
    def _due_after_issue(self) -> Self:
        if self.due_date and self.invoice_date and self.due_date < self.invoice_date:
            raise ValueError("due_date cannot be before invoice_date")
        return self


class InvoiceUpdate(BaseSchema):
    """Drafts only. A posted invoice is a statutory record - cancel it instead."""

    invoice_date: dt.date | None = None
    due_date: dt.date | None = None
    lines: list[SalesLineInput] | None = Field(default=None, min_length=1)
    notes: str | None = None
    terms: str | None = None
    round_to_whole: bool | None = None


class InvoiceRead(TimestampedSchema, DocumentTotalsRead):
    id: uuid.UUID
    invoice_number: str
    customer_id: uuid.UUID
    customer_name: str
    sales_order_id: uuid.UUID | None
    invoice_date: dt.date
    due_date: dt.date
    status: InvoiceStatus
    paid_amount: Decimal
    outstanding: Decimal
    is_overdue: bool
    days_overdue: int
    customer_gstin: str | None
    place_of_supply: str | None
    journal_entry_id: uuid.UUID | None
    posted_at: dt.datetime | None
    cancelled_at: dt.datetime | None
    notes: str | None
    terms: str | None
    lines: list[SalesLineRead] = Field(default_factory=list)


class CancelInvoiceRequest(BaseSchema):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    #: A cancellation may be dated later than the invoice - you cannot post into a
    #: closed period just because that is where the mistake was made.
    cancellation_date: dt.date | None = None


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
class PaymentAllocationInput(BaseSchema):
    invoice_id: uuid.UUID
    amount: PositiveAmount


class PaymentCreate(BaseSchema):
    customer_id: uuid.UUID
    payment_date: dt.date | None = None
    amount: PositiveAmount
    method: PaymentMethod = PaymentMethod.BANK_TRANSFER
    reference: str | None = Field(default=None, max_length=100)
    deposit_account_id: uuid.UUID | None = None
    notes: str | None = None
    #: Optional. An empty list records a payment on account, which is a real thing
    #: customers do - they pay before you invoice.
    allocations: list[PaymentAllocationInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _allocations_within_amount(self) -> Self:
        allocated = sum((a.amount for a in self.allocations), Decimal("0"))
        if allocated > self.amount:
            raise ValueError(
                f"allocations total {allocated} exceeds the payment amount {self.amount}"
            )
        seen = {a.invoice_id for a in self.allocations}
        if len(seen) != len(self.allocations):
            raise ValueError("the same invoice appears twice in allocations")
        return self


class AllocatePaymentRequest(BaseSchema):
    """Apply an existing payment's unallocated balance to invoices."""

    allocations: list[PaymentAllocationInput] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicates(self) -> Self:
        seen = {a.invoice_id for a in self.allocations}
        if len(seen) != len(self.allocations):
            raise ValueError("the same invoice appears twice in allocations")
        return self


class PaymentAllocationRead(ResponseSchema):
    id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: str
    amount: Decimal


class PaymentRead(TimestampedSchema):
    id: uuid.UUID
    payment_number: str
    customer_id: uuid.UUID
    customer_name: str
    payment_date: dt.date
    amount: Decimal
    unallocated_amount: Decimal
    allocated_amount: Decimal
    method: PaymentMethod
    reference: str | None
    currency: str
    notes: str | None
    deposit_account_id: uuid.UUID | None
    journal_entry_id: uuid.UUID | None
    allocations: list[PaymentAllocationRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
class AgeingBucket(ResponseSchema):
    label: str
    amount: Decimal
    invoice_count: int


class ReceivablesAgeing(ResponseSchema):
    """Outstanding receivables bucketed by how overdue they are.

    The standard 0/30/60/90+ buckets, because that is what a collections
    conversation and a bank's working-capital assessment both expect.
    """

    as_of: dt.date
    buckets: list[AgeingBucket]
    total_outstanding: Decimal
    total_overdue: Decimal


class SalesSummary(ResponseSchema):
    from_date: dt.date
    to_date: dt.date
    invoice_count: int
    gross_sales: Decimal
    tax_collected: Decimal
    net_sales: Decimal
    payments_received: Decimal
    outstanding: Decimal
