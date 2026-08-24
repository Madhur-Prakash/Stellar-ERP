"""Purchasing and inventory API contracts."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from app.core.schemas import BaseSchema, Email, ResponseSchema, TimestampedSchema
from app.modules.purchasing.models import (
    BillStatus,
    GoodsReceiptStatus,
    MovementKind,
    ProductKind,
    PurchaseOrderStatus,
)
from app.modules.sales.schemas import Gstin, PartyName
from app.modules.tax.gst import TaxTreatment

Code = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30, to_upper=True)
]
Sku = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Barcode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=50)]
Amount = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]
PositiveAmount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
Qty = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
Percent = Annotated[Decimal, Field(ge=0, le=100, max_digits=9, decimal_places=4)]
Cost = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)]


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------
class SupplierCreate(BaseSchema):
    code: Code | None = None
    name: PartyName
    legal_name: str | None = Field(default=None, max_length=250)
    email: Email | None = None
    phone: str | None = Field(default=None, max_length=32)
    contact_person: str | None = Field(default=None, max_length=200)
    gstin: Gstin | None = None
    pan: str | None = Field(default=None, max_length=10)
    address_line1: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    country: Annotated[str, StringConstraints(to_upper=True, min_length=2, max_length=2)] = "IN"
    payment_terms_days: int = Field(default=30, ge=0, le=365)
    bank_account_name: str | None = Field(default=None, max_length=200)
    bank_account_number: str | None = Field(default=None, max_length=50)
    bank_ifsc: str | None = Field(default=None, max_length=20)
    notes: str | None = None


class SupplierUpdate(BaseSchema):
    name: PartyName | None = None
    email: Email | None = None
    phone: str | None = Field(default=None, max_length=32)
    contact_person: str | None = Field(default=None, max_length=200)
    gstin: Gstin | None = None
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    bank_account_number: str | None = Field(default=None, max_length=50)
    bank_ifsc: str | None = Field(default=None, max_length=20)
    notes: str | None = None
    is_active: bool | None = None


class SupplierRead(TimestampedSchema):
    id: uuid.UUID
    code: str
    name: str
    legal_name: str | None
    email: str | None
    phone: str | None
    contact_person: str | None
    gstin: str | None
    state_code: str | None
    city: str | None
    state: str | None
    country: str
    payment_terms_days: int
    currency: str
    bank_account_number: str | None
    bank_ifsc: str | None
    notes: str | None
    is_active: bool


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
class ProductCreate(BaseSchema):
    sku: Sku | None = None
    name: PartyName
    description: str | None = None
    barcode: Barcode | None = None
    kind: ProductKind = ProductKind.STOCKED
    hsn_code: str | None = Field(default=None, max_length=10)
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] = "NOS"
    tax_rate: Percent = Decimal("0")
    sale_price: Amount = Decimal("0")
    purchase_price: Amount = Decimal("0")
    reorder_level: Amount = Decimal("0")
    revenue_account_id: uuid.UUID | None = None
    inventory_account_id: uuid.UUID | None = None
    cogs_account_id: uuid.UUID | None = None


class ProductUpdate(BaseSchema):
    name: PartyName | None = None
    description: str | None = None
    barcode: Barcode | None = None
    hsn_code: str | None = Field(default=None, max_length=10)
    unit: str | None = Field(default=None, max_length=20)
    tax_rate: Percent | None = None
    sale_price: Amount | None = None
    purchase_price: Amount | None = None
    reorder_level: Amount | None = None
    revenue_account_id: uuid.UUID | None = None
    inventory_account_id: uuid.UUID | None = None
    cogs_account_id: uuid.UUID | None = None
    is_active: bool | None = None


class ProductRead(TimestampedSchema):
    id: uuid.UUID
    sku: str
    name: str
    description: str | None
    barcode: str | None
    kind: ProductKind
    hsn_code: str | None
    unit: str
    tax_rate: Decimal
    sale_price: Decimal
    purchase_price: Decimal
    reorder_level: Decimal
    is_active: bool
    tracks_stock: bool


class ProductWithStock(ProductRead):
    """A product plus its aggregate position across all warehouses."""

    quantity_on_hand: Decimal
    stock_value: Decimal
    needs_reorder: bool


# ---------------------------------------------------------------------------
# Warehouses and stock
# ---------------------------------------------------------------------------
class WarehouseCreate(BaseSchema):
    code: Code
    name: PartyName
    address_line1: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    is_default: bool = False


class WarehouseRead(TimestampedSchema):
    id: uuid.UUID
    code: str
    name: str
    city: str | None
    state: str | None
    is_default: bool
    is_active: bool


class StockLevelRead(ResponseSchema):
    product_id: uuid.UUID
    product_sku: str
    product_name: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    quantity: Decimal
    reserved_quantity: Decimal
    available_quantity: Decimal
    average_cost: Decimal
    total_value: Decimal
    last_movement_at: dt.datetime | None


class StockMovementRead(ResponseSchema):
    id: uuid.UUID
    created_at: dt.datetime
    product_id: uuid.UUID
    product_name: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    kind: MovementKind
    movement_date: dt.date
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    balance_after: Decimal
    average_cost_after: Decimal
    source_type: str | None
    source_id: uuid.UUID | None
    reference: str | None
    notes: str | None
    journal_entry_id: uuid.UUID | None


class StockAdjustRequest(BaseSchema):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
    #: Signed: positive found, negative lost.
    quantity_delta: Annotated[Decimal, Field(max_digits=18, decimal_places=4)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    movement_date: dt.date | None = None
    #: Only meaningful for a positive delta; found stock is otherwise valued at the
    #: current average.
    unit_cost: Cost | None = None

    @model_validator(mode="after")
    def _non_zero(self) -> Self:
        if self.quantity_delta == 0:
            raise ValueError("quantity_delta cannot be zero")
        return self


class StockTransferRequest(BaseSchema):
    product_id: uuid.UUID
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    quantity: Qty
    movement_date: dt.date | None = None

    @model_validator(mode="after")
    def _different_warehouses(self) -> Self:
        if self.from_warehouse_id == self.to_warehouse_id:
            raise ValueError("Source and destination warehouses must differ")
        return self


class StockValuationRow(ResponseSchema):
    product_id: uuid.UUID
    sku: str
    name: str
    quantity: Decimal
    average_cost: Decimal
    total_value: Decimal


class StockValuationReport(ResponseSchema):
    """Total inventory value. Must equal the Inventory account's ledger balance."""

    as_of: dt.date
    warehouse_id: uuid.UUID | None
    rows: list[StockValuationRow]
    total_value: Decimal
    product_count: int


# ---------------------------------------------------------------------------
# Purchase document lines
# ---------------------------------------------------------------------------
class PurchaseLineInput(BaseSchema):
    """A purchase-document line as submitted.

    As on the sales side, no computed field is accepted: taxable base, tax split,
    and totals are all derived server-side.
    """

    product_id: uuid.UUID | None = None
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]
    hsn_code: str | None = Field(default=None, max_length=10)
    quantity: Qty
    unit: str | None = Field(default=None, max_length=20)
    unit_price: Amount
    discount_percent: Percent = Decimal("0")
    discount_amount: Amount | None = None
    tax_rate: Percent = Decimal("0")
    expense_account_id: uuid.UUID | None = None


class PurchaseLineRead(ResponseSchema):
    id: uuid.UUID
    line_number: int
    product_id: uuid.UUID | None
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


class PurchaseTotalsRead(ResponseSchema):
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
# Purchase orders
# ---------------------------------------------------------------------------
class PurchaseOrderCreate(BaseSchema):
    supplier_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
    order_date: dt.date | None = None
    expected_date: dt.date | None = None
    lines: list[PurchaseLineInput] = Field(min_length=1)
    notes: str | None = None
    terms: str | None = None
    round_to_whole: bool = False


class PurchaseOrderLineRead(PurchaseLineRead):
    received_quantity: Decimal
    outstanding_quantity: Decimal


class PurchaseOrderRead(TimestampedSchema, PurchaseTotalsRead):
    id: uuid.UUID
    order_number: str
    supplier_id: uuid.UUID
    supplier_name: str
    warehouse_id: uuid.UUID | None
    order_date: dt.date
    expected_date: dt.date | None
    status: PurchaseOrderStatus
    approved_at: dt.datetime | None
    cancelled_at: dt.datetime | None
    notes: str | None
    terms: str | None
    lines: list[PurchaseOrderLineRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Goods receipts
# ---------------------------------------------------------------------------
class GoodsReceiptLineInput(BaseSchema):
    product_id: uuid.UUID
    purchase_order_line_id: uuid.UUID | None = None
    quantity: Qty
    unit_cost: Cost
    rejected_quantity: Amount = Decimal("0")
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _rejected_within_quantity(self) -> Self:
        if self.rejected_quantity > self.quantity:
            raise ValueError("rejected_quantity cannot exceed quantity")
        return self


class GoodsReceiptCreate(BaseSchema):
    supplier_id: uuid.UUID
    purchase_order_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    receipt_date: dt.date | None = None
    supplier_reference: str | None = Field(default=None, max_length=100)
    lines: list[GoodsReceiptLineInput] = Field(min_length=1)
    notes: str | None = None
    #: Move stock and post the accrual immediately.
    post: bool = False


class GoodsReceiptLineRead(ResponseSchema):
    id: uuid.UUID
    line_number: int
    product_id: uuid.UUID
    product_name: str
    purchase_order_line_id: uuid.UUID | None
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    rejected_quantity: Decimal
    accepted_quantity: Decimal
    notes: str | None


class GoodsReceiptRead(TimestampedSchema):
    id: uuid.UUID
    receipt_number: str
    supplier_id: uuid.UUID
    supplier_name: str
    purchase_order_id: uuid.UUID | None
    warehouse_id: uuid.UUID
    warehouse_code: str
    receipt_date: dt.date
    supplier_reference: str | None
    status: GoodsReceiptStatus
    total_cost: Decimal
    journal_entry_id: uuid.UUID | None
    posted_at: dt.datetime | None
    cancelled_at: dt.datetime | None
    notes: str | None
    lines: list[GoodsReceiptLineRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Bills
# ---------------------------------------------------------------------------
class BillCreate(BaseSchema):
    supplier_id: uuid.UUID
    supplier_invoice_number: str | None = Field(default=None, max_length=100)
    purchase_order_id: uuid.UUID | None = None
    goods_receipt_id: uuid.UUID | None = None
    bill_date: dt.date | None = None
    due_date: dt.date | None = None
    lines: list[PurchaseLineInput] = Field(min_length=1)
    notes: str | None = None
    terms: str | None = None
    round_to_whole: bool = False
    post: bool = False

    @model_validator(mode="after")
    def _due_after_issue(self) -> Self:
        if self.due_date and self.bill_date and self.due_date < self.bill_date:
            raise ValueError("due_date cannot be before bill_date")
        return self


class BillUpdate(BaseSchema):
    """Drafts only."""

    supplier_invoice_number: str | None = Field(default=None, max_length=100)
    bill_date: dt.date | None = None
    due_date: dt.date | None = None
    lines: list[PurchaseLineInput] | None = Field(default=None, min_length=1)
    notes: str | None = None
    terms: str | None = None


class BillRead(TimestampedSchema, PurchaseTotalsRead):
    id: uuid.UUID
    bill_number: str
    supplier_invoice_number: str | None
    supplier_id: uuid.UUID
    supplier_name: str
    purchase_order_id: uuid.UUID | None
    goods_receipt_id: uuid.UUID | None
    bill_date: dt.date
    due_date: dt.date
    status: BillStatus
    paid_amount: Decimal
    outstanding: Decimal
    is_overdue: bool
    supplier_gstin: str | None
    journal_entry_id: uuid.UUID | None
    posted_at: dt.datetime | None
    cancelled_at: dt.datetime | None
    notes: str | None
    lines: list[PurchaseLineRead] = Field(default_factory=list)


class CancelBillRequest(BaseSchema):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    cancellation_date: dt.date | None = None


# ---------------------------------------------------------------------------
# Supplier payments
# ---------------------------------------------------------------------------
class SupplierAllocationInput(BaseSchema):
    bill_id: uuid.UUID
    amount: PositiveAmount


class SupplierPaymentCreate(BaseSchema):
    supplier_id: uuid.UUID
    payment_date: dt.date | None = None
    amount: PositiveAmount
    method: Annotated[str, StringConstraints(max_length=20)] = "bank_transfer"
    reference: str | None = Field(default=None, max_length=100)
    source_account_id: uuid.UUID | None = None
    notes: str | None = None
    allocations: list[SupplierAllocationInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _allocations_within_amount(self) -> Self:
        allocated = sum((a.amount for a in self.allocations), Decimal("0"))
        if allocated > self.amount:
            raise ValueError(
                f"allocations total {allocated} exceeds the payment amount {self.amount}"
            )
        if len({a.bill_id for a in self.allocations}) != len(self.allocations):
            raise ValueError("the same bill appears twice in allocations")
        return self


class AllocateSupplierPaymentRequest(BaseSchema):
    allocations: list[SupplierAllocationInput] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicates(self) -> Self:
        if len({a.bill_id for a in self.allocations}) != len(self.allocations):
            raise ValueError("the same bill appears twice in allocations")
        return self


class SupplierAllocationRead(ResponseSchema):
    id: uuid.UUID
    bill_id: uuid.UUID
    bill_number: str
    amount: Decimal


class SupplierPaymentRead(TimestampedSchema):
    id: uuid.UUID
    payment_number: str
    supplier_id: uuid.UUID
    supplier_name: str
    payment_date: dt.date
    amount: Decimal
    unallocated_amount: Decimal
    allocated_amount: Decimal
    method: str
    reference: str | None
    currency: str
    notes: str | None
    source_account_id: uuid.UUID | None
    journal_entry_id: uuid.UUID | None
    allocations: list[SupplierAllocationRead] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
class PayablesAgeingBucket(ResponseSchema):
    label: str
    amount: Decimal
    bill_count: int


class PayablesAgeing(ResponseSchema):
    as_of: dt.date
    buckets: list[PayablesAgeingBucket]
    total_outstanding: Decimal
    total_overdue: Decimal


class ReorderRow(ResponseSchema):
    product_id: uuid.UUID
    sku: str
    name: str
    quantity_on_hand: Decimal
    reorder_level: Decimal
    shortfall: Decimal
