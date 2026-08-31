"""Analytics endpoints."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.exceptions import NotFoundError, ValidationError
from app.db.types import ZERO
from app.modules.analytics.periods import (
    DateRange,
    Period,
    local_date,
    resolve_period,
)
from app.modules.analytics.schemas import (
    ControlCheckRead,
    ControlChecksRead,
    DashboardRead,
    MovementRead,
    PeriodOption,
    PeriodOptions,
    PeriodRead,
    RankedRowRead,
    RankingRead,
    TrendPointRead,
    TrendRead,
)
from app.modules.analytics.service import (
    AnalyticsService,
    DashboardSnapshot,
    Movement,
    Ranking,
)
from app.modules.auth.dependencies import (
    ActiveOrganizationId,
    DbSession,
    require_permission,
)
from app.modules.organizations.models import Organization
from app.modules.rbac.permissions import Permission

router = APIRouter(prefix="/analytics", tags=["Analytics"])


def get_analytics(session: DbSession) -> AnalyticsService:
    return AnalyticsService(session)


AnalyticsDep = Annotated[AnalyticsService, Depends(get_analytics)]


class OrgCalendar:
    """The organization's fiscal settings, resolved once per request.

    Its own dependency because every endpoint here needs the same three facts -
    fiscal-year start, timezone, and currency - and reading them ad hoc in each
    handler is how one endpoint ends up computing "this year" on the calendar year
    while another uses the fiscal one.
    """

    def __init__(self, fiscal_start_month: int, timezone: str, currency: str) -> None:
        self.fiscal_start_month = fiscal_start_month
        self.timezone = timezone
        self.currency = currency

    @property
    def today(self) -> dt.date:
        """Today in the organization's timezone.

        Not ``dt.date.today()``: at 00:30 IST it is still the previous day in UTC, so
        "this month" would resolve to the whole of last month for the first five and a
        half hours of every Indian day.
        """
        return local_date(dt.datetime.now(dt.UTC), self.timezone)

    def span(self, period: Period) -> DateRange:
        return resolve_period(period, today=self.today, fiscal_start_month=self.fiscal_start_month)


async def get_calendar(organization_id: ActiveOrganizationId, session: DbSession) -> OrgCalendar:
    row = (
        await session.execute(
            select(
                Organization.fiscal_year_start_month,
                Organization.timezone,
                Organization.currency,
            ).where(Organization.id == organization_id)
        )
    ).one_or_none()

    if row is None:  # pragma: no cover - the org was resolved by the auth dependency
        raise NotFoundError("Organization")

    return OrgCalendar(row.fiscal_year_start_month, row.timezone, row.currency)


CalendarDep = Annotated[OrgCalendar, Depends(get_calendar)]


# ---------------------------------------------------------------------------
# Response assembly
# ---------------------------------------------------------------------------
def _period(span: DateRange) -> PeriodRead:
    return PeriodRead(start=span.start, end=span.end, days=span.days)


def _movement(movement: Movement) -> MovementRead:
    return MovementRead(
        current=movement.current,
        previous=movement.previous,
        change_percent=movement.change_percent,
    )


def _dashboard(snapshot: DashboardSnapshot) -> DashboardRead:
    return DashboardRead(
        period=snapshot.period,
        period_label=snapshot.period.label,
        span=_period(snapshot.span),
        comparison=_period(snapshot.comparison),
        currency=snapshot.currency,
        revenue=_movement(snapshot.revenue),
        expenses=_movement(snapshot.expenses),
        gross_profit=_movement(snapshot.gross_profit),
        net_profit=_movement(snapshot.net_profit),
        cash=snapshot.cash,
        receivables=snapshot.receivables,
        payables=snapshot.payables,
        inventory_value=snapshot.inventory_value,
        overdue_receivables=snapshot.overdue_receivables,
        overdue_payables=snapshot.overdue_payables,
        invoices_issued=snapshot.invoices_issued,
        bills_received=snapshot.bills_received,
    )


def _ranking(ranking: Ranking, span: DateRange) -> RankingRead:
    return RankingRead(
        span=_period(span),
        rows=[
            RankedRowRead(id=row.id, label=row.label, amount=row.amount, count=row.count)
            for row in ranking.rows
        ],
        total=ranking.total,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/periods", response_model=PeriodOptions, summary="Selectable date windows")
async def list_periods(
    calendar: CalendarDep,
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
) -> PeriodOptions:
    """The windows the dashboard offers, plus the organization's fiscal settings.

    Served rather than hard-coded in the frontend so "this financial year" resolves
    to the same dates on both sides. Duplicating the April-start rule in TypeScript
    would eventually disagree with the ledger, and the disagreement would be silent.
    """
    return PeriodOptions(
        options=[PeriodOption(value=period, label=period.label) for period in Period],
        fiscal_year_start_month=calendar.fiscal_start_month,
        today=calendar.today,
    )


@router.get("/dashboard", response_model=DashboardRead, summary="Dashboard figures")
async def dashboard(
    organization_id: ActiveOrganizationId,
    service: AnalyticsDep,
    calendar: CalendarDep,
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
    period: Annotated[Period, Query()] = Period.THIS_MONTH,
) -> DashboardRead:
    """Revenue, expenses, profit, and position for one window.

    Every figure here comes from the same `ReportingService` that renders the P&L and
    balance sheet, so a tile can never disagree with the statement it summarises.

    The response includes the `comparison` window explicitly. "Revenue up 12%" is
    meaningless without knowing 12% against what - and for a month-to-date figure the
    comparison is deliberately truncated to the same number of days, which the user
    can only verify if the dates are shown.
    """
    snapshot = await service.dashboard(
        organization_id,
        period=period,
        today=calendar.today,
        fiscal_start_month=calendar.fiscal_start_month,
        currency=calendar.currency,
    )
    return _dashboard(snapshot)


@router.get("/trend", response_model=TrendRead, summary="Monthly income and expenses")
async def trend(
    organization_id: ActiveOrganizationId,
    service: AnalyticsDep,
    calendar: CalendarDep,
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
    period: Annotated[Period, Query()] = Period.LAST_12_MONTHS,
    from_date: Annotated[dt.date | None, Query()] = None,
    to_date: Annotated[dt.date | None, Query()] = None,
) -> TrendRead:
    """Income, expenses, and profit per calendar month.

    Months with no activity are returned with zeroes rather than omitted: a chart
    that silently skips an empty month draws a straight line across it and implies
    trading that did not happen.

    Explicit dates override ``period``. Both exist because they answer different needs:
    the presets keep a dashboard aligned with the organization's fiscal calendar, while a
    custom range is what someone reconciling one particular fortnight wants - and a chart
    sitting beside a report filtered to those dates has to cover the same window, or the
    two quietly disagree.
    """
    if from_date is not None and to_date is not None:
        if to_date < from_date:
            raise ValidationError("to_date cannot be before from_date")
        span = DateRange(from_date, to_date)
    else:
        span = calendar.span(period)
    points = await service.trend(organization_id, span=span)

    return TrendRead(
        span=_period(span),
        points=[
            TrendPointRead(
                label=point.label,
                start=point.start,
                end=point.end,
                income=point.income,
                expenses=point.expenses,
                profit=point.profit,
            )
            for point in points
        ],
        total_income=sum((point.income for point in points), start=ZERO),
        total_expenses=sum((point.expenses for point in points), start=ZERO),
        total_profit=sum((point.profit for point in points), start=ZERO),
    )


@router.get("/top-customers", response_model=RankingRead, summary="Customers by revenue")
async def top_customers(
    organization_id: ActiveOrganizationId,
    service: AnalyticsDep,
    calendar: CalendarDep,
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
    period: Annotated[Period, Query()] = Period.THIS_FISCAL_YEAR,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> RankingRead:
    """Ranked on taxable value, not the invoice total.

    GST collected is money held on the government's behalf. Including it would rank a
    customer buying 28% goods above one buying more of a 5% product - a fiction about
    who is actually worth more to the business.
    """
    span = calendar.span(period)
    return _ranking(await service.top_customers(organization_id, span=span, limit=limit), span)


@router.get("/top-products", response_model=RankingRead, summary="Best-selling lines")
async def top_products(
    organization_id: ActiveOrganizationId,
    service: AnalyticsDep,
    calendar: CalendarDep,
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
    period: Annotated[Period, Query()] = Period.THIS_FISCAL_YEAR,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> RankingRead:
    """Grouped by line description rather than product id.

    Invoice lines are deliberately free-text so a service or a one-off charge can be
    billed without inventing a product record; grouping on the nullable product
    foreign key would silently drop exactly those lines.
    """
    span = calendar.span(period)
    return _ranking(await service.top_products(organization_id, span=span, limit=limit), span)


@router.get(
    "/control-checks",
    response_model=ControlChecksRead,
    summary="Reconcile control accounts against their documents",
)
async def control_checks(
    organization_id: ActiveOrganizationId,
    service: AnalyticsDep,
    calendar: CalendarDep,
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
    as_of: Annotated[dt.date | None, Query()] = None,
) -> ControlChecksRead:
    """Does the ledger agree with the documents behind it?

    Receivables, payables, and inventory are each derived twice - once from the
    control account, once from the invoices, bills, or stock levels that should have
    produced it. They must agree.

    This is the reconciliation a bookkeeper does monthly by hand. Most
    small-business software never shows it, so a document that updated one table but
    not the other is found a year later by an accountant who cannot say when it
    started. Disagreement is reported, not raised - a broken figure should be
    *visible* rather than turning a useful screen into a 500.
    """
    on = as_of or calendar.today
    checks = await service.control_checks(organization_id, as_of=on)

    return ControlChecksRead(
        as_of=on,
        checks=[
            ControlCheckRead(
                name=check.name,
                ledger=check.ledger,
                subledger=check.subledger,
                difference=check.difference,
                agrees=check.agrees,
            )
            for check in checks
        ],
        all_agree=all(check.agrees for check in checks),
    )


__all__ = ["router"]
