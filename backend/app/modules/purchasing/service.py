"""Purchasing services.

Two of these write to the ledger, and the split between them is the point:

**Goods receipt** - stock arrives, and a liability for goods-received-not-invoiced
is recognised:

===============================  ==========  ==========
Account                              Debit      Credit
===============================  ==========  ==========
Inventory                           cost
Goods Received Not Invoiced                     cost
===============================  ==========  ==========

**Bill** - the supplier's invoice replaces that accrual with a real payable and
books recoverable input GST:

===============================  ==========  ==========
Goods Received Not Invoiced        net
GST Input Tax Credit              tax
Accounts Payable                                gross
===============================  ==========  ==========

When a bill arrives with no receipt behind it (a service, a utility bill), the debit
goes straight to inventory or expense instead. Keeping the two events apart is what
lets stock arrive on the 30th and the invoice on the 3rd without either month being
misstated.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.core.pagination import PageParams
from app.db.types import ZERO
from app.modules.accounting.repository import JournalRepository, SequenceRepository
from app.modules.accounting.service import ChartOfAccountsService, PostingService
from app.modules.audit.models import AuditAction, AuditSeverity
from app.modules.audit.service import AuditService
from app.modules.organizations.clock import organization_today
from app.modules.purchasing.models import (
    Product,
    ProductKind,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    StockLevel,
    Supplier,
    Warehouse,
)
from app.modules.purchasing.schemas import (
    ProductCreate,
    ProductUpdate,
    PurchaseLineInput,
    PurchaseOrderCreate,
    SupplierCreate,
    SupplierUpdate,
    WarehouseCreate,
)
from app.modules.purchasing.stock import StockService, _audit_ctx
from app.modules.purchasing.valuation import round_value
from app.modules.tax.gst import (
    LineTotals,
    compute_document,
    compute_line,
    resolve_treatment,
    state_code_from_gstin,
)
from app.modules.users.models import User

log = get_logger(__name__)


# =============================================================================
# Line computation
# =============================================================================
class PurchaseLineBuilder:
    """Computes purchase-document lines with the same engine sales uses.

    Shared deliberately: a purchase and a sale of the same item at the same price
    must produce identical tax, or the books cannot reconcile.
    """

    @staticmethod
    def compute(
        inputs: list[PurchaseLineInput], *, treatment: Any
    ) -> list[tuple[PurchaseLineInput, LineTotals]]:
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
    def apply(target: Any, source: PurchaseLineInput, totals: LineTotals, index: int) -> None:
        target.line_number = index
        target.product_id = source.product_id
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
    def apply_totals(document: Any, computed: list[LineTotals], *, round_to_whole: bool) -> None:
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


class PurchasingBase:
    """Shared lookups and numbering."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.sequences = SequenceRepository(session)
        self.chart = ChartOfAccountsService(session)
        self.posting = PostingService(session)
        self.journals = JournalRepository(session)
        self.stock = StockService(session)
        self.audit = AuditService(session)

    async def _today(self, organization_id: uuid.UUID) -> dt.date:
        """Today by the organization's clock, not the server's.

        See :mod:`app.modules.organizations.clock` for why those differ, and why it matters
        for a date stamped on a record that someone will later reconcile.
        """
        return await organization_today(self.session, organization_id)

    async def _supplier(self, organization_id: uuid.UUID, supplier_id: uuid.UUID) -> Supplier:
        supplier = (
            await self.session.execute(
                select(Supplier).where(
                    Supplier.id == supplier_id,
                    Supplier.organization_id == organization_id,
                    Supplier.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if supplier is None:
            raise NotFoundError("Supplier")
        if not supplier.is_active:
            raise BusinessRuleError(f"{supplier.name} is inactive.")
        return supplier

    async def _product(self, organization_id: uuid.UUID, product_id: uuid.UUID) -> Product:
        product = (
            await self.session.execute(
                select(Product).where(
                    Product.id == product_id,
                    Product.organization_id == organization_id,
                    Product.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("Product")
        return product

    async def _seller_state(self, organization_id: uuid.UUID) -> str | None:
        from app.modules.organizations.repository import OrganizationRepository

        org = await OrganizationRepository(self.session).get(organization_id)
        return state_code_from_gstin(org.gstin) if org else None

    async def _treatment(self, organization_id: uuid.UUID, supplier: Supplier) -> Any:
        """Place of supply, from the *buyer's* perspective.

        The organization is the buyer here, so its state is compared against the
        supplier's - the mirror of the sales case.
        """
        return resolve_treatment(
            seller_state_code=supplier.state_code,
            buyer_state_code=await self._seller_state(organization_id),
            buyer_country=supplier.country,
        )

    async def _next_number(self, organization_id: uuid.UUID, *, scope: str, prefix: str) -> str:
        year = (await self._today(organization_id)).year
        return await self.sequences.next_number(
            organization_id, scope=f"{scope}:{year}", prefix=f"{prefix}-{year}-"
        )


# =============================================================================
# Masters
# =============================================================================
class SupplierService(PurchasingBase):
    async def get(self, organization_id: uuid.UUID, supplier_id: uuid.UUID) -> Supplier:
        return await self._supplier(organization_id, supplier_id)

    async def paginate(
        self, organization_id: uuid.UUID, params: PageParams, *, include_inactive: bool = False
    ) -> tuple[list[Supplier], int]:
        clauses = [Supplier.organization_id == organization_id, Supplier.deleted_at.is_(None)]
        if not include_inactive:
            clauses.append(Supplier.is_active.is_(True))

        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(Supplier).where(*clauses)
                )
            ).scalar_one()
        )
        rows = (
            (
                await self.session.execute(
                    select(Supplier)
                    .where(*clauses)
                    .order_by(Supplier.name)
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
        data: SupplierCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Supplier:
        code = data.code
        if code is None:
            count = int(
                (
                    await self.session.execute(
                        select(func.count())
                        .select_from(Supplier)
                        .where(Supplier.organization_id == organization_id)
                    )
                ).scalar_one()
            )
            code = f"SUP-{count + 1:04d}"

        existing = (
            await self.session.execute(
                select(Supplier.id).where(
                    Supplier.organization_id == organization_id, Supplier.code == code
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError(f"Supplier code {code} is already in use")

        supplier = Supplier(
            organization_id=organization_id,
            code=code,
            state_code=state_code_from_gstin(data.gstin),
            **data.model_dump(exclude={"code"}),
        )
        self.session.add(supplier)
        await self.session.flush()

        await self.audit.record(
            AuditAction.SUPPLIER_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="supplier",
            resource_id=supplier.id,
            summary=f"Created supplier {supplier.name}",
            **_audit_ctx(ctx),
        )
        return supplier

    async def update(
        self,
        organization_id: uuid.UUID,
        supplier_id: uuid.UUID,
        data: SupplierUpdate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Supplier:
        supplier = await self._supplier(organization_id, supplier_id)
        changes: dict[str, Any] = {}
        for field, value in data.model_dump(exclude_unset=True).items():
            if getattr(supplier, field) != value:
                changes[field] = {"before": str(getattr(supplier, field)), "after": str(value)}
                setattr(supplier, field, value)
        if "gstin" in changes:
            supplier.state_code = state_code_from_gstin(supplier.gstin)
        await self.session.flush()

        if changes:
            await self.audit.record(
                AuditAction.SUPPLIER_UPDATED,
                actor=actor,
                organization_id=organization_id,
                resource_type="supplier",
                resource_id=supplier.id,
                summary=f"Updated supplier {supplier.name}",
                changes=changes,
                **_audit_ctx(ctx),
            )
        return supplier


class ProductService(PurchasingBase):
    async def get(self, organization_id: uuid.UUID, product_id: uuid.UUID) -> Product:
        return await self._product(organization_id, product_id)

    async def by_barcode(self, organization_id: uuid.UUID, barcode: str) -> Product:
        """Point lookup for a barcode scan - the reason the column is indexed."""
        product = (
            await self.session.execute(
                select(Product).where(
                    Product.organization_id == organization_id,
                    Product.barcode == barcode.strip(),
                    Product.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if product is None:
            raise NotFoundError("Product")
        return product

    async def paginate(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        query: str | None = None,
        kind: ProductKind | None = None,
        include_inactive: bool = False,
    ) -> tuple[list[Product], int]:
        from sqlalchemy import or_

        clauses: list[Any] = [
            Product.organization_id == organization_id,
            Product.deleted_at.is_(None),
        ]
        if not include_inactive:
            clauses.append(Product.is_active.is_(True))
        if kind is not None:
            clauses.append(Product.kind == kind)
        if query:
            pattern = f"%{query}%"
            clauses.append(
                or_(
                    Product.name.ilike(pattern),
                    Product.sku.ilike(pattern),
                    Product.barcode.ilike(pattern),
                )
            )

        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(Product).where(*clauses)
                )
            ).scalar_one()
        )
        rows = (
            (
                await self.session.execute(
                    select(Product)
                    .where(*clauses)
                    .order_by(Product.name)
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
        data: ProductCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Product:
        sku = data.sku
        if sku is None:
            count = int(
                (
                    await self.session.execute(
                        select(func.count())
                        .select_from(Product)
                        .where(Product.organization_id == organization_id)
                    )
                ).scalar_one()
            )
            sku = f"SKU-{count + 1:05d}"

        clash = (
            await self.session.execute(
                select(Product.id).where(
                    Product.organization_id == organization_id, Product.sku == sku
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise ConflictError(f"SKU {sku} is already in use")

        if data.barcode:
            barcode_clash = (
                await self.session.execute(
                    select(Product.id).where(
                        Product.organization_id == organization_id,
                        Product.barcode == data.barcode,
                        Product.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if barcode_clash is not None:
                raise ConflictError(f"Barcode {data.barcode} is already assigned")

        product = Product(
            organization_id=organization_id, sku=sku, **data.model_dump(exclude={"sku"})
        )
        self.session.add(product)
        await self.session.flush()

        await self.audit.record(
            AuditAction.PRODUCT_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="product",
            resource_id=product.id,
            summary=f"Created product {product.sku} - {product.name}",
            **_audit_ctx(ctx),
        )
        return product

    async def update(
        self,
        organization_id: uuid.UUID,
        product_id: uuid.UUID,
        data: ProductUpdate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Product:
        product = await self._product(organization_id, product_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(product, field, value)
        await self.session.flush()

        await self.audit.record(
            AuditAction.PRODUCT_UPDATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="product",
            resource_id=product.id,
            summary=f"Updated product {product.sku}",
            **_audit_ctx(ctx),
        )
        return product

    async def aggregate_stock(
        self, organization_id: uuid.UUID, product_id: uuid.UUID
    ) -> tuple[Decimal, Decimal]:
        """``(quantity, value)`` across every warehouse."""
        row = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(StockLevel.quantity), ZERO),
                    func.coalesce(func.sum(StockLevel.quantity * StockLevel.average_cost), ZERO),
                ).where(StockLevel.product_id == product_id)
            )
        ).one()
        return row[0], round_value(row[1])


class WarehouseService(PurchasingBase):
    async def paginate(self, organization_id: uuid.UUID) -> list[Warehouse]:
        rows = (
            (
                await self.session.execute(
                    select(Warehouse)
                    .where(
                        Warehouse.organization_id == organization_id,
                        Warehouse.deleted_at.is_(None),
                    )
                    .order_by(Warehouse.code)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def create(
        self,
        organization_id: uuid.UUID,
        data: WarehouseCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Warehouse:
        clash = (
            await self.session.execute(
                select(Warehouse.id).where(
                    Warehouse.organization_id == organization_id, Warehouse.code == data.code
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise ConflictError(f"Warehouse code {data.code} is already in use")

        if data.is_default:
            # Exactly one default per organization; the partial unique index would
            # otherwise reject the insert.
            for existing in await self.paginate(organization_id):
                existing.is_default = False
            await self.session.flush()

        warehouse = Warehouse(organization_id=organization_id, **data.model_dump())
        self.session.add(warehouse)
        await self.session.flush()

        await self.audit.record(
            AuditAction.WAREHOUSE_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="warehouse",
            resource_id=warehouse.id,
            summary=f"Created warehouse {warehouse.code}",
            **_audit_ctx(ctx),
        )
        return warehouse


# =============================================================================
# Purchase orders
# =============================================================================
class PurchaseOrderService(PurchasingBase):
    async def get(self, organization_id: uuid.UUID, order_id: uuid.UUID) -> PurchaseOrder:
        from sqlalchemy.orm import selectinload

        order = (
            await self.session.execute(
                select(PurchaseOrder)
                .where(
                    PurchaseOrder.id == order_id,
                    PurchaseOrder.organization_id == organization_id,
                    PurchaseOrder.deleted_at.is_(None),
                )
                .options(selectinload(PurchaseOrder.lines), selectinload(PurchaseOrder.supplier))
            )
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError("Purchase order")
        return order

    async def paginate(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        supplier_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> tuple[list[PurchaseOrder], int]:
        from sqlalchemy.orm import selectinload

        clauses: list[Any] = [
            PurchaseOrder.organization_id == organization_id,
            PurchaseOrder.deleted_at.is_(None),
        ]
        if supplier_id is not None:
            clauses.append(PurchaseOrder.supplier_id == supplier_id)
        if status is not None:
            clauses.append(PurchaseOrder.status == status)

        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(PurchaseOrder).where(*clauses)
                )
            ).scalar_one()
        )
        rows = (
            (
                await self.session.execute(
                    select(PurchaseOrder)
                    .where(*clauses)
                    .options(
                        selectinload(PurchaseOrder.lines),
                        selectinload(PurchaseOrder.supplier),
                    )
                    .order_by(PurchaseOrder.order_date.desc())
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
        data: PurchaseOrderCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> PurchaseOrder:
        supplier = await self._supplier(organization_id, data.supplier_id)
        treatment = await self._treatment(organization_id, supplier)
        computed = PurchaseLineBuilder.compute(data.lines, treatment=treatment)

        order = PurchaseOrder(
            organization_id=organization_id,
            order_number=await self._next_number(
                organization_id, scope="purchase_order", prefix="PO"
            ),
            supplier_id=supplier.id,
            warehouse_id=data.warehouse_id,
            order_date=data.order_date or await self._today(organization_id),
            expected_date=data.expected_date,
            status=PurchaseOrderStatus.DRAFT,
            tax_treatment=treatment,
            currency=supplier.currency,
            notes=data.notes,
            terms=data.terms,
            created_by_id=actor.id,
        )
        order.lines = [PurchaseOrderLine() for _ in computed]
        for index, ((source, totals), row) in enumerate(
            zip(computed, order.lines, strict=True), start=1
        ):
            PurchaseLineBuilder.apply(row, source, totals, index)
        PurchaseLineBuilder.apply_totals(
            order, [t for _, t in computed], round_to_whole=data.round_to_whole
        )

        self.session.add(order)
        await self.session.flush()

        await self.audit.record(
            AuditAction.PURCHASE_ORDER_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="purchase_order",
            resource_id=order.id,
            summary=f"Created PO {order.order_number} for {order.grand_total}",
            **_audit_ctx(ctx),
        )
        return order

    async def approve(
        self,
        organization_id: uuid.UUID,
        order_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> PurchaseOrder:
        order = await self.get(organization_id, order_id)
        if order.status not in (
            PurchaseOrderStatus.DRAFT,
            PurchaseOrderStatus.PENDING_APPROVAL,
        ):
            raise ConflictError(f"This order is already {order.status}.")

        order.status = PurchaseOrderStatus.APPROVED
        order.approved_at = dt.datetime.now(dt.UTC)
        order.approved_by_id = actor.id
        await self.session.flush()

        await self.audit.record(
            AuditAction.PURCHASE_ORDER_APPROVED,
            actor=actor,
            organization_id=organization_id,
            resource_type="purchase_order",
            resource_id=order.id,
            summary=f"Approved PO {order.order_number}",
            **_audit_ctx(ctx),
        )
        return order

    async def cancel(
        self,
        organization_id: uuid.UUID,
        order_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> PurchaseOrder:
        order = await self.get(organization_id, order_id)
        if any(line.received_quantity > 0 for line in order.lines):
            raise BusinessRuleError(
                "Goods have been received against this order. Cancel the receipts first."
            )

        order.status = PurchaseOrderStatus.CANCELLED
        order.cancelled_at = dt.datetime.now(dt.UTC)
        await self.session.flush()

        await self.audit.record(
            AuditAction.PURCHASE_ORDER_CANCELLED,
            actor=actor,
            organization_id=organization_id,
            resource_type="purchase_order",
            resource_id=order.id,
            summary=f"Cancelled PO {order.order_number}",
            severity=AuditSeverity.WARNING,
            **_audit_ctx(ctx),
        )
        return order
