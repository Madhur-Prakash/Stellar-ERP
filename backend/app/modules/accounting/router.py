"""Accounting endpoints.

Split into four routers by concern - chart, calendar, journals/entries, reports -
so permissions map cleanly: reading a report is `report:read`, closing a period is
`period:close`, and neither implies the other.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select

from app.core.pagination import Page, PageParams
from app.core.schemas import MessageResponse, with_computed
from app.db.types import ZERO
from app.modules.accounting.exports import export_filename, to_pdf, to_xlsx
from app.modules.accounting.models import AccountType, EntryStatus
from app.modules.accounting.reports import ReportingService
from app.modules.accounting.schemas import (
    AccountCreate,
    AccountLedger,
    AccountRead,
    AccountUpdate,
    AccountWithBalance,
    BalanceSheet,
    BalanceSheetView,
    CashFlowStatement,
    FiscalYearCreate,
    FiscalYearRead,
    JournalCreate,
    JournalEntryCreate,
    JournalEntryLineRead,
    JournalEntryRead,
    JournalEntryUpdate,
    JournalRead,
    ProfitAndLoss,
    ReverseEntryRequest,
    TrialBalance,
)
from app.modules.accounting.service import (
    ChartOfAccountsService,
    FiscalCalendarService,
    JournalService,
    PostingService,
)
from app.modules.accounting.statement_periods import (
    StatementPeriod,
    resolve_statement_period,
)
from app.modules.analytics.router import CalendarDep as OrgCalendarDep
from app.modules.analytics.router import OrgCalendar
from app.modules.auth.dependencies import (
    ActiveOrganizationId,
    CurrentUser,
    DbSession,
    OrganizationToday,
    RequestCtx,
    require_permission,
)
from app.modules.organizations.models import Organization
from app.modules.rbac.permissions import Permission


# ---------------------------------------------------------------------------
# Dependency wiring
# ---------------------------------------------------------------------------
def get_chart_service(session: DbSession) -> ChartOfAccountsService:
    return ChartOfAccountsService(session)


def get_calendar_service(session: DbSession) -> FiscalCalendarService:
    return FiscalCalendarService(session)


def get_journal_service(session: DbSession) -> JournalService:
    return JournalService(session)


def get_posting_service(session: DbSession) -> PostingService:
    return PostingService(session)


def get_reporting_service(session: DbSession) -> ReportingService:
    return ReportingService(session)


ChartDep = Annotated[ChartOfAccountsService, Depends(get_chart_service)]
CalendarDep = Annotated[FiscalCalendarService, Depends(get_calendar_service)]
JournalDep = Annotated[JournalService, Depends(get_journal_service)]
PostingDep = Annotated[PostingService, Depends(get_posting_service)]
ReportingDep = Annotated[ReportingService, Depends(get_reporting_service)]


def _entry_response(entry: object) -> JournalEntryRead:
    """Flatten an entry and its lines into the response shape.

    Account code/name are denormalised onto each line so the client can render a
    ledger without a second request per account.
    """
    from app.modules.accounting.models import JournalEntry

    assert isinstance(entry, JournalEntry)  # noqa: S101

    # Net movement across every cash-equivalent line. Netting rather than taking the
    # first one matters for a transfer between two of your own accounts: one is debited
    # and the other credited, the net is zero, and reporting it as either "in" or "out"
    # would double-count money that never left the business.
    cash_lines = [line for line in entry.lines if line.account.subtype.is_cash_equivalent]
    net = sum((line.debit - line.credit for line in cash_lines), start=ZERO)
    direction = "in" if net > 0 else "out" if net < 0 else None

    return with_computed(
        JournalEntryRead,
        entry,
        journal_code=entry.journal.code,
        cash_direction=direction,
        cash_amount=abs(net),
        lines=[
            with_computed(
                JournalEntryLineRead,
                line,
                account_code=line.account.code,
                account_name=line.account.name,
            )
            for line in entry.lines
        ],
    )


# =============================================================================
# Chart of accounts
# =============================================================================
accounts_router = APIRouter(prefix="/accounts", tags=["Chart of accounts"])


@accounts_router.get("", response_model=list[AccountWithBalance], summary="List accounts")
async def list_accounts(
    organization_id: ActiveOrganizationId,
    chart: ChartDep,
    reporting: ReportingDep,
    today: OrganizationToday,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_READ))],
    account_type: Annotated[AccountType | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
    postable_only: Annotated[bool, Query()] = False,
    as_of: Annotated[dt.date | None, Query(description="Balances as at this date")] = None,
) -> list[AccountWithBalance]:
    """The chart of accounts with each account's balance."""
    accounts = await chart.list_accounts(
        organization_id,
        account_type=account_type,
        include_inactive=include_inactive,
        postable_only=postable_only,
    )
    effective = as_of or today
    balances = await reporting.accounts.balances(organization_id, to_date=effective)

    result: list[AccountWithBalance] = []
    for account in accounts:
        balance = balances.get(account.id)
        debit = balance.total_debit if balance else ZERO
        credit = balance.total_credit if balance else ZERO
        result.append(
            with_computed(
                AccountWithBalance,
                account,
                total_debit=debit,
                total_credit=credit,
                balance=account.signed_balance(debit, credit),
            )
        )
    return result


@accounts_router.post(
    "",
    response_model=AccountRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def create_account(
    data: AccountCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    chart: ChartDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> AccountRead:
    account = await chart.create_account(organization_id, data, user, ctx)
    return AccountRead.model_validate(account)


@accounts_router.get("/{account_id}", response_model=AccountRead, summary="Get an account")
async def get_account(
    account_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    chart: ChartDep,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_READ))],
) -> AccountRead:
    return AccountRead.model_validate(await chart.get_account(organization_id, account_id))


@accounts_router.patch("/{account_id}", response_model=AccountRead, summary="Update an account")
async def update_account(
    account_id: uuid.UUID,
    data: AccountUpdate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    chart: ChartDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> AccountRead:
    account = await chart.update_account(organization_id, account_id, data, user, ctx)
    return AccountRead.model_validate(account)


@accounts_router.delete(
    "/{account_id}", response_model=MessageResponse, summary="Delete an account"
)
async def delete_account(
    account_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    chart: ChartDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> MessageResponse:
    """Soft-delete. Refused if the account has children or any postings."""
    await chart.delete_account(organization_id, account_id, user, ctx)
    return MessageResponse(message="Account deleted")


@accounts_router.get("/{account_id}/ledger", response_model=AccountLedger, summary="Account ledger")
async def account_ledger(
    account_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    reporting: ReportingDep,
    from_date: Annotated[dt.date, Query()],
    to_date: Annotated[dt.date, Query()],
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_READ))],
) -> AccountLedger:
    """Movements with an opening balance and a running total."""
    return await reporting.account_ledger(
        organization_id, account_id, from_date=from_date, to_date=to_date
    )


# =============================================================================
# Fiscal calendar
# =============================================================================
calendar_router = APIRouter(prefix="/fiscal-years", tags=["Fiscal calendar"])


@calendar_router.get("", response_model=list[FiscalYearRead], summary="List fiscal years")
async def list_fiscal_years(
    organization_id: ActiveOrganizationId,
    calendar: CalendarDep,
    _: Annotated[None, Depends(require_permission(Permission.PERIOD_READ))],
) -> list[FiscalYearRead]:
    years = await calendar.list_years(organization_id)
    return [FiscalYearRead.model_validate(year) for year in years]


@calendar_router.post(
    "",
    response_model=FiscalYearRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a fiscal year",
)
async def create_fiscal_year(
    data: FiscalYearCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    calendar: CalendarDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PERIOD_CLOSE))],
) -> FiscalYearRead:
    """Creates the year and, by default, its monthly periods."""
    year = await calendar.create_year(organization_id, data, user, ctx)
    return FiscalYearRead.model_validate(year)


@calendar_router.post(
    "/periods/{period_id}/close", response_model=MessageResponse, summary="Close a period"
)
async def close_period(
    period_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    calendar: CalendarDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PERIOD_CLOSE))],
    lock: Annotated[bool, Query(description="Lock permanently instead of soft close")] = False,
) -> MessageResponse:
    """No further postings. Earlier periods must be closed first."""
    period = await calendar.close_period(organization_id, period_id, user, lock=lock, ctx=ctx)
    return MessageResponse(message=f"{period.name} is now {period.status}")


@calendar_router.post(
    "/periods/{period_id}/reopen", response_model=MessageResponse, summary="Reopen a period"
)
async def reopen_period(
    period_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    calendar: CalendarDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PERIOD_CLOSE))],
) -> MessageResponse:
    """Locked periods cannot be reopened through the API."""
    period = await calendar.reopen_period(organization_id, period_id, user, ctx)
    return MessageResponse(message=f"{period.name} is open")


# =============================================================================
# Journals and entries
# =============================================================================
journals_router = APIRouter(prefix="/journals", tags=["Journals"])


@journals_router.get("", response_model=list[JournalRead], summary="List journals")
async def list_journals(
    organization_id: ActiveOrganizationId,
    journals: JournalDep,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_READ))],
) -> list[JournalRead]:
    return [
        JournalRead.model_validate(journal)
        for journal in await journals.list_journals(organization_id)
    ]


@journals_router.post(
    "",
    response_model=JournalRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a journal",
)
async def create_journal(
    data: JournalCreate,
    organization_id: ActiveOrganizationId,
    journals: JournalDep,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> JournalRead:
    return JournalRead.model_validate(await journals.create_journal(organization_id, data))


entries_router = APIRouter(prefix="/journal-entries", tags=["Journal entries"])


@entries_router.get("", response_model=Page[JournalEntryRead], summary="List entries")
async def list_entries(
    organization_id: ActiveOrganizationId,
    posting: PostingDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_READ))],
    journal_id: Annotated[uuid.UUID | None, Query()] = None,
    entry_status: Annotated[EntryStatus | None, Query(alias="status")] = None,
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    from_date: Annotated[dt.date | None, Query()] = None,
    to_date: Annotated[dt.date | None, Query()] = None,
) -> Page[JournalEntryRead]:
    entries, total = await posting.entries.paginate_entries(
        organization_id,
        params,
        journal_id=journal_id,
        status=entry_status,
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
    )
    return Page.create([_entry_response(entry) for entry in entries], total=total, params=params)


@entries_router.post(
    "",
    response_model=JournalEntryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an entry",
)
async def create_entry(
    data: JournalEntryCreate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    posting: PostingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_WRITE))],
) -> JournalEntryRead:
    """Creates a draft, or posts immediately with `post: true`.

    The payload must balance - debits equal credits - or it is rejected with a
    field-level error before anything is written.
    """
    entry = await posting.create_entry(organization_id, data, user, ctx)
    return _entry_response(await posting.get_entry(organization_id, entry.id))


@entries_router.get("/{entry_id}", response_model=JournalEntryRead, summary="Get an entry")
async def get_entry(
    entry_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    posting: PostingDep,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_READ))],
) -> JournalEntryRead:
    return _entry_response(await posting.get_entry(organization_id, entry_id))


@entries_router.patch(
    "/{entry_id}", response_model=JournalEntryRead, summary="Update a draft entry"
)
async def update_entry(
    entry_id: uuid.UUID,
    data: JournalEntryUpdate,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    posting: PostingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_WRITE))],
) -> JournalEntryRead:
    """Drafts only. A posted entry is corrected by reversal."""
    await posting.update_entry(organization_id, entry_id, data, user, ctx)
    return _entry_response(await posting.get_entry(organization_id, entry_id))


@entries_router.post("/{entry_id}/post", response_model=JournalEntryRead, summary="Post an entry")
async def post_entry(
    entry_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    posting: PostingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_POST))],
) -> JournalEntryRead:
    """Moves the entry into the books and assigns its number."""
    await posting.post_entry(organization_id, entry_id, user, ctx)
    return _entry_response(await posting.get_entry(organization_id, entry_id))


@entries_router.post(
    "/{entry_id}/reverse", response_model=JournalEntryRead, summary="Reverse an entry"
)
async def reverse_entry(
    entry_id: uuid.UUID,
    data: ReverseEntryRequest,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    posting: PostingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_REVERSE))],
) -> JournalEntryRead:
    """Posts a mirror entry cancelling this one. Returns the reversal."""
    reversal = await posting.reverse_entry(
        organization_id,
        entry_id,
        user,
        reversal_date=data.reversal_date,
        narration=data.narration,
        ctx=ctx,
    )
    return _entry_response(await posting.get_entry(organization_id, reversal.id))


@entries_router.delete(
    "/{entry_id}", response_model=MessageResponse, summary="Delete a draft entry"
)
async def delete_entry(
    entry_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    posting: PostingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_WRITE))],
) -> MessageResponse:
    await posting.delete_draft(organization_id, entry_id, user, ctx)
    return MessageResponse(message="Draft entry deleted")


# =============================================================================
# Reports
# =============================================================================
reports_router = APIRouter(prefix="/reports", tags=["Financial reports"])


@reports_router.get("/trial-balance", response_model=TrialBalance, summary="Trial balance")
async def trial_balance(
    organization_id: ActiveOrganizationId,
    reporting: ReportingDep,
    today: OrganizationToday,
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
    as_of: Annotated[dt.date | None, Query()] = None,
    from_date: Annotated[dt.date | None, Query()] = None,
    include_zero: Annotated[bool, Query()] = False,
) -> TrialBalance:
    """Total debits must equal total credits - see `is_balanced`."""
    return await reporting.trial_balance(
        organization_id,
        as_of=as_of or today,
        from_date=from_date,
        include_zero=include_zero,
    )


@reports_router.get("/profit-and-loss", response_model=ProfitAndLoss, summary="Profit & loss")
async def profit_and_loss(
    organization_id: ActiveOrganizationId,
    reporting: ReportingDep,
    from_date: Annotated[dt.date, Query()],
    to_date: Annotated[dt.date, Query()],
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
) -> ProfitAndLoss:
    return await reporting.profit_and_loss(organization_id, from_date=from_date, to_date=to_date)


@reports_router.get("/balance-sheet", response_model=BalanceSheet, summary="Balance sheet")
async def balance_sheet(
    organization_id: ActiveOrganizationId,
    reporting: ReportingDep,
    today: OrganizationToday,
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
    as_of: Annotated[dt.date | None, Query()] = None,
) -> BalanceSheet:
    """Assets must equal liabilities plus equity - see `is_balanced`.

    `current_period_earnings` carries this year's profit, which is not yet in
    retained earnings until the year is closed.
    """
    return await reporting.balance_sheet(organization_id, as_of=as_of or today)


async def _sheet_for(
    reporting: ReportingService,
    organization_id: uuid.UUID,
    calendar: OrgCalendar,
    period: StatementPeriod,
    as_of: dt.date | None,
    compare_to: dt.date | None,
    comparative: bool,
) -> tuple[BalanceSheet, BalanceSheet | None, dt.date, dt.date | None]:
    """Resolve the dates once, then build one or two statements.

    Shared by the JSON route and both exports so a downloaded file cannot disagree with what
    was on screen when the button was pressed - which is the whole reason exports are built
    from the same call rather than each assembling its own report.
    """
    if period is StatementPeriod.CUSTOM:
        at = as_of or calendar.today
        against = compare_to
    else:
        at, against = resolve_statement_period(
            period, today=calendar.today, fiscal_start_month=calendar.fiscal_start_month
        )
        # An explicit `as_of` still wins, so a named period can be nudged without dropping
        # to CUSTOM and losing its label.
        at = as_of or at
        against = compare_to or against

    if not comparative:
        against = None

    sheet = await reporting.balance_sheet(organization_id, as_of=at)
    prior = (
        await reporting.balance_sheet(organization_id, as_of=against)
        if against is not None
        else None
    )
    return sheet, prior, at, against


@reports_router.get(
    "/balance-sheet/view",
    response_model=BalanceSheetView,
    summary="Balance sheet for a period, with its opening position",
)
async def balance_sheet_view(
    organization_id: ActiveOrganizationId,
    reporting: ReportingDep,
    calendar: OrgCalendarDep,
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
    period: Annotated[StatementPeriod, Query()] = StatementPeriod.TO_DATE,
    as_of: Annotated[dt.date | None, Query()] = None,
    compare_to: Annotated[dt.date | None, Query()] = None,
    comparative: Annotated[bool, Query()] = True,
) -> BalanceSheetView:
    """The statement, and the position it opened from.

    **A balance sheet is a position at a date, not a total over a window.** Asking for "the
    balance sheet for this quarter" therefore means "as at the last day of it", and the period
    only decides which date that is - see `statement_periods.py`. What makes the period
    genuinely useful is the second column: the closing position of the day before the window
    opened, which is this window's opening position, so the pair shows movement.

    A window still running is cut off at today rather than reported to its future end, which
    would date the statement to something that has not happened yet.
    """
    sheet, prior, _at, _against = await _sheet_for(
        reporting, organization_id, calendar, period, as_of, compare_to, comparative
    )
    return BalanceSheetView(
        period=period,
        period_label=period.label,
        sheet=sheet,
        comparative=prior,
        currency=calendar.currency,
    )


@reports_router.get(
    "/balance-sheet/export",
    summary="Balance sheet as a spreadsheet or a PDF",
    response_class=Response,
)
async def balance_sheet_export(
    organization_id: ActiveOrganizationId,
    reporting: ReportingDep,
    calendar: OrgCalendarDep,
    session: DbSession,
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
    fmt: Annotated[Literal["xlsx", "pdf"], Query(alias="format")] = "xlsx",
    period: Annotated[StatementPeriod, Query()] = StatementPeriod.TO_DATE,
    as_of: Annotated[dt.date | None, Query()] = None,
    compare_to: Annotated[dt.date | None, Query()] = None,
    comparative: Annotated[bool, Query()] = True,
) -> Response:
    """The same statement as a file, built from the same call that renders the screen.

    `Content-Disposition: attachment` with a dated filename, so the browser saves rather than
    tries to display it and the file says what it is once it is sitting in a downloads folder.
    """
    sheet, prior, at, _against = await _sheet_for(
        reporting, organization_id, calendar, period, as_of, compare_to, comparative
    )

    name = (
        await session.execute(select(Organization.name).where(Organization.id == organization_id))
    ).scalar_one_or_none() or "Balance sheet"

    writer = to_xlsx if fmt == "xlsx" else to_pdf
    payload = writer(sheet, organization=name, currency=calendar.currency, comparative=prior)
    media = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if fmt == "xlsx"
        else "application/pdf"
    )
    filename = export_filename("balance-sheet", at, fmt)
    return Response(
        content=payload,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@reports_router.get("/cash-flow", response_model=CashFlowStatement, summary="Cash flow")
async def cash_flow(
    organization_id: ActiveOrganizationId,
    reporting: ReportingDep,
    from_date: Annotated[dt.date, Query()],
    to_date: Annotated[dt.date, Query()],
    _: Annotated[None, Depends(require_permission(Permission.REPORT_READ))],
) -> CashFlowStatement:
    """Direct method: actual cash movements grouped by counter-account."""
    return await reporting.cash_flow(organization_id, from_date=from_date, to_date=to_date)


__all__ = [
    "accounts_router",
    "calendar_router",
    "entries_router",
    "journals_router",
    "reports_router",
]
