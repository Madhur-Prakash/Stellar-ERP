"""Goods receipts, bills, and supplier payments - the ledger-writing half.

See :mod:`app.modules.purchasing.service` for the two-step accrual model and why
receipt and bill post separately.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

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
from app.modules.accounting.schemas import JournalEntryCreate, JournalEntryLineInput
from app.modules.audit.models import AuditAction, AuditSeverity
from app.modules.purchasing.models import (
    Bill,
    BillLine,
    BillStatus,
    GoodsReceipt,
    GoodsReceiptLine,
    GoodsReceiptStatus,
    MovementKind,
    ProductKind,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    SupplierPayment,
    SupplierPaymentAllocation,
)
from app.modules.purchasing.schemas import (
    BillCreate,
    GoodsReceiptCreate,
    SupplierPaymentCreate,
)
from app.modules.purchasing.service import PurchaseLineBuilder, PurchasingBase
from app.modules.purchasing.stock import _audit_ctx
from app.modules.purchasing.valuation import round_value
from app.modules.users.models import User

log = get_logger(__name__)


# =============================================================================
# Goods receipts
# =============================================================================
class GoodsReceiptService(PurchasingBase):
    async def get(self, organization_id: uuid.UUID, receipt_id: uuid.UUID) -> GoodsReceipt:
        receipt = (
            await self.session.execute(
                select(GoodsReceipt)
                .where(
                    GoodsReceipt.id == receipt_id,
                    GoodsReceipt.organization_id == organization_id,
                    GoodsReceipt.deleted_at.is_(None),
                )
                .options(
                    selectinload(GoodsReceipt.lines).selectinload(GoodsReceiptLine.product),
                    selectinload(GoodsReceipt.supplier),
                    selectinload(GoodsReceipt.warehouse),
                )
            )
        ).scalar_one_or_none()
        if receipt is None:
            raise NotFoundError("Goods receipt")
        return receipt

    async def paginate(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        supplier_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[GoodsReceipt], int]:
        clauses: list[Any] = [
            GoodsReceipt.organization_id == organization_id,
            GoodsReceipt.deleted_at.is_(None),
        ]
        if supplier_id is not None:
            clauses.append(GoodsReceipt.supplier_id == supplier_id)
        if status is not None:
            clauses.append(GoodsReceipt.status == status)

        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(GoodsReceipt).where(*clauses)
                )
            ).scalar_one()
        )
        rows = (
            (
                await self.session.execute(
                    select(GoodsReceipt)
                    .where(*clauses)
                    .options(
                        selectinload(GoodsReceipt.lines).selectinload(GoodsReceiptLine.product),
                        selectinload(GoodsReceipt.supplier),
                        selectinload(GoodsReceipt.warehouse),
                    )
                    .order_by(GoodsReceipt.receipt_date.desc())
                    .offset(params.offset)
                    .limit(params.limit)
                )
            )
            .scalars()
            .all()
        )
        return list(rows), total

    async def create(
        self,
        organization_id: uuid.UUID,
        data: GoodsReceiptCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> GoodsReceipt:
        supplier = await self._supplier(organization_id, data.supplier_id)
        warehouse = await self.stock.get_warehouse(organization_id, data.warehouse_id)

        receipt = GoodsReceipt(
            organization_id=organization_id,
            receipt_number=await self._next_number(
                organization_id, scope="goods_receipt", prefix="GRN"
            ),
            supplier_id=supplier.id,
            purchase_order_id=data.purchase_order_id,
            warehouse_id=warehouse.id,
            receipt_date=data.receipt_date or await self._today(organization_id),
            supplier_reference=data.supplier_reference,
            status=GoodsReceiptStatus.DRAFT,
            notes=data.notes,
            created_by_id=actor.id,
        )

        total = ZERO
        receipt.lines = []
        for index, line in enumerate(data.lines, start=1):
            product = await self._product(organization_id, line.product_id)
            if not product.tracks_stock:
                raise BusinessRuleError(
                    f"{product.name} is a {product.kind} and cannot be received into stock. "
                    "Bill it directly instead.",
                    details={"product_id": str(product.id)},
                )
            line_total = round_value(line.quantity * line.unit_cost)
            receipt.lines.append(
                GoodsReceiptLine(
                    line_number=index,
                    product_id=product.id,
                    purchase_order_line_id=line.purchase_order_line_id,
                    quantity=line.quantity,
                    unit_cost=line.unit_cost,
                    total_cost=line_total,
                    rejected_quantity=line.rejected_quantity,
                    notes=line.notes,
                )
            )
            total += line_total

        receipt.total_cost = round_value(total)
        self.session.add(receipt)
        await self.session.flush()

        await self.audit.record(
            AuditAction.GOODS_RECEIPT_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="goods_receipt",
            resource_id=receipt.id,
            summary=f"Created {receipt.receipt_number} from {supplier.name}",
            **_audit_ctx(ctx),
        )

        if data.post:
            return await self.post(organization_id, receipt.id, actor, ctx)
        return receipt

    async def post(
        self,
        organization_id: uuid.UUID,
        receipt_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> GoodsReceipt:
        """Move the stock and recognise the accrual.

        Only the **accepted** quantity enters stock - rejected units are recorded on
        the line for a supplier claim but were never really received, and adding them
        would overstate both inventory and the liability.
        """
        receipt = await self.get(organization_id, receipt_id)

        if receipt.status is GoodsReceiptStatus.POSTED:
            raise ConflictError(f"{receipt.receipt_number} is already posted")
        if receipt.status is GoodsReceiptStatus.CANCELLED:
            raise BusinessRuleError("A cancelled receipt cannot be posted.")

        posted_value = ZERO
        for line in receipt.lines:
            accepted = line.accepted_quantity
            if accepted <= 0:
                continue

            product = await self._product(organization_id, line.product_id)
            await self.stock.record(
                organization_id,
                product=product,
                warehouse=receipt.warehouse,
                kind=MovementKind.RECEIPT,
                quantity=accepted,
                unit_cost=line.unit_cost,
                movement_date=receipt.receipt_date,
                actor=actor,
                source_type="goods_receipt",
                source_id=receipt.id,
                reference=receipt.receipt_number,
            )
            posted_value += round_value(accepted * line.unit_cost)

            # Roll the PO line forward so partial delivery is visible.
            if line.purchase_order_line_id is not None:
                po_line = await self.session.get(PurchaseOrderLine, line.purchase_order_line_id)
                if po_line is not None:
                    po_line.received_quantity += accepted

        if posted_value > 0:
            inventory = await self.chart.resolve_system_account(
                organization_id, SystemAccount.INVENTORY
            )
            grni = await self.chart.resolve_system_account(
                organization_id, SystemAccount.GOODS_RECEIVED_NOT_INVOICED
            )
            journal = await self.journals.get_by_type(organization_id, JournalType.PURCHASE)
            if journal is None:
                raise BusinessRuleError("No purchase journal is configured.")

            entry = await self.posting.create_entry(
                organization_id,
                JournalEntryCreate(
                    journal_id=journal.id,
                    entry_date=receipt.receipt_date,
                    narration=f"Goods received {receipt.receipt_number} - {receipt.supplier.name}",
                    reference=receipt.supplier_reference or receipt.receipt_number,
                    post=True,
                    lines=[
                        JournalEntryLineInput(
                            account_id=inventory.id,
                            debit=posted_value,
                            description="Stock received",
                        ),
                        JournalEntryLineInput(
                            account_id=grni.id,
                            credit=posted_value,
                            description="Awaiting supplier invoice",
                        ),
                    ],
                ),
                actor,
                ctx,
                source_type="goods_receipt",
                source_id=receipt.id,
            )
            receipt.journal_entry_id = entry.id

        receipt.status = GoodsReceiptStatus.POSTED
        receipt.posted_at = dt.datetime.now(dt.UTC)
        receipt.total_cost = round_value(posted_value)

        await self._refresh_order_status(organization_id, receipt.purchase_order_id)
        await self.session.flush()

        await self.audit.record(
            AuditAction.GOODS_RECEIPT_POSTED,
            actor=actor,
            organization_id=organization_id,
            resource_type="goods_receipt",
            resource_id=receipt.id,
            summary=f"Posted {receipt.receipt_number} - stock in, {posted_value} accrued",
            **_audit_ctx(ctx),
        )
        log.info(
            "goods receipt posted",
            extra={"receipt": receipt.receipt_number, "value": str(posted_value)},
        )
        return receipt

    async def _refresh_order_status(
        self, organization_id: uuid.UUID, order_id: uuid.UUID | None
    ) -> None:
        """Advance the PO between partially-received and received."""
        if order_id is None:
            return
        order = (
            await self.session.execute(
                select(PurchaseOrder)
                .where(PurchaseOrder.id == order_id)
                .options(selectinload(PurchaseOrder.lines))
            )
        ).scalar_one_or_none()
        if order is None or order.status is PurchaseOrderStatus.CANCELLED:
            return

        if all(line.is_fully_received for line in order.lines):
            order.status = PurchaseOrderStatus.RECEIVED
        elif any(line.received_quantity > 0 for line in order.lines):
            order.status = PurchaseOrderStatus.PARTIALLY_RECEIVED

    async def cancel(
        self,
        organization_id: uuid.UUID,
        receipt_id: uuid.UUID,
        *,
        reason: str,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> GoodsReceipt:
        """Reverse the stock and the accrual.

        Refused once a bill references the receipt: the payable would then point at
        an accrual that no longer exists.
        """
        receipt = await self.get(organization_id, receipt_id)

        if receipt.status is GoodsReceiptStatus.CANCELLED:
            raise ConflictError(f"{receipt.receipt_number} is already cancelled")

        billed = (
            await self.session.execute(
                select(Bill.id).where(
                    Bill.goods_receipt_id == receipt.id, Bill.deleted_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if billed is not None:
            raise BusinessRuleError("A bill references this receipt. Cancel the bill first.")

        if receipt.status is GoodsReceiptStatus.POSTED:
            for line in receipt.lines:
                accepted = line.accepted_quantity
                if accepted <= 0:
                    continue
                product = await self._product(organization_id, line.product_id)
                await self.stock.record(
                    organization_id,
                    product=product,
                    warehouse=receipt.warehouse,
                    kind=MovementKind.REVERSAL,
                    quantity=accepted,
                    unit_cost=line.unit_cost,
                    movement_date=receipt.receipt_date,
                    actor=actor,
                    source_type="goods_receipt_cancellation",
                    source_id=receipt.id,
                    notes=reason,
                )
                if line.purchase_order_line_id is not None:
                    po_line = await self.session.get(PurchaseOrderLine, line.purchase_order_line_id)
                    if po_line is not None:
                        po_line.received_quantity = max(ZERO, po_line.received_quantity - accepted)

            if receipt.journal_entry_id is not None:
                reversal = await self.posting.reverse_entry(
                    organization_id,
                    receipt.journal_entry_id,
                    actor,
                    narration=f"Cancellation of {receipt.receipt_number}: {reason}",
                    ctx=ctx,
                )
                receipt.reversal_entry_id = reversal.id

        receipt.status = GoodsReceiptStatus.CANCELLED
        receipt.cancelled_at = dt.datetime.now(dt.UTC)
        await self.session.flush()

        await self.audit.record(
            AuditAction.GOODS_RECEIPT_CANCELLED,
            actor=actor,
            organization_id=organization_id,
            resource_type="goods_receipt",
            resource_id=receipt.id,
            summary=f"Cancelled {receipt.receipt_number}: {reason}",
            severity=AuditSeverity.WARNING,
            **_audit_ctx(ctx),
        )
        return receipt


# =============================================================================
# Bills
# =============================================================================
class BillService(PurchasingBase):
    async def get(self, organization_id: uuid.UUID, bill_id: uuid.UUID) -> Bill:
        bill = (
            await self.session.execute(
                select(Bill)
                .where(
                    Bill.id == bill_id,
                    Bill.organization_id == organization_id,
                    Bill.deleted_at.is_(None),
                )
                .options(selectinload(Bill.lines), selectinload(Bill.supplier))
            )
        ).scalar_one_or_none()
        if bill is None:
            raise NotFoundError("Bill")
        return bill

    async def paginate(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        supplier_id: uuid.UUID | None = None,
        status: str | None = None,
        overdue_only: bool = False,
    ) -> tuple[list[Bill], int]:
        clauses: list[Any] = [
            Bill.organization_id == organization_id,
            Bill.deleted_at.is_(None),
        ]
        if supplier_id is not None:
            clauses.append(Bill.supplier_id == supplier_id)
        if status is not None:
            clauses.append(Bill.status == status)
        if overdue_only:
            clauses.extend(
                [
                    Bill.due_date < await self._today(organization_id),
                    Bill.status.in_([BillStatus.POSTED, BillStatus.PARTIALLY_PAID]),
                    Bill.paid_amount < Bill.grand_total,
                ]
            )

        total = int(
            (
                await self.session.execute(select(func.count()).select_from(Bill).where(*clauses))
            ).scalar_one()
        )
        rows = (
            (
                await self.session.execute(
                    select(Bill)
                    .where(*clauses)
                    .options(selectinload(Bill.lines), selectinload(Bill.supplier))
                    .order_by(Bill.bill_date.desc())
                    .offset(params.offset)
                    .limit(params.limit)
                )
            )
            .scalars()
            .all()
        )
        return list(rows), total

    async def create(
        self,
        organization_id: uuid.UUID,
        data: BillCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Bill:
        supplier = await self._supplier(organization_id, data.supplier_id)

        if data.supplier_invoice_number:
            duplicate = (
                await self.session.execute(
                    select(Bill.bill_number).where(
                        Bill.organization_id == organization_id,
                        Bill.supplier_id == supplier.id,
                        Bill.supplier_invoice_number == data.supplier_invoice_number,
                        Bill.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                # The most expensive data-entry error in accounts payable: paying
                # the same invoice twice.
                raise ConflictError(
                    f"Invoice {data.supplier_invoice_number} from {supplier.name} is "
                    f"already entered as {duplicate}",
                    details={"existing_bill": duplicate},
                )

        treatment = await self._treatment(organization_id, supplier)
        computed = PurchaseLineBuilder.compute(data.lines, treatment=treatment)
        bill_date = data.bill_date or await self._today(organization_id)

        bill = Bill(
            organization_id=organization_id,
            bill_number=await self._next_number(organization_id, scope="bill", prefix="BILL"),
            supplier_invoice_number=data.supplier_invoice_number,
            supplier_id=supplier.id,
            purchase_order_id=data.purchase_order_id,
            goods_receipt_id=data.goods_receipt_id,
            bill_date=bill_date,
            due_date=data.due_date or supplier.due_date_for(bill_date),
            status=BillStatus.DRAFT,
            tax_treatment=treatment,
            currency=supplier.currency,
            supplier_gstin=supplier.gstin,
            notes=data.notes,
            terms=data.terms,
            created_by_id=actor.id,
        )
        bill.lines = [BillLine() for _ in computed]
        for index, ((source, totals), row) in enumerate(
            zip(computed, bill.lines, strict=True), start=1
        ):
            PurchaseLineBuilder.apply(row, source, totals, index)
            row.expense_account_id = source.expense_account_id
        PurchaseLineBuilder.apply_totals(
            bill, [t for _, t in computed], round_to_whole=data.round_to_whole
        )

        self.session.add(bill)
        await self.session.flush()

        await self.audit.record(
            AuditAction.BILL_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="bill",
            resource_id=bill.id,
            summary=f"Created bill {bill.bill_number} from {supplier.name} for {bill.grand_total}",
            **_audit_ctx(ctx),
        )

        if data.post:
            return await self.post(organization_id, bill.id, actor, ctx)
        return bill

    async def post(
        self,
        organization_id: uuid.UUID,
        bill_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Bill:
        """Recognise the payable and claim input GST.

        When the bill follows a goods receipt the net debit clears the GRNI accrual;
        otherwise it goes to inventory or expense directly. Getting this wrong
        double-counts the cost, which is why the receipt link decides it.
        """
        bill = await self.get(organization_id, bill_id)

        if bill.status.is_posted:
            raise ConflictError(f"Bill {bill.bill_number} is already posted")
        if bill.status is BillStatus.CANCELLED:
            raise BusinessRuleError("A cancelled bill cannot be posted.")
        if bill.grand_total <= 0:
            raise BusinessRuleError("A bill total must be positive.")

        journal = await self.journals.get_by_type(organization_id, JournalType.PURCHASE)
        if journal is None:
            raise BusinessRuleError("No purchase journal is configured.")

        payable = await self.chart.resolve_system_account(
            organization_id, SystemAccount.ACCOUNTS_PAYABLE
        )
        lines: list[JournalEntryLineInput] = []

        if bill.goods_receipt_id is not None:
            # Clear the accrual raised when the goods arrived.
            grni = await self.chart.resolve_system_account(
                organization_id, SystemAccount.GOODS_RECEIVED_NOT_INVOICED
            )
            lines.append(
                JournalEntryLineInput(
                    account_id=grni.id,
                    debit=bill.taxable_total,
                    description="Clearing goods received not invoiced",
                )
            )
        else:
            # No receipt: cost goes straight to inventory or expense, grouped by
            # account so a twenty-line bill does not produce twenty debits.
            by_account: dict[uuid.UUID, Decimal] = {}
            for line in bill.lines:
                account_id = await self._line_debit_account(organization_id, line)
                by_account[account_id] = by_account.get(account_id, ZERO) + line.taxable_amount
            for account_id, amount in by_account.items():
                if amount > 0:
                    lines.append(
                        JournalEntryLineInput(
                            account_id=account_id, debit=amount, description="Purchase"
                        )
                    )

        # Input GST is an asset - recoverable against output tax.
        if bill.tax_total > 0:
            gst_input = await self.chart.resolve_system_account(
                organization_id, SystemAccount.GST_INPUT
            )
            for label, amount in (
                ("CGST", bill.cgst_total),
                ("SGST", bill.sgst_total),
                ("IGST", bill.igst_total),
            ):
                if amount > 0:
                    lines.append(
                        JournalEntryLineInput(
                            account_id=gst_input.id, debit=amount, description=f"Input {label}"
                        )
                    )

        if bill.round_off != 0:
            rounding = await self.chart.resolve_system_account(
                organization_id, SystemAccount.ROUNDING
            )
            if bill.round_off > 0:
                lines.append(
                    JournalEntryLineInput(
                        account_id=rounding.id, debit=bill.round_off, description="Rounding"
                    )
                )
            else:
                lines.append(
                    JournalEntryLineInput(
                        account_id=rounding.id, credit=-bill.round_off, description="Rounding"
                    )
                )

        lines.append(
            JournalEntryLineInput(
                account_id=payable.id,
                credit=bill.grand_total,
                description=f"{bill.supplier.name} - {bill.bill_number}",
            )
        )

        entry = await self.posting.create_entry(
            organization_id,
            JournalEntryCreate(
                journal_id=journal.id,
                entry_date=bill.bill_date,
                narration=f"Bill {bill.bill_number} - {bill.supplier.name}",
                reference=bill.supplier_invoice_number or bill.bill_number,
                post=True,
                lines=lines,
            ),
            actor,
            ctx,
            source_type="bill",
            source_id=bill.id,
        )

        bill.status = BillStatus.POSTED
        bill.journal_entry_id = entry.id
        bill.posted_at = dt.datetime.now(dt.UTC)
        await self.session.flush()

        await self.audit.record(
            AuditAction.BILL_POSTED,
            actor=actor,
            organization_id=organization_id,
            resource_type="bill",
            resource_id=bill.id,
            summary=f"Posted bill {bill.bill_number} for {bill.grand_total}",
            context={"journal_entry": entry.entry_number},
            **_audit_ctx(ctx),
        )
        return bill

    async def _line_debit_account(self, organization_id: uuid.UUID, line: BillLine) -> uuid.UUID:
        """Where a bill line's cost lands.

        Explicit override, then the product's inventory account for stocked items,
        then a plain expense. A consumable is expensed on purchase by design - it is
        not held as inventory.
        """
        if line.expense_account_id is not None:
            return line.expense_account_id

        if line.product_id is not None:
            product = await self._product(organization_id, line.product_id)
            if product.kind is ProductKind.STOCKED:
                if product.inventory_account_id is not None:
                    return product.inventory_account_id
                account = await self.chart.resolve_system_account(
                    organization_id, SystemAccount.INVENTORY
                )
                return account.id

        account = await self.chart.resolve_system_account(
            organization_id, SystemAccount.COST_OF_GOODS_SOLD
        )
        return account.id

    async def cancel(
        self,
        organization_id: uuid.UUID,
        bill_id: uuid.UUID,
        *,
        reason: str,
        cancellation_date: dt.date | None = None,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Bill:
        bill = await self.get(organization_id, bill_id)

        if bill.status is BillStatus.CANCELLED:
            raise ConflictError(f"Bill {bill.bill_number} is already cancelled")
        if bill.paid_amount > 0:
            raise BusinessRuleError(
                "This bill has payments against it. Unallocate them first.",
                details={"paid_amount": str(bill.paid_amount)},
            )

        if bill.status.is_posted and bill.journal_entry_id is not None:
            reversal = await self.posting.reverse_entry(
                organization_id,
                bill.journal_entry_id,
                actor,
                reversal_date=cancellation_date,
                narration=f"Cancellation of {bill.bill_number}: {reason}",
                ctx=ctx,
            )
            bill.reversal_entry_id = reversal.id

        bill.status = BillStatus.CANCELLED
        bill.cancelled_at = dt.datetime.now(dt.UTC)
        await self.session.flush()

        await self.audit.record(
            AuditAction.BILL_CANCELLED,
            actor=actor,
            organization_id=organization_id,
            resource_type="bill",
            resource_id=bill.id,
            summary=f"Cancelled bill {bill.bill_number}: {reason}",
            severity=AuditSeverity.WARNING,
            **_audit_ctx(ctx),
        )
        return bill


# =============================================================================
# Supplier payments
# =============================================================================
class SupplierPaymentService(PurchasingBase):
    async def get(self, organization_id: uuid.UUID, payment_id: uuid.UUID) -> SupplierPayment:
        payment = (
            await self.session.execute(
                select(SupplierPayment)
                .where(
                    SupplierPayment.id == payment_id,
                    SupplierPayment.organization_id == organization_id,
                    SupplierPayment.deleted_at.is_(None),
                )
                .options(
                    selectinload(SupplierPayment.allocations).selectinload(
                        SupplierPaymentAllocation.bill
                    ),
                    selectinload(SupplierPayment.supplier),
                )
            )
        ).scalar_one_or_none()
        if payment is None:
            raise NotFoundError("Supplier payment")
        return payment

    async def paginate(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        supplier_id: uuid.UUID | None = None,
    ) -> tuple[list[SupplierPayment], int]:
        clauses: list[Any] = [
            SupplierPayment.organization_id == organization_id,
            SupplierPayment.deleted_at.is_(None),
        ]
        if supplier_id is not None:
            clauses.append(SupplierPayment.supplier_id == supplier_id)

        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(SupplierPayment).where(*clauses)
                )
            ).scalar_one()
        )
        rows = (
            (
                await self.session.execute(
                    select(SupplierPayment)
                    .where(*clauses)
                    .options(
                        selectinload(SupplierPayment.allocations).selectinload(
                            SupplierPaymentAllocation.bill
                        ),
                        selectinload(SupplierPayment.supplier),
                    )
                    .order_by(SupplierPayment.payment_date.desc())
                    .offset(params.offset)
                    .limit(params.limit)
                )
            )
            .scalars()
            .all()
        )
        return list(rows), total

    async def pay(
        self,
        organization_id: uuid.UUID,
        data: SupplierPaymentCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> SupplierPayment:
        """Record a payment out: debit payables, credit bank or cash."""
        supplier = await self._supplier(organization_id, data.supplier_id)

        if data.source_account_id is not None:
            source = await self.chart.get_account(organization_id, data.source_account_id)
            if not source.is_postable:
                raise ValidationError("The source account must be postable")
        else:
            key = SystemAccount.CASH if data.method == "cash" else SystemAccount.BANK
            source = await self.chart.resolve_system_account(organization_id, key)

        payment = SupplierPayment(
            organization_id=organization_id,
            payment_number=await self._next_number(
                organization_id, scope="supplier_payment", prefix="PAY"
            ),
            supplier_id=supplier.id,
            payment_date=data.payment_date or await self._today(organization_id),
            amount=data.amount,
            unallocated_amount=data.amount,
            method=data.method,
            reference=data.reference,
            currency=supplier.currency,
            notes=data.notes,
            source_account_id=source.id,
            created_by_id=actor.id,
        )
        self.session.add(payment)
        await self.session.flush()

        payable = await self.chart.resolve_system_account(
            organization_id, SystemAccount.ACCOUNTS_PAYABLE
        )
        journal_type = JournalType.CASH if data.method == "cash" else JournalType.BANK
        journal = await self.journals.get_by_type(organization_id, journal_type)
        if journal is None:
            raise BusinessRuleError(f"No {journal_type} journal is configured.")

        entry = await self.posting.create_entry(
            organization_id,
            JournalEntryCreate(
                journal_id=journal.id,
                entry_date=payment.payment_date,
                narration=f"Payment {payment.payment_number} - {supplier.name}",
                reference=data.reference or payment.payment_number,
                post=True,
                lines=[
                    JournalEntryLineInput(
                        account_id=payable.id, debit=payment.amount, description=supplier.name
                    ),
                    JournalEntryLineInput(
                        account_id=source.id, credit=payment.amount, description="Payment made"
                    ),
                ],
            ),
            actor,
            ctx,
            source_type="supplier_payment",
            source_id=payment.id,
        )
        payment.journal_entry_id = entry.id

        if data.allocations:
            await self._apply(
                organization_id,
                payment,
                [(a.bill_id, a.amount) for a in data.allocations],
                actor,
                ctx,
            )

        await self.session.flush()

        await self.audit.record(
            AuditAction.SUPPLIER_PAYMENT_MADE,
            actor=actor,
            organization_id=organization_id,
            resource_type="supplier_payment",
            resource_id=payment.id,
            summary=f"Paid {payment.amount} to {supplier.name} ({payment.payment_number})",
            **_audit_ctx(ctx),
        )
        return payment

    async def allocate(
        self,
        organization_id: uuid.UUID,
        payment_id: uuid.UUID,
        allocations: list[tuple[uuid.UUID, Decimal]],
        actor: User,
        ctx: RequestContext | None = None,
    ) -> SupplierPayment:
        payment = await self.get(organization_id, payment_id)
        await self._apply(organization_id, payment, allocations, actor, ctx)
        await self.session.flush()
        return payment

    async def _apply(
        self,
        organization_id: uuid.UUID,
        payment: SupplierPayment,
        allocations: list[tuple[uuid.UUID, Decimal]],
        actor: User,
        ctx: RequestContext | None,
    ) -> None:
        """Attach amounts to bills. No further posting - the payment already cleared
        payables in aggregate."""
        requested = sum((amount for _, amount in allocations), ZERO)
        if requested > payment.unallocated_amount:
            raise BusinessRuleError(
                f"Only {payment.unallocated_amount} of this payment is unallocated, "
                f"but {requested} was requested."
            )

        existing_rows = (
            (
                await self.session.execute(
                    select(SupplierPaymentAllocation).where(
                        SupplierPaymentAllocation.payment_id == payment.id
                    )
                )
            )
            .scalars()
            .all()
        )
        existing = {row.bill_id: row for row in existing_rows}

        bill_service = BillService(self.session)
        for bill_id, amount in allocations:
            bill = await bill_service.get(organization_id, bill_id)
            if bill.supplier_id != payment.supplier_id:
                raise ValidationError(
                    "That bill belongs to a different supplier",
                    details={"bill_number": bill.bill_number},
                )
            if not bill.status.is_posted:
                raise BusinessRuleError(
                    f"Bill {bill.bill_number} is {bill.status} - only a posted bill can be paid."
                )
            if amount > bill.outstanding:
                raise BusinessRuleError(
                    f"Bill {bill.bill_number} has only {bill.outstanding} outstanding, "
                    f"but {amount} was allocated."
                )

            if bill_id in existing:
                existing[bill_id].amount += amount
            else:
                self.session.add(
                    SupplierPaymentAllocation(payment_id=payment.id, bill_id=bill.id, amount=amount)
                )

            bill.paid_amount += amount
            bill.status = BillStatus.PAID if bill.is_fully_paid else BillStatus.PARTIALLY_PAID
            payment.unallocated_amount -= amount

        await self.session.flush()

        await self.audit.record(
            AuditAction.SUPPLIER_PAYMENT_ALLOCATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="supplier_payment",
            resource_id=payment.id,
            summary=f"Allocated {requested} of {payment.payment_number}",
            **_audit_ctx(ctx),
        )
