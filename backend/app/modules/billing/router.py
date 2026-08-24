"""Billing endpoints - the simple money in / money out path."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select

from app.core.exceptions import NotFoundError
from app.core.pagination import Page, PageParams
from app.modules.analytics.periods import local_date
from app.modules.auth.dependencies import (
    ActiveOrganizationId,
    CurrentUser,
    DbSession,
    RequestCtx,
    require_permission,
)
from app.modules.billing.schemas import (
    AddCardRequest,
    BankDetailsRead,
    BillingOptions,
    BillingSummary,
    CardRead,
    CategoryRead,
    CreateCategoryRequest,
    CreateMoneyAccountRequest,
    EntryRead,
    MoneyAccountRead,
    RecordEntryRequest,
    ReverseEntryRequest,
    TransferRead,
    TransferRequest,
    UpdateBankDetailsRequest,
    UpdateCardRequest,
)
from app.modules.billing.service import BillingService, Card, Direction, Entry, MoneyAccount
from app.modules.organizations.models import Organization
from app.modules.rbac.permissions import Permission

router = APIRouter(prefix="/billing", tags=["Billing"])


def get_billing(session: DbSession) -> BillingService:
    return BillingService(session)


def _account_response(account: MoneyAccount) -> MoneyAccountRead:
    """One mapper, so the shape cannot drift between the routes that return one."""
    return MoneyAccountRead(
        id=account.id,
        code=account.code,
        name=account.name,
        is_default=account.is_default,
        kind=account.kind,
        card_id=account.card_id,
        card_last4=account.card_last4,
        card_network=account.card_network,
        bank_name=account.bank_name,
        holder_name=account.holder_name,
        account_number_last4=account.account_number_last4,
        is_active=account.is_active,
        can_archive=account.can_archive,
        can_delete=account.can_delete,
        delete_blocked_reason=account.delete_blocked_reason,
    )


def _card_response(card: Card) -> CardRead:
    return CardRead(
        id=card.id,
        label=card.label,
        kind=card.kind,
        network=card.network,
        last4=card.last4,
        account_id=card.account_id,
        account_name=card.account_name,
        is_active=card.is_active,
        holder_name=card.holder_name,
        can_delete=card.can_delete,
        delete_blocked_reason=card.delete_blocked_reason,
    )


BillingDep = Annotated[BillingService, Depends(get_billing)]


async def get_today(organization_id: ActiveOrganizationId, session: DbSession) -> dt.date:
    """Today in the organization's timezone.

    Not the server's UTC date: at 00:30 IST it is still yesterday in UTC, so a form
    defaulting to "today" would pre-fill the wrong date - and an entry dated a day
    early can land in a month that is already closed.
    """
    row = (
        await session.execute(
            select(Organization.timezone).where(Organization.id == organization_id)
        )
    ).scalar_one_or_none()
    if row is None:  # pragma: no cover - resolved by the auth dependency
        raise NotFoundError("Organization")
    return local_date(dt.datetime.now(dt.UTC), row)


TodayDep = Annotated[dt.date, Depends(get_today)]


def _entry(entry: Entry) -> EntryRead:
    return EntryRead(
        id=entry.id,
        entry_number=entry.entry_number,
        date=entry.date,
        direction=entry.direction,
        amount=entry.amount,
        description=entry.description,
        reference=entry.reference,
        party=entry.party,
        category_id=entry.category_id,
        category_name=entry.category_name,
        money_account_id=entry.money_account_id,
        money_account_name=entry.money_account_name,
        created_at=entry.created_at,
        is_reversed=entry.is_reversed,
    )


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
@router.get("/options", response_model=BillingOptions, summary="Categories and accounts")
async def options(
    organization_id: ActiveOrganizationId,
    service: BillingDep,
    session: DbSession,
    today: TodayDep,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_READ))],
) -> BillingOptions:
    """Everything the entry form needs, in one call.

    One request rather than three, because the form cannot render usefully until it
    has all of them and three round trips would show it assembling itself.
    """
    currency = (
        await session.execute(
            select(Organization.currency).where(Organization.id == organization_id)
        )
    ).scalar_one_or_none() or "INR"

    categories = await service.categories(organization_id)
    accounts = await service.money_accounts(organization_id)
    cards = await service.cards(organization_id)

    return BillingOptions(
        categories=[
            CategoryRead(
                id=c.id,
                code=c.code,
                name=c.name,
                direction=c.direction,
                group=c.group,
                is_default=c.is_default,
            )
            for c in categories
        ],
        money_accounts=[_account_response(a) for a in accounts],
        cards=[_card_response(c) for c in cards],
        today=today,
        currency=currency,
    )


@router.post(
    "/categories",
    response_model=CategoryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a category",
)
async def create_category(
    data: CreateCategoryRequest,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: BillingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> CategoryRead:
    """Create an income or expense category from a name alone.

    The built-in list covers a general small business and a household, but it cannot
    anticipate every trade. This is the escape hatch, and deliberately the only
    account-creating path on this screen: the account code, parent group, and subtype
    are all derived, so nobody has to understand the chart of accounts to file a
    payment under "Tempo Hire".

    Guarded on `account:write` rather than `journal:write` - it does add to the chart of
    accounts, and an organization may want that narrower than day-to-day recording.
    """
    category = await service.create_category(
        organization_id, user, name=data.name, direction=data.direction, ctx=ctx
    )
    return CategoryRead(
        id=category.id,
        code=category.code,
        name=category.name,
        direction=category.direction,
        group=category.group,
        is_default=category.is_default,
    )


@router.post(
    "/money-accounts",
    response_model=MoneyAccountRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a cash or bank account",
)
async def create_money_account(
    data: CreateMoneyAccountRequest,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: BillingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> MoneyAccountRead:
    """Create a place money can sit.

    The seeded chart has one till and one current account, which covers a business
    with exactly those. A second bank, a UPI wallet, a card-settlement account, or a
    partner's petty cash are all ordinary - and without this, money that moved through
    a wallet gets filed as cash and no balance matches anything real.
    """
    account = await service.create_money_account(
        organization_id,
        user,
        name=data.name,
        kind=data.kind,
        bank_name=data.bank_name,
        holder_name=data.holder_name,
        account_number=data.account_number,
        ctx=ctx,
    )
    return _account_response(account)


@router.get(
    "/money-accounts",
    response_model=list[MoneyAccountRead],
    summary="Cash, bank, and card accounts",
)
async def list_money_accounts(
    organization_id: ActiveOrganizationId,
    service: BillingDep,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_READ))],
    include_archived: Annotated[bool, Query()] = False,
) -> list[MoneyAccountRead]:
    """Everywhere money can sit.

    Separate from `/options` so the accounts screen can ask for archived ones without the
    recording form ever seeing them - `/options` never includes archived accounts, because a
    picker that offers a closed bank account is a picker that posts to it.
    """
    return [
        _account_response(a)
        for a in await service.money_accounts(organization_id, include_archived=include_archived)
    ]


@router.post(
    "/money-accounts/{account_id}/archive",
    response_model=MoneyAccountRead,
    summary="Archive an account",
)
async def archive_money_account(
    account_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: BillingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> MoneyAccountRead:
    """Stop offering an account without deleting it.

    **Archived, never deleted** - entries already point at it, and its name is how somebody
    recognises them a year later. A closed bank account has to leave the picker without
    taking its history with it.

    A **seeded** account cannot be archived: later modules post to "Cash on Hand" and
    "Primary Bank Account" by role, so the accounting service refuses to deactivate them.
    `MoneyAccountRead.can_archive` says so up front, which is what lets a client avoid
    offering a button that would always fail.
    """
    account = await service.set_money_account_active(
        organization_id, account_id, user, is_active=False, ctx=ctx
    )
    return _account_response(account)


@router.post(
    "/money-accounts/{account_id}/restore",
    response_model=MoneyAccountRead,
    summary="Restore an account",
)
async def restore_money_account(
    account_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: BillingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> MoneyAccountRead:
    """Offer it again when recording a payment."""
    account = await service.set_money_account_active(
        organization_id, account_id, user, is_active=True, ctx=ctx
    )
    return _account_response(account)


@router.get(
    "/money-accounts/{account_id}/details",
    response_model=BankDetailsRead,
    summary="One account's bank details",
)
async def get_bank_details(
    account_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: BillingDep,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_READ))],
) -> BankDetailsRead:
    """The bank, the holder, and **the full account number.**

    Its own route rather than a field on the picker payload, so that decrypting an account
    number is a deliberate request behind its own permission check instead of something
    every load of the recording screen does for every account.

    Returns empty fields rather than a 404 when an account has no details - "this account
    has nothing recorded" is an answer, and a cash box will never have any.
    """
    details = await service.bank_details(organization_id, account_id)
    return BankDetailsRead(
        account_id=account_id,
        name=details.name,
        bank_name=details.bank_name,
        holder_name=details.holder_name,
        account_number=details.account_number,
        account_number_last4=details.account_number_last4,
    )


@router.put(
    "/money-accounts/{account_id}/details",
    response_model=BankDetailsRead,
    summary="Set an account's bank details",
)
async def put_bank_details(
    account_id: uuid.UUID,
    data: UpdateBankDetailsRequest,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: BillingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> BankDetailsRead:
    """Rename an account, and fill in or correct which bank it is at, whose it is, and its
    number.

    A `PUT` because it replaces the whole set: sending a blank field clears it, which is how
    someone removes a number they entered by mistake. The seeded "Primary Bank Account"
    exists before anyone has said which bank it is, so this is the only way that account -
    the one most organizations actually use - ever gets its details.
    """
    details = await service.update_bank_details(
        organization_id,
        account_id,
        user,
        name=data.name,
        bank_name=data.bank_name,
        holder_name=data.holder_name,
        account_number=data.account_number,
        ctx=ctx,
    )
    return BankDetailsRead(
        account_id=account_id,
        name=details.name,
        bank_name=details.bank_name,
        holder_name=details.holder_name,
        account_number=details.account_number,
        account_number_last4=details.account_number_last4,
    )


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
@router.get("/cards", response_model=list[CardRead], summary="Cards on file")
async def list_cards(
    organization_id: ActiveOrganizationId,
    service: BillingDep,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_READ))],
    include_archived: Annotated[bool, Query()] = False,
) -> list[CardRead]:
    """Every card registered, without a number among them."""
    return [
        _card_response(c)
        for c in await service.cards(organization_id, include_archived=include_archived)
    ]


@router.post(
    "/cards",
    response_model=CardRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a card",
)
async def add_card(
    data: AddCardRequest,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: BillingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> CardRead:
    """Register a card so a payment can say which one it went on.

    **The number is validated, reduced to a network and four digits, and discarded.**
    Nothing here or downstream stores it - there is no column that could, which is a
    stronger guarantee than a rule saying not to. A full card number would put this
    database inside PCI DSS scope, and the last four are what a receipt and a bank
    statement both show anyway.

    A credit card gets its own liability account, because spending on one creates a debt
    rather than moving money. A debit card gets none: it names a bank account that already
    exists, and a second account for it would double-count the same money.
    """
    card = await service.create_card(
        organization_id,
        user,
        label=data.label,
        kind=data.kind,
        card_number=data.card_number,
        holder_name=data.holder_name,
        bank_account_id=data.bank_account_id,
        ctx=ctx,
    )
    return _card_response(card)


@router.patch("/cards/{card_id}", response_model=CardRead, summary="Edit a card")
async def update_card(
    card_id: uuid.UUID,
    data: UpdateCardRequest,
    organization_id: ActiveOrganizationId,
    service: BillingDep,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> CardRead:
    """Correct what a card is called, whose name is on it, or which number it is.

    A `PATCH`, so an omitted field is left alone. **The kind is not editable** - see
    `UpdateCardRequest`. A corrected number is read and discarded exactly as on create, and
    can change the derived network as well as the last four digits.
    """
    card = await service.update_card(
        organization_id,
        card_id,
        label=data.label,
        holder_name=data.holder_name,
        card_number=data.card_number,
    )
    return _card_response(card)


@router.delete(
    "/cards/{card_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a card",
)
async def delete_card(
    card_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: BillingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> None:
    """Remove a card entirely, when nothing has been recorded on it.

    **Refused once it has entries**, with archiving offered instead - an entry names the card
    it was made on, and deleting the card would leave that entry pointing at nothing.
    `CardRead.can_delete` says which case a card is in, so a client can offer the right one.

    A credit card's liability account goes with it; a debit card's bank account existed first
    and is left alone.
    """
    await service.delete_card(organization_id, card_id, user, ctx)


@router.delete(
    "/money-accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an account",
)
async def delete_money_account(
    account_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: BillingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> None:
    """Remove a cash box or bank account, when nothing depends on it.

    Refused if it has postings, is a seeded account, or has a card drawing on it -
    `MoneyAccountRead.can_delete` covers all three so the button only appears where it will
    work. Archiving is the answer in every other case, and it keeps the history.
    """
    await service.delete_money_account(organization_id, account_id, user, ctx)


@router.post("/cards/{card_id}/archive", response_model=CardRead, summary="Archive a card")
async def archive_card(
    card_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: BillingDep,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> CardRead:
    """Hide a card from the pickers, keeping its history.

    Archive rather than delete, the same rule as a product: entries already posted point
    at the card's account, and the card is how somebody recognises them a year later.
    """
    return _card_response(await service.set_card_active(organization_id, card_id, active=False))


@router.post("/cards/{card_id}/restore", response_model=CardRead, summary="Restore a card")
async def restore_card(
    card_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: BillingDep,
    _: Annotated[None, Depends(require_permission(Permission.ACCOUNT_WRITE))],
) -> CardRead:
    return _card_response(await service.set_card_active(organization_id, card_id, active=True))


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------
@router.post(
    "/transfers",
    response_model=TransferRead,
    status_code=status.HTTP_201_CREATED,
    summary="Move money between your own accounts",
)
async def create_transfer(
    data: TransferRequest,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: BillingDep,
    ctx: RequestCtx,
    today: TodayDep,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_WRITE))],
) -> TransferRead:
    """Cash to bank, bank to bank, or a payment towards a credit card.

    Posts two lines - debit the destination, credit the source - and neither touches
    income or expense, because moving your own money is neither earning nor spending it.
    A transfer that hit the P&L would inflate both sides by the same amount and leave
    profit right while every other figure was wrong.

    Guarded on `journal:write` rather than a permission of its own: this writes journal
    entries, and inventing a parallel permission for the same underlying capability would
    be security theatre.
    """
    transfer = await service.transfer(
        organization_id,
        user,
        from_account_id=data.from_account_id,
        to_account_id=data.to_account_id,
        amount=data.amount,
        entry_date=data.entry_date or today,
        description=data.description,
        reference=data.reference,
        ctx=ctx,
    )
    return TransferRead(
        entry_id=transfer.entry_id,
        entry_number=transfer.entry_number,
        date=transfer.date,
        amount=transfer.amount,
        description=transfer.description,
        from_account_id=transfer.from_account_id,
        from_account_name=transfer.from_account_name,
        to_account_id=transfer.to_account_id,
        to_account_name=transfer.to_account_name,
    )


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=EntryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record money in or out",
)
async def record_entry(
    data: RecordEntryRequest,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: BillingDep,
    ctx: RequestCtx,
    today: TodayDep,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_WRITE))],
) -> EntryRead:
    """Record one movement. It is posted to the ledger immediately.

    Posted, not saved as a draft - the whole point of this feature is that the figure
    shows up on the dashboard, and a draft entry does not reach any report. "I recorded
    it and it is not showing" would be the worst outcome for the one screen meant to be
    effortless.

    Because it is a real ledger posting, it appears in the trial balance, the P&L, the
    cash flow statement, and the analytics trend without anything else being wired up.
    """
    entry = await service.record(
        organization_id,
        user,
        direction=data.direction,
        entry_date=data.entry_date or today,
        amount=data.amount,
        description=data.description,
        category_id=data.category_id,
        money_account_id=data.money_account_id,
        reference=data.reference,
        party=data.party,
        ctx=ctx,
    )
    return _entry(entry)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
@router.get("", response_model=Page[EntryRead], summary="List entries")
async def list_entries(
    organization_id: ActiveOrganizationId,
    service: BillingDep,
    params: Annotated[PageParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_READ))],
    direction: Annotated[Direction | None, Query()] = None,
    from_date: Annotated[dt.date | None, Query()] = None,
    to_date: Annotated[dt.date | None, Query()] = None,
    q: Annotated[str | None, Query(description="Search the description")] = None,
) -> Page[EntryRead]:
    """The day book, most recent first."""
    rows, total = await service.paginate(
        organization_id,
        params,
        direction=direction,
        from_date=from_date,
        to_date=to_date,
        q=q,
    )
    return Page.create([_entry(row) for row in rows], total=total, params=params)


@router.get("/summary", response_model=BillingSummary, summary="Totals for a window")
async def summary(
    organization_id: ActiveOrganizationId,
    service: BillingDep,
    today: TodayDep,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_READ))],
    from_date: Annotated[dt.date | None, Query()] = None,
    to_date: Annotated[dt.date | None, Query()] = None,
) -> BillingSummary:
    """In, out, and the net for what this screen recorded.

    Deliberately narrower than the P&L: it answers "what have I logged here", not
    "what did the business earn", which also includes invoices. Two different
    questions, and conflating them would make the smaller number look wrong.
    """
    result = await service.summary(
        organization_id,
        from_date=from_date or today.replace(day=1),
        to_date=to_date or today,
    )
    return BillingSummary(
        from_date=result.from_date,
        to_date=result.to_date,
        money_in=result.money_in,
        money_out=result.money_out,
        net=result.net,
        entry_count=result.entry_count,
    )


@router.get("/{entry_id}", response_model=EntryRead, summary="Get one entry")
async def get_entry(
    entry_id: uuid.UUID,
    organization_id: ActiveOrganizationId,
    service: BillingDep,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_READ))],
) -> EntryRead:
    return _entry(await service.get(organization_id, entry_id))


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------
@router.post("/{entry_id}/reverse", response_model=EntryRead, summary="Cancel an entry")
async def reverse_entry(
    entry_id: uuid.UUID,
    data: ReverseEntryRequest,
    organization_id: ActiveOrganizationId,
    user: CurrentUser,
    service: BillingDep,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.JOURNAL_REVERSE))],
) -> EntryRead:
    """Cancel an entry by posting its mirror image.

    There is no delete and no edit. A posted ledger entry is immutable here, so the
    only honest undo is an opposite entry that nets it to zero - which is also what an
    auditor expects to find. Both rows survive, and the original stays in the list
    marked as reversed.
    """
    return _entry(
        await service.reverse(organization_id, entry_id, user, reason=data.reason, ctx=ctx)
    )


__all__ = ["router"]
