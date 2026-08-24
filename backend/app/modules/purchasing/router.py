"""Purchasing and inventory endpoints."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select

from app.core.pagination import Page, PageParams
from app.core.schemas import with_computed
from app.db.types import ZERO
from app.modules.auth.dependencies import (
    ActiveOrganizationId,
    CurrentUser,
    DbSession,
    OrganizationToday,
    RequestCtx,
    require_permission,
)
from app.modules.purchasing.models import (
    Bill,
    BillStatus,
    GoodsReceiptStatus,
    Product,
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
    AllocateSupplierPaymentRequest,
    BillCreate,
    BillRead,
    CancelBillRequest,
    GoodsReceiptCreate,
    GoodsReceiptLineRead,
    GoodsReceiptRead,
    PayablesAgeing,
    PayablesAgeingBucket,
    ProductCreate,
    ProductRead,
    ProductUpdate,
    ProductWithStock,
    PurchaseLineRead,
    PurchaseOrderCreate,
    PurchaseOrderLineRead,
    PurchaseOrderRead,
    ReorderRow,
    StockAdjustRequest,
    StockLevelRead,
    StockMovementRead,
    StockTransferRequest,
    StockValuationReport,
    StockValuationRow,
    SupplierAllocationRead,
    SupplierCreate,
    SupplierPaymentCreate,
    SupplierPaymentRead,
    SupplierRead,
    SupplierUpdate,
    WarehouseCreate,
    WarehouseRead,
)
from app.modules.purchasing.service import (
    ProductService,
    PurchaseOrderService,
    SupplierService,
    WarehouseService,
)
from app.modules.purchasing.stock import StockService
from app.modules.rbac.permissions import Permission


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
def get_suppliers(session: DbSession) -> SupplierService:
    return SupplierService(session)


def get_products(session: DbSession) -> ProductService:
    return ProductService(session)


def get_warehouses(session: DbSession) -> WarehouseService:
    return WarehouseService(session)


def get_orders(session: DbSession) -> PurchaseOrderService:
    return PurchaseOrderService(session)


def get_receipts(session: DbSession) -> GoodsReceiptService:
    return GoodsReceiptService(session)


def get_bills(session: DbSession) -> BillService:
    return BillService(session)


def get_supplier_payments(session: DbSession) -> SupplierPaymentService:
    return SupplierPaymentService(session)


def get_stock(session: DbSession) -> StockService:
    return StockService(session)


SuppliersDep = Annotated[SupplierService, Depends(get_suppliers)]
ProductsDep = Annotated[ProductService, Depends(get_products)]
WarehousesDep = Annotated[WarehouseService, Depends(get_warehouses)]
OrdersDep = Annotated[PurchaseOrderService, Depends(get_orders)]
ReceiptsDep = Annotated[GoodsReceiptService, Depends(get_receipts)]
BillsDep = Annotated[BillService, Depends(get_bills)]
SupplierPaymentsDep = Annotated[SupplierPaymentService, Depends(get_supplier_payments)]
StockDep = Annotated[StockService, Depends(get_stock)]


# ---------------------------------------------------------------------------
# Response assembly
# ---------------------------------------------------------------------------
def _order_response(order: Any) -> PurchaseOrderRead:
    return with_computed(
        PurchaseOrderRead,
        order,
        supplier_name=order.supplier.name,
        lines=[
            with_computed(
                PurchaseOrderLineRead,
                line,
                outstanding_quantity=line.outstanding_quantity,
            )
            for line in order.lines
        ],
    )


def _receipt_response(receipt: Any) -> GoodsReceiptRead:
    return with_computed(
        GoodsReceiptRead,
        receipt,
        supplier_name=receipt.supplier.name,
        warehouse_code=receipt.warehouse.code,
        lines=[
            with_computed(
                GoodsReceiptLineRead,
                line,
                product_name=line.product.name,
                accepted_quantity=line.accepted_quantity,
            )
            for line in receipt.lines
        ],
    )


def bill_response(bill: Any, today: dt.date) -> BillRead:
    """Assemble a bill response.

    Public, unlike its siblings: the documents module creates bills too, and one
    shape for a bill across both routers beats a second assembly that drifts.

    ``today`` is passed in because `is_overdue` otherwise falls back to the *server's*
    date, which turns a bill overdue a day early for an organization ahead of it.
    """
    return with_computed(
        BillRead,
        bill,
        supplier_name=bill.supplier.name,
        outstanding=bill.outstanding,
        is_overdue=bill.is_overdue(today),
        lines=[PurchaseLineRead.model_validate(line) for line in bill.lines],
    )


def _payment_response(payment: Any) -> SupplierPaymentRead:
    return with_computed(
        SupplierPaymentRead,
        payment,
        supplier_name=payment.supplier.name,
        allocated_amount=payment.allocated_amount,
        allocations=[
            with_computed(SupplierAllocationRead, a, bill_number=a.bill.bill_number)
            for a in payment.allocations
        ],
    )


# =============================================================================
# Suppliers
# =============================================================================
suppliers_router = APIRouter(prefix="/suppliers", tags=["Suppliers"])


@suppliers_router.get("", response_model=Page[SupplierRead], summary="List suppliers")
async def list_suppliers(
    organization_id: ActiveOrganizationId,
    service: SuppliersDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.SUPPLIER_READ))],
    include_inactive: Annotated[bool, Query()] = False,
) -> Page[SupplierRead]:
    rows, total = await service.paginate(organization_id, params, include_inactive=include_inactive)
    return Page.create([SupplierRead.model_validate(r) for r in rows], total=total, params=params)


@suppliers_router.post(
    "",
    response_model=SupplierRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a supplier",
)
async def create_supplier(
    data: SupplierCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: SuppliersDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.SUPPLIER_WRITE))],
) -> SupplierRead:
    return SupplierRead.model_validate(await service.create(organization_id, data, user, ctx))


@suppliers_router.get("/{supplier_id}", response_model=SupplierRead, summary="Get a supplier")
async def get_supplier(
    supplier_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: SuppliersDep,
    _: Annotated[None, Depends(require_permission(Permission.SUPPLIER_READ))],
) -> SupplierRead:
    return SupplierRead.model_validate(await service.get(organization_id, supplier_id))


@suppliers_router.patch("/{supplier_id}", response_model=SupplierRead, summary="Update a supplier")
async def update_supplier(
    supplier_id: uuid.UUID,
    data: SupplierUpdate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: SuppliersDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.SUPPLIER_WRITE))],
) -> SupplierRead:
    return SupplierRead.model_validate(
        await service.update(organization_id, supplier_id, data, user, ctx)
    )


# =============================================================================
# Products
# =============================================================================
products_router = APIRouter(prefix="/products", tags=["Products"])


@products_router.get("", response_model=Page[ProductWithStock], summary="List products")
async def list_products(
    organization_id: ActiveOrganizationId,
    service: ProductsDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.INVENTORY_READ))],
    q: Annotated[str | None, Query(description="Search name, SKU, or barcode")] = None,
    kind: Annotated[ProductKind | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> Page[ProductWithStock]:
    rows, total = await service.paginate(
        organization_id, params, query=q, kind=kind, include_inactive=include_inactive
    )
    items: list[ProductWithStock] = []
    for product in rows:
        quantity, value = await service.aggregate_stock(organization_id, product.id)
        items.append(
            with_computed(
                ProductWithStock,
                product,
                tracks_stock=product.tracks_stock,
                quantity_on_hand=quantity,
                stock_value=value,
                needs_reorder=quantity <= product.reorder_level,
            )
        )
    return Page.create(items, total=total, params=params)


@products_router.post(
    "", response_model=ProductRead, status_code=status.HTTP_201_CREATED, summary="Create a product"
)
async def create_product(
    data: ProductCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: ProductsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVENTORY_WRITE))],
) -> ProductRead:
    product = await service.create(organization_id, data, user, ctx)
    return with_computed(ProductRead, product, tracks_stock=product.tracks_stock)


@products_router.get(
    "/by-barcode/{barcode}", response_model=ProductRead, summary="Look up by barcode"
)
async def product_by_barcode(
    barcode: str,
    organization_id: ActiveOrganizationId,
    service: ProductsDep,
    _: Annotated[None, Depends(require_permission(Permission.INVENTORY_READ))],
) -> ProductRead:
    """Single point lookup, for a barcode scanner."""
    product = await service.by_barcode(organization_id, barcode)
    return with_computed(ProductRead, product, tracks_stock=product.tracks_stock)


@products_router.get("/reorder", response_model=list[ReorderRow], summary="Products to reorder")
async def reorder_report(
    organization_id: ActiveOrganizationId,
    session: DbSession,
    _: Annotated[None, Depends(require_permission(Permission.INVENTORY_READ))],
) -> list[ReorderRow]:
    """Stocked products at or below their reorder level."""
    rows = (
        await session.execute(
            select(
                Product.id,
                Product.sku,
                Product.name,
                Product.reorder_level,
                func.coalesce(func.sum(StockLevel.quantity), ZERO).label("on_hand"),
            )
            .outerjoin(StockLevel, StockLevel.product_id == Product.id)
            .where(
                Product.organization_id == organization_id,
                Product.deleted_at.is_(None),
                Product.is_active.is_(True),
                Product.kind == ProductKind.STOCKED,
                Product.reorder_level > 0,
            )
            .group_by(Product.id, Product.sku, Product.name, Product.reorder_level)
            .having(func.coalesce(func.sum(StockLevel.quantity), ZERO) <= Product.reorder_level)
            .order_by(Product.name)
        )
    ).all()

    return [
        ReorderRow(
            product_id=row.id,
            sku=row.sku,
            name=row.name,
            quantity_on_hand=row.on_hand,
            reorder_level=row.reorder_level,
            shortfall=row.reorder_level - row.on_hand,
        )
        for row in rows
    ]


@products_router.get("/{product_id}", response_model=ProductRead, summary="Get a product")
async def get_product(
    product_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: ProductsDep,
    _: Annotated[None, Depends(require_permission(Permission.INVENTORY_READ))],
) -> ProductRead:
    product = await service.get(organization_id, product_id)
    return with_computed(ProductRead, product, tracks_stock=product.tracks_stock)


@products_router.patch("/{product_id}", response_model=ProductRead, summary="Update a product")
async def update_product(
    product_id: uuid.UUID,
    data: ProductUpdate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: ProductsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVENTORY_WRITE))],
) -> ProductRead:
    product = await service.update(organization_id, product_id, data, user, ctx)
    return with_computed(ProductRead, product, tracks_stock=product.tracks_stock)


# =============================================================================
# Warehouses and stock
# =============================================================================
inventory_router = APIRouter(prefix="/inventory", tags=["Inventory"])


@inventory_router.get("/warehouses", response_model=list[WarehouseRead], summary="List warehouses")
async def list_warehouses(
    organization_id: ActiveOrganizationId,
    service: WarehousesDep,
    _: Annotated[None, Depends(require_permission(Permission.INVENTORY_READ))],
) -> list[WarehouseRead]:
    return [WarehouseRead.model_validate(w) for w in await service.paginate(organization_id)]


@inventory_router.post(
    "/warehouses",
    response_model=WarehouseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a warehouse",
)
async def create_warehouse(
    data: WarehouseCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: WarehousesDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVENTORY_WRITE))],
) -> WarehouseRead:
    return WarehouseRead.model_validate(await service.create(organization_id, data, user, ctx))


@inventory_router.get("/levels", response_model=list[StockLevelRead], summary="Stock on hand")
async def stock_levels(
    organization_id: ActiveOrganizationId,
    session: DbSession,
    _: Annotated[None, Depends(require_permission(Permission.INVENTORY_READ))],
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    product_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[StockLevelRead]:
    from sqlalchemy.orm import selectinload

    query = (
        select(StockLevel)
        .where(StockLevel.organization_id == organization_id)
        .options(selectinload(StockLevel.product), selectinload(StockLevel.warehouse))
        .join(Product, Product.id == StockLevel.product_id)
        .order_by(Product.name)
    )
    if warehouse_id is not None:
        query = query.where(StockLevel.warehouse_id == warehouse_id)
    if product_id is not None:
        query = query.where(StockLevel.product_id == product_id)

    levels = (await session.execute(query)).scalars().all()
    return [
        with_computed(
            StockLevelRead,
            level,
            product_sku=level.product.sku,
            product_name=level.product.name,
            warehouse_code=level.warehouse.code,
            available_quantity=level.available_quantity,
            total_value=level.total_value,
        )
        for level in levels
    ]


@inventory_router.get(
    "/movements", response_model=Page[StockMovementRead], summary="Stock movement history"
)
async def stock_movements(
    organization_id: ActiveOrganizationId,
    session: DbSession,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.INVENTORY_READ))],
    product_id: Annotated[uuid.UUID | None, Query()] = None,
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
    from_date: Annotated[dt.date | None, Query()] = None,
    to_date: Annotated[dt.date | None, Query()] = None,
) -> Page[StockMovementRead]:
    """The stock card. Append-only, so this is the authoritative history."""
    from sqlalchemy.orm import selectinload

    clauses: list[Any] = [StockMovement.organization_id == organization_id]
    if product_id is not None:
        clauses.append(StockMovement.product_id == product_id)
    if warehouse_id is not None:
        clauses.append(StockMovement.warehouse_id == warehouse_id)
    if from_date is not None:
        clauses.append(StockMovement.movement_date >= from_date)
    if to_date is not None:
        clauses.append(StockMovement.movement_date <= to_date)

    total = int(
        (
            await session.execute(select(func.count()).select_from(StockMovement).where(*clauses))
        ).scalar_one()
    )
    rows = (
        (
            await session.execute(
                select(StockMovement)
                .where(*clauses)
                .options(
                    selectinload(StockMovement.product),
                    selectinload(StockMovement.warehouse),
                )
                .order_by(StockMovement.movement_date.desc(), StockMovement.created_at.desc())
                .offset(params.offset)
                .limit(params.limit)
            )
        )
        .scalars()
        .all()
    )
    return Page.create(
        [
            with_computed(
                StockMovementRead,
                m,
                product_name=m.product.name,
                warehouse_code=m.warehouse.code,
            )
            for m in rows
        ],
        total=total,
        params=params,
    )


@inventory_router.get(
    "/valuation", response_model=StockValuationReport, summary="Inventory valuation"
)
async def stock_valuation(
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    session: DbSession,
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
    warehouse_id: Annotated[uuid.UUID | None, Query()] = None,
) -> StockValuationReport:
    """Total inventory value.

    This must equal the Inventory account's ledger balance. `stock_value` is the
    stored authority rather than `quantity * average_cost`, which cannot reconcile -
    see `app.modules.purchasing.valuation`.
    """
    clauses: list[Any] = [StockLevel.organization_id == organization_id, StockLevel.quantity > 0]
    if warehouse_id is not None:
        clauses.append(StockLevel.warehouse_id == warehouse_id)

    rows = (
        await session.execute(
            select(
                Product.id,
                Product.sku,
                Product.name,
                func.sum(StockLevel.quantity).label("quantity"),
                func.sum(StockLevel.stock_value).label("value"),
            )
            .join(Product, Product.id == StockLevel.product_id)
            .where(*clauses)
            .group_by(Product.id, Product.sku, Product.name)
            .order_by(Product.name)
        )
    ).all()

    items = [
        StockValuationRow(
            product_id=row.id,
            sku=row.sku,
            name=row.name,
            quantity=row.quantity,
            # Derived for display only; the total below sums the stored value.
            average_cost=(row.value / row.quantity) if row.quantity else ZERO,
            total_value=row.value,
        )
        for row in rows
    ]
    return StockValuationReport(
        as_of=today,
        warehouse_id=warehouse_id,
        rows=items,
        total_value=sum((r.total_value for r in items), ZERO),
        product_count=len(items),
    )


@inventory_router.post(
    "/adjust",
    response_model=StockMovementRead,
    # A movement is a new, immutable record, like every other 201 in this router.
    status_code=status.HTTP_201_CREATED,
    summary="Adjust stock (stock take)",
)
async def adjust_stock(
    data: StockAdjustRequest,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    products: ProductsDep,
    stock: StockDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVENTORY_WRITE))],
) -> StockMovementRead:
    """Writes value off against shrinkage. Audited at warning severity."""
    product = await products.get(organization_id, data.product_id)
    movement = await stock.adjust(
        organization_id,
        product=product,
        quantity_delta=data.quantity_delta,
        movement_date=data.movement_date or today,
        reason=data.reason,
        actor=user,
        warehouse_id=data.warehouse_id,
        unit_cost=data.unit_cost,
        ctx=ctx,
    )
    warehouse = await stock.get_warehouse(organization_id, data.warehouse_id)
    return with_computed(
        StockMovementRead, movement, product_name=product.name, warehouse_code=warehouse.code
    )


@inventory_router.post(
    "/transfer",
    response_model=list[StockMovementRead],
    status_code=status.HTTP_201_CREATED,
    summary="Transfer between warehouses",
)
async def transfer_stock(
    data: StockTransferRequest,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    products: ProductsDep,
    stock: StockDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVENTORY_WRITE))],
) -> list[StockMovementRead]:
    """Posts nothing to the ledger - a transfer is not an economic event."""
    product = await products.get(organization_id, data.product_id)
    outbound, inbound = await stock.transfer(
        organization_id,
        product=product,
        quantity=data.quantity,
        from_warehouse_id=data.from_warehouse_id,
        to_warehouse_id=data.to_warehouse_id,
        movement_date=data.movement_date or today,
        actor=user,
        ctx=ctx,
    )
    source = await stock.get_warehouse(organization_id, data.from_warehouse_id)
    destination = await stock.get_warehouse(organization_id, data.to_warehouse_id)
    return [
        with_computed(
            StockMovementRead, outbound, product_name=product.name, warehouse_code=source.code
        ),
        with_computed(
            StockMovementRead, inbound, product_name=product.name, warehouse_code=destination.code
        ),
    ]


# =============================================================================
# Purchase orders
# =============================================================================
purchase_orders_router = APIRouter(prefix="/purchase-orders", tags=["Purchase orders"])


@purchase_orders_router.get("", response_model=Page[PurchaseOrderRead], summary="List POs")
async def list_purchase_orders(
    organization_id: ActiveOrganizationId,
    service: OrdersDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_READ))],
    supplier_id: Annotated[uuid.UUID | None, Query()] = None,
    po_status: Annotated[PurchaseOrderStatus | None, Query(alias="status")] = None,
) -> Page[PurchaseOrderRead]:
    rows, total = await service.paginate(
        organization_id, params, supplier_id=supplier_id, status=po_status
    )
    return Page.create([_order_response(r) for r in rows], total=total, params=params)


@purchase_orders_router.post(
    "",
    response_model=PurchaseOrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a PO",
)
async def create_purchase_order(
    data: PurchaseOrderCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: OrdersDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_WRITE))],
) -> PurchaseOrderRead:
    order = await service.create(organization_id, data, user, ctx)
    return _order_response(await service.get(organization_id, order.id))


@purchase_orders_router.get("/{order_id}", response_model=PurchaseOrderRead, summary="Get a PO")
async def get_purchase_order(
    order_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: OrdersDep,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_READ))],
) -> PurchaseOrderRead:
    return _order_response(await service.get(organization_id, order_id))


@purchase_orders_router.post(
    "/{order_id}/approve", response_model=PurchaseOrderRead, summary="Approve a PO"
)
async def approve_purchase_order(
    order_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: OrdersDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_APPROVE))],
) -> PurchaseOrderRead:
    await service.approve(organization_id, order_id, user, ctx)
    return _order_response(await service.get(organization_id, order_id))


@purchase_orders_router.post(
    "/{order_id}/cancel", response_model=PurchaseOrderRead, summary="Cancel a PO"
)
async def cancel_purchase_order(
    order_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: OrdersDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_WRITE))],
) -> PurchaseOrderRead:
    """Refused once goods have been received against it."""
    await service.cancel(organization_id, order_id, user, ctx)
    return _order_response(await service.get(organization_id, order_id))


# =============================================================================
# Goods receipts
# =============================================================================
receipts_router = APIRouter(prefix="/goods-receipts", tags=["Goods receipts"])


@receipts_router.get("", response_model=Page[GoodsReceiptRead], summary="List receipts")
async def list_receipts(
    organization_id: ActiveOrganizationId,
    service: ReceiptsDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_READ))],
    supplier_id: Annotated[uuid.UUID | None, Query()] = None,
    receipt_status: Annotated[GoodsReceiptStatus | None, Query(alias="status")] = None,
) -> Page[GoodsReceiptRead]:
    rows, total = await service.paginate(
        organization_id, params, supplier_id=supplier_id, status=receipt_status
    )
    return Page.create([_receipt_response(r) for r in rows], total=total, params=params)


@receipts_router.post(
    "",
    response_model=GoodsReceiptRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a goods receipt",
)
async def create_receipt(
    data: GoodsReceiptCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: ReceiptsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_WRITE))],
) -> GoodsReceiptRead:
    """With `post: true`, moves stock and accrues Goods Received Not Invoiced."""
    receipt = await service.create(organization_id, data, user, ctx)
    return _receipt_response(await service.get(organization_id, receipt.id))


@receipts_router.get("/{receipt_id}", response_model=GoodsReceiptRead, summary="Get a receipt")
async def get_receipt(
    receipt_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: ReceiptsDep,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_READ))],
) -> GoodsReceiptRead:
    return _receipt_response(await service.get(organization_id, receipt_id))


@receipts_router.post(
    "/{receipt_id}/post", response_model=GoodsReceiptRead, summary="Post a receipt"
)
async def post_receipt(
    receipt_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: ReceiptsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_APPROVE))],
) -> GoodsReceiptRead:
    """Only accepted quantities enter stock; rejected units stay recorded for a claim."""
    await service.post(organization_id, receipt_id, user, ctx)
    return _receipt_response(await service.get(organization_id, receipt_id))


@receipts_router.post(
    "/{receipt_id}/cancel", response_model=GoodsReceiptRead, summary="Cancel a receipt"
)
async def cancel_receipt(
    receipt_id: uuid.UUID,
    data: CancelBillRequest,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: ReceiptsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_APPROVE))],
) -> GoodsReceiptRead:
    """Reverses the stock and the accrual. Refused if a bill references it."""
    await service.cancel(organization_id, receipt_id, reason=data.reason, actor=user, ctx=ctx)
    return _receipt_response(await service.get(organization_id, receipt_id))


# =============================================================================
# Bills
# =============================================================================
bills_router = APIRouter(prefix="/bills", tags=["Bills"])


@bills_router.get("", response_model=Page[BillRead], summary="List bills")
async def list_bills(
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    service: BillsDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_READ))],
    supplier_id: Annotated[uuid.UUID | None, Query()] = None,
    bill_status: Annotated[BillStatus | None, Query(alias="status")] = None,
    overdue_only: Annotated[bool, Query()] = False,
) -> Page[BillRead]:
    rows, total = await service.paginate(
        organization_id,
        params,
        supplier_id=supplier_id,
        status=bill_status,
        overdue_only=overdue_only,
    )
    return Page.create([bill_response(r, today) for r in rows], total=total, params=params)


@bills_router.post(
    "", response_model=BillRead, status_code=status.HTTP_201_CREATED, summary="Enter a bill"
)
async def create_bill(
    data: BillCreate,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: BillsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_WRITE))],
) -> BillRead:
    """A duplicate `supplier_invoice_number` for the same supplier is refused -
    entering the same invoice twice is the most expensive error in payables."""
    bill = await service.create(organization_id, data, user, ctx)
    return bill_response(await service.get(organization_id, bill.id), today)


@bills_router.get("/ageing", response_model=PayablesAgeing, summary="Payables ageing")
async def payables_ageing(
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    session: DbSession,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_READ))],
    as_of: Annotated[dt.date | None, Query()] = None,
) -> PayablesAgeing:
    """What is owed, bucketed by how overdue it is."""
    effective = as_of or today
    rows = (
        await session.execute(
            select(Bill.due_date, Bill.grand_total, Bill.paid_amount).where(
                Bill.organization_id == organization_id,
                Bill.deleted_at.is_(None),
                Bill.status.in_([BillStatus.POSTED, BillStatus.PARTIALLY_PAID]),
                Bill.paid_amount < Bill.grand_total,
            )
        )
    ).all()

    buckets: list[tuple[str, int, int]] = [
        ("Current", -10_000, 0),
        ("1-30 days", 1, 30),
        ("31-60 days", 31, 60),
        ("61-90 days", 61, 90),
        ("90+ days", 91, 10_000),
    ]
    # Two typed dicts rather than one of `[amount, count]` lists: a mixed-type list
    # infers as `list[object]`, and the arithmetic below then fails to typecheck.
    amounts: dict[str, Decimal] = dict.fromkeys((label for label, _, _ in buckets), ZERO)
    counts: dict[str, int] = dict.fromkeys((label for label, _, _ in buckets), 0)

    for due_date, grand_total, paid in rows:
        outstanding = grand_total - paid
        days = (effective - due_date).days
        for label, low, high in buckets:
            if low <= days <= high:
                amounts[label] += outstanding
                counts[label] += 1
                break

    items = [
        PayablesAgeingBucket(label=label, amount=amounts[label], bill_count=counts[label])
        for label, _, _ in buckets
    ]
    return PayablesAgeing(
        as_of=effective,
        buckets=items,
        total_outstanding=sum((b.amount for b in items), ZERO),
        total_overdue=sum((b.amount for b in items if b.label != "Current"), ZERO),
    )


@bills_router.get("/{bill_id}", response_model=BillRead, summary="Get a bill")
async def get_bill(
    bill_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    service: BillsDep,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_READ))],
) -> BillRead:
    return bill_response(await service.get(organization_id, bill_id), today)


@bills_router.post("/{bill_id}/post", response_model=BillRead, summary="Post a bill")
async def post_bill(
    bill_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: BillsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_APPROVE))],
) -> BillRead:
    """Recognises the payable and claims input GST. Clears the GRNI accrual when the
    bill follows a goods receipt."""
    await service.post(organization_id, bill_id, user, ctx)
    return bill_response(await service.get(organization_id, bill_id), today)


@bills_router.post("/{bill_id}/cancel", response_model=BillRead, summary="Cancel a bill")
async def cancel_bill(
    bill_id: uuid.UUID,
    data: CancelBillRequest,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: BillsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PURCHASE_APPROVE))],
) -> BillRead:
    await service.cancel(
        organization_id,
        bill_id,
        reason=data.reason,
        cancellation_date=data.cancellation_date,
        actor=user,
        ctx=ctx,
    )
    return bill_response(await service.get(organization_id, bill_id), today)


# =============================================================================
# Supplier payments
# =============================================================================
supplier_payments_router = APIRouter(prefix="/supplier-payments", tags=["Supplier payments"])


@supplier_payments_router.get(
    "", response_model=Page[SupplierPaymentRead], summary="List supplier payments"
)
async def list_supplier_payments(
    organization_id: ActiveOrganizationId,
    service: SupplierPaymentsDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.PAYMENT_READ))],
    supplier_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[SupplierPaymentRead]:
    rows, total = await service.paginate(organization_id, params, supplier_id=supplier_id)
    return Page.create([_payment_response(r) for r in rows], total=total, params=params)


@supplier_payments_router.post(
    "",
    response_model=SupplierPaymentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Pay a supplier",
)
async def pay_supplier(
    data: SupplierPaymentCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: SupplierPaymentsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PAYMENT_WRITE))],
) -> SupplierPaymentRead:
    """Debits payables, credits bank or cash."""
    payment = await service.pay(organization_id, data, user, ctx)
    return _payment_response(await service.get(organization_id, payment.id))


@supplier_payments_router.get(
    "/{payment_id}", response_model=SupplierPaymentRead, summary="Get a payment"
)
async def get_supplier_payment(
    payment_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: SupplierPaymentsDep,
    _: Annotated[None, Depends(require_permission(Permission.PAYMENT_READ))],
) -> SupplierPaymentRead:
    return _payment_response(await service.get(organization_id, payment_id))


@supplier_payments_router.post(
    "/{payment_id}/allocate", response_model=SupplierPaymentRead, summary="Allocate to bills"
)
async def allocate_supplier_payment(
    payment_id: uuid.UUID,
    data: AllocateSupplierPaymentRequest,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: SupplierPaymentsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PAYMENT_WRITE))],
) -> SupplierPaymentRead:
    await service.allocate(
        organization_id,
        payment_id,
        [(a.bill_id, a.amount) for a in data.allocations],
        user,
        ctx,
    )
    return _payment_response(await service.get(organization_id, payment_id))


__all__ = [
    "bills_router",
    "inventory_router",
    "products_router",
    "purchase_orders_router",
    "receipts_router",
    "supplier_payments_router",
    "suppliers_router",
]
