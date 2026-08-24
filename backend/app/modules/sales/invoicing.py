"""Invoicing and payment collection - where sales becomes accounting.

Kept in its own module rather than alongquotations and orders because this is the
only part of sales that writes to the ledger, and that boundary is worth being able
to see at a glance.

**The posting.** An invoice for ₹1,180 (₹1,000 goods + 18% intra-state GST) becomes:

===========================  ==========  ==========
Account                          Debit      Credit
===========================  ==========  ==========
Accounts Receivable           1,180.00
Sales Revenue                             1,000.00
GST Output Tax - CGST                        90.00
GST Output Tax - SGST                        90.00
===========================  ==========  ==========

**The collection.** A ₹1,180 receipt into the bank becomes:

===========================  ==========  ==========
Bank                          1,180.00
Accounts Receivable                       1,180.00
===========================  ==========  ==========

Both go through :class:`PostingService`, so the double-entry invariants are
enforced by the module that owns them. This module never constructs a
``JournalEntry`` itself.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.pagination import PageParams
from app.db.types import ZERO
from app.modules.accounting.coa_template import SystemAccount
from app.modules.accounting.models import JournalType
from app.modules.accounting.repository import JournalRepository
from app.modules.accounting.schemas import JournalEntryCreate, JournalEntryLineInput
from app.modules.accounting.service import ChartOfAccountsService, PostingService
from app.modules.audit.models import AuditAction, AuditSeverity
from app.modules.sales.models import (
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Payment,
    PaymentAllocation,
    SalesOrderStatus,
)
from app.modules.sales.repository import (
    InvoiceRepository,
    PaymentRepository,
    SalesOrderRepository,
)
from app.modules.sales.schemas import (
    AllocatePaymentRequest,
    InvoiceCreate,
    InvoiceUpdate,
    PaymentCreate,
)
from app.modules.sales.service import LineBuilder, SalesDocumentService, _audit_ctx
from app.modules.users.models import User

log = get_logger(__name__)


class InvoiceService(SalesDocumentService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.invoices = InvoiceRepository(session)
        self.orders = SalesOrderRepository(session)
        self.posting = PostingService(session)
        self.chart = ChartOfAccountsService(session)
        self.journals = JournalRepository(session)

    # -- Reads ---------------------------------------------------------------
    async def get(self, organization_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
        invoice = await self.invoices.get_with_lines(organization_id, invoice_id)
        if invoice is None:
            raise NotFoundError("Invoice")
        return invoice

    async def paginate(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        **filters: Any,
    ) -> tuple[list[Invoice], int]:
        rows, total = await self.invoices.search(organization_id, params, **filters)
        return list(rows), total

    # -- Create --------------------------------------------------------------
    async def create(
        self,
        organization_id: uuid.UUID,
        data: InvoiceCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Invoice:
        customer = await self._customer(organization_id, data.customer_id)
        treatment = await self._treatment(organization_id, customer)
        computed = LineBuilder.compute(data.lines, treatment=treatment)

        invoice_date = data.invoice_date or await self._today(organization_id)
        invoice = Invoice(
            organization_id=organization_id,
            invoice_number=await self._next_number(organization_id, scope="invoice", prefix="INV"),
            customer_id=customer.id,
            sales_order_id=data.sales_order_id,
            invoice_date=invoice_date,
            due_date=data.due_date or customer.due_date_for(invoice_date),
            status=InvoiceStatus.DRAFT,
            tax_treatment=treatment,
            currency=customer.currency,
            # Snapshotted: a customer who later corrects their GSTIN must not
            # retroactively alter an invoice that has already been filed.
            customer_gstin=customer.gstin,
            place_of_supply=customer.state_code,
            notes=data.notes,
            terms=data.terms,
            created_by_id=actor.id,
        )
        invoice.lines = [InvoiceLine() for _ in computed]
        for index, ((source, totals), row) in enumerate(
            zip(computed, invoice.lines, strict=True), start=1
        ):
            LineBuilder.apply(row, source, totals, index)
            row.revenue_account_id = source.revenue_account_id
        LineBuilder.apply_document_totals(
            invoice, [totals for _, totals in computed], round_to_whole=data.round_to_whole
        )

        self.session.add(invoice)
        await self.session.flush()

        await self.audit.record(
            AuditAction.INVOICE_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="invoice",
            resource_id=invoice.id,
            summary=f"Created invoice {invoice.invoice_number} for {invoice.grand_total}",
            **_audit_ctx(ctx),
        )

        if data.post:
            return await self.post(organization_id, invoice.id, actor, ctx)
        return invoice

    # -- Update (drafts only) ------------------------------------------------
    async def update(
        self,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        data: InvoiceUpdate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Invoice:
        invoice = await self.get(organization_id, invoice_id)
        if not invoice.status.is_editable:
            raise BusinessRuleError(
                f"Invoice {invoice.invoice_number} is {invoice.status} and cannot be "
                "edited. Cancel it and raise a new one - a posted invoice is a "
                "statutory record.",
                details={"status": str(invoice.status)},
            )

        if data.invoice_date is not None:
            invoice.invoice_date = data.invoice_date
            if data.due_date is None and invoice.due_date < data.invoice_date:
                # Keep the invariant due_date >= invoice_date, which the database
                # also enforces.
                customer = await self._customer(organization_id, invoice.customer_id)
                invoice.due_date = customer.due_date_for(data.invoice_date)
        if data.due_date is not None:
            invoice.due_date = data.due_date
        if data.notes is not None:
            invoice.notes = data.notes
        if data.terms is not None:
            invoice.terms = data.terms

        if data.lines is not None:
            customer = await self._customer(organization_id, invoice.customer_id)
            treatment = await self._treatment(organization_id, customer)
            computed = LineBuilder.compute(data.lines, treatment=treatment)
            rows = [InvoiceLine() for _ in computed]
            for index, ((source, totals), row) in enumerate(
                zip(computed, rows, strict=True), start=1
            ):
                LineBuilder.apply(row, source, totals, index)
                row.revenue_account_id = source.revenue_account_id
            invoice.lines = rows
            invoice.tax_treatment = treatment
            LineBuilder.apply_document_totals(
                invoice,
                [totals for _, totals in computed],
                round_to_whole=bool(data.round_to_whole or invoice.round_off != 0),
            )

        await self.session.flush()
        return invoice

    # -- Post ----------------------------------------------------------------
    async def post(
        self,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Invoice:
        """Post the invoice to the ledger.

        This is the point at which a commercial document acquires an accounting
        effect. After it the invoice is immutable.

        Tax is credited to CGST/SGST or IGST as separate lines, because the split
        determines which government is owed and a single combined tax line could
        not be filed.
        """
        invoice = await self.get(organization_id, invoice_id)

        if invoice.status.is_posted:
            raise ConflictError(
                f"Invoice {invoice.invoice_number} is already posted",
                details={"journal_entry_id": str(invoice.journal_entry_id)},
            )
        if invoice.status is InvoiceStatus.CANCELLED:
            raise BusinessRuleError("A cancelled invoice cannot be posted.")
        if not invoice.lines:
            raise BusinessRuleError("An invoice needs at least one line.")
        if invoice.grand_total <= 0:
            raise BusinessRuleError("An invoice total must be positive.")

        journal = await self.journals.get_by_type(organization_id, JournalType.SALES)
        if journal is None:
            raise BusinessRuleError("No sales journal is configured for this organization.")

        lines = await self._build_posting_lines(organization_id, invoice)

        entry = await self.posting.create_entry(
            organization_id,
            JournalEntryCreate(
                journal_id=journal.id,
                entry_date=invoice.invoice_date,
                narration=f"Invoice {invoice.invoice_number} - {invoice.customer.name}",
                reference=invoice.invoice_number,
                post=True,
                lines=lines,
            ),
            actor,
            ctx,
            source_type="invoice",
            source_id=invoice.id,
        )

        invoice.status = InvoiceStatus.POSTED
        invoice.journal_entry_id = entry.id
        invoice.posted_at = dt.datetime.now(dt.UTC)

        # Roll the order's invoiced total forward so partial fulfilment is visible.
        if invoice.sales_order_id is not None:
            order = await self.orders.get(invoice.sales_order_id)
            if order is not None:
                order.invoiced_total += invoice.grand_total
                order.status = (
                    SalesOrderStatus.INVOICED
                    if order.invoiced_total >= order.grand_total
                    else SalesOrderStatus.PARTIALLY_INVOICED
                )

        await self.session.flush()

        await self.audit.record(
            AuditAction.INVOICE_POSTED,
            actor=actor,
            organization_id=organization_id,
            resource_type="invoice",
            resource_id=invoice.id,
            summary=f"Posted invoice {invoice.invoice_number} for {invoice.grand_total}",
            context={
                "journal_entry": entry.entry_number,
                "amount": str(invoice.grand_total),
            },
            **_audit_ctx(ctx),
        )
        log.info(
            "invoice posted",
            extra={
                "invoice_number": invoice.invoice_number,
                "amount": str(invoice.grand_total),
                "journal_entry": entry.entry_number,
            },
        )
        return invoice

    async def _build_posting_lines(
        self, organization_id: uuid.UUID, invoice: Invoice
    ) -> list[JournalEntryLineInput]:
        """Assemble the journal lines for an invoice.

        Revenue is grouped by account so a multi-line invoice against one revenue
        account produces one credit rather than twenty - the ledger records the
        accounting effect, not the invoice layout.
        """
        receivable = await self.chart.resolve_system_account(
            organization_id, SystemAccount.ACCOUNTS_RECEIVABLE
        )
        default_revenue = await self.chart.resolve_system_account(
            organization_id, SystemAccount.SALES_REVENUE
        )

        lines: list[JournalEntryLineInput] = [
            JournalEntryLineInput(
                account_id=receivable.id,
                debit=invoice.grand_total,
                description=f"{invoice.customer.name} - {invoice.invoice_number}",
            )
        ]

        revenue_by_account: dict[uuid.UUID, Decimal] = {}
        for line in invoice.lines:
            account_id = line.revenue_account_id or default_revenue.id
            revenue_by_account[account_id] = (
                revenue_by_account.get(account_id, ZERO) + line.taxable_amount
            )

        for account_id, amount in revenue_by_account.items():
            if amount > 0:
                lines.append(
                    JournalEntryLineInput(
                        account_id=account_id, credit=amount, description="Sales revenue"
                    )
                )

        # Tax, split so each component can be filed separately.
        if invoice.cgst_total > 0 or invoice.sgst_total > 0 or invoice.igst_total > 0:
            gst_output = await self.chart.resolve_system_account(
                organization_id, SystemAccount.GST_OUTPUT
            )
            for label, amount in (
                ("CGST", invoice.cgst_total),
                ("SGST", invoice.sgst_total),
                ("IGST", invoice.igst_total),
            ):
                if amount > 0:
                    lines.append(
                        JournalEntryLineInput(
                            account_id=gst_output.id, credit=amount, description=label
                        )
                    )

        # Rounding difference, if the total was rounded to a whole unit. Without a
        # home for it the entry would not balance and could not be posted.
        if invoice.round_off != 0:
            rounding = await self.chart.resolve_system_account(
                organization_id, SystemAccount.ROUNDING
            )
            if invoice.round_off > 0:
                # Rounded up: the customer owes more, so the gain offsets it.
                lines.append(
                    JournalEntryLineInput(
                        account_id=rounding.id,
                        credit=invoice.round_off,
                        description="Rounding",
                    )
                )
            else:
                lines.append(
                    JournalEntryLineInput(
                        account_id=rounding.id,
                        debit=-invoice.round_off,
                        description="Rounding",
                    )
                )

        return lines

    # -- Cancel --------------------------------------------------------------
    async def cancel(
        self,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        *,
        reason: str,
        cancellation_date: dt.date | None = None,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Invoice:
        """Cancel a posted invoice by reversing its ledger entry.

        The invoice is retained, never deleted - it had a number, it was sent, and
        the audit trail must show both that it existed and that it was cancelled.
        """
        invoice = await self.get(organization_id, invoice_id)

        if invoice.status is InvoiceStatus.CANCELLED:
            raise ConflictError(f"Invoice {invoice.invoice_number} is already cancelled")
        if invoice.paid_amount > 0:
            raise BusinessRuleError(
                "This invoice has payments against it. Unallocate them first - "
                "cancelling would leave the receipt pointing at nothing.",
                details={"paid_amount": str(invoice.paid_amount)},
            )

        if invoice.status is InvoiceStatus.DRAFT:
            # No ledger effect to undo.
            invoice.status = InvoiceStatus.CANCELLED
            invoice.cancelled_at = dt.datetime.now(dt.UTC)
        else:
            if invoice.journal_entry_id is None:  # pragma: no cover - CHECK prevents it
                raise BusinessRuleError("This invoice is posted but has no journal entry.")

            reversal = await self.posting.reverse_entry(
                organization_id,
                invoice.journal_entry_id,
                actor,
                reversal_date=cancellation_date,
                narration=f"Cancellation of {invoice.invoice_number}: {reason}",
                ctx=ctx,
            )
            invoice.status = InvoiceStatus.CANCELLED
            invoice.cancelled_at = dt.datetime.now(dt.UTC)
            invoice.reversal_entry_id = reversal.id

            if invoice.sales_order_id is not None:
                order = await self.orders.get(invoice.sales_order_id)
                if order is not None:
                    order.invoiced_total = max(ZERO, order.invoiced_total - invoice.grand_total)
                    order.status = (
                        SalesOrderStatus.CONFIRMED
                        if order.invoiced_total == 0
                        else SalesOrderStatus.PARTIALLY_INVOICED
                    )

        await self.session.flush()

        await self.audit.record(
            AuditAction.INVOICE_CANCELLED,
            actor=actor,
            organization_id=organization_id,
            resource_type="invoice",
            resource_id=invoice.id,
            summary=f"Cancelled invoice {invoice.invoice_number}: {reason}",
            severity=AuditSeverity.WARNING,
            context={"reason": reason},
            **_audit_ctx(ctx),
        )
        return invoice

    async def delete_draft(
        self,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> None:
        invoice = await self.get(organization_id, invoice_id)
        if invoice.status is not InvoiceStatus.DRAFT:
            raise BusinessRuleError(
                "Only drafts can be deleted. Cancel the invoice instead - it has a "
                "number and a ledger effect."
            )

        await self.invoices.soft_delete(invoice)
        await self.audit.record(
            AuditAction.INVOICE_DELETED,
            actor=actor,
            organization_id=organization_id,
            resource_type="invoice",
            resource_id=invoice.id,
            summary=f"Deleted draft invoice {invoice.invoice_number}",
            **_audit_ctx(ctx),
        )


# =============================================================================
# Payments
# =============================================================================
class PaymentService(SalesDocumentService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.payments = PaymentRepository(session)
        self.invoices = InvoiceRepository(session)
        self.posting = PostingService(session)
        self.chart = ChartOfAccountsService(session)
        self.journals = JournalRepository(session)

    async def get(self, organization_id: uuid.UUID, payment_id: uuid.UUID) -> Payment:
        payment = await self.payments.get_with_allocations(organization_id, payment_id)
        if payment is None:
            raise NotFoundError("Payment")
        return payment

    async def paginate(
        self, organization_id: uuid.UUID, params: PageParams, **filters: Any
    ) -> tuple[list[Payment], int]:
        rows, total = await self.payments.search(organization_id, params, **filters)
        return list(rows), total

    async def record(
        self,
        organization_id: uuid.UUID,
        data: PaymentCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Payment:
        """Record a receipt, post it, and apply it to invoices.

        Allocation is optional: a payment with none is a payment on account, which
        customers genuinely make before an invoice exists.
        """
        customer = await self._customer(organization_id, data.customer_id)

        deposit_account = await self._resolve_deposit_account(organization_id, data)

        payment = Payment(
            organization_id=organization_id,
            payment_number=await self._next_number(organization_id, scope="payment", prefix="RCP"),
            customer_id=customer.id,
            payment_date=data.payment_date or await self._today(organization_id),
            amount=data.amount,
            unallocated_amount=data.amount,
            method=data.method,
            reference=data.reference,
            currency=customer.currency,
            notes=data.notes,
            deposit_account_id=deposit_account.id,
            created_by_id=actor.id,
        )
        self.session.add(payment)
        await self.session.flush()

        entry = await self._post_receipt(organization_id, payment, customer, actor, ctx)
        payment.journal_entry_id = entry.id

        if data.allocations:
            await self._apply(
                organization_id,
                payment,
                [(a.invoice_id, a.amount) for a in data.allocations],
                actor,
                ctx,
            )

        await self.session.flush()

        await self.audit.record(
            AuditAction.PAYMENT_RECEIVED,
            actor=actor,
            organization_id=organization_id,
            resource_type="payment",
            resource_id=payment.id,
            summary=f"Received {payment.amount} from {customer.name} ({payment.payment_number})",
            context={"journal_entry": entry.entry_number},
            **_audit_ctx(ctx),
        )
        return payment

    async def _resolve_deposit_account(
        self, organization_id: uuid.UUID, data: PaymentCreate
    ) -> Any:
        """Where the money landed.

        An explicit account wins; otherwise the method decides - cash to the cash
        account, everything else to the bank.
        """
        if data.deposit_account_id is not None:
            account = await self.chart.get_account(organization_id, data.deposit_account_id)
            if not account.is_postable:
                raise ValidationError("The deposit account must be postable")
            return account

        key = SystemAccount.CASH if data.method.is_cash else SystemAccount.BANK
        return await self.chart.resolve_system_account(organization_id, key)

    async def _post_receipt(
        self,
        organization_id: uuid.UUID,
        payment: Payment,
        customer: Any,
        actor: User,
        ctx: RequestContext | None,
    ) -> Any:
        """Debit cash/bank, credit receivables.

        ``customer`` is passed in rather than read from ``payment.customer``: the
        payment was constructed moments ago, so that relationship is unloaded, and
        async SQLAlchemy cannot lazy-load it mid-statement - it raises
        ``MissingGreenlet``.
        """
        receivable = await self.chart.resolve_system_account(
            organization_id, SystemAccount.ACCOUNTS_RECEIVABLE
        )
        journal_type = JournalType.CASH if payment.method.is_cash else JournalType.BANK
        journal = await self.journals.get_by_type(organization_id, journal_type)
        if journal is None:
            raise BusinessRuleError(f"No {journal_type} journal is configured.")

        assert payment.deposit_account_id is not None  # noqa: S101 - set by caller
        return await self.posting.create_entry(
            organization_id,
            JournalEntryCreate(
                journal_id=journal.id,
                entry_date=payment.payment_date,
                narration=f"Receipt {payment.payment_number} - {customer.name}",
                reference=payment.reference or payment.payment_number,
                post=True,
                lines=[
                    JournalEntryLineInput(
                        account_id=payment.deposit_account_id,
                        debit=payment.amount,
                        description="Payment received",
                    ),
                    JournalEntryLineInput(
                        account_id=receivable.id,
                        credit=payment.amount,
                        description=customer.name,
                    ),
                ],
            ),
            actor,
            ctx,
            source_type="payment",
            source_id=payment.id,
        )

    async def allocate(
        self,
        organization_id: uuid.UUID,
        payment_id: uuid.UUID,
        data: AllocatePaymentRequest,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Payment:
        """Apply an existing payment's unallocated balance to invoices."""
        payment = await self.get(organization_id, payment_id)
        await self._apply(
            organization_id,
            payment,
            [(a.invoice_id, a.amount) for a in data.allocations],
            actor,
            ctx,
        )
        await self.session.flush()
        return payment

    async def _apply(
        self,
        organization_id: uuid.UUID,
        payment: Payment,
        allocations: list[tuple[uuid.UUID, Decimal]],
        actor: User,
        ctx: RequestContext | None,
    ) -> None:
        """Attach amounts to invoices, updating both sides.

        No ledger posting happens here. The receipt already moved cash and cleared
        receivables in aggregate; allocation only records *which* invoices it
        settles, which is a subsidiary-ledger concern. Posting again would
        double-count the payment.
        """
        requested = sum((amount for _, amount in allocations), ZERO)
        if requested > payment.unallocated_amount:
            raise BusinessRuleError(
                f"Only {payment.unallocated_amount} of this payment is unallocated, "
                f"but {requested} was requested.",
                details={
                    "unallocated": str(payment.unallocated_amount),
                    "requested": str(requested),
                },
            )

        # Queried rather than read off `payment.allocations`: on a payment created
        # moments ago that relationship is unloaded, and async SQLAlchemy cannot
        # lazy-load it here (MissingGreenlet).
        existing_rows = (
            (
                await self.session.execute(
                    select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id)
                )
            )
            .scalars()
            .all()
        )
        existing = {row.invoice_id: row for row in existing_rows}

        for invoice_id, amount in allocations:
            invoice = await self.invoices.get_with_lines(organization_id, invoice_id)
            if invoice is None:
                raise NotFoundError("Invoice")
            if invoice.customer_id != payment.customer_id:
                raise ValidationError(
                    "That invoice belongs to a different customer",
                    details={"invoice_number": invoice.invoice_number},
                )
            if not invoice.status.is_posted:
                raise BusinessRuleError(
                    f"Invoice {invoice.invoice_number} is {invoice.status} - only a "
                    "posted invoice can be paid.",
                )
            if amount > invoice.outstanding:
                raise BusinessRuleError(
                    f"Invoice {invoice.invoice_number} has only "
                    f"{invoice.outstanding} outstanding, but {amount} was allocated.",
                    details={
                        "invoice_number": invoice.invoice_number,
                        "outstanding": str(invoice.outstanding),
                    },
                )

            if invoice_id in existing:
                existing[invoice_id].amount += amount
            else:
                self.session.add(
                    PaymentAllocation(payment_id=payment.id, invoice_id=invoice.id, amount=amount)
                )

            invoice.paid_amount += amount
            invoice.status = (
                InvoiceStatus.PAID if invoice.is_fully_paid else InvoiceStatus.PARTIALLY_PAID
            )
            payment.unallocated_amount -= amount

        await self.session.flush()

        await self.audit.record(
            AuditAction.PAYMENT_ALLOCATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="payment",
            resource_id=payment.id,
            summary=f"Allocated {requested} of {payment.payment_number}",
            **_audit_ctx(ctx),
        )

    async def auto_allocate(
        self,
        organization_id: uuid.UUID,
        payment_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Payment:
        """Apply the unallocated balance oldest-invoice-first.

        The convention every accounts-receivable clerk uses, and what a customer
        paying a round number against several invoices expects.
        """
        payment = await self.get(organization_id, payment_id)
        if payment.unallocated_amount <= 0:
            return payment

        outstanding = await self.invoices.outstanding_for_customer(
            organization_id, payment.customer_id
        )

        remaining = payment.unallocated_amount
        plan: list[tuple[uuid.UUID, Decimal]] = []
        for invoice in outstanding:
            if remaining <= 0:
                break
            applied = min(remaining, invoice.outstanding)
            if applied > 0:
                plan.append((invoice.id, applied))
                remaining -= applied

        if plan:
            await self._apply(organization_id, payment, plan, actor, ctx)
            await self.session.flush()
        return payment
