"""Sales data access."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import ColumnElement, Select, and_, case, func, or_, select
from sqlalchemy.orm import selectinload

from app.core.pagination import PageParams
from app.db.repository import BaseRepository
from app.db.types import ZERO
from app.modules.sales.models import (
    Customer,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Lead,
    LeadStatus,
    Payment,
    PaymentAllocation,
    Quotation,
    QuotationLine,
    SalesOrder,
    SalesOrderLine,
)

#: Invoice states that represent a real, outstanding receivable. Drafts have no
#: accounting effect and cancelled invoices have been reversed, so neither is a
#: debt anyone owes.
LIVE_INVOICE_STATUSES = (
    InvoiceStatus.POSTED,
    InvoiceStatus.PARTIALLY_PAID,
    InvoiceStatus.PAID,
)


class CustomerRepository(BaseRepository[Customer]):
    model = Customer
    sortable_fields = frozenset({"name", "code", "created_at"})
    default_sort = "name"

    async def get_for_org(
        self, organization_id: uuid.UUID, customer_id: uuid.UUID
    ) -> Customer | None:
        return await self.get_by(id=customer_id, organization_id=organization_id)

    async def search(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        query: str | None = None,
        include_inactive: bool = False,
    ) -> tuple[Sequence[Customer], int]:
        clauses: list[ColumnElement[bool]] = [Customer.organization_id == organization_id]
        if not include_inactive:
            clauses.append(Customer.is_active.is_(True))
        if query:
            # ILIKE across the three fields someone would actually type. A trigram
            # index is the upgrade path if this ever gets slow.
            pattern = f"%{query}%"
            clauses.append(
                or_(
                    Customer.name.ilike(pattern),
                    Customer.code.ilike(pattern),
                    Customer.email.ilike(pattern),
                )
            )
        return await self.paginate(params, *clauses)

    async def next_code(self, organization_id: uuid.UUID) -> str:
        """Generate a customer code when none is supplied.

        Counts existing customers rather than using a locked sequence: unlike an
        invoice number this is a convenience label with no statutory meaning, so a
        collision is caught by the unique constraint and retried rather than
        prevented by serialising every insert.
        """
        total = await self.count(Customer.organization_id == organization_id)
        return f"CUST-{total + 1:04d}"


class LeadRepository(BaseRepository[Lead]):
    model = Lead
    sortable_fields = frozenset({"name", "status", "estimated_value", "created_at"})
    default_sort = "-created_at"

    async def get_for_org(self, organization_id: uuid.UUID, lead_id: uuid.UUID) -> Lead | None:
        return await self.get_by(id=lead_id, organization_id=organization_id)

    async def search(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        status: LeadStatus | None = None,
        owner_id: uuid.UUID | None = None,
        open_only: bool = False,
    ) -> tuple[Sequence[Lead], int]:
        clauses: list[ColumnElement[bool]] = [Lead.organization_id == organization_id]
        if status is not None:
            clauses.append(Lead.status == status)
        if owner_id is not None:
            clauses.append(Lead.owner_id == owner_id)
        if open_only:
            clauses.append(Lead.status.notin_([LeadStatus.WON, LeadStatus.LOST]))
        return await self.paginate(params, *clauses)

    async def pipeline_totals(self, organization_id: uuid.UUID) -> dict[str, Decimal]:
        """Estimated value per pipeline stage, for the funnel view."""
        query = (
            select(Lead.status, func.coalesce(func.sum(Lead.estimated_value), ZERO))
            .where(Lead.organization_id == organization_id, Lead.deleted_at.is_(None))
            .group_by(Lead.status)
        )
        return {str(status): total for status, total in (await self.session.execute(query)).all()}


class QuotationRepository(BaseRepository[Quotation]):
    model = Quotation
    sortable_fields = frozenset({"quotation_number", "quotation_date", "grand_total"})
    default_sort = "-quotation_date"

    def _loaded(self) -> Select[tuple[Quotation]]:
        return select(Quotation).options(
            selectinload(Quotation.lines), selectinload(Quotation.customer)
        )

    async def get_with_lines(
        self, organization_id: uuid.UUID, quotation_id: uuid.UUID
    ) -> Quotation | None:
        query = self._loaded().where(
            Quotation.organization_id == organization_id,
            Quotation.id == quotation_id,
            Quotation.deleted_at.is_(None),
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def search(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        customer_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> tuple[Sequence[Quotation], int]:
        clauses: list[ColumnElement[bool]] = [
            Quotation.organization_id == organization_id,
            Quotation.deleted_at.is_(None),
        ]
        if customer_id is not None:
            clauses.append(Quotation.customer_id == customer_id)
        if status is not None:
            clauses.append(Quotation.status == status)

        count_query = select(func.count()).select_from(Quotation).where(and_(*clauses))
        total = int((await self.session.execute(count_query)).scalar_one())

        query = (
            self._loaded()
            .where(and_(*clauses))
            .order_by(Quotation.quotation_date.desc(), Quotation.created_at.desc())
            .offset(params.offset)
            .limit(params.limit)
        )
        return (await self.session.execute(query)).scalars().all(), total

    async def replace_lines(self, quotation: Quotation, lines: list[QuotationLine]) -> None:
        quotation.lines = lines
        await self.session.flush()


class SalesOrderRepository(BaseRepository[SalesOrder]):
    model = SalesOrder
    sortable_fields = frozenset({"order_number", "order_date", "grand_total"})
    default_sort = "-order_date"

    def _loaded(self) -> Select[tuple[SalesOrder]]:
        return select(SalesOrder).options(
            selectinload(SalesOrder.lines), selectinload(SalesOrder.customer)
        )

    async def get_with_lines(
        self, organization_id: uuid.UUID, order_id: uuid.UUID
    ) -> SalesOrder | None:
        query = self._loaded().where(
            SalesOrder.organization_id == organization_id,
            SalesOrder.id == order_id,
            SalesOrder.deleted_at.is_(None),
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def search(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        customer_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> tuple[Sequence[SalesOrder], int]:
        clauses: list[ColumnElement[bool]] = [
            SalesOrder.organization_id == organization_id,
            SalesOrder.deleted_at.is_(None),
        ]
        if customer_id is not None:
            clauses.append(SalesOrder.customer_id == customer_id)
        if status is not None:
            clauses.append(SalesOrder.status == status)

        count_query = select(func.count()).select_from(SalesOrder).where(and_(*clauses))
        total = int((await self.session.execute(count_query)).scalar_one())

        query = (
            self._loaded()
            .where(and_(*clauses))
            .order_by(SalesOrder.order_date.desc())
            .offset(params.offset)
            .limit(params.limit)
        )
        return (await self.session.execute(query)).scalars().all(), total

    async def replace_lines(self, order: SalesOrder, lines: list[SalesOrderLine]) -> None:
        order.lines = lines
        await self.session.flush()


class InvoiceRepository(BaseRepository[Invoice]):
    model = Invoice
    sortable_fields = frozenset({"invoice_number", "invoice_date", "due_date", "grand_total"})
    default_sort = "-invoice_date"

    def _loaded(self) -> Select[tuple[Invoice]]:
        return select(Invoice).options(selectinload(Invoice.lines), selectinload(Invoice.customer))

    async def get_with_lines(
        self, organization_id: uuid.UUID, invoice_id: uuid.UUID
    ) -> Invoice | None:
        query = self._loaded().where(
            Invoice.organization_id == organization_id,
            Invoice.id == invoice_id,
            Invoice.deleted_at.is_(None),
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def search(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        customer_id: uuid.UUID | None = None,
        status: str | None = None,
        from_date: dt.date | None = None,
        to_date: dt.date | None = None,
        overdue_only: bool = False,
        as_of: dt.date | None = None,
    ) -> tuple[Sequence[Invoice], int]:
        clauses: list[ColumnElement[bool]] = [
            Invoice.organization_id == organization_id,
            Invoice.deleted_at.is_(None),
        ]
        if customer_id is not None:
            clauses.append(Invoice.customer_id == customer_id)
        if status is not None:
            clauses.append(Invoice.status == status)
        if from_date is not None:
            clauses.append(Invoice.invoice_date >= from_date)
        if to_date is not None:
            clauses.append(Invoice.invoice_date <= to_date)
        if overdue_only:
            clauses.extend(
                [
                    Invoice.due_date < as_of,
                    Invoice.status.in_([InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID]),
                    Invoice.paid_amount < Invoice.grand_total,
                ]
            )

        count_query = select(func.count()).select_from(Invoice).where(and_(*clauses))
        total = int((await self.session.execute(count_query)).scalar_one())

        query = (
            self._loaded()
            .where(and_(*clauses))
            .order_by(Invoice.invoice_date.desc(), Invoice.created_at.desc())
            .offset(params.offset)
            .limit(params.limit)
        )
        return (await self.session.execute(query)).scalars().all(), total

    async def replace_lines(self, invoice: Invoice, lines: list[InvoiceLine]) -> None:
        invoice.lines = lines
        await self.session.flush()

    async def outstanding_for_customer(
        self, organization_id: uuid.UUID, customer_id: uuid.UUID
    ) -> Sequence[Invoice]:
        """Unpaid posted invoices, oldest first.

        Oldest-first because that is how payments are applied by default, and how
        any collections conversation proceeds.
        """
        query = (
            select(Invoice)
            .where(
                Invoice.organization_id == organization_id,
                Invoice.customer_id == customer_id,
                Invoice.deleted_at.is_(None),
                Invoice.status.in_([InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID]),
                Invoice.paid_amount < Invoice.grand_total,
            )
            .order_by(Invoice.due_date, Invoice.invoice_date)
        )
        return (await self.session.execute(query)).scalars().all()

    async def customer_totals(
        self, organization_id: uuid.UUID, customer_id: uuid.UUID, *, as_of: dt.date
    ) -> tuple[int, Decimal, Decimal, Decimal]:
        """``(count, invoiced, paid, overdue)`` for one customer, in one query."""
        query = select(
            func.count(),
            func.coalesce(func.sum(Invoice.grand_total), ZERO),
            func.coalesce(func.sum(Invoice.paid_amount), ZERO),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                Invoice.due_date < as_of,
                                Invoice.paid_amount < Invoice.grand_total,
                            ),
                            Invoice.grand_total - Invoice.paid_amount,
                        ),
                        else_=ZERO,
                    )
                ),
                ZERO,
            ),
        ).where(
            Invoice.organization_id == organization_id,
            Invoice.customer_id == customer_id,
            Invoice.deleted_at.is_(None),
            Invoice.status.in_(LIVE_INVOICE_STATUSES),
        )
        count, invoiced, paid, overdue = (await self.session.execute(query)).one()
        return int(count), invoiced, paid, overdue

    async def ageing(
        self, organization_id: uuid.UUID, *, as_of: dt.date
    ) -> list[tuple[str, Decimal, int]]:
        """Receivables bucketed by days overdue.

        Bucketed in Python rather than SQL: the boundaries are presentation policy,
        and a `CASE` expression spanning four ranges is far harder to read and
        change than the loop below. The row count here is the number of *unpaid*
        invoices, which stays small even for a busy business.
        """
        invoices = (
            await self.session.execute(
                select(Invoice.due_date, Invoice.grand_total, Invoice.paid_amount).where(
                    Invoice.organization_id == organization_id,
                    Invoice.deleted_at.is_(None),
                    Invoice.status.in_([InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID]),
                    Invoice.paid_amount < Invoice.grand_total,
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
        totals = {label: (ZERO, 0) for label, _, _ in buckets}

        for due_date, grand_total, paid in invoices:
            outstanding = grand_total - paid
            days = (as_of - due_date).days
            for label, low, high in buckets:
                if low <= days <= high:
                    amount, count = totals[label]
                    totals[label] = (amount + outstanding, count + 1)
                    break

        return [(label, totals[label][0], totals[label][1]) for label, _, _ in buckets]


class PaymentRepository(BaseRepository[Payment]):
    model = Payment
    sortable_fields = frozenset({"payment_number", "payment_date", "amount"})
    default_sort = "-payment_date"

    def _loaded(self) -> Select[tuple[Payment]]:
        return select(Payment).options(
            selectinload(Payment.allocations).selectinload(PaymentAllocation.invoice),
            selectinload(Payment.customer),
        )

    async def get_with_allocations(
        self, organization_id: uuid.UUID, payment_id: uuid.UUID
    ) -> Payment | None:
        query = self._loaded().where(
            Payment.organization_id == organization_id,
            Payment.id == payment_id,
            Payment.deleted_at.is_(None),
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def search(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        customer_id: uuid.UUID | None = None,
        from_date: dt.date | None = None,
        to_date: dt.date | None = None,
        unallocated_only: bool = False,
    ) -> tuple[Sequence[Payment], int]:
        clauses: list[ColumnElement[bool]] = [
            Payment.organization_id == organization_id,
            Payment.deleted_at.is_(None),
        ]
        if customer_id is not None:
            clauses.append(Payment.customer_id == customer_id)
        if from_date is not None:
            clauses.append(Payment.payment_date >= from_date)
        if to_date is not None:
            clauses.append(Payment.payment_date <= to_date)
        if unallocated_only:
            clauses.append(Payment.unallocated_amount > 0)

        count_query = select(func.count()).select_from(Payment).where(and_(*clauses))
        total = int((await self.session.execute(count_query)).scalar_one())

        query = (
            self._loaded()
            .where(and_(*clauses))
            .order_by(Payment.payment_date.desc())
            .offset(params.offset)
            .limit(params.limit)
        )
        return (await self.session.execute(query)).scalars().all(), total

    async def received_between(
        self, organization_id: uuid.UUID, from_date: dt.date, to_date: dt.date
    ) -> Decimal:
        query = select(func.coalesce(func.sum(Payment.amount), ZERO)).where(
            Payment.organization_id == organization_id,
            Payment.deleted_at.is_(None),
            Payment.payment_date >= from_date,
            Payment.payment_date <= to_date,
        )
        return Decimal((await self.session.execute(query)).scalar_one())
