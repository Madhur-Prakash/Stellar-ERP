"""Analytics - the numbers a business owner looks at first.

**Analytics composes the reporting service; it does not re-query the ledger.** The
headline revenue, expense, and profit figures come from
:meth:`ReportingService.profit_and_loss`, the same call that renders the P&L screen.
That is the whole design: a dashboard tile that disagrees with the statement it
summarises is worse than no tile, because it destroys trust in both, and nobody can
tell which one is wrong.

The one exception is the monthly trend, which uses a single grouped query rather than
twelve P&L computations. It reuses :func:`signed_balance` and ``POSTED_STATUSES`` so
the convention cannot drift, and a test asserts the series sums to the P&L for the
same span. Where a shortcut is taken for performance, the reconciliation is pinned by
a test rather than by hope.

**Control-account reconciliation is surfaced, not hidden.** The sales module's
receivables total and the Accounts Receivable ledger balance are computed two
different ways from two different tables, and they must agree. Real accounting
practice checks this monthly; most small-business software never shows it, so the
drift is found a year later by an accountant. It costs one extra query here.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.types import ZERO
from app.modules.accounting.coa_template import SystemAccount
from app.modules.accounting.models import (
    Account,
    AccountType,
    JournalEntry,
    JournalEntryLine,
)
from app.modules.accounting.reports import ReportingService, signed_balance
from app.modules.accounting.repository import POSTED_STATUSES, AccountRepository
from app.modules.analytics.periods import (
    DateRange,
    Period,
    month_buckets,
    month_label,
    percent_change,
    previous_comparable,
    resolve_period,
)
from app.modules.purchasing.models import Bill, BillStatus, StockLevel
from app.modules.sales.models import Invoice, InvoiceLine, InvoiceStatus

log = get_logger(__name__)

#: Rows in a "top N" list. Ten is what fits on a dashboard card without scrolling;
#: beyond that it is a report, not a summary.
DEFAULT_TOP_N = 5


@dataclass(frozen=True, slots=True)
class Movement:
    """A figure with its comparison against the previous equivalent period."""

    current: Decimal
    previous: Decimal
    #: ``None`` when the previous period gives no basis for a percentage - see
    #: :func:`~app.modules.analytics.periods.percent_change`.
    change_percent: Decimal | None

    @classmethod
    def of(cls, current: Decimal, previous: Decimal) -> Movement:
        return cls(
            current=current,
            previous=previous,
            change_percent=percent_change(current, previous),
        )


@dataclass(frozen=True, slots=True)
class TrendPoint:
    label: str
    start: dt.date
    end: dt.date
    income: Decimal
    expenses: Decimal
    profit: Decimal


@dataclass(frozen=True, slots=True)
class RankedRow:
    id: uuid.UUID | None
    label: str
    amount: Decimal
    count: int


@dataclass(frozen=True, slots=True)
class Ranking:
    """A top-N list plus the total across *all* rows.

    The total is what makes the list readable: "these five customers are 62% of
    revenue" is a fact about concentration risk, while five names and five numbers
    on their own are just five numbers.
    """

    rows: list[RankedRow]
    total: Decimal


@dataclass(frozen=True, slots=True)
class ControlCheck:
    """One control-account comparison.

    ``ledger`` is the balance on the control account; ``subledger`` is the same
    figure derived from the documents that should have produced it.
    """

    name: str
    ledger: Decimal
    subledger: Decimal

    @property
    def difference(self) -> Decimal:
        return self.ledger - self.subledger

    @property
    def agrees(self) -> bool:
        # A rupee of tolerance absorbs rounding on individual documents. Anything
        # larger is a real discrepancy, not a presentation artefact.
        return abs(self.difference) <= Decimal("1")


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    period: Period
    span: DateRange
    comparison: DateRange
    currency: str

    revenue: Movement
    expenses: Movement
    gross_profit: Movement
    net_profit: Movement

    #: Position figures, as at the end of the window rather than movement within it.
    #: A balance is a point-in-time fact: "cash over the last 30 days" is not a
    #: number, and averaging it would answer a question nobody asked.
    cash: Decimal
    receivables: Decimal
    payables: Decimal
    inventory_value: Decimal

    overdue_receivables: Decimal
    overdue_payables: Decimal

    invoices_issued: int
    bills_received: int


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.reports = ReportingService(session)
        self.accounts = AccountRepository(session)

    # -----------------------------------------------------------------------
    # Dashboard
    # -----------------------------------------------------------------------
    async def dashboard(
        self,
        organization_id: uuid.UUID,
        *,
        period: Period = Period.THIS_MONTH,
        today: dt.date,
        fiscal_start_month: int = 4,
        currency: str = "INR",
    ) -> DashboardSnapshot:
        """Headline figures for one window, with a like-for-like comparison.

        ``today`` is passed in rather than read from the clock so the same request
        replayed for debugging resolves the same dates.
        """
        span = resolve_period(period, today=today, fiscal_start_month=fiscal_start_month)
        comparison = previous_comparable(period, span)

        # Two P&L computations, not a bespoke aggregate. Slightly more work than one
        # clever query, and it guarantees the tiles match the statement.
        current = await self.reports.profit_and_loss(
            organization_id, from_date=span.start, to_date=span.end
        )
        prior = await self.reports.profit_and_loss(
            organization_id, from_date=comparison.start, to_date=comparison.end
        )

        position = await self._position(organization_id, as_of=span.end)
        overdue = await self._overdue(organization_id, as_of=span.end)
        counts = await self._document_counts(organization_id, span)

        return DashboardSnapshot(
            period=period,
            span=span,
            comparison=comparison,
            currency=currency,
            revenue=Movement.of(current.total_income, prior.total_income),
            expenses=Movement.of(current.total_expenses, prior.total_expenses),
            gross_profit=Movement.of(current.gross_profit, prior.gross_profit),
            net_profit=Movement.of(current.net_profit, prior.net_profit),
            cash=position["cash"],
            receivables=position["receivables"],
            payables=position["payables"],
            inventory_value=position["inventory"],
            overdue_receivables=overdue["receivables"],
            overdue_payables=overdue["payables"],
            invoices_issued=counts["invoices"],
            bills_received=counts["bills"],
        )

    async def _position(self, organization_id: uuid.UUID, *, as_of: dt.date) -> dict[str, Decimal]:
        """Cash, receivables, payables, and stock value from the ledger.

        **From the ledger, not from the documents.** The invoice table also knows what
        is outstanding, but the Accounts Receivable account is the authoritative
        figure - it is what the balance sheet reports and what an accountant checks.
        Where the two disagree, :meth:`control_checks` is what surfaces it.
        """
        accounts = await self.accounts.list_for_org(
            organization_id, include_inactive=True, postable_only=True
        )
        balances = await self.accounts.balances(organization_id, to_date=as_of)

        totals = {"cash": ZERO, "receivables": ZERO, "payables": ZERO, "inventory": ZERO}

        for account in accounts:
            balance = balances.get(account.id)
            if balance is None:
                continue
            amount = signed_balance(account.account_type, balance.total_debit, balance.total_credit)

            if account.subtype.is_cash_equivalent:
                totals["cash"] += amount
            elif account.system_key == SystemAccount.ACCOUNTS_RECEIVABLE:
                totals["receivables"] += amount
            elif account.system_key == SystemAccount.ACCOUNTS_PAYABLE:
                totals["payables"] += amount
            elif account.system_key == SystemAccount.INVENTORY:
                totals["inventory"] += amount

        return totals

    async def _overdue(self, organization_id: uuid.UUID, *, as_of: dt.date) -> dict[str, Decimal]:
        """Past-due receivables and payables.

        This one *must* come from the documents: a due date lives on an invoice, not
        on a ledger account, so the control account cannot answer "how much is late".
        """
        receivable = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(Invoice.grand_total - Invoice.paid_amount), ZERO)
                ).where(
                    Invoice.organization_id == organization_id,
                    Invoice.deleted_at.is_(None),
                    Invoice.status.in_((InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID)),
                    Invoice.due_date < as_of,
                )
            )
        ).scalar_one()

        payable = (
            await self.session.execute(
                select(func.coalesce(func.sum(Bill.grand_total - Bill.paid_amount), ZERO)).where(
                    Bill.organization_id == organization_id,
                    Bill.deleted_at.is_(None),
                    Bill.status.in_((BillStatus.POSTED, BillStatus.PARTIALLY_PAID)),
                    Bill.due_date < as_of,
                )
            )
        ).scalar_one()

        return {"receivables": receivable, "payables": payable}

    async def _document_counts(self, organization_id: uuid.UUID, span: DateRange) -> dict[str, int]:
        invoices = (
            await self.session.execute(
                select(func.count())
                .select_from(Invoice)
                .where(
                    Invoice.organization_id == organization_id,
                    Invoice.deleted_at.is_(None),
                    Invoice.status != InvoiceStatus.CANCELLED,
                    Invoice.invoice_date.between(span.start, span.end),
                )
            )
        ).scalar_one()

        bills = (
            await self.session.execute(
                select(func.count())
                .select_from(Bill)
                .where(
                    Bill.organization_id == organization_id,
                    Bill.deleted_at.is_(None),
                    Bill.status != BillStatus.CANCELLED,
                    Bill.bill_date.between(span.start, span.end),
                )
            )
        ).scalar_one()

        return {"invoices": invoices, "bills": bills}

    # -----------------------------------------------------------------------
    # Trend
    # -----------------------------------------------------------------------
    async def trend(self, organization_id: uuid.UUID, *, span: DateRange) -> list[TrendPoint]:
        """Income, expenses, and profit per month across ``span``.

        One grouped query rather than a P&L per month - twelve P&L computations is
        twenty-four round trips for a chart. The sign convention and the posted-status
        filter are the same ones the statements use, and
        ``test_analytics_api.py::test_the_trend_sums_to_the_profit_and_loss`` asserts
        the series adds up to the statement for the same span. That test is what makes
        the shortcut safe.

        Months with no activity are present with zeroes. Omitting them would let a
        chart imply continuity across a gap it cannot see.
        """
        buckets = month_buckets(span)

        rows = (
            await self.session.execute(
                select(
                    func.date_trunc("month", JournalEntry.entry_date).label("month"),
                    Account.account_type,
                    func.coalesce(func.sum(JournalEntryLine.debit), ZERO).label("debit"),
                    func.coalesce(func.sum(JournalEntryLine.credit), ZERO).label("credit"),
                )
                .join(JournalEntry, JournalEntry.id == JournalEntryLine.entry_id)
                .join(Account, Account.id == JournalEntryLine.account_id)
                .where(
                    JournalEntry.organization_id == organization_id,
                    JournalEntry.status.in_(POSTED_STATUSES),
                    JournalEntry.entry_date.between(span.start, span.end),
                    Account.account_type.in_((AccountType.INCOME, AccountType.EXPENSE)),
                )
                .group_by("month", Account.account_type)
            )
        ).all()

        # Keyed by (year, month) rather than by date, because `date_trunc` returns a
        # timestamp and the bucket boundaries are dates.
        income: dict[tuple[int, int], Decimal] = {}
        expenses: dict[tuple[int, int], Decimal] = {}

        for row in rows:
            key = (row.month.year, row.month.month)
            amount = signed_balance(row.account_type, row.debit, row.credit)
            target = income if row.account_type is AccountType.INCOME else expenses
            target[key] = target.get(key, ZERO) + amount

        points: list[TrendPoint] = []
        for bucket in buckets:
            key = (bucket.start.year, bucket.start.month)
            earned = income.get(key, ZERO)
            spent = expenses.get(key, ZERO)
            points.append(
                TrendPoint(
                    label=month_label(bucket.start),
                    start=bucket.start,
                    end=bucket.end,
                    income=earned,
                    expenses=spent,
                    profit=earned - spent,
                )
            )
        return points

    # -----------------------------------------------------------------------
    # Rankings
    # -----------------------------------------------------------------------
    async def top_customers(
        self, organization_id: uuid.UUID, *, span: DateRange, limit: int = DEFAULT_TOP_N
    ) -> Ranking:
        """Customers by invoiced value in the window.

        Ranked on ``taxable_total``, not ``grand_total``. GST collected is money held
        on the government's behalf and passed on - including it would rank a customer
        buying 28% goods above one buying more of a 5% product, which is a fiction
        about who is worth more to the business.
        """
        from app.modules.sales.models import Customer  # avoids an import cycle

        rows = (
            await self.session.execute(
                select(
                    Customer.id,
                    Customer.name,
                    func.coalesce(func.sum(Invoice.taxable_total), ZERO).label("total"),
                    func.count(Invoice.id).label("invoices"),
                )
                .join(Invoice, Invoice.customer_id == Customer.id)
                .where(
                    Invoice.organization_id == organization_id,
                    Invoice.deleted_at.is_(None),
                    Invoice.status != InvoiceStatus.CANCELLED,
                    Invoice.invoice_date.between(span.start, span.end),
                )
                .group_by(Customer.id, Customer.name)
                .order_by(func.sum(Invoice.taxable_total).desc())
                .limit(limit)
            )
        ).all()

        total = (
            await self.session.execute(
                select(func.coalesce(func.sum(Invoice.taxable_total), ZERO)).where(
                    Invoice.organization_id == organization_id,
                    Invoice.deleted_at.is_(None),
                    Invoice.status != InvoiceStatus.CANCELLED,
                    Invoice.invoice_date.between(span.start, span.end),
                )
            )
        ).scalar_one()

        return Ranking(
            rows=[
                RankedRow(id=row.id, label=row.name, amount=row.total, count=row.invoices)
                for row in rows
            ],
            total=total,
        )

    async def top_products(
        self, organization_id: uuid.UUID, *, span: DateRange, limit: int = DEFAULT_TOP_N
    ) -> Ranking:
        """Best-selling lines by taxable value.

        Grouped by the line **description** rather than by a product id: invoice lines
        are deliberately free-text so a service or a one-off charge can be billed
        without inventing a product record. Grouping on a nullable foreign key would
        silently drop exactly those lines.
        """
        rows = (
            await self.session.execute(
                select(
                    InvoiceLine.description,
                    func.coalesce(func.sum(InvoiceLine.taxable_amount), ZERO).label("total"),
                    func.count(InvoiceLine.id).label("lines"),
                )
                .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
                .where(
                    Invoice.organization_id == organization_id,
                    Invoice.deleted_at.is_(None),
                    Invoice.status != InvoiceStatus.CANCELLED,
                    Invoice.invoice_date.between(span.start, span.end),
                )
                .group_by(InvoiceLine.description)
                .order_by(func.sum(InvoiceLine.taxable_amount).desc())
                .limit(limit)
            )
        ).all()

        total = (
            await self.session.execute(
                select(func.coalesce(func.sum(InvoiceLine.taxable_amount), ZERO))
                .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
                .where(
                    Invoice.organization_id == organization_id,
                    Invoice.deleted_at.is_(None),
                    Invoice.status != InvoiceStatus.CANCELLED,
                    Invoice.invoice_date.between(span.start, span.end),
                )
            )
        ).scalar_one()

        return Ranking(
            rows=[
                RankedRow(id=None, label=row.description, amount=row.total, count=row.lines)
                for row in rows
            ],
            total=total,
        )

    # -----------------------------------------------------------------------
    # Control accounts
    # -----------------------------------------------------------------------
    async def control_checks(
        self, organization_id: uuid.UUID, *, as_of: dt.date
    ) -> list[ControlCheck]:
        """Compare each control account against the documents behind it.

        Three independent derivations that must agree:

        * **Accounts Receivable** vs unpaid posted invoices.
        * **Accounts Payable** vs unpaid posted bills.
        * **Inventory** vs the sum of stock valuations.

        This is the monthly reconciliation a bookkeeper does by hand, and it catches
        the class of bug that nothing else does - a document that updated a table but
        not the ledger, or the reverse. It is cheap here and invaluable: without it,
        the drift is found a year later by an accountant who cannot say when it began.
        """
        position = await self._position(organization_id, as_of=as_of)

        invoiced = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(Invoice.grand_total - Invoice.paid_amount), ZERO)
                ).where(
                    Invoice.organization_id == organization_id,
                    Invoice.deleted_at.is_(None),
                    Invoice.status.in_((InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID)),
                    Invoice.invoice_date <= as_of,
                )
            )
        ).scalar_one()

        billed = (
            await self.session.execute(
                select(func.coalesce(func.sum(Bill.grand_total - Bill.paid_amount), ZERO)).where(
                    Bill.organization_id == organization_id,
                    Bill.deleted_at.is_(None),
                    Bill.status.in_((BillStatus.POSTED, BillStatus.PARTIALLY_PAID)),
                    Bill.bill_date <= as_of,
                )
            )
        ).scalar_one()

        # `stock_value`, not the `total_value` property. Both exist and mean the same
        # thing, but only the column can be aggregated in SQL - the property would be
        # passed to `func.sum` as a Python callable.
        stock = (
            await self.session.execute(
                select(func.coalesce(func.sum(StockLevel.stock_value), ZERO)).where(
                    StockLevel.organization_id == organization_id
                )
            )
        ).scalar_one()

        return [
            ControlCheck("Accounts receivable", position["receivables"], invoiced),
            ControlCheck("Accounts payable", position["payables"], billed),
            ControlCheck("Inventory", position["inventory"], stock),
        ]


__all__ = [
    "AnalyticsService",
    "ControlCheck",
    "DashboardSnapshot",
    "Movement",
    "RankedRow",
    "Ranking",
    "TrendPoint",
]
