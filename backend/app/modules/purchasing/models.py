"""Purchasing and inventory - suppliers, products, stock, POs, receipts, bills.

**Stock is a ledger, not a counter.** ``StockMovement`` is append-only: every
receipt, issue, adjustment, and transfer is a row that is never updated or deleted.
``StockLevel`` holds the derived position (quantity on hand and average cost) so
that reads are cheap, but it is reconstructible by replaying movements - and a test
asserts it matches. A bare mutable counter would make "why is stock wrong?"
unanswerable.

**The document chain is purchase order → goods receipt → bill → payment**, and as
with sales each link is optional. A shop buying cash-and-carry records a bill with
no PO; a business with approval workflow raises the PO first.

**Where the ledger gets written.** Two distinct events, and conflating them is the
classic error:

* **Goods receipt** moves stock and recognises a liability for goods received but
  not yet invoiced - debit Inventory, credit *Goods Received Not Invoiced*.
* **Bill** replaces that accrual with the real payable and books input GST -
  debit GRNI, debit GST Input, credit Accounts Payable.

Keeping them separate is what lets stock arrive on the 30th and the invoice arrive
on the 3rd without either month being wrong.
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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, OrgScopedMixin, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import (
    ZERO,
    CurrencyCode,
    LedgerDate,
    Money,
    Quantity,
    Rate,
    enum_column,
)
from app.modules.tax.gst import TaxTreatment

if TYPE_CHECKING:
    from app.modules.accounting.models import Account, JournalEntry
    from app.modules.organizations.models import Organization


# =============================================================================
# Enumerations
# =============================================================================
class ProductKind(StrEnum):
    """Whether a product carries stock.

    A service line (consulting, delivery charge) appears on documents and is taxed
    identically, but has no quantity on hand and generates no stock movement.
    Modelling it as a product with a flag rather than a separate entity keeps
    document lines uniform.
    """

    STOCKED = "stocked"
    SERVICE = "service"
    #: Consumed internally and expensed on receipt rather than held as inventory.
    CONSUMABLE = "consumable"

    @property
    def tracks_stock(self) -> bool:
        return self is ProductKind.STOCKED


class MovementKind(StrEnum):
    """Why stock moved. Drives both the sign and the ledger treatment."""

    RECEIPT = "receipt"
    ISSUE = "issue"
    #: Stock found: a stock-take surplus, or opening stock entered by hand.
    #:
    #: Only ever an *increase*. A stock-take shortfall is an ``ISSUE`` - it consumes
    #: stock and costs money exactly as a sale does, and giving it its own inbound-looking
    #: kind is how a positive adjustment silently became a write-off. What separates a
    #: shortfall from a sale is ``source_type="stock_adjustment"`` on the movement, not
    #: the kind.
    ADJUSTMENT = "adjustment"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    #: Sent back to the supplier.
    RETURN_OUT = "return_out"
    #: Customer sent it back.
    RETURN_IN = "return_in"
    #: Undoes an earlier movement.
    REVERSAL = "reversal"

    @property
    def increases_stock(self) -> bool:
        return self in (
            MovementKind.RECEIPT,
            MovementKind.ADJUSTMENT,
            MovementKind.TRANSFER_IN,
            MovementKind.RETURN_IN,
        )


class PurchaseOrderStatus(StrEnum):
    DRAFT = "draft"
    #: Awaiting internal approval.
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PARTIALLY_RECEIVED = "partially_received"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class GoodsReceiptStatus(StrEnum):
    DRAFT = "draft"
    #: Stock moved and the accrual posted.
    POSTED = "posted"
    CANCELLED = "cancelled"


class BillStatus(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    CANCELLED = "cancelled"

    @property
    def is_posted(self) -> bool:
        return self in (BillStatus.POSTED, BillStatus.PARTIALLY_PAID, BillStatus.PAID)

    @property
    def is_editable(self) -> bool:
        return self is BillStatus.DRAFT


# =============================================================================
# Suppliers
# =============================================================================
class Supplier(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, SoftDeleteMixin):
    """A party you buy from and owe money to."""

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False, index=True)
    legal_name: Mapped[str | None] = mapped_column(String(250))

    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    contact_person: Mapped[str | None] = mapped_column(String(200))

    gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    pan: Mapped[str | None] = mapped_column(String(10))
    #: Decides CGST/SGST versus IGST on every bill, so derived once from the GSTIN.
    state_code: Mapped[str | None] = mapped_column(String(2))

    address_line1: Mapped[str | None] = mapped_column(String(255))
    address_line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="IN")

    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    currency: Mapped[CurrencyCode] = mapped_column(nullable=False, default="INR")

    #: Bank details for making payment.
    bank_account_name: Mapped[str | None] = mapped_column(String(200))
    bank_account_number: Mapped[str | None] = mapped_column(String(50))
    bank_ifsc: Mapped[str | None] = mapped_column(String(20))

    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped[Organization] = relationship()

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_supplier_org_code"),
        Index("ix_supplier_org_name", "organization_id", "name"),
        CheckConstraint("payment_terms_days >= 0", name="terms_non_negative"),
    )

    def due_date_for(self, bill_date: dt.date) -> dt.date:
        return bill_date + dt.timedelta(days=self.payment_terms_days)


# =============================================================================
# Products
# =============================================================================
class Product(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, SoftDeleteMixin):
    """An item master entry - the thing that appears on a document line.

    Shared by sales and purchasing rather than duplicated: a shop buys and sells the
    same widget, and two records would let its HSN code, tax rate, or unit disagree
    depending on which document you were looking at.
    """

    sku: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)

    #: EAN/UPC. Indexed and unique per organization so a barcode scan is a single
    #: point lookup - the whole point of supporting them.
    barcode: Mapped[str | None] = mapped_column(String(50))

    kind: Mapped[ProductKind] = mapped_column(
        enum_column(ProductKind, length=20), nullable=False, default=ProductKind.STOCKED
    )

    #: Harmonised System / Service Accounting Code, required on a GST invoice.
    hsn_code: Mapped[str | None] = mapped_column(String(10))
    unit: Mapped[str] = mapped_column(String(20), nullable=False, default="NOS")
    tax_rate: Mapped[Rate] = mapped_column(nullable=False, default=ZERO)

    #: Defaults for document lines. Overridable per line, because the price you
    #: actually charge is a negotiation, not a constant.
    sale_price: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    purchase_price: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    #: Below this, the reorder report flags it.
    reorder_level: Mapped[Quantity] = mapped_column(nullable=False, default=ZERO)

    # --- Ledger account overrides ---
    #: Where this product's revenue, cost, and stock value post. Null falls back to
    #: the organization's system accounts, which is the common case.
    revenue_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="RESTRICT")
    )
    inventory_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="RESTRICT")
    )
    cogs_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="RESTRICT")
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped[Organization] = relationship()
    revenue_account: Mapped[Account | None] = relationship(foreign_keys=[revenue_account_id])
    inventory_account: Mapped[Account | None] = relationship(foreign_keys=[inventory_account_id])
    cogs_account: Mapped[Account | None] = relationship(foreign_keys=[cogs_account_id])
    stock_levels: Mapped[list[StockLevel]] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "sku", name="uq_product_org_sku"),
        # Partial unique: many products have no barcode, and NULLs must not collide.
        Index(
            "uq_product_org_barcode",
            "organization_id",
            "barcode",
            unique=True,
            postgresql_where=text("barcode IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index("ix_product_org_active", "organization_id", "is_active"),
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 100", name="tax_rate_range"),
        CheckConstraint("sale_price >= 0 AND purchase_price >= 0", name="prices_non_negative"),
        CheckConstraint("reorder_level >= 0", name="reorder_non_negative"),
    )

    @property
    def tracks_stock(self) -> bool:
        return self.kind.tracks_stock


# =============================================================================
# Warehouses and stock
# =============================================================================
class Warehouse(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, SoftDeleteMixin):
    """A place stock sits. One is created by default."""

    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    address_line1: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str | None] = mapped_column(String(20))

    #: Where receipts and issues go when no warehouse is named. Exactly one per
    #: organization, so single-location businesses never think about warehouses.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped[Organization] = relationship()

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_warehouse_org_code"),
        Index(
            "uq_warehouse_single_default",
            "organization_id",
            unique=True,
            postgresql_where=text("is_default IS TRUE AND deleted_at IS NULL"),
        ),
    )


class StockLevel(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """Derived stock position for one product in one warehouse.

    Denormalised on purpose: "how many do we have?" is the most-asked question in
    the module, and replaying the movement ledger per read would make a stock report
    O(movements). The authoritative history is :class:`StockMovement`, and
    ``test_stock_level_matches_replayed_movements`` asserts the two agree.

    ``average_cost`` is carried at 6dp - see
    :mod:`app.modules.purchasing.valuation` for why the extra precision matters.
    """

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product.id", ondelete="CASCADE"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouse.id", ondelete="CASCADE"), nullable=False, index=True
    )

    quantity: Mapped[Quantity] = mapped_column(nullable=False, default=ZERO)
    #: Total cost of the stock on hand. **Authoritative** - it changes only by
    #: amounts posted to the ledger, so it can never drift from the Inventory
    #: account. See :class:`app.modules.purchasing.valuation.ValuationState`.
    stock_value: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    #: Derived from ``stock_value / quantity`` and stored for querying and display.
    #: Never the basis of a calculation - the value is.
    average_cost: Mapped[Decimal] = mapped_column(nullable=False, default=ZERO)

    #: Committed to sales orders but not yet shipped. Informational in this module;
    #: allocation logic belongs to a later stage.
    reserved_quantity: Mapped[Quantity] = mapped_column(nullable=False, default=ZERO)

    last_movement_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship()
    product: Mapped[Product] = relationship(back_populates="stock_levels")
    warehouse: Mapped[Warehouse] = relationship()

    __table_args__ = (
        UniqueConstraint("product_id", "warehouse_id", name="uq_stock_product_warehouse"),
        # Negative stock makes the weighted average undefined; the service refuses
        # to create it and the database backs that up.
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
        CheckConstraint("average_cost >= 0", name="cost_non_negative"),
        CheckConstraint("stock_value >= 0", name="value_non_negative"),
        CheckConstraint("reserved_quantity >= 0", name="reserved_non_negative"),
    )

    @property
    def total_value(self) -> Decimal:
        return self.stock_value

    @property
    def available_quantity(self) -> Decimal:
        return self.quantity - self.reserved_quantity

    @property
    def needs_reorder(self) -> bool:
        return self.quantity <= self.product.reorder_level


class StockMovement(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """One append-only stock event.

    No :class:`~app.db.base.TimestampMixin` ``updated_at`` and no soft delete: a
    movement is history. A mistake is corrected by a compensating ``REVERSAL``
    movement, exactly as the ledger corrects a posted entry.

    ``quantity`` is stored **signed** here - unlike journal lines, which use
    separate debit/credit columns. The reason is the opposite of the one there: a
    stock report wants a running total, which a signed column gives as a plain
    ``SUM``, whereas debit/credit would need a conditional aggregate. Journals have
    the reverse requirement.
    """

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), index=True
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouse.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    kind: Mapped[MovementKind] = mapped_column(
        enum_column(MovementKind, length=20), nullable=False, index=True
    )
    movement_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)

    #: Signed: positive in, negative out.
    quantity: Mapped[Quantity] = mapped_column(nullable=False)
    #: Cost per unit for this movement. For an issue this is the average at the
    #: time, captured so COGS stays explainable after the average moves on.
    unit_cost: Mapped[Decimal] = mapped_column(nullable=False, default=ZERO)
    #: ``abs(quantity) * unit_cost``. Stored so a valuation report needs no
    #: multiplication across millions of rows.
    total_cost: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    #: The position after this movement, for audit. Makes "when did stock go wrong?"
    #: answerable by scanning one column rather than replaying.
    balance_after: Mapped[Quantity] = mapped_column(nullable=False, default=ZERO)
    average_cost_after: Mapped[Decimal] = mapped_column(nullable=False, default=ZERO)

    # --- Provenance ---
    source_type: Mapped[str | None] = mapped_column(String(50), index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(String(500))

    #: The ledger entry this movement caused, when it had one. A transfer between
    #: warehouses moves no value and so posts nothing.
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="RESTRICT")
    )
    #: On a REVERSAL: the movement being undone.
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("stock_movement.id", ondelete="RESTRICT")
    )

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship()
    product: Mapped[Product] = relationship()
    warehouse: Mapped[Warehouse] = relationship()
    journal_entry: Mapped[JournalEntry | None] = relationship()

    __table_args__ = (
        CheckConstraint("quantity <> 0", name="quantity_non_zero"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_non_negative"),
        CheckConstraint("balance_after >= 0", name="balance_non_negative"),
        # The stock-card query: one product in one warehouse, chronological.
        Index("ix_movement_product_warehouse_date", "product_id", "warehouse_id", "movement_date"),
        Index("ix_movement_org_date", "organization_id", "movement_date"),
    )

    @property
    def is_inbound(self) -> bool:
        return self.quantity > 0


# =============================================================================
# Shared purchase-document structure
# =============================================================================
class PurchaseLineMixin:
    """Columns common to purchase-document lines.

    Mirrors :class:`app.modules.sales.models.SalesLineMixin` and computes tax with
    the same engine - a purchase and a sale of the same item at the same price must
    produce identical tax, or the books cannot reconcile.
    """

    line_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    hsn_code: Mapped[str | None] = mapped_column(String(10))

    quantity: Mapped[Quantity] = mapped_column(nullable=False)
    unit: Mapped[str | None] = mapped_column(String(20))
    unit_price: Mapped[Money] = mapped_column(nullable=False)

    discount_percent: Mapped[Rate] = mapped_column(nullable=False, default=ZERO)
    discount_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    tax_rate: Mapped[Rate] = mapped_column(nullable=False, default=ZERO)
    cgst_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    sgst_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    igst_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    gross_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    taxable_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    tax_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    line_total: Mapped[Money] = mapped_column(nullable=False, default=ZERO)


def _purchase_line_constraints(prefix: str) -> tuple[CheckConstraint, ...]:
    return (
        CheckConstraint("quantity > 0", name=f"{prefix}_quantity_positive"),
        CheckConstraint("unit_price >= 0", name=f"{prefix}_price_non_negative"),
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 100", name=f"{prefix}_tax_rate_range"),
        CheckConstraint(
            "cgst_amount + sgst_amount + igst_amount = tax_amount",
            name=f"{prefix}_tax_split_reconciles",
        ),
        CheckConstraint(
            "taxable_amount + tax_amount = line_total", name=f"{prefix}_total_reconciles"
        ),
    )


class PurchaseDocumentMixin:
    """Header totals shared by purchase orders and bills."""

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
        enum_column(TaxTreatment, length=20), nullable=False, default=TaxTreatment.INTRA_STATE
    )

    notes: Mapped[str | None] = mapped_column(Text)
    terms: Mapped[str | None] = mapped_column(Text)


def _purchase_document_constraints(prefix: str) -> tuple[CheckConstraint, ...]:
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
# Purchase orders
# =============================================================================
class PurchaseOrder(
    Base,
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    PurchaseDocumentMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    order_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("warehouse.id", ondelete="RESTRICT")
    )

    order_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)
    expected_date: Mapped[LedgerDate | None] = mapped_column(default=None)

    status: Mapped[PurchaseOrderStatus] = mapped_column(
        enum_column(PurchaseOrderStatus, length=25),
        nullable=False,
        default=PurchaseOrderStatus.DRAFT,
        index=True,
    )
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship()
    supplier: Mapped[Supplier] = relationship()
    warehouse: Mapped[Warehouse | None] = relationship()
    lines: Mapped[list[PurchaseOrderLine]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PurchaseOrderLine.line_number",
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "order_number", name="uq_po_org_number"),
        *_purchase_document_constraints("po"),
    )


class PurchaseOrderLine(Base, UUIDPrimaryKeyMixin, PurchaseLineMixin, TimestampMixin):
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_order.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product.id", ondelete="RESTRICT"), index=True
    )

    #: How much has arrived. Maintained by goods receipts so partial delivery is
    #: visible without summing receipt lines on every read.
    received_quantity: Mapped[Quantity] = mapped_column(nullable=False, default=ZERO)

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")
    product: Mapped[Product | None] = relationship()

    __table_args__ = (
        CheckConstraint("received_quantity >= 0", name="po_line_received_non_negative"),
        *_purchase_line_constraints("po_line"),
    )

    @property
    def outstanding_quantity(self) -> Decimal:
        return self.quantity - self.received_quantity

    @property
    def is_fully_received(self) -> bool:
        return self.received_quantity >= self.quantity


# =============================================================================
# Goods receipts
# =============================================================================
class GoodsReceipt(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, SoftDeleteMixin):
    """Goods physically arriving.

    Deliberately separate from the bill. Stock arrives when it arrives; the
    supplier's invoice turns up whenever it turns up. Posting the receipt against a
    *Goods Received Not Invoiced* accrual is what lets both months be right.
    """

    receipt_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_order.id", ondelete="SET NULL"), index=True
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("warehouse.id", ondelete="RESTRICT"), nullable=False
    )

    receipt_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)
    #: The supplier's delivery-note number.
    supplier_reference: Mapped[str | None] = mapped_column(String(100))

    status: Mapped[GoodsReceiptStatus] = mapped_column(
        enum_column(GoodsReceiptStatus, length=20),
        nullable=False,
        default=GoodsReceiptStatus.DRAFT,
        index=True,
    )

    #: Value of the goods received, at receipt cost. Not a tax-inclusive figure -
    #: tax is not recoverable until the bill arrives.
    total_cost: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="RESTRICT")
    )
    reversal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="RESTRICT")
    )
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship()
    supplier: Mapped[Supplier] = relationship()
    purchase_order: Mapped[PurchaseOrder | None] = relationship()
    warehouse: Mapped[Warehouse] = relationship()
    journal_entry: Mapped[JournalEntry | None] = relationship(foreign_keys=[journal_entry_id])
    reversal_entry: Mapped[JournalEntry | None] = relationship(foreign_keys=[reversal_entry_id])
    lines: Mapped[list[GoodsReceiptLine]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="GoodsReceiptLine.line_number",
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "receipt_number", name="uq_grn_org_number"),
        CheckConstraint("total_cost >= 0", name="grn_cost_non_negative"),
        CheckConstraint(
            "(status = 'draft' AND journal_entry_id IS NULL) OR status <> 'draft'",
            name="grn_draft_has_no_entry",
        ),
    )


class GoodsReceiptLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A quantity of one product arriving.

    No tax columns: a receipt records goods and cost, not tax. Input GST becomes
    claimable on the *bill*, and putting it here too would double-count it.
    """

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goods_receipt.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("product.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purchase_order_line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_order_line.id", ondelete="SET NULL")
    )

    line_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    quantity: Mapped[Quantity] = mapped_column(nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(nullable=False, default=ZERO)
    total_cost: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    #: Arrived damaged or short. Recorded rather than silently reducing the
    #: quantity, so a supplier claim has evidence.
    rejected_quantity: Mapped[Quantity] = mapped_column(nullable=False, default=ZERO)
    notes: Mapped[str | None] = mapped_column(String(500))

    receipt: Mapped[GoodsReceipt] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship()
    purchase_order_line: Mapped[PurchaseOrderLine | None] = relationship()

    __table_args__ = (
        CheckConstraint("quantity > 0", name="grn_line_quantity_positive"),
        CheckConstraint("unit_cost >= 0", name="grn_line_cost_non_negative"),
        CheckConstraint(
            "rejected_quantity >= 0 AND rejected_quantity <= quantity",
            name="grn_line_rejected_within_quantity",
        ),
    )

    @property
    def accepted_quantity(self) -> Decimal:
        return self.quantity - self.rejected_quantity


# =============================================================================
# Bills (purchase invoices)
# =============================================================================
class Bill(
    Base,
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    PurchaseDocumentMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    """A supplier's invoice. The document that creates the payable and the input-GST
    claim."""

    bill_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    #: The supplier's own invoice number - what they will quote when chasing payment.
    supplier_invoice_number: Mapped[str | None] = mapped_column(String(100), index=True)

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_order.id", ondelete="SET NULL")
    )
    goods_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("goods_receipt.id", ondelete="SET NULL")
    )

    bill_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)
    due_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)

    status: Mapped[BillStatus] = mapped_column(
        enum_column(BillStatus, length=25),
        nullable=False,
        default=BillStatus.DRAFT,
        index=True,
    )
    paid_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    supplier_gstin: Mapped[str | None] = mapped_column(String(15))

    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="RESTRICT")
    )
    reversal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="RESTRICT")
    )
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship()
    supplier: Mapped[Supplier] = relationship()
    purchase_order: Mapped[PurchaseOrder | None] = relationship()
    goods_receipt: Mapped[GoodsReceipt | None] = relationship()
    journal_entry: Mapped[JournalEntry | None] = relationship(foreign_keys=[journal_entry_id])
    reversal_entry: Mapped[JournalEntry | None] = relationship(foreign_keys=[reversal_entry_id])
    lines: Mapped[list[BillLine]] = relationship(
        back_populates="bill",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="BillLine.line_number",
    )
    allocations: Mapped[list[SupplierPaymentAllocation]] = relationship(
        back_populates="bill", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "bill_number", name="uq_bill_org_number"),
        # The same supplier invoice must not be entered twice - the single most
        # common and most expensive data-entry error in accounts payable.
        Index(
            "uq_bill_supplier_invoice",
            "organization_id",
            "supplier_id",
            "supplier_invoice_number",
            unique=True,
            postgresql_where=text("supplier_invoice_number IS NOT NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint("paid_amount >= 0", name="bill_paid_non_negative"),
        CheckConstraint("paid_amount <= grand_total", name="bill_paid_within_total"),
        CheckConstraint("due_date >= bill_date", name="bill_due_after_issue"),
        *_purchase_document_constraints("bill"),
        Index("ix_bill_org_status_due", "organization_id", "status", "due_date"),
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


class BillLine(Base, UUIDPrimaryKeyMixin, PurchaseLineMixin, TimestampMixin):
    bill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bill.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product.id", ondelete="RESTRICT"), index=True
    )
    #: Which account the cost lands in. For a stocked product this is inventory; for
    #: a service or consumable it is an expense.
    expense_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="RESTRICT")
    )

    bill: Mapped[Bill] = relationship(back_populates="lines")
    product: Mapped[Product | None] = relationship()

    __table_args__ = _purchase_line_constraints("bill_line")


# =============================================================================
# Supplier payments
# =============================================================================
class SupplierPayment(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, SoftDeleteMixin):
    """Money paid out. The mirror of a customer receipt."""

    payment_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    payment_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)
    amount: Mapped[Money] = mapped_column(nullable=False)
    unallocated_amount: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    #: Reuses the sales-side vocabulary rather than defining a parallel one: a
    #: cheque is a cheque whichever direction the money moves.
    method: Mapped[str] = mapped_column(String(20), nullable=False, default="bank_transfer")
    reference: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[CurrencyCode] = mapped_column(nullable=False, default="INR")
    notes: Mapped[str | None] = mapped_column(Text)

    #: The account the money left.
    source_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="RESTRICT")
    )
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="RESTRICT")
    )

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship()
    supplier: Mapped[Supplier] = relationship()
    allocations: Mapped[list[SupplierPaymentAllocation]] = relationship(
        back_populates="payment", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "payment_number", name="uq_supplier_payment_org_number"
        ),
        CheckConstraint("amount > 0", name="sp_amount_positive"),
        CheckConstraint(
            "unallocated_amount >= 0 AND unallocated_amount <= amount",
            name="sp_unallocated_within_amount",
        ),
    )

    @property
    def allocated_amount(self) -> Decimal:
        return self.amount - self.unallocated_amount


class SupplierPaymentAllocation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_payment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bill.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Money] = mapped_column(nullable=False)

    payment: Mapped[SupplierPayment] = relationship(back_populates="allocations")
    bill: Mapped[Bill] = relationship(back_populates="allocations")

    __table_args__ = (
        UniqueConstraint("payment_id", "bill_id", name="uq_sp_allocation_payment_bill"),
        CheckConstraint("amount > 0", name="sp_allocation_amount_positive"),
    )
