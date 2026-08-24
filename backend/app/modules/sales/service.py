"""Sales services.

The important part is :meth:`InvoiceService.post` - the seam where a commercial
document becomes accounting. An invoice for ₹1,180 (₹1,000 + 18% GST) posts:

===========================  ========  ========
Account                        Debit    Credit
===========================  ========  ========
Accounts Receivable          1,180.00
Sales Revenue                            1,000.00
GST Output Tax                             180.00
===========================  ========  ========

Which is why the ledger had to exist first. This module never touches
``JournalEntry`` directly - it calls :class:`PostingService`, so the double-entry
invariants (balance, open period, postable accounts) are enforced by the module
that owns them rather than re-implemented here.

**Nothing computes money from client input.** Line totals come from
:mod:`app.modules.tax.gst` and are then persisted. A client-supplied total is
never trusted, because trusting it would let a caller invoice one amount and book
another.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.core.pagination import PageParams
from app.modules.accounting.repository import SequenceRepository
from app.modules.audit.models import AuditAction, AuditSeverity
from app.modules.audit.service import AuditService
from app.modules.organizations.clock import organization_today
from app.modules.sales.models import (
    Customer,
    Lead,
    LeadStatus,
    Quotation,
    QuotationLine,
    QuotationStatus,
    SalesOrder,
    SalesOrderLine,
    SalesOrderStatus,
)
from app.modules.sales.repository import (
    CustomerRepository,
    InvoiceRepository,
    LeadRepository,
    QuotationRepository,
    SalesOrderRepository,
)
from app.modules.sales.schemas import (
    CustomerCreate,
    CustomerUpdate,
    LeadConvert,
    LeadCreate,
    LeadUpdate,
    QuotationCreate,
    QuotationUpdate,
    SalesLineInput,
    SalesOrderCreate,
)
from app.modules.tax.gst import (
    LineTotals,
    compute_document,
    compute_line,
    resolve_treatment,
    state_code_from_gstin,
)
from app.modules.users.models import User

log = get_logger(__name__)


def _audit_ctx(ctx: RequestContext | None) -> dict[str, Any]:
    if ctx is None:
        return {}
    return {"ip_address": ctx.ip_address, "user_agent": ctx.user_agent}


# =============================================================================
# Line computation, shared by every document type
# =============================================================================
class LineBuilder:
    """Turns client line input into computed, persistable line rows.

    One implementation for quotations, orders, and invoices. If each document type
    computed its own totals they would eventually disagree, and a quotation whose
    figures do not match the invoice it becomes is worse than useless.
    """

    @staticmethod
    def compute(
        inputs: list[SalesLineInput], *, treatment: Any
    ) -> list[tuple[SalesLineInput, LineTotals]]:
        return [
            (
                line,
                compute_line(
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    tax_rate=line.tax_rate,
                    treatment=treatment,
                    discount_percent=line.discount_percent,
                    discount_amount=line.discount_amount,
                ),
            )
            for line in inputs
        ]

    @staticmethod
    def apply(target: Any, source: SalesLineInput, totals: LineTotals, index: int) -> None:
        """Copy inputs and computed figures onto a line row."""
        target.line_number = index
        target.description = source.description
        target.hsn_code = source.hsn_code
        target.quantity = source.quantity
        target.unit = source.unit
        target.unit_price = source.unit_price
        target.discount_percent = source.discount_percent
        target.discount_amount = totals.discount_amount
        target.tax_rate = source.tax_rate
        target.cgst_amount = totals.cgst
        target.sgst_amount = totals.sgst
        target.igst_amount = totals.igst
        target.gross_amount = totals.gross
        target.taxable_amount = totals.taxable
        target.tax_amount = totals.tax_amount
        target.line_total = totals.total

    @staticmethod
    def apply_document_totals(
        document: Any, computed: list[LineTotals], *, round_to_whole: bool
    ) -> None:
        totals = compute_document(computed, round_to_whole=round_to_whole)
        document.subtotal = totals.subtotal
        document.discount_total = totals.discount_total
        document.taxable_total = totals.taxable_total
        document.cgst_total = totals.cgst_total
        document.sgst_total = totals.sgst_total
        document.igst_total = totals.igst_total
        document.tax_total = totals.tax_total
        document.round_off = totals.round_off
        document.grand_total = totals.grand_total


# =============================================================================
# Customers
# =============================================================================
class CustomerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.customers = CustomerRepository(session)
        self.invoices = InvoiceRepository(session)
        self.audit = AuditService(session)

    async def get(self, organization_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
        customer = await self.customers.get_for_org(organization_id, customer_id)
        if customer is None:
            raise NotFoundError("Customer")
        return customer

    async def paginate(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        query: str | None = None,
        include_inactive: bool = False,
    ) -> tuple[list[Customer], int]:
        rows, total = await self.customers.search(
            organization_id, params, query=query, include_inactive=include_inactive
        )
        return list(rows), total

    async def create(
        self,
        organization_id: uuid.UUID,
        data: CustomerCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Customer:
        code = data.code or await self.customers.next_code(organization_id)
        if await self.customers.exists(organization_id=organization_id, code=code):
            raise ConflictError(f"Customer code {code} is already in use")

        payload = data.model_dump(exclude={"code"})
        customer = Customer(
            organization_id=organization_id,
            code=code,
            # Derived from the GSTIN they supplied - it decides CGST/SGST vs IGST
            # on every future invoice, so it is resolved once here.
            state_code=state_code_from_gstin(data.gstin),
            **payload,
        )
        await self.customers.add(customer)

        await self.audit.record(
            AuditAction.CUSTOMER_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="customer",
            resource_id=customer.id,
            summary=f"Created customer {customer.name}",
            **_audit_ctx(ctx),
        )
        return customer

    async def update(
        self,
        organization_id: uuid.UUID,
        customer_id: uuid.UUID,
        data: CustomerUpdate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Customer:
        customer = await self.get(organization_id, customer_id)
        changes: dict[str, Any] = {}

        for field, value in data.model_dump(exclude_unset=True).items():
            current = getattr(customer, field)
            if current != value:
                changes[field] = {"before": current, "after": value}
                setattr(customer, field, value)

        if "gstin" in changes:
            customer.state_code = state_code_from_gstin(customer.gstin)

        await self.session.flush()

        if changes:
            await self.audit.record(
                AuditAction.CUSTOMER_UPDATED,
                actor=actor,
                organization_id=organization_id,
                resource_type="customer",
                resource_id=customer.id,
                summary=f"Updated customer {customer.name}",
                changes=changes,
                **_audit_ctx(ctx),
            )
        return customer

    async def delete(
        self,
        organization_id: uuid.UUID,
        customer_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> None:
        customer = await self.get(organization_id, customer_id)

        if await self.invoices.exists(organization_id=organization_id, customer_id=customer_id):
            raise BusinessRuleError(
                "This customer has invoices and cannot be deleted. "
                "Deactivate them instead - the documents must remain intact."
            )

        await self.customers.soft_delete(customer)
        await self.audit.record(
            AuditAction.CUSTOMER_DELETED,
            actor=actor,
            organization_id=organization_id,
            resource_type="customer",
            resource_id=customer.id,
            summary=f"Deleted customer {customer.name}",
            severity=AuditSeverity.WARNING,
            **_audit_ctx(ctx),
        )

    async def statement(
        self, organization_id: uuid.UUID, customer_id: uuid.UUID, *, as_of: dt.date
    ) -> tuple[Customer, int, Decimal, Decimal, Decimal]:
        customer = await self.get(organization_id, customer_id)
        count, invoiced, paid, overdue = await self.invoices.customer_totals(
            organization_id, customer_id, as_of=as_of
        )
        return customer, count, invoiced, paid, overdue


# =============================================================================
# Leads
# =============================================================================
class LeadService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.leads = LeadRepository(session)
        self.customers = CustomerService(session)
        self.audit = AuditService(session)

    async def get(self, organization_id: uuid.UUID, lead_id: uuid.UUID) -> Lead:
        lead = await self.leads.get_for_org(organization_id, lead_id)
        if lead is None:
            raise NotFoundError("Lead")
        return lead

    async def paginate(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        status: LeadStatus | None = None,
        owner_id: uuid.UUID | None = None,
        open_only: bool = False,
    ) -> tuple[list[Lead], int]:
        rows, total = await self.leads.search(
            organization_id, params, status=status, owner_id=owner_id, open_only=open_only
        )
        return list(rows), total

    async def create(
        self,
        organization_id: uuid.UUID,
        data: LeadCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Lead:
        lead = Lead(organization_id=organization_id, **data.model_dump())
        await self.leads.add(lead)

        await self.audit.record(
            AuditAction.LEAD_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="lead",
            resource_id=lead.id,
            summary=f"Created lead {lead.name}",
            **_audit_ctx(ctx),
        )
        return lead

    async def update(
        self,
        organization_id: uuid.UUID,
        lead_id: uuid.UUID,
        data: LeadUpdate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Lead:
        lead = await self.get(organization_id, lead_id)
        changes: dict[str, Any] = {}

        for field, value in data.model_dump(exclude_unset=True).items():
            current = getattr(lead, field)
            if current != value:
                changes[field] = {"before": str(current), "after": str(value)}
                setattr(lead, field, value)

        if data.status is not None and data.status is not LeadStatus.NEW:
            lead.last_contacted_at = dt.datetime.now(dt.UTC)

        await self.session.flush()

        if changes:
            await self.audit.record(
                AuditAction.LEAD_UPDATED,
                actor=actor,
                organization_id=organization_id,
                resource_type="lead",
                resource_id=lead.id,
                summary=f"Updated lead {lead.name}",
                changes=changes,
                **_audit_ctx(ctx),
            )
        return lead

    async def convert(
        self,
        organization_id: uuid.UUID,
        lead_id: uuid.UUID,
        data: LeadConvert,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Customer:
        """Promote a lead to a customer.

        The lead is kept and linked rather than deleted, so "which source produced
        revenue?" stays answerable once the customer starts being invoiced.
        """
        lead = await self.get(organization_id, lead_id)
        if lead.is_converted:
            raise ConflictError(
                f"{lead.name} has already been converted",
                details={"customer_id": str(lead.converted_customer_id)},
            )

        customer = await self.customers.create(
            organization_id,
            CustomerCreate(
                code=data.code,
                name=data.name or lead.company or lead.name,
                email=lead.email,
                phone=lead.phone,
                contact_person=lead.name,
                gstin=data.gstin,
                payment_terms_days=data.payment_terms_days
                if data.payment_terms_days is not None
                else 30,
                notes=lead.notes,
            ),
            actor,
            ctx,
        )

        lead.converted_customer_id = customer.id
        lead.converted_at = dt.datetime.now(dt.UTC)
        lead.status = LeadStatus.WON
        await self.session.flush()

        await self.audit.record(
            AuditAction.LEAD_CONVERTED,
            actor=actor,
            organization_id=organization_id,
            resource_type="lead",
            resource_id=lead.id,
            summary=f"Converted lead {lead.name} to customer {customer.name}",
            context={"customer_id": str(customer.id)},
            **_audit_ctx(ctx),
        )
        return customer


# =============================================================================
# Shared document helpers
# =============================================================================
class SalesDocumentService:
    """Behaviour shared by quotations, orders, and invoices."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.customers = CustomerRepository(session)
        self.sequences = SequenceRepository(session)
        self.audit = AuditService(session)

    async def _today(self, organization_id: uuid.UUID) -> dt.date:
        """Today by the organization's clock, not the server's.

        See :mod:`app.modules.organizations.clock` for why those differ, and why it matters
        for a date stamped on a record that someone will later reconcile.
        """
        return await organization_today(self.session, organization_id)

    async def _customer(self, organization_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
        customer = await self.customers.get_for_org(organization_id, customer_id)
        if customer is None:
            raise NotFoundError("Customer")
        if not customer.is_active:
            raise BusinessRuleError(f"{customer.name} is inactive.")
        return customer

    async def _seller_state(self, organization_id: uuid.UUID) -> str | None:
        """The seller's state code, from the organization's own GSTIN."""
        from app.modules.organizations.repository import OrganizationRepository

        org = await OrganizationRepository(self.session).get(organization_id)
        return state_code_from_gstin(org.gstin) if org else None

    async def _treatment(self, organization_id: uuid.UUID, customer: Customer) -> Any:
        return resolve_treatment(
            seller_state_code=await self._seller_state(organization_id),
            buyer_state_code=customer.state_code,
            buyer_country=customer.billing_country,
            is_exempt=customer.is_tax_exempt,
        )

    async def _next_number(self, organization_id: uuid.UUID, *, scope: str, prefix: str) -> str:
        """Gap-free document number, reusing the ledger's sequence table."""
        year = (await self._today(organization_id)).year
        return await self.sequences.next_number(
            organization_id, scope=f"{scope}:{year}", prefix=f"{prefix}-{year}-"
        )


# =============================================================================
# Quotations
# =============================================================================
class QuotationService(SalesDocumentService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.quotations = QuotationRepository(session)

    async def get(self, organization_id: uuid.UUID, quotation_id: uuid.UUID) -> Quotation:
        quotation = await self.quotations.get_with_lines(organization_id, quotation_id)
        if quotation is None:
            raise NotFoundError("Quotation")
        return quotation

    async def paginate(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        customer_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[Quotation], int]:
        rows, total = await self.quotations.search(
            organization_id, params, customer_id=customer_id, status=status
        )
        return list(rows), total

    async def create(
        self,
        organization_id: uuid.UUID,
        data: QuotationCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Quotation:
        customer = await self._customer(organization_id, data.customer_id)
        treatment = await self._treatment(organization_id, customer)
        computed = LineBuilder.compute(data.lines, treatment=treatment)

        quotation = Quotation(
            organization_id=organization_id,
            quotation_number=await self._next_number(
                organization_id, scope="quotation", prefix="QT"
            ),
            customer_id=customer.id,
            lead_id=data.lead_id,
            quotation_date=data.quotation_date or await self._today(organization_id),
            valid_until=data.valid_until,
            status=QuotationStatus.DRAFT,
            tax_treatment=treatment,
            currency=customer.currency,
            notes=data.notes,
            terms=data.terms,
            created_by_id=actor.id,
        )
        quotation.lines = [QuotationLine() for _ in computed]
        for index, ((source, totals), row) in enumerate(
            zip(computed, quotation.lines, strict=True), start=1
        ):
            LineBuilder.apply(row, source, totals, index)
        LineBuilder.apply_document_totals(
            quotation, [totals for _, totals in computed], round_to_whole=data.round_to_whole
        )

        self.session.add(quotation)
        await self.session.flush()

        await self.audit.record(
            AuditAction.QUOTATION_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="quotation",
            resource_id=quotation.id,
            summary=f"Created quotation {quotation.quotation_number} for {quotation.grand_total}",
            **_audit_ctx(ctx),
        )
        return quotation

    async def update(
        self,
        organization_id: uuid.UUID,
        quotation_id: uuid.UUID,
        data: QuotationUpdate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Quotation:
        quotation = await self.get(organization_id, quotation_id)
        if quotation.status in (QuotationStatus.CONVERTED, QuotationStatus.ACCEPTED):
            raise BusinessRuleError(f"This quotation is {quotation.status} and cannot be changed.")

        if data.quotation_date is not None:
            quotation.quotation_date = data.quotation_date
        if data.valid_until is not None:
            quotation.valid_until = data.valid_until
        if data.notes is not None:
            quotation.notes = data.notes
        if data.terms is not None:
            quotation.terms = data.terms

        if data.lines is not None:
            customer = await self._customer(organization_id, quotation.customer_id)
            treatment = await self._treatment(organization_id, customer)
            computed = LineBuilder.compute(data.lines, treatment=treatment)
            rows = [QuotationLine() for _ in computed]
            for index, ((source, totals), row) in enumerate(
                zip(computed, rows, strict=True), start=1
            ):
                LineBuilder.apply(row, source, totals, index)
            quotation.lines = rows
            quotation.tax_treatment = treatment
            LineBuilder.apply_document_totals(
                quotation,
                [totals for _, totals in computed],
                round_to_whole=bool(data.round_to_whole or quotation.round_off != 0),
            )

        await self.session.flush()
        return quotation

    async def mark_sent(
        self,
        organization_id: uuid.UUID,
        quotation_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Quotation:
        quotation = await self.get(organization_id, quotation_id)
        if quotation.status is not QuotationStatus.DRAFT:
            raise ConflictError(f"This quotation is already {quotation.status}.")

        quotation.status = QuotationStatus.SENT
        quotation.sent_at = dt.datetime.now(dt.UTC)
        await self.session.flush()

        await self.audit.record(
            AuditAction.QUOTATION_SENT,
            actor=actor,
            organization_id=organization_id,
            resource_type="quotation",
            resource_id=quotation.id,
            summary=f"Sent quotation {quotation.quotation_number}",
            **_audit_ctx(ctx),
        )
        return quotation

    async def respond(
        self,
        organization_id: uuid.UUID,
        quotation_id: uuid.UUID,
        *,
        accepted: bool,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Quotation:
        quotation = await self.get(organization_id, quotation_id)
        if quotation.status not in (QuotationStatus.DRAFT, QuotationStatus.SENT):
            raise ConflictError(f"This quotation is already {quotation.status}.")

        quotation.status = QuotationStatus.ACCEPTED if accepted else QuotationStatus.REJECTED
        quotation.responded_at = dt.datetime.now(dt.UTC)
        await self.session.flush()

        await self.audit.record(
            AuditAction.QUOTATION_ACCEPTED if accepted else AuditAction.QUOTATION_REJECTED,
            actor=actor,
            organization_id=organization_id,
            resource_type="quotation",
            resource_id=quotation.id,
            summary=f"Quotation {quotation.quotation_number} "
            f"{'accepted' if accepted else 'rejected'}",
            **_audit_ctx(ctx),
        )
        return quotation


# =============================================================================
# Sales orders
# =============================================================================
class SalesOrderService(SalesDocumentService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.orders = SalesOrderRepository(session)
        self.quotations = QuotationRepository(session)

    async def get(self, organization_id: uuid.UUID, order_id: uuid.UUID) -> SalesOrder:
        order = await self.orders.get_with_lines(organization_id, order_id)
        if order is None:
            raise NotFoundError("Sales order")
        return order

    async def paginate(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        customer_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[SalesOrder], int]:
        rows, total = await self.orders.search(
            organization_id, params, customer_id=customer_id, status=status
        )
        return list(rows), total

    async def create(
        self,
        organization_id: uuid.UUID,
        data: SalesOrderCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> SalesOrder:
        customer = await self._customer(organization_id, data.customer_id)
        treatment = await self._treatment(organization_id, customer)
        computed = LineBuilder.compute(data.lines, treatment=treatment)

        order = SalesOrder(
            organization_id=organization_id,
            order_number=await self._next_number(organization_id, scope="sales_order", prefix="SO"),
            customer_id=customer.id,
            quotation_id=data.quotation_id,
            order_date=data.order_date or await self._today(organization_id),
            expected_delivery_date=data.expected_delivery_date,
            customer_reference=data.customer_reference,
            status=SalesOrderStatus.DRAFT,
            tax_treatment=treatment,
            currency=customer.currency,
            notes=data.notes,
            terms=data.terms,
            created_by_id=actor.id,
        )
        order.lines = [SalesOrderLine() for _ in computed]
        for index, ((source, totals), row) in enumerate(
            zip(computed, order.lines, strict=True), start=1
        ):
            LineBuilder.apply(row, source, totals, index)
        LineBuilder.apply_document_totals(
            order, [totals for _, totals in computed], round_to_whole=data.round_to_whole
        )

        self.session.add(order)
        await self.session.flush()

        await self.audit.record(
            AuditAction.SALES_ORDER_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="sales_order",
            resource_id=order.id,
            summary=f"Created sales order {order.order_number} for {order.grand_total}",
            **_audit_ctx(ctx),
        )
        return order

    async def from_quotation(
        self,
        organization_id: uuid.UUID,
        quotation_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> SalesOrder:
        """Convert an accepted quotation into an order.

        The quotation becomes ``CONVERTED``, which is terminal - so the same quote
        cannot spawn two orders.
        """
        quotation = await self.quotations.get_with_lines(organization_id, quotation_id)
        if quotation is None:
            raise NotFoundError("Quotation")
        if quotation.status is QuotationStatus.CONVERTED:
            raise ConflictError(
                f"Quotation {quotation.quotation_number} has already been converted"
            )
        if quotation.status is QuotationStatus.REJECTED:
            raise BusinessRuleError("A rejected quotation cannot become an order.")

        order = await self.create(
            organization_id,
            SalesOrderCreate(
                customer_id=quotation.customer_id,
                quotation_id=quotation.id,
                lines=[
                    SalesLineInput(
                        description=line.description,
                        hsn_code=line.hsn_code,
                        quantity=line.quantity,
                        unit=line.unit,
                        unit_price=line.unit_price,
                        discount_percent=line.discount_percent,
                        tax_rate=line.tax_rate,
                    )
                    for line in quotation.lines
                ],
                notes=quotation.notes,
                terms=quotation.terms,
                round_to_whole=quotation.round_off != 0,
            ),
            actor,
            ctx,
        )

        quotation.status = QuotationStatus.CONVERTED
        await self.session.flush()

        await self.audit.record(
            AuditAction.QUOTATION_CONVERTED,
            actor=actor,
            organization_id=organization_id,
            resource_type="quotation",
            resource_id=quotation.id,
            summary=f"Converted {quotation.quotation_number} to order {order.order_number}",
            context={"order_id": str(order.id)},
            **_audit_ctx(ctx),
        )
        return order

    async def confirm(
        self,
        organization_id: uuid.UUID,
        order_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> SalesOrder:
        order = await self.get(organization_id, order_id)
        if order.status is not SalesOrderStatus.DRAFT:
            raise ConflictError(f"This order is already {order.status}.")

        order.status = SalesOrderStatus.CONFIRMED
        order.confirmed_at = dt.datetime.now(dt.UTC)
        await self.session.flush()

        await self.audit.record(
            AuditAction.SALES_ORDER_CONFIRMED,
            actor=actor,
            organization_id=organization_id,
            resource_type="sales_order",
            resource_id=order.id,
            summary=f"Confirmed order {order.order_number}",
            **_audit_ctx(ctx),
        )
        return order

    async def cancel(
        self,
        organization_id: uuid.UUID,
        order_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> SalesOrder:
        order = await self.get(organization_id, order_id)
        if order.invoiced_total > 0:
            raise BusinessRuleError("This order has been invoiced. Cancel the invoices first.")

        order.status = SalesOrderStatus.CANCELLED
        order.cancelled_at = dt.datetime.now(dt.UTC)
        await self.session.flush()

        await self.audit.record(
            AuditAction.SALES_ORDER_CANCELLED,
            actor=actor,
            organization_id=organization_id,
            resource_type="sales_order",
            resource_id=order.id,
            summary=f"Cancelled order {order.order_number}",
            severity=AuditSeverity.WARNING,
            **_audit_ctx(ctx),
        )
        return order
