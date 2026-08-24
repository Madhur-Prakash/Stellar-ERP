"""Sales endpoints."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.core.pagination import Page, PageParams
from app.core.schemas import MessageResponse, with_computed
from app.db.types import ZERO
from app.modules.auth.dependencies import (
    ActiveOrganizationId,
    CurrentUser,
    DbSession,
    OrganizationToday,
    RequestCtx,
    require_permission,
)
from app.modules.rbac.permissions import Permission
from app.modules.sales.invoicing import InvoiceService, PaymentService
from app.modules.sales.models import InvoiceStatus, LeadStatus
from app.modules.sales.repository import InvoiceRepository, PaymentRepository
from app.modules.sales.schemas import (
    AgeingBucket,
    AllocatePaymentRequest,
    CancelInvoiceRequest,
    CustomerCreate,
    CustomerRead,
    CustomerStatement,
    CustomerUpdate,
    InvoiceCreate,
    InvoiceRead,
    InvoiceUpdate,
    LeadConvert,
    LeadCreate,
    LeadRead,
    LeadUpdate,
    PaymentAllocationRead,
    PaymentCreate,
    PaymentRead,
    QuotationCreate,
    QuotationRead,
    QuotationUpdate,
    ReceivablesAgeing,
    SalesLineRead,
    SalesOrderCreate,
    SalesOrderRead,
    SalesSummary,
)
from app.modules.sales.service import (
    CustomerService,
    LeadService,
    QuotationService,
    SalesOrderService,
)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
def get_customers(session: DbSession) -> CustomerService:
    return CustomerService(session)


def get_leads(session: DbSession) -> LeadService:
    return LeadService(session)


def get_quotations(session: DbSession) -> QuotationService:
    return QuotationService(session)


def get_orders(session: DbSession) -> SalesOrderService:
    return SalesOrderService(session)


def get_invoices(session: DbSession) -> InvoiceService:
    return InvoiceService(session)


def get_payments(session: DbSession) -> PaymentService:
    return PaymentService(session)


CustomersDep = Annotated[CustomerService, Depends(get_customers)]
LeadsDep = Annotated[LeadService, Depends(get_leads)]
QuotationsDep = Annotated[QuotationService, Depends(get_quotations)]
OrdersDep = Annotated[SalesOrderService, Depends(get_orders)]
InvoicesDep = Annotated[InvoiceService, Depends(get_invoices)]
PaymentsDep = Annotated[PaymentService, Depends(get_payments)]


# ---------------------------------------------------------------------------
# Response assembly
# ---------------------------------------------------------------------------
def _lines(document: Any) -> list[SalesLineRead]:
    return [SalesLineRead.model_validate(line) for line in document.lines]


def _quotation_response(quotation: Any, today: dt.date) -> QuotationRead:
    # `today` is passed in rather than defaulted inside `is_expired`, whose fallback is the
    # *server's* date - a quotation would expire a day early for an organization ahead of it.
    return with_computed(
        QuotationRead,
        quotation,
        customer_name=quotation.customer.name,
        is_expired=quotation.is_expired(today),
        lines=_lines(quotation),
    )


def _order_response(order: Any) -> SalesOrderRead:
    return with_computed(
        SalesOrderRead,
        order,
        customer_name=order.customer.name,
        uninvoiced_total=order.uninvoiced_total,
        lines=_lines(order),
    )


def _invoice_response(invoice: Any, today: dt.date) -> InvoiceRead:
    return with_computed(
        InvoiceRead,
        invoice,
        customer_name=invoice.customer.name,
        outstanding=invoice.outstanding,
        is_overdue=invoice.is_overdue(today),
        days_overdue=invoice.days_overdue(today),
        lines=_lines(invoice),
    )


def _payment_response(payment: Any) -> PaymentRead:
    return with_computed(
        PaymentRead,
        payment,
        customer_name=payment.customer.name,
        allocated_amount=payment.allocated_amount,
        allocations=[
            with_computed(
                PaymentAllocationRead,
                allocation,
                invoice_number=allocation.invoice.invoice_number,
            )
            for allocation in payment.allocations
        ],
    )


# =============================================================================
# Customers
# =============================================================================
customers_router = APIRouter(prefix="/customers", tags=["Customers"])


@customers_router.get("", response_model=Page[CustomerRead], summary="List customers")
async def list_customers(
    organization_id: ActiveOrganizationId,
    service: CustomersDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.CUSTOMER_READ))],
    q: Annotated[str | None, Query(description="Search name, code, or email")] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> Page[CustomerRead]:
    rows, total = await service.paginate(
        organization_id, params, query=q, include_inactive=include_inactive
    )
    return Page.create(
        [CustomerRead.model_validate(row) for row in rows], total=total, params=params
    )


@customers_router.post(
    "",
    response_model=CustomerRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a customer",
)
async def create_customer(
    data: CustomerCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: CustomersDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.CUSTOMER_WRITE))],
) -> CustomerRead:
    """The state code is derived from the GSTIN, since it decides CGST/SGST vs IGST."""
    customer = await service.create(organization_id, data, user, ctx)
    return CustomerRead.model_validate(customer)


@customers_router.get("/{customer_id}", response_model=CustomerRead, summary="Get a customer")
async def get_customer(
    customer_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: CustomersDep,
    _: Annotated[None, Depends(require_permission(Permission.CUSTOMER_READ))],
) -> CustomerRead:
    return CustomerRead.model_validate(await service.get(organization_id, customer_id))


@customers_router.patch("/{customer_id}", response_model=CustomerRead, summary="Update a customer")
async def update_customer(
    customer_id: uuid.UUID,
    data: CustomerUpdate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: CustomersDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.CUSTOMER_WRITE))],
) -> CustomerRead:
    customer = await service.update(organization_id, customer_id, data, user, ctx)
    return CustomerRead.model_validate(customer)


@customers_router.delete(
    "/{customer_id}", response_model=MessageResponse, summary="Delete a customer"
)
async def delete_customer(
    customer_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: CustomersDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.CUSTOMER_WRITE))],
) -> MessageResponse:
    """Refused if the customer has invoices - deactivate instead."""
    await service.delete(organization_id, customer_id, user, ctx)
    return MessageResponse(message="Customer deleted")


@customers_router.get(
    "/{customer_id}/statement", response_model=CustomerStatement, summary="Customer statement"
)
async def customer_statement(
    customer_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    service: CustomersDep,
    _: Annotated[None, Depends(require_permission(Permission.CUSTOMER_READ))],
    as_of: Annotated[dt.date | None, Query()] = None,
) -> CustomerStatement:
    effective = as_of or today
    customer, count, invoiced, paid, overdue = await service.statement(
        organization_id, customer_id, as_of=effective
    )
    outstanding = invoiced - paid
    return CustomerStatement(
        customer=CustomerRead.model_validate(customer),
        invoice_count=count,
        total_invoiced=invoiced,
        total_paid=paid,
        total_outstanding=outstanding,
        overdue_amount=overdue,
        credit_limit=customer.credit_limit,
        credit_available=customer.credit_limit - outstanding,
    )


# =============================================================================
# Leads
# =============================================================================
leads_router = APIRouter(prefix="/leads", tags=["Leads"])


@leads_router.get("", response_model=Page[LeadRead], summary="List leads")
async def list_leads(
    organization_id: ActiveOrganizationId,
    service: LeadsDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.CUSTOMER_READ))],
    lead_status: Annotated[LeadStatus | None, Query(alias="status")] = None,
    owner_id: Annotated[uuid.UUID | None, Query()] = None,
    open_only: Annotated[bool, Query()] = False,
) -> Page[LeadRead]:
    rows, total = await service.paginate(
        organization_id, params, status=lead_status, owner_id=owner_id, open_only=open_only
    )
    return Page.create([LeadRead.model_validate(row) for row in rows], total=total, params=params)


@leads_router.post(
    "", response_model=LeadRead, status_code=status.HTTP_201_CREATED, summary="Create a lead"
)
async def create_lead(
    data: LeadCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: LeadsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.CUSTOMER_WRITE))],
) -> LeadRead:
    return LeadRead.model_validate(await service.create(organization_id, data, user, ctx))


@leads_router.get("/pipeline", response_model=dict[str, str], summary="Pipeline value by stage")
async def lead_pipeline(
    organization_id: ActiveOrganizationId,
    service: LeadsDep,
    _: Annotated[None, Depends(require_permission(Permission.CUSTOMER_READ))],
) -> dict[str, str]:
    """Estimated value per stage. Amounts are strings, like all money here."""
    totals = await service.leads.pipeline_totals(organization_id)
    return {stage: str(amount) for stage, amount in totals.items()}


@leads_router.get("/{lead_id}", response_model=LeadRead, summary="Get a lead")
async def get_lead(
    lead_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: LeadsDep,
    _: Annotated[None, Depends(require_permission(Permission.CUSTOMER_READ))],
) -> LeadRead:
    return LeadRead.model_validate(await service.get(organization_id, lead_id))


@leads_router.patch("/{lead_id}", response_model=LeadRead, summary="Update a lead")
async def update_lead(
    lead_id: uuid.UUID,
    data: LeadUpdate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: LeadsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.CUSTOMER_WRITE))],
) -> LeadRead:
    return LeadRead.model_validate(await service.update(organization_id, lead_id, data, user, ctx))


@leads_router.post(
    "/{lead_id}/convert",
    response_model=CustomerRead,
    status_code=status.HTTP_201_CREATED,
    summary="Convert a lead to a customer",
)
async def convert_lead(
    lead_id: uuid.UUID,
    data: LeadConvert,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: LeadsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.CUSTOMER_WRITE))],
) -> CustomerRead:
    """The lead is kept and linked, so the revenue source stays traceable."""
    customer = await service.convert(organization_id, lead_id, data, user, ctx)
    return CustomerRead.model_validate(customer)


# =============================================================================
# Quotations
# =============================================================================
quotations_router = APIRouter(prefix="/quotations", tags=["Quotations"])


@quotations_router.get("", response_model=Page[QuotationRead], summary="List quotations")
async def list_quotations(
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    service: QuotationsDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_READ))],
    customer_id: Annotated[uuid.UUID | None, Query()] = None,
    quote_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[QuotationRead]:
    rows, total = await service.paginate(
        organization_id, params, customer_id=customer_id, status=quote_status
    )
    return Page.create(
        [_quotation_response(row, today) for row in rows], total=total, params=params
    )


@quotations_router.post(
    "",
    response_model=QuotationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a quotation",
)
async def create_quotation(
    data: QuotationCreate,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: QuotationsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_WRITE))],
) -> QuotationRead:
    """Totals and the GST split are computed server-side from the lines."""
    quotation = await service.create(organization_id, data, user, ctx)
    return _quotation_response(await service.get(organization_id, quotation.id), today)


@quotations_router.get("/{quotation_id}", response_model=QuotationRead, summary="Get a quotation")
async def get_quotation(
    quotation_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    service: QuotationsDep,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_READ))],
) -> QuotationRead:
    return _quotation_response(await service.get(organization_id, quotation_id), today)


@quotations_router.patch(
    "/{quotation_id}", response_model=QuotationRead, summary="Update a quotation"
)
async def update_quotation(
    quotation_id: uuid.UUID,
    data: QuotationUpdate,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: QuotationsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_WRITE))],
) -> QuotationRead:
    await service.update(organization_id, quotation_id, data, user, ctx)
    return _quotation_response(await service.get(organization_id, quotation_id), today)


@quotations_router.post(
    "/{quotation_id}/send", response_model=QuotationRead, summary="Mark as sent"
)
async def send_quotation(
    quotation_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: QuotationsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_WRITE))],
) -> QuotationRead:
    await service.mark_sent(organization_id, quotation_id, user, ctx)
    return _quotation_response(await service.get(organization_id, quotation_id), today)


@quotations_router.post(
    "/{quotation_id}/accept", response_model=QuotationRead, summary="Record acceptance"
)
async def accept_quotation(
    quotation_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: QuotationsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_WRITE))],
) -> QuotationRead:
    await service.respond(organization_id, quotation_id, accepted=True, actor=user, ctx=ctx)
    return _quotation_response(await service.get(organization_id, quotation_id), today)


@quotations_router.post(
    "/{quotation_id}/reject", response_model=QuotationRead, summary="Record rejection"
)
async def reject_quotation(
    quotation_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: QuotationsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_WRITE))],
) -> QuotationRead:
    await service.respond(organization_id, quotation_id, accepted=False, actor=user, ctx=ctx)
    return _quotation_response(await service.get(organization_id, quotation_id), today)


# =============================================================================
# Sales orders
# =============================================================================
orders_router = APIRouter(prefix="/sales-orders", tags=["Sales orders"])


@orders_router.get("", response_model=Page[SalesOrderRead], summary="List sales orders")
async def list_orders(
    organization_id: ActiveOrganizationId,
    service: OrdersDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_READ))],
    customer_id: Annotated[uuid.UUID | None, Query()] = None,
    order_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[SalesOrderRead]:
    rows, total = await service.paginate(
        organization_id, params, customer_id=customer_id, status=order_status
    )
    return Page.create([_order_response(row) for row in rows], total=total, params=params)


@orders_router.post(
    "",
    response_model=SalesOrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a sales order",
)
async def create_order(
    data: SalesOrderCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: OrdersDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_WRITE))],
) -> SalesOrderRead:
    order = await service.create(organization_id, data, user, ctx)
    return _order_response(await service.get(organization_id, order.id))


@orders_router.post(
    "/from-quotation/{quotation_id}",
    response_model=SalesOrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Convert a quotation to an order",
)
async def order_from_quotation(
    quotation_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: OrdersDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_WRITE))],
) -> SalesOrderRead:
    """The quotation becomes CONVERTED, so it cannot spawn a second order."""
    order = await service.from_quotation(organization_id, quotation_id, user, ctx)
    return _order_response(await service.get(organization_id, order.id))


@orders_router.get("/{order_id}", response_model=SalesOrderRead, summary="Get a sales order")
async def get_order(
    order_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: OrdersDep,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_READ))],
) -> SalesOrderRead:
    return _order_response(await service.get(organization_id, order_id))


@orders_router.post("/{order_id}/confirm", response_model=SalesOrderRead, summary="Confirm")
async def confirm_order(
    order_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: OrdersDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_WRITE))],
) -> SalesOrderRead:
    await service.confirm(organization_id, order_id, user, ctx)
    return _order_response(await service.get(organization_id, order_id))


@orders_router.post("/{order_id}/cancel", response_model=SalesOrderRead, summary="Cancel")
async def cancel_order(
    order_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: OrdersDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_WRITE))],
) -> SalesOrderRead:
    """Refused once any part of the order has been invoiced."""
    await service.cancel(organization_id, order_id, user, ctx)
    return _order_response(await service.get(organization_id, order_id))


# =============================================================================
# Invoices
# =============================================================================
invoices_router = APIRouter(prefix="/invoices", tags=["Invoices"])


@invoices_router.get("", response_model=Page[InvoiceRead], summary="List invoices")
async def list_invoices(
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    service: InvoicesDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_READ))],
    customer_id: Annotated[uuid.UUID | None, Query()] = None,
    invoice_status: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
    from_date: Annotated[dt.date | None, Query()] = None,
    to_date: Annotated[dt.date | None, Query()] = None,
    overdue_only: Annotated[bool, Query()] = False,
) -> Page[InvoiceRead]:
    rows, total = await service.paginate(
        organization_id,
        params,
        customer_id=customer_id,
        status=invoice_status,
        from_date=from_date,
        to_date=to_date,
        overdue_only=overdue_only,
    )
    return Page.create([_invoice_response(row, today) for row in rows], total=total, params=params)


@invoices_router.post(
    "", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED, summary="Create an invoice"
)
async def create_invoice(
    data: InvoiceCreate,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: InvoicesDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_WRITE))],
) -> InvoiceRead:
    """Creates a draft, or posts to the ledger immediately with `post: true`.

    A draft has no accounting effect. Posting debits receivables and credits
    revenue and tax.
    """
    invoice = await service.create(organization_id, data, user, ctx)
    return _invoice_response(await service.get(organization_id, invoice.id), today)


@invoices_router.get("/ageing", response_model=ReceivablesAgeing, summary="Receivables ageing")
async def receivables_ageing(
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    session: DbSession,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_READ))],
    as_of: Annotated[dt.date | None, Query()] = None,
) -> ReceivablesAgeing:
    """Outstanding receivables in the standard 0/30/60/90+ buckets."""
    effective = as_of or today
    rows = await InvoiceRepository(session).ageing(organization_id, as_of=effective)

    buckets = [
        AgeingBucket(label=label, amount=amount, invoice_count=count)
        for label, amount, count in rows
    ]
    total = sum((bucket.amount for bucket in buckets), ZERO)
    overdue = sum((b.amount for b in buckets if b.label != "Current"), ZERO)
    return ReceivablesAgeing(
        as_of=effective, buckets=buckets, total_outstanding=total, total_overdue=overdue
    )


@invoices_router.get("/summary", response_model=SalesSummary, summary="Sales summary")
async def sales_summary(
    organization_id: ActiveOrganizationId,
    session: DbSession,
    from_date: Annotated[dt.date, Query()],
    to_date: Annotated[dt.date, Query()],
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
) -> SalesSummary:
    invoices = InvoiceRepository(session)
    rows, _total = await invoices.search(
        organization_id,
        PageParams(page=1, page_size=200),
        from_date=from_date,
        to_date=to_date,
    )
    live = [i for i in rows if i.status.is_posted]
    gross = sum((i.grand_total for i in live), ZERO)
    tax = sum((i.tax_total for i in live), ZERO)
    received = await PaymentRepository(session).received_between(
        organization_id, from_date, to_date
    )
    return SalesSummary(
        from_date=from_date,
        to_date=to_date,
        invoice_count=len(live),
        gross_sales=gross,
        tax_collected=tax,
        net_sales=gross - tax,
        payments_received=received,
        outstanding=sum((i.outstanding for i in live), ZERO),
    )


@invoices_router.get("/{invoice_id}", response_model=InvoiceRead, summary="Get an invoice")
async def get_invoice(
    invoice_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    service: InvoicesDep,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_READ))],
) -> InvoiceRead:
    return _invoice_response(await service.get(organization_id, invoice_id), today)


@invoices_router.patch(
    "/{invoice_id}", response_model=InvoiceRead, summary="Update a draft invoice"
)
async def update_invoice(
    invoice_id: uuid.UUID,
    data: InvoiceUpdate,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: InvoicesDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_WRITE))],
) -> InvoiceRead:
    """Drafts only. A posted invoice is a statutory record - cancel it instead."""
    await service.update(organization_id, invoice_id, data, user, ctx)
    return _invoice_response(await service.get(organization_id, invoice_id), today)


@invoices_router.post(
    "/{invoice_id}/post", response_model=InvoiceRead, summary="Post to the ledger"
)
async def post_invoice(
    invoice_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: InvoicesDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_APPROVE))],
) -> InvoiceRead:
    """Debits receivables, credits revenue and each tax component separately."""
    await service.post(organization_id, invoice_id, user, ctx)
    return _invoice_response(await service.get(organization_id, invoice_id), today)


@invoices_router.post(
    "/{invoice_id}/cancel", response_model=InvoiceRead, summary="Cancel an invoice"
)
async def cancel_invoice(
    invoice_id: uuid.UUID,
    data: CancelInvoiceRequest,
    organization_id: ActiveOrganizationId,
    today: OrganizationToday,
    user: CurrentUser,
    service: InvoicesDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_APPROVE))],
) -> InvoiceRead:
    """Reverses the ledger entry. The invoice is retained, never deleted."""
    await service.cancel(
        organization_id,
        invoice_id,
        reason=data.reason,
        cancellation_date=data.cancellation_date,
        actor=user,
        ctx=ctx,
    )
    return _invoice_response(await service.get(organization_id, invoice_id), today)


@invoices_router.delete(
    "/{invoice_id}", response_model=MessageResponse, summary="Delete a draft invoice"
)
async def delete_invoice(
    invoice_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: InvoicesDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.INVOICE_WRITE))],
) -> MessageResponse:
    await service.delete_draft(organization_id, invoice_id, user, ctx)
    return MessageResponse(message="Draft invoice deleted")


# =============================================================================
# Payments
# =============================================================================
payments_router = APIRouter(prefix="/payments", tags=["Payments"])


@payments_router.get("", response_model=Page[PaymentRead], summary="List payments")
async def list_payments(
    organization_id: ActiveOrganizationId,
    service: PaymentsDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.PAYMENT_READ))],
    customer_id: Annotated[uuid.UUID | None, Query()] = None,
    from_date: Annotated[dt.date | None, Query()] = None,
    to_date: Annotated[dt.date | None, Query()] = None,
    unallocated_only: Annotated[bool, Query()] = False,
) -> Page[PaymentRead]:
    rows, total = await service.paginate(
        organization_id,
        params,
        customer_id=customer_id,
        from_date=from_date,
        to_date=to_date,
        unallocated_only=unallocated_only,
    )
    return Page.create([_payment_response(row) for row in rows], total=total, params=params)


@payments_router.post(
    "", response_model=PaymentRead, status_code=status.HTTP_201_CREATED, summary="Record a payment"
)
async def record_payment(
    data: PaymentCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: PaymentsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PAYMENT_WRITE))],
) -> PaymentRead:
    """Posts the receipt and optionally allocates it to invoices.

    An empty `allocations` list records a payment on account.
    """
    payment = await service.record(organization_id, data, user, ctx)
    return _payment_response(await service.get(organization_id, payment.id))


@payments_router.get("/{payment_id}", response_model=PaymentRead, summary="Get a payment")
async def get_payment(
    payment_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: PaymentsDep,
    _: Annotated[None, Depends(require_permission(Permission.PAYMENT_READ))],
) -> PaymentRead:
    return _payment_response(await service.get(organization_id, payment_id))


@payments_router.post(
    "/{payment_id}/allocate", response_model=PaymentRead, summary="Allocate to invoices"
)
async def allocate_payment(
    payment_id: uuid.UUID,
    data: AllocatePaymentRequest,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: PaymentsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PAYMENT_WRITE))],
) -> PaymentRead:
    """Records which invoices the receipt settles. No further ledger posting -
    the receipt already cleared receivables in aggregate."""
    await service.allocate(organization_id, payment_id, data, user, ctx)
    return _payment_response(await service.get(organization_id, payment_id))


@payments_router.post(
    "/{payment_id}/auto-allocate", response_model=PaymentRead, summary="Auto-allocate oldest first"
)
async def auto_allocate_payment(
    payment_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: PaymentsDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PAYMENT_WRITE))],
) -> PaymentRead:
    await service.auto_allocate(organization_id, payment_id, user, ctx)
    return _payment_response(await service.get(organization_id, payment_id))


__all__ = [
    "customers_router",
    "invoices_router",
    "leads_router",
    "orders_router",
    "payments_router",
    "quotations_router",
]
