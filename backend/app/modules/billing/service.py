"""Billing - record money in and money out, without naming anyone.

The simple path through this product. A shopkeeper types a date, an amount, and what
it was for; the ledger gets a correct double-entry posting and every report picks it
up. No customer, no supplier, no invoice.

**A bill with nobody's name on it is an expense, not a payable.** That is not a
shortcut - it is the correct treatment. A payable exists because you owe a specific
party a specific amount; if the money has already left your hand, there is nothing
owed and nobody to owe it to. So money out is ``debit expense, credit cash`` and money
in is ``debit cash, credit income``. Two lines, and the accounting equation holds
without inventing a party.

**There is no billing table.** Entries are posted straight to the ledger through
:meth:`PostingService.create_entry` and read back from it. That is a deliberate
decision, and the reason is the control-account reconciliation added alongside
analytics: a parallel table holding "the user's simple view" is a cache that can
disagree with the ledger, and this codebase has already been bitten by a figure stored
in two places. Reading back costs one indexed query, and in exchange these entries
appear in the trial balance, the P&L, the dashboard, and the analytics trend
automatically, because they *are* ledger entries.

Reconstruction is exact rather than heuristic: every entry written here has precisely
two lines, one on a cash-equivalent account and one on an income or expense account,
and is tagged ``source_type="billing"``. Direction, amount, category, and method all
follow from that shape with no guessing.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.context import RequestContext
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.pagination import PageParams
from app.core.security import decrypt_secret, encrypt_secret
from app.db.types import ZERO
from app.modules.accounting.coa_template import SystemAccount
from app.modules.accounting.models import (
    Account,
    AccountSubtype,
    AccountType,
    EntryStatus,
    JournalEntry,
    JournalEntryLine,
    JournalType,
)
from app.modules.accounting.repository import POSTED_STATUSES, AccountRepository
from app.modules.accounting.schemas import AccountCreate, AccountUpdate
from app.modules.accounting.service import (
    ChartOfAccountsService,
    FiscalCalendarService,
    PostingService,
)
from app.modules.billing.cards import MAX_DIGITS, MIN_DIGITS, inspect_card_number
from app.modules.billing.models import (
    BankAccountDetail,
    CardKind,
    CardNetwork,
    PaymentCard,
)
from app.modules.organizations.models import Organization
from app.modules.users.models import User

log = get_logger(__name__)

#: Tags the ledger entries this module owns, so they can be read back and so an
#: accountant can tell a hand-entered movement from an invoice posting.
SOURCE_TYPE: Final = "billing"

#: Transfers are tagged separately from money in and out, and that separation is load
#: bearing rather than tidiness: the day book reconstructs direction, category, and
#: amount from an entry's two-line shape, and a transfer has the same shape with neither
#: line on an income or expense account. Reading one back as a payment would invent a
#: category that does not exist. So they are excluded by tag, not guessed at by shape.
TRANSFER_SOURCE_TYPE: Final = "transfer"


class MoneyKind(StrEnum):
    """How a money account reconciles.

    Both are cash-equivalent for the cash flow statement. They are separate because
    they are checked differently: cash against a physical count, a bank against a
    statement - and a UPI wallet or card-settlement account behaves like a bank.
    """

    CASH = "cash"
    BANK = "bank"


class Direction(StrEnum):
    """Which way the money went."""

    #: Money received - a sale, a refund, an owner contribution.
    IN = "in"
    #: Money spent - a bill, an expense, a purchase.
    OUT = "out"

    @property
    def label(self) -> str:
        return "Money in" if self is Direction.IN else "Money out"

    @property
    def category_type(self) -> AccountType:
        """Which side of the P&L the non-cash leg belongs to."""
        return AccountType.INCOME if self is Direction.IN else AccountType.EXPENSE


@dataclass(frozen=True, slots=True)
class Category:
    """An account the user can file an entry against."""

    id: uuid.UUID
    code: str
    name: str
    direction: Direction
    #: The parent group's name, so the dropdown can be grouped. A flat list of nearly
    #: eighty categories is a list nobody reads to the end of.
    group: str
    #: Pre-selected in the form, so the common case needs no choice at all.
    is_default: bool = False


class MoneyAccountKind(StrEnum):
    """What a place-money-can-sit actually is, for the picker.

    Wider than :class:`MoneyKind`, which is only about *creating* a cash box or a bank
    account. This is the read side, and it has a third member because a credit card
    genuinely belongs in the same list while being a different accounting object.
    """

    CASH = "cash"
    BANK = "bank"
    #: A liability, not an asset. Kept out of "cash" everywhere it matters.
    CREDIT_CARD = "credit_card"

    @property
    def is_cash_equivalent(self) -> bool:
        return self is not MoneyAccountKind.CREDIT_CARD


@dataclass(frozen=True, slots=True)
class MoneyAccount:
    """Where the money sat or landed - a cash box, a bank account, or a card."""

    id: uuid.UUID
    code: str
    name: str
    #: Required, and ordered ahead of the optional fields to keep it that way. It had a
    #: ``CASH`` default once, which meant a newly created *bank* account came back tagged
    #: as cash - the one construction site that forgot to pass it was silently wrong
    #: rather than rejected. A liability quietly labelled as cash is the worst version of
    #: this mistake, so mypy is left to insist on it at every call site.
    kind: MoneyAccountKind
    is_default: bool = False

    #: Set when a card is what identifies this entry. On a credit card these describe
    #: the account itself; on a *debit* card they describe one of the ways of touching a
    #: bank account, which is why the card id is carried separately from the account id.
    card_id: uuid.UUID | None = None
    card_last4: str | None = None
    card_network: str | None = None

    #: Who the account belongs to and which bank it is at. Present for a bank account that
    #: was given them, absent for cash in hand and for a credit card - a card's
    #: counterparty is its issuer, which the network already names.
    bank_name: str | None = None
    holder_name: str | None = None
    #: The tail of the account number. **Not the whole number**, which is deliberate: this
    #: list is what fills the picker on the recording screen, and shipping a full account
    #: number to a client that only needs to tell two accounts apart is a payload nobody
    #: asked for. The full value is on the account's own detail response.
    account_number_last4: str | None = None

    #: False once archived. Archived accounts are excluded from the picker and only
    #: appear on the accounts screen when it is asked to show them.
    is_active: bool = True

    #: Whether deleting this one is permitted.
    #:
    #: False once anything has been posted to it, because an entry names the account it
    #: moved through and removing it would leave that entry pointing at nothing. Archiving
    #: is the answer then - hence two separate flags rather than one "editable".
    can_delete: bool = False

    #: Why deleting is refused, or None when it is allowed.
    #:
    #: **The server knows the reason; the client should not have to guess it.** A flag alone
    #: produces a control that is either missing or greyed out with no explanation, which is
    #: the same silent-disable problem as a form that will not submit. Phrased for a person,
    #: because it goes straight into a tooltip.
    delete_blocked_reason: str | None = None

    #: Whether archiving this one is permitted at all.
    #:
    #: **A capability flag rather than the client re-deriving the rule.** A seeded account -
    #: "Cash on Hand", "Primary Bank Account" - is a system account, and the accounting
    #: service refuses to deactivate one because later modules post to it by role. A client
    #: that offered the button anyway would produce a request that always fails, so the
    #: server states the answer and the client only renders it.
    can_archive: bool = False

    @property
    def is_card(self) -> bool:
        return self.card_id is not None

    @property
    def subtitle(self) -> str | None:
        """The line under the name: "HDFC Bank ··4321", or nothing to say."""
        parts = [part for part in (self.bank_name, self.card_network) if part]
        tail = self.account_number_last4 or self.card_last4
        if tail:
            parts.append(f"··{tail}")
        return " · ".join(parts) or None


@dataclass(frozen=True, slots=True)
class Card:
    """A card on file. Never carries a number - see ``billing/models.py``."""

    id: uuid.UUID
    label: str
    kind: CardKind
    network: CardNetwork
    last4: str
    account_id: uuid.UUID
    account_name: str
    is_active: bool
    holder_name: str | None = None
    #: False once anything has been recorded on the card's account. See
    #: `MoneyAccount.can_delete`.
    can_delete: bool = False
    #: Why deleting is refused, or None. See `MoneyAccount.delete_blocked_reason`.
    delete_blocked_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BankDetails:
    """Who a bank account belongs to and which number it is.

    ``account_number`` is the full number, decrypted. Returned to a caller that has
    already passed the account-read guard, because the whole point of keeping it is to be
    able to quote it - masking it here would leave it stored and useless. ``last4`` is
    carried separately so a list can render without decrypting every row.
    """

    bank_name: str | None = None
    holder_name: str | None = None
    account_number: str | None = None
    account_number_last4: str | None = None
    #: The account's own name. Carried here so a rename comes back on the same response the
    #: form already reads, rather than needing a second request to see the result.
    name: str = ""

    @property
    def is_empty(self) -> bool:
        """Nothing worth persisting - which is the normal case for cash in hand."""
        return not any((self.bank_name, self.holder_name, self.account_number))


#: Stands in for "this account has no details", so the lookups below can read
#: ``details.get(id, _NO_BANK_DETAILS).bank_name`` instead of guarding each field.
_NO_BANK_DETAILS: Final = BankDetails()


def _why_card_not_deletable(*, kind: CardKind, has_postings: bool) -> str | None:
    """Why a card cannot be deleted, or None.

    **The wording differs by kind, because the situation does.** A credit card owns its
    account, so entries against it really are entries on the card. A *debit* card shares a
    bank account that existed first, and those entries may have nothing to do with the card
    at all - telling someone "entries have been recorded on this card" would then be simply
    untrue, and would send them looking for card transactions that do not exist.
    """
    if not has_postings:
        return None
    if kind is CardKind.CREDIT:
        return (
            "Entries have been recorded on this card. Archive it instead - they name it, "
            "and deleting it would leave them pointing at nothing."
        )
    return (
        "Entries have been recorded against the bank account this card draws on. Archive "
        "the card instead - the account keeps its history either way."
    )


def _why_not_deletable(
    *, is_system: bool, has_postings: bool, card_labels: list[str]
) -> str | None:
    """The first reason an account cannot be deleted, phrased for a person.

    Ordered by what the user can do about it, not by how the checks happen to run: a seeded
    account will never be deletable and there is nothing to act on, whereas a card can be
    removed and postings can at least be understood. Returning one reason rather than all of
    them keeps a tooltip readable - fixing the first reveals the next.
    """
    if is_system:
        return (
            "This account came with your books and cannot be deleted - the software posts "
            "to it by role. Archive it if you no longer use it."
        )
    if card_labels:
        return f"Remove the card drawing on this account first: {', '.join(card_labels)}."
    if has_postings:
        return (
            "Entries have been recorded against this account. Archive it instead - they "
            "name it, and deleting it would leave them pointing at nothing."
        )
    return None


@dataclass(frozen=True, slots=True)
class Transfer:
    """One movement between two of the organization's own accounts."""

    entry_id: uuid.UUID
    entry_number: str | None
    date: dt.date
    amount: Decimal
    description: str
    from_account_id: uuid.UUID
    from_account_name: str
    to_account_id: uuid.UUID
    to_account_name: str


@dataclass(frozen=True, slots=True)
class Entry:
    """One recorded movement, reconstructed from its ledger entry."""

    id: uuid.UUID
    entry_number: str | None
    date: dt.date
    direction: Direction
    amount: Decimal
    description: str
    reference: str | None
    #: Who it came from (money in) or went to (money out). Free text.
    party: str | None

    category_id: uuid.UUID
    category_name: str
    money_account_id: uuid.UUID
    money_account_name: str

    created_at: dt.datetime
    #: True once a reversal has cancelled it. Kept visible rather than hidden: the
    #: original and its reversal are both permanent records.
    is_reversed: bool


@dataclass(frozen=True, slots=True)
class Summary:
    from_date: dt.date
    to_date: dt.date
    money_in: Decimal
    money_out: Decimal
    entry_count: int

    @property
    def net(self) -> Decimal:
        return self.money_in - self.money_out


#: Default categories, by system key where one exists and by account code otherwise.
#: Chosen so the form opens on a sensible answer: most money in is a sale, and most
#: uncategorised money out is a general operating cost.
DEFAULT_INCOME_CODE: Final = "4100"
DEFAULT_EXPENSE_CODE: Final = "5250"


class BillingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.accounts = AccountRepository(session)
        self.posting = PostingService(session)
        self.chart = ChartOfAccountsService(session)
        self.calendar = FiscalCalendarService(session)

    # -----------------------------------------------------------------------
    # Pick lists
    # -----------------------------------------------------------------------
    async def categories(self, organization_id: uuid.UUID) -> list[Category]:
        """Income and expense accounts, as a flat pick-list.

        Groups are excluded - you cannot post to a heading. Returned flat rather than
        as a tree because this form has one dropdown, and a shopkeeper choosing
        "Rent" does not care that it sits under "Operating Expenses".
        """
        await self.ensure_books(organization_id)
        # Every account, groups included: the groups are not selectable but their names
        # are what the dropdown is organised by.
        every = await self.accounts.list_for_org(organization_id, include_inactive=False)
        group_names = {account.id: account.name for account in every if account.is_group}
        rows = [account for account in every if not account.is_group]

        categories: list[Category] = []
        for account in rows:
            if account.account_type is AccountType.INCOME:
                direction = Direction.IN
                default_code = DEFAULT_INCOME_CODE
            elif account.account_type is AccountType.EXPENSE:
                direction = Direction.OUT
                default_code = DEFAULT_EXPENSE_CODE
            else:
                continue

            categories.append(
                Category(
                    id=account.id,
                    code=account.code,
                    name=account.name,
                    direction=direction,
                    group=group_names.get(account.parent_id or uuid.UUID(int=0))
                    or ("Income" if direction is Direction.IN else "Expenses"),
                    is_default=account.code == default_code,
                )
            )
        return categories

    async def create_category(
        self,
        organization_id: uuid.UUID,
        actor: User,
        *,
        name: str,
        direction: Direction,
        ctx: RequestContext | None = None,
    ) -> Category:
        """Add a category of the user's own.

        The template cannot anticipate every business, so this is the escape hatch -
        and it is deliberately the *only* account-creation path exposed on this screen.
        The user supplies a name and a direction; the code, the parent group, the
        subtype, and the depth are all derived. Asking a shopkeeper to choose an account
        code and a subtype to record a payment would defeat the point of the screen.

        The new account is filed under the same group the direction's own defaults live
        in, so it appears alongside the categories it belongs with rather than at the
        top level.
        """
        await self.ensure_books(organization_id)

        cleaned = name.strip()
        if not cleaned:
            raise ValidationError("Give the category a name.")

        every = await self.accounts.list_for_org(organization_id, include_inactive=True)

        if any(a.name.casefold() == cleaned.casefold() for a in every):
            raise ConflictError(
                f'A category called "{cleaned}" already exists.',
                code="category_exists",
            )

        wanted = direction.category_type
        anchor_code = DEFAULT_INCOME_CODE if direction is Direction.IN else DEFAULT_EXPENSE_CODE
        anchor = next((a for a in every if a.code == anchor_code), None)
        parent = next(
            (a for a in every if anchor is not None and a.id == anchor.parent_id),
            None,
        ) or next((a for a in every if a.is_group and a.account_type is wanted), None)

        if parent is None:  # pragma: no cover - ensure_books guarantees a group exists
            raise BusinessRuleError(
                "The chart of accounts has no group to file this under.",
                code="no_parent_group",
            )

        account = await self.chart.create_account(
            organization_id,
            AccountCreate(
                code=self._next_code(every, parent.code),
                name=cleaned,
                account_type=wanted,
                subtype=anchor.subtype if anchor is not None else parent.subtype,
                parent_id=parent.id,
                is_group=False,
            ),
            actor,
            ctx,
        )

        log.info(
            "billing category created",
            extra={"name": cleaned, "code": account.code, "direction": direction.value},
        )
        return Category(
            id=account.id,
            code=account.code,
            name=account.name,
            direction=direction,
            group=parent.name,
            is_default=False,
        )

    async def create_money_account(
        self,
        organization_id: uuid.UUID,
        actor: User,
        *,
        name: str,
        kind: MoneyKind,
        bank_name: str | None = None,
        holder_name: str | None = None,
        account_number: str | None = None,
        ctx: RequestContext | None = None,
    ) -> MoneyAccount:
        """Add a cash box or a bank account.

        The seeded chart gives one of each, which covers a business with a till and a
        current account and nobody else. A second bank, a UPI wallet, a partner's
        petty cash, or the card machine's settlement account are all ordinary, and
        without this the only choices are "Cash on Hand" and "Primary Bank Account" -
        so money that moved through a wallet gets filed as cash and the balances stop
        matching anything real.

        Only a name and whether it behaves like cash or a bank are required. The subtype
        is what matters to the software: both are cash-equivalent for the cash flow
        statement, but they reconcile differently - cash against a physical count, a bank
        against a statement - so they are separate subtypes rather than one.

        ``bank_name``, ``holder_name`` and ``account_number`` are the facts a person needs
        and the ledger does not. All three are optional and all three are ignored for a
        cash box, which has no bank, no number, and no holder - see
        :class:`~app.modules.billing.models.BankAccountDetail`. The number is encrypted
        before it is written.
        """
        await self.ensure_books(organization_id)

        cleaned = name.strip()
        if not cleaned:
            raise ValidationError("Give the account a name.")

        every = await self.accounts.list_for_org(organization_id, include_inactive=True)
        if any(a.name.casefold() == cleaned.casefold() for a in every):
            raise ConflictError(
                f'An account called "{cleaned}" already exists.', code="account_exists"
            )

        by_code = {a.code: a for a in every}
        if kind is MoneyKind.BANK:
            # Bank accounts nest under their own group, so several read as a set.
            group = by_code.get("1120")
            parent = group if group is not None and group.is_group else by_code.get("1100")
            anchor = "1120"
            subtype = AccountSubtype.BANK
        else:
            parent = by_code.get("1100")
            # Numbered after Cash on Hand rather than from the parent, so a second till
            # sorts next to the first instead of ahead of it at 1101.
            anchor = "1110"
            subtype = AccountSubtype.CASH

        if parent is None:  # pragma: no cover - ensure_books guarantees the group
            raise BusinessRuleError(
                "The chart of accounts has no current-assets group.", code="no_parent_group"
            )

        account = await self.chart.create_account(
            organization_id,
            AccountCreate(
                code=self._next_code(every, anchor),
                name=cleaned,
                account_type=AccountType.ASSET,
                subtype=subtype,
                parent_id=parent.id,
                is_group=False,
                is_reconcilable=True,
            ),
            actor,
            ctx,
        )

        # Only a bank account gets a detail row. A cash box has nothing to put in one, and
        # writing an all-null row would mean every later read has to tell "no details" from
        # "a row of nothings".
        details = BankDetails()
        if kind is MoneyKind.BANK:
            details = await self._save_bank_details(
                organization_id,
                account.id,
                bank_name=bank_name,
                holder_name=holder_name,
                account_number=account_number,
            )

        log.info(
            "money account created",
            extra={
                "name": cleaned,
                "code": account.code,
                "kind": kind.value,
                # The bank and the tail, never the number itself. This is the one place a
                # full account number is in scope, and a log line is exactly where it must
                # not end up.
                "bank": details.bank_name,
                "account_last4": details.account_number_last4,
            },
        )
        return MoneyAccount(
            id=account.id,
            code=account.code,
            name=account.name,
            # From the requested kind rather than defaulted: a bank account that came back
            # labelled "cash" would show under the wrong heading in the picker the client
            # builds from this field, immediately after being created.
            kind=(MoneyAccountKind.BANK if kind is MoneyKind.BANK else MoneyAccountKind.CASH),
            is_default=False,
            bank_name=details.bank_name,
            holder_name=details.holder_name,
            account_number_last4=details.account_number_last4,
            is_active=True,
            # Always archivable, and always deletable: this route only ever creates a
            # user-added account, so it is not seeded, has no postings yet, and has no card
            # drawing on it. Stated explicitly because both fields default to `False` -
            # which fails safe, but would leave a just-created account with neither option.
            can_archive=True,
            can_delete=True,
        )

    async def _save_bank_details(
        self,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        *,
        bank_name: str | None,
        holder_name: str | None,
        account_number: str | None,
    ) -> BankDetails:
        """Write (or update) the human facts about a bank account.

        Returns what was stored, so the caller does not have to reassemble it. The account
        number is encrypted here and nowhere else, and the last four digits are peeled off
        first so a list can be rendered without decrypting anything.
        """
        bank = (bank_name or "").strip() or None
        holder = (holder_name or "").strip() or None
        # Spaces and dashes are how people write these on paper; the stored value is the
        # digits so that two spellings of one account compare equal.
        digits = re.sub(r"[\s-]", "", account_number or "") or None

        if digits is not None and not digits.isdigit():
            raise ValidationError(
                "An account number should be digits only.", code="account_number_invalid"
            )

        details = BankDetails(
            bank_name=bank,
            holder_name=holder,
            account_number=digits,
            account_number_last4=digits[-4:] if digits else None,
        )
        if details.is_empty:
            return details

        existing = (
            await self.session.execute(
                select(BankAccountDetail).where(BankAccountDetail.account_id == account_id)
            )
        ).scalar_one_or_none()

        row = existing or BankAccountDetail(organization_id=organization_id, account_id=account_id)
        row.bank_name = bank
        row.holder_name = holder
        row.account_number_encrypted = encrypt_secret(digits) if digits else None
        row.account_number_last4 = details.account_number_last4
        if existing is None:
            self.session.add(row)
        await self.session.flush()

        return details

    @staticmethod
    def _next_code(existing: Sequence[Account], parent_code: str) -> str:
        """The next free code inside a parent's block.

        Codes are hierarchical (``5200`` owns ``5201``-``5299``), so a new child is
        numbered inside its parent's range. Walking upward from the parent finds the
        first gap, which keeps user-added categories sorted next to their siblings
        rather than appended at the end of the chart.
        """
        taken = {account.code for account in existing}
        base = int(parent_code)
        for offset in range(1, 100):
            candidate = str(base + offset)
            if candidate not in taken:
                return candidate
        # The parent's block is full; fall back to a free code anywhere above it.
        for candidate_int in range(base + 100, base + 1000):
            candidate = str(candidate_int)
            if candidate not in taken:
                return candidate
        raise BusinessRuleError(  # pragma: no cover - 1000 codes exhausted
            "No account code is free near this group.", code="no_free_code"
        )

    async def money_accounts(
        self, organization_id: uuid.UUID, *, include_archived: bool = False
    ) -> list[MoneyAccount]:
        """Everywhere money can sit or land, for "where did it come from / go to".

        Three kinds in one list, because that is the question the form asks - but they
        are not three flavours of the same thing:

        * **Cash and bank** are assets. Money in one of these is money you have.
        * **A credit card** is a liability. Money "in" it is money you owe, and it is
          tagged :attr:`MoneyAccountKind.CREDIT_CARD` so nothing downstream mistakes it
          for cash. The dashboard's cash figure and the cash flow statement both key off
          ``subtype.is_cash_equivalent``, which a liability account is not - so a card
          appearing in this picker does not put its balance inside "Cash and bank".
        * **A debit card** is another way of naming a bank account already in the list.
          It resolves to the same ``id``, so choosing either posts identically; the entry
          exists so somebody who thinks "I paid by card" can say so.
        """
        await self.ensure_books(organization_id)
        # `include_inactive` only when asked. The picker on the recording screen must never
        # offer an archived account, so the default stays as it was and the accounts screen
        # opts in.
        rows = await self.accounts.list_for_org(
            organization_id,
            postable_only=True,
            include_inactive=include_archived,
        )
        # Fetched once including archived, then narrowed: the listing wants what was asked
        # for, but `carded` has to know about every card - an archived one still holds the
        # `RESTRICT` reference that blocks deleting its account.
        all_cards = await self.cards(organization_id, include_archived=True)
        cards = all_cards if include_archived else [c for c in all_cards if c.is_active]

        cash = [a for a in rows if a.subtype.is_cash_equivalent]
        by_id = {a.id: a for a in rows}

        # One query for every detail row rather than one per account. The full numbers are
        # not decrypted here - only the bank, the holder, and the tail are needed to fill a
        # picker, and decrypting a column nobody is going to read would be work done purely
        # to widen the blast radius of a log line.
        details = await self._bank_details_by_account(organization_id)
        # One query for the whole list - see `_accounts_with_postings`.
        posted_to = await self._accounts_with_postings(organization_id)
        carded = {c.account_id for c in all_cards}

        # Cash is the default: a business recording movements by hand is far more often
        # dealing in cash than reconciling a bank feed. A card is never the default -
        # spending on credit is a deliberate choice, not a fallback.
        default_id = next(
            (a.id for a in cash if a.system_key == SystemAccount.CASH),
            cash[0].id if cash else None,
        )

        accounts = [
            MoneyAccount(
                id=a.id,
                code=a.code,
                name=a.name,
                is_default=a.id == default_id,
                kind=(
                    MoneyAccountKind.CASH
                    if a.subtype is AccountSubtype.CASH
                    else MoneyAccountKind.BANK
                ),
                bank_name=details.get(a.id, _NO_BANK_DETAILS).bank_name,
                holder_name=details.get(a.id, _NO_BANK_DETAILS).holder_name,
                account_number_last4=details.get(a.id, _NO_BANK_DETAILS).account_number_last4,
                is_active=a.is_active,
                # A seeded account is a system account and cannot be deactivated - the
                # accounting service refuses, because later modules post to it by role.
                can_archive=not a.is_system,
                # Deletable only while nothing depends on it: no postings, not seeded, and
                # no card drawing on it. Each of those would otherwise be an error the user
                # could only discover by pressing the button - so the reason travels with the
                # flag.
                can_delete=(not a.is_system and a.id not in posted_to and a.id not in carded),
                delete_blocked_reason=_why_not_deletable(
                    is_system=a.is_system,
                    has_postings=a.id in posted_to,
                    card_labels=[
                        f"{c.label} ··{c.last4}" for c in all_cards if c.account_id == a.id
                    ],
                ),
            )
            for a in cash
        ]

        for card in cards:
            account = by_id.get(card.account_id)
            if account is None:
                # The account was archived from under the card. Skipped rather than
                # raised: an unusable picker entry is worse than a missing one, and the
                # card is still listed on the accounts panel where it can be archived.
                continue
            accounts.append(
                MoneyAccount(
                    id=card.account_id,
                    code=account.code,
                    name=f"{card.label} ··{card.last4}",
                    is_default=False,
                    kind=(
                        MoneyAccountKind.CREDIT_CARD
                        if card.kind is CardKind.CREDIT
                        else MoneyAccountKind.BANK
                    ),
                    card_id=card.id,
                    card_last4=card.last4,
                    card_network=card.network.value,
                    # A debit card inherits its account's bank, because it *is* that
                    # account - "SBI Debit ··1234" under "State Bank of India" is the same
                    # fact stated twice, and it is what someone reconciling expects to see.
                    # A credit card has no bank of its own; the network names the issuer.
                    bank_name=(
                        details.get(card.account_id, _NO_BANK_DETAILS).bank_name
                        if card.kind is CardKind.DEBIT
                        else None
                    ),
                    holder_name=card.holder_name,
                )
            )

        return accounts

    async def _accounts_with_postings(self, organization_id: uuid.UUID) -> set[uuid.UUID]:
        """Which of this organization's accounts have a journal line against them.

        **One aggregate query, not one per account.** The repository's ``has_postings`` asks
        about a single account, and calling it while building a list would be a query per
        row - on the screen that renders every account and card at once.

        Needed because "can this be deleted" is not a property of the account itself: an
        account with history cannot be removed without orphaning entries, so the answer
        depends on the ledger. The clients get it as a flag so they can offer delete only
        where it will work, and archiving everywhere else.
        """
        rows = (
            await self.session.execute(
                select(JournalEntryLine.account_id)
                .join(JournalEntry, JournalEntryLine.entry_id == JournalEntry.id)
                .where(JournalEntry.organization_id == organization_id)
                .distinct()
            )
        ).all()
        return {row[0] for row in rows}

    async def _bank_details_by_account(
        self, organization_id: uuid.UUID
    ) -> dict[uuid.UUID, BankDetails]:
        """Every detail row for the organization, keyed by account, **without decrypting**.

        The full number is left in its ciphertext here on purpose - see
        :meth:`bank_details` for the one path that unlocks it. Callers filling a list need
        the bank and the last four and nothing more.
        """
        rows = (
            (
                await self.session.execute(
                    select(BankAccountDetail).where(
                        BankAccountDetail.organization_id == organization_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return {
            row.account_id: BankDetails(
                bank_name=row.bank_name,
                holder_name=row.holder_name,
                account_number_last4=row.account_number_last4,
            )
            for row in rows
        }

    async def set_money_account_active(
        self,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        actor: User,
        *,
        is_active: bool,
        ctx: RequestContext | None = None,
    ) -> MoneyAccount:
        """Stop offering an account, or offer it again.

        **Archived, never deleted**, for the same reason a card is: entries already point at
        it, and the account name is how somebody recognises them a year later. A closed bank
        account has to leave the picker without taking its history with it.

        Delegates to the accounting service rather than flipping the column here, so the
        rules stay in one place - a system account cannot be deactivated, and the change is
        audited as an account update. Which of those two guards fires is not this module's
        business.
        """
        rows = await self.accounts.list_for_org(
            organization_id, postable_only=True, include_inactive=True
        )
        account = next((a for a in rows if a.id == account_id), None)
        if account is None:
            raise NotFoundError("That account does not exist.", code="account_not_found")
        if not account.subtype.is_cash_equivalent:
            raise BusinessRuleError(
                "Only a cash or bank account can be archived from here.",
                code="not_a_money_account",
            )

        await self.chart.update_account(
            organization_id,
            account_id,
            AccountUpdate(is_active=is_active),
            actor,
            ctx,
        )

        log.info(
            "money account archived" if not is_active else "money account restored",
            extra={"code": account.code, "name": account.name},
        )

        # Re-read rather than patching the row by hand, so what comes back is what the
        # picker will see - including whether it is still in it at all.
        listed = await self.money_accounts(organization_id, include_archived=True)
        return next(
            (a for a in listed if a.id == account_id and not a.is_card),
            MoneyAccount(
                id=account.id,
                code=account.code,
                name=account.name,
                kind=(
                    MoneyAccountKind.CASH
                    if account.subtype is AccountSubtype.CASH
                    else MoneyAccountKind.BANK
                ),
                is_active=is_active,
                can_archive=not account.is_system,
            ),
        )

    async def bank_details(self, organization_id: uuid.UUID, account_id: uuid.UUID) -> BankDetails:
        """One account's details, **with the number decrypted.**

        Separate from the list path so that reading a full account number is an explicit
        act with its own route and its own permission check, rather than something that
        rides along on every page load of the recording screen.
        """
        rows = await self.accounts.list_for_org(
            organization_id, postable_only=True, include_inactive=True
        )
        account = next((a for a in rows if a.id == account_id), None)
        if account is None:
            raise NotFoundError("That account does not exist.", code="account_not_found")

        row = (
            await self.session.execute(
                select(BankAccountDetail).where(
                    BankAccountDetail.organization_id == organization_id,
                    BankAccountDetail.account_id == account_id,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            # No details recorded, but the account still has a name - which the edit form
            # needs in order to open with it rather than blank.
            return BankDetails(name=account.name)

        return BankDetails(
            bank_name=row.bank_name,
            holder_name=row.holder_name,
            account_number=(
                decrypt_secret(row.account_number_encrypted)
                if row.account_number_encrypted
                else None
            ),
            account_number_last4=row.account_number_last4,
            name=account.name,
        )

    async def update_bank_details(
        self,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        actor: User,
        *,
        name: str | None = None,
        bank_name: str | None,
        holder_name: str | None,
        account_number: str | None,
        ctx: RequestContext | None = None,
    ) -> BankDetails:
        """Fill in or correct an account's details after the fact.

        Needed because the seeded chart creates "Primary Bank Account" before anyone has
        said which bank that is, so without this the one account most organizations
        actually use is the only one that could never carry its own details.
        """
        # Resolved out of the organization's own accounts rather than fetched by id, which
        # is what makes this tenant-safe: an id belonging to another organization simply is
        # not in this list, so it 404s instead of being written to. Same approach as
        # `create_card` uses for the account a debit card draws on.
        rows = await self.accounts.list_for_org(organization_id, postable_only=True)
        account = next((a for a in rows if a.id == account_id), None)
        if account is None:
            raise NotFoundError("That account does not exist.", code="account_not_found")
        if not account.subtype.is_cash_equivalent:
            raise BusinessRuleError(
                "Only a cash or bank account can carry bank details.",
                code="not_a_money_account",
            )

        # Renaming comes first, so a clash is refused before anything is written.
        if name is not None and name.strip() and name.strip() != account.name:
            cleaned = name.strip()
            every = await self.accounts.list_for_org(organization_id, include_inactive=True)
            if any(a.id != account_id and a.name.casefold() == cleaned.casefold() for a in every):
                raise ConflictError(
                    f'An account called "{cleaned}" already exists.',
                    code="account_exists",
                )
            # Through the chart service, so the rename is audited like any other account
            # change. A *seeded* account can be renamed - the software finds it by
            # `system_key`, not by name - which is the whole point: "Primary Bank Account"
            # is a placeholder nobody chose.
            await self.chart.update_account(
                organization_id,
                account_id,
                AccountUpdate(name=cleaned),
                actor,
                ctx,
            )

        details = await self._save_bank_details(
            organization_id,
            account_id,
            bank_name=bank_name,
            holder_name=holder_name,
            account_number=account_number,
        )
        # Re-read so the response carries the name as stored, renamed or not.
        return BankDetails(
            bank_name=details.bank_name,
            holder_name=details.holder_name,
            account_number=details.account_number,
            account_number_last4=details.account_number_last4,
            name=account.name,
        )

    # -----------------------------------------------------------------------
    # Cards
    # -----------------------------------------------------------------------
    async def cards(
        self, organization_id: uuid.UUID, *, include_archived: bool = False
    ) -> list[Card]:
        """Every card on file, oldest first so the list reads in the order added."""
        stmt = (
            select(PaymentCard)
            .where(PaymentCard.organization_id == organization_id)
            .options(selectinload(PaymentCard.account))
            .order_by(PaymentCard.created_at)
        )
        if not include_archived:
            stmt = stmt.where(PaymentCard.is_active.is_(True))

        rows = (await self.session.execute(stmt)).scalars().all()
        if not rows:
            return []

        # One aggregate for the whole list rather than `has_postings` per card.
        posted_to = await self._accounts_with_postings(organization_id)
        return [
            self._to_card(
                row,
                can_delete=row.account_id not in posted_to,
                delete_blocked_reason=_why_card_not_deletable(
                    kind=row.kind, has_postings=row.account_id in posted_to
                ),
            )
            for row in rows
        ]

    @staticmethod
    def _to_card(
        row: PaymentCard,
        *,
        can_delete: bool = False,
        delete_blocked_reason: str | None = None,
    ) -> Card:
        """Requires ``account`` to be loaded - every caller here does so eagerly."""
        return Card(
            id=row.id,
            label=row.label,
            kind=row.kind,
            network=row.network,
            last4=row.last4,
            account_id=row.account_id,
            account_name=row.account.name,
            is_active=row.is_active,
            holder_name=row.holder_name,
            can_delete=can_delete,
            delete_blocked_reason=delete_blocked_reason,
        )

    async def create_card(
        self,
        organization_id: uuid.UUID,
        actor: User,
        *,
        label: str,
        kind: CardKind,
        card_number: str,
        holder_name: str | None = None,
        bank_account_id: uuid.UUID | None = None,
        ctx: RequestContext | None = None,
    ) -> Card:
        """Put a card on file.

        **The number is read and discarded in this method and nowhere else.** It arrives,
        :func:`inspect_card_number` reduces it to a network and four digits, and the
        local goes out of scope - there is no column that could hold it and no log
        statement that receives it. See ``billing/models.py`` for why.

        What gets created depends on the kind, because the two are different accounting
        objects:

        * A **credit card** gets its own liability account under Current Liabilities.
          Spending on it is ``debit expense, credit card`` - a debt, not a payment - and
          settling the balance later is an ordinary transfer from a bank account.
        * A **debit card** creates no account. It names a bank account that already
          exists, so ``bank_account_id`` is required and every posting still lands on
          that one account. A second account for it would double-count the same money,
          and the balance sheet would be wrong by the card's balance.
        """
        await self.ensure_books(organization_id)

        cleaned_label = label.strip()
        if not cleaned_label:
            raise ValidationError("Give the card a name, so you can tell it apart later.")

        # Two different rejections, reported apart so the message can say which happened.
        # **Neither echoes what was submitted**: the 422 handler forwards only messages, and
        # a message quoting the digits would put a card number in an error body and very
        # likely in a client-side log.
        identity = inspect_card_number(card_number)
        if identity is None:
            raise ValidationError(
                f"A card number is {MIN_DIGITS} to {MAX_DIGITS} digits. Check what you "
                "entered and try again - only the last four are kept.",
                details={"fields": {"card_number": "Enter a valid card number"}},
            )
        if not identity.checksum_ok:
            # The Luhn check digit. Every real card number satisfies it, so failing means a
            # typo - and it catches every single-digit slip and almost every transposition,
            # which is the whole class of mistake someone makes copying digits off a card.
            # Worth refusing: the last four digits are how this card is recognised later, and
            # a wrong label defeats the point of keeping one.
            raise ValidationError(
                "That is not a valid card number. Check the digits and try again - only "
                "the last four are kept.",
                details={"fields": {"card_number": "Check the digits"}},
            )

        existing = await self.cards(organization_id, include_archived=True)
        if any(
            c.network is identity.network and c.last4 == identity.last4 and c.kind is kind
            for c in existing
        ):
            raise ConflictError(
                f"A {kind.label.lower()} ending {identity.last4} is already on file.",
                code="card_exists",
            )

        if kind is CardKind.CREDIT:
            account = await self._create_card_liability_account(
                organization_id, actor, label=cleaned_label, last4=identity.last4, ctx=ctx
            )
        else:
            if bank_account_id is None:
                raise ValidationError(
                    "Choose the bank account this debit card draws on. A debit card is a "
                    "way of using an account you already have, not an account of its own.",
                    details={"fields": {"bank_account_id": "Choose a bank account"}},
                )
            rows = await self.accounts.list_for_org(organization_id, postable_only=True)
            match = next(
                (a for a in rows if a.id == bank_account_id and a.subtype.is_cash_equivalent),
                None,
            )
            if match is None:
                raise ValidationError(
                    "That is not one of your cash or bank accounts.",
                    details={"fields": {"bank_account_id": "Choose a bank account"}},
                )
            account = match

        card = PaymentCard(
            organization_id=organization_id,
            label=cleaned_label,
            kind=kind,
            network=identity.network,
            last4=identity.last4,
            account_id=account.id,
            holder_name=(holder_name or "").strip() or None,
        )
        self.session.add(card)
        await self.session.flush()

        log.info(
            "payment card added",
            # Label, kind, network, and last four only. The number is not in scope here
            # and must never be added to this call. The holder's name is left out too -
            # it is not a secret, but it is a person's name in a log nobody needs.
            extra={
                "label": cleaned_label,
                "kind": kind.value,
                "network": identity.network.value,
                "last4": identity.last4,
                # No `checksum_ok` here: a number that failed it never reaches this line, so
                # logging it would only ever record `True`.
            },
        )
        created_can_delete, created_reason = await self._card_delete_flags(card)
        return Card(
            id=card.id,
            label=card.label,
            kind=card.kind,
            network=card.network,
            last4=card.last4,
            account_id=account.id,
            account_name=account.name,
            is_active=True,
            holder_name=card.holder_name,
            # A credit card's account was just created, so it has no postings. A *debit*
            # card points at a bank account that existed first and may well have some - in
            # which case deleting the card would be refused, so the flags are asked for
            # rather than assumed, through the one helper the list also uses.
            can_delete=created_can_delete,
            delete_blocked_reason=created_reason,
        )

    async def _create_card_liability_account(
        self,
        organization_id: uuid.UUID,
        actor: User,
        *,
        label: str,
        last4: str,
        ctx: RequestContext | None,
    ) -> Account:
        """The liability account behind a credit card.

        Under Current Liabilities rather than beside the bank accounts, and that is the
        whole accounting point of this feature: a card balance is money owed to an issuer.
        Filed as an asset it would inflate cash and understate debt, and both errors look
        entirely plausible on a balance sheet.
        """
        every = await self.accounts.list_for_org(organization_id, include_inactive=True)
        by_code = {a.code: a for a in every}
        parent = by_code.get("2100") or by_code.get("2000")
        if parent is None:  # pragma: no cover - ensure_books guarantees the group
            raise BusinessRuleError(
                "The chart of accounts has no current-liabilities group.",
                code="no_parent_group",
            )

        return await self.chart.create_account(
            organization_id,
            AccountCreate(
                code=self._next_code(every, "2100"),
                name=f"{label} ··{last4}",
                account_type=AccountType.LIABILITY,
                subtype=AccountSubtype.OTHER_CURRENT_LIABILITY,
                parent_id=parent.id,
                is_group=False,
                is_reconcilable=True,
            ),
            actor,
            ctx,
        )

    async def _card_delete_flags(self, row: PaymentCard) -> tuple[bool, str | None]:
        """`(can_delete, reason)` for one card.

        Exists so the single-row paths - create, edit, archive - answer the same way the list
        does. They used to return `can_delete=False` with no reason at all, which made a card
        look undeletable the moment you renamed it, with a tooltip claiming the opposite.
        """
        has_postings = await self.accounts.has_postings(row.account_id)
        return (
            not has_postings,
            _why_card_not_deletable(kind=row.kind, has_postings=has_postings),
        )

    async def update_card(
        self,
        organization_id: uuid.UUID,
        card_id: uuid.UUID,
        *,
        label: str | None = None,
        holder_name: str | None = None,
        card_number: str | None = None,
    ) -> Card:
        """Correct what a card is called, whose name is on it, or which number it is.

        **The kind cannot change, and that is not an oversight.** A credit card owns a
        liability account created alongside it; a debit card points at a bank account that
        already existed. Flipping the kind would either orphan an account with postings
        against it or silently start filing card spending as a payment from a bank account
        that never lost the money. The honest correction is a new card and an archive of the
        wrong one.

        Re-entering the number is allowed, because a mistyped one leaves the wrong four
        digits on screen and those digits are the whole point of storing anything. It is
        read, reduced, and discarded exactly as on create - and the derived network can
        change with it, since a corrected number may belong to a different scheme.
        """
        row = (
            await self.session.execute(
                select(PaymentCard)
                .where(
                    PaymentCard.organization_id == organization_id,
                    PaymentCard.id == card_id,
                )
                .options(selectinload(PaymentCard.account))
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("That card is not on file.", code="card_not_found")

        if label is not None:
            cleaned = label.strip()
            if not cleaned:
                raise ValidationError("Give the card a name, so you can tell it apart later.")
            row.label = cleaned

        if holder_name is not None:
            row.holder_name = holder_name.strip() or None

        if card_number is not None and card_number.strip():
            identity = inspect_card_number(card_number)
            if identity is None:
                raise ValidationError(
                    f"A card number is {MIN_DIGITS} to {MAX_DIGITS} digits. Check what you "
                    "entered and try again - only the last four are kept.",
                    details={"fields": {"card_number": "Enter a valid card number"}},
                )
            if not identity.checksum_ok:
                raise ValidationError(
                    "That is not a valid card number. Check the digits and try again - "
                    "only the last four are kept.",
                    details={"fields": {"card_number": "Check the digits"}},
                )

            # The unique constraint is on (org, network, last4, kind), so a correction that
            # lands on another card already on file has to be caught here rather than as an
            # integrity error with no useful message.
            clash = next(
                (
                    c
                    for c in await self.cards(organization_id, include_archived=True)
                    if c.id != card_id
                    and c.network is identity.network
                    and c.last4 == identity.last4
                    and c.kind is row.kind
                ),
                None,
            )
            if clash is not None:
                raise ConflictError(
                    f"A {row.kind.label.lower()} ending {identity.last4} is already on file.",
                    code="card_exists",
                )

            row.network = identity.network
            row.last4 = identity.last4

        await self.session.flush()

        log.info(
            "payment card updated",
            # Never the number, on this path either.
            extra={"label": row.label, "last4": row.last4},
        )
        can_delete, reason = await self._card_delete_flags(row)
        return self._to_card(row, can_delete=can_delete, delete_blocked_reason=reason)

    async def delete_card(
        self,
        organization_id: uuid.UUID,
        card_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> None:
        """Remove a card entirely, when nothing depends on it.

        **Refused once anything has been recorded on it**, with archiving offered instead.
        That is the same rule the chart of accounts applies to an account, and for the same
        reason: an entry names the card it was made on, and deleting the card would leave
        that entry pointing at nothing. Archiving keeps the record and stops offering it.

        A credit card's liability account goes with it, because that account was created
        for this card alone and would otherwise sit in the chart forever with a zero
        balance. A debit card's account is a bank account that existed first and is left
        exactly as it was.
        """
        row = (
            await self.session.execute(
                select(PaymentCard)
                .where(
                    PaymentCard.organization_id == organization_id,
                    PaymentCard.id == card_id,
                )
                .options(selectinload(PaymentCard.account))
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("That card is not on file.", code="card_not_found")

        owns_account = row.kind is CardKind.CREDIT
        if await self.accounts.has_postings(row.account_id):
            raise BusinessRuleError(
                "This card has entries recorded on it and cannot be deleted. Archive it "
                "instead - the history has to stay intact."
                if owns_account
                else "Entries have been recorded on the account this card draws on. "
                "Archive the card instead.",
                code="card_has_postings",
            )

        label = row.label
        account_id = row.account_id
        await self.session.delete(row)
        # Flushed before touching the account: the FK from card to account is RESTRICT, so
        # the account cannot go while the row still references it.
        await self.session.flush()

        if owns_account:
            await self.chart.delete_account(organization_id, account_id, actor, ctx)

        log.info("payment card deleted", extra={"label": label})

    async def delete_money_account(
        self,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> None:
        """Remove a cash box or bank account, when nothing depends on it.

        Thin on purpose: the rules - no postings, no children, not a system account - live in
        the chart of accounts and are enforced there, so this only narrows the route to the
        accounts this screen owns. Deleting Sales Revenue from the billing screen should not
        be possible, whatever the chart would allow.

        Its bank details go too, by the ``CASCADE`` on that table - they describe the
        account rather than record anything that happened.
        """
        rows = await self.accounts.list_for_org(
            organization_id, postable_only=True, include_inactive=True
        )
        account = next((a for a in rows if a.id == account_id), None)
        if account is None:
            raise NotFoundError("That account does not exist.", code="account_not_found")
        if not account.subtype.is_cash_equivalent:
            raise BusinessRuleError(
                "Only a cash or bank account can be deleted from here.",
                code="not_a_money_account",
            )

        # A card pointing at this account would be left dangling, and the FK is RESTRICT so
        # the database would refuse anyway - with an error nobody could act on.
        attached = [
            c
            for c in await self.cards(organization_id, include_archived=True)
            if c.account_id == account_id
        ]
        if attached:
            names = ", ".join(f"{c.label} ··{c.last4}" for c in attached)
            raise BusinessRuleError(
                f"Remove the card drawing on this account first: {names}.",
                code="account_has_cards",
            )

        await self.chart.delete_account(organization_id, account_id, actor, ctx)
        log.info("money account deleted", extra={"code": account.code})

    async def set_card_active(
        self, organization_id: uuid.UUID, card_id: uuid.UUID, *, active: bool
    ) -> Card:
        """Archive or restore a card.

        Archive, never delete - the same rule as a product. Entries already posted point
        at the card's account, and the card is how somebody recognises them a year later.
        """
        card = (
            await self.session.execute(
                select(PaymentCard)
                .where(
                    PaymentCard.organization_id == organization_id,
                    PaymentCard.id == card_id,
                )
                .options(selectinload(PaymentCard.account))
            )
        ).scalar_one_or_none()
        if card is None:
            raise NotFoundError("Card")

        card.is_active = active
        await self.session.flush()
        can_delete, reason = await self._card_delete_flags(card)
        return self._to_card(card, can_delete=can_delete, delete_blocked_reason=reason)

    # -----------------------------------------------------------------------
    # Recording
    # -----------------------------------------------------------------------
    async def record(
        self,
        organization_id: uuid.UUID,
        actor: User,
        *,
        direction: Direction,
        entry_date: dt.date,
        amount: Decimal,
        description: str,
        category_id: uuid.UUID | None = None,
        money_account_id: uuid.UUID | None = None,
        reference: str | None = None,
        party: str,
        ctx: RequestContext | None = None,
    ) -> Entry:
        """Record one movement and post it to the ledger.

        ``category_id`` and ``money_account_id`` are optional: omitted, they fall back
        to the defaults, so an entry needs no understanding of the chart of accounts.

        ``party`` is required - who the money was with is part of what makes an entry
        identifiable later, not an embellishment on it. Typed as ``str`` rather than
        defaulted, so a caller that forgets it fails at the call site instead of quietly
        writing an entry with no counterparty.
        """
        if amount <= 0:
            raise ValidationError(
                "Enter an amount greater than zero. To correct a mistake, reverse the "
                "original entry rather than recording a negative one."
            )

        # First, because the resolvers below need a chart to resolve against and the
        # posting needs an open period. Cheap and idempotent when both already exist.
        await self.ensure_books(organization_id, entry_date)

        category = await self._resolve_category(organization_id, direction, category_id)
        money = await self._resolve_money_account(organization_id, money_account_id)

        # Money out: the expense grows (debit), the cash shrinks (credit).
        # Money in: the cash grows (debit), the income grows (credit).
        if direction is Direction.OUT:
            debit_account, credit_account = category.id, money.id
        else:
            debit_account, credit_account = money.id, category.id

        journal_type = self._journal_for(money)

        from app.modules.accounting.schemas import JournalEntryCreate, JournalEntryLineInput

        journal = await self.posting.journals.get_by_type(organization_id, journal_type)
        if journal is None:
            raise BusinessRuleError(
                f"This organization has no {journal_type} journal configured. "
                "Set up the chart of accounts first.",
                code="no_cash_journal",
            )

        entry = await self.posting.create_entry(
            organization_id,
            JournalEntryCreate(
                journal_id=journal.id,
                entry_date=entry_date,
                narration=description.strip(),
                reference=reference,
                counterparty=party.strip(),
                lines=[
                    JournalEntryLineInput(account_id=debit_account, debit=amount, credit=ZERO),
                    JournalEntryLineInput(account_id=credit_account, debit=ZERO, credit=amount),
                ],
                # Posted immediately. A draft would mean the figure does not reach the
                # dashboard, and "I recorded it but it is not showing" is the worst
                # possible outcome for the one feature meant to be effortless.
                post=True,
            ),
            actor,
            ctx,
            source_type=SOURCE_TYPE,
        )

        log.info(
            "billing entry recorded",
            extra={
                "direction": direction.value,
                "amount": str(amount),
                "entry_number": entry.entry_number,
            },
        )
        return await self.get(organization_id, entry.id)

    async def _resolve_category(
        self, organization_id: uuid.UUID, direction: Direction, category_id: uuid.UUID | None
    ) -> Account:
        wanted = direction.category_type
        rows = await self.accounts.list_for_org(organization_id, postable_only=True)

        if category_id is not None:
            match = next((a for a in rows if a.id == category_id), None)
            if match is None:
                raise NotFoundError("Category")
            if match.account_type is not wanted:
                raise ValidationError(
                    f"{match.name} is a{'n' if wanted is AccountType.INCOME else ''} "
                    f"{match.account_type.value} account, so it cannot be used for "
                    f"{direction.label.lower()}."
                )
            return match

        default_code = DEFAULT_INCOME_CODE if direction is Direction.IN else DEFAULT_EXPENSE_CODE
        candidates = [a for a in rows if a.account_type is wanted]
        if not candidates:
            raise BusinessRuleError(
                f"No {wanted.value} accounts exist yet. Set up the chart of accounts first.",
                code="no_categories",
            )
        return next((a for a in candidates if a.code == default_code), candidates[0])

    async def transfer(
        self,
        organization_id: uuid.UUID,
        actor: User,
        *,
        from_account_id: uuid.UUID,
        to_account_id: uuid.UUID,
        amount: Decimal,
        entry_date: dt.date,
        description: str | None = None,
        reference: str | None = None,
        ctx: RequestContext | None = None,
    ) -> Transfer:
        """Move money between two of the organization's own accounts.

        **Debit the destination, credit the source.** Two lines, neither of them on an
        income or expense account - which is the whole point: moving your own money is not
        earning or spending it, and a transfer that touched the P&L would inflate both
        sides of it by the same amount and leave profit right while every other figure
        was wrong.

        One rule covers every case, which is why there is one method rather than several:

        * **Cash to bank, or bank to bank.** Both lines are cash-equivalent, so the net
          change in cash is nil - and the journal-entry reader already nets across cash
          lines, so it reports "no cash movement" rather than double-counting.
        * **Bank to credit card** - paying the card off. Debit the card liability, which
          reduces what you owe; credit the bank, which reduces what you have. Cash goes
          genuinely out, and the reader says so, because only one line is cash.
        * **Credit card to bank** - a cash advance. The mirror image, and correct for the
          same reason.

        Deliberately *not* offered: a transfer to or from a category, a customer, or a
        supplier. Those are payments, and they belong on the screens that know how to
        allocate them.
        """
        if amount <= 0:
            raise ValidationError(
                "Enter an amount greater than zero. To undo a transfer, reverse it rather "
                "than transferring a negative amount back."
            )
        if from_account_id == to_account_id:
            raise ValidationError(
                "Choose two different accounts. Moving money to the account it is already "
                "in has no effect to record.",
                details={"fields": {"to_account_id": "Pick a different account"}},
            )

        await self.ensure_books(organization_id, entry_date)

        rows = await self.accounts.list_for_org(organization_id, postable_only=True)
        allowed = await self._spendable_account_ids(organization_id)
        by_id = {a.id: a for a in rows}

        source = by_id.get(from_account_id)
        destination = by_id.get(to_account_id)
        for account, field in ((source, "from_account_id"), (destination, "to_account_id")):
            if account is None or account.id not in allowed:
                raise ValidationError(
                    "That is not one of your cash, bank, or card accounts.",
                    details={"fields": {field: "Choose one of your accounts"}},
                )
        assert source is not None and destination is not None  # noqa: S101 - narrowed above

        # The bank book when a bank is involved, the cash book when only cash is, and the
        # general journal for a card-to-card movement. Chosen from the pair rather than
        # from one side, because a transfer belongs to both accounts equally.
        journals = {self._journal_for(source), self._journal_for(destination)}
        if JournalType.BANK in journals:
            journal_type = JournalType.BANK
        elif JournalType.CASH in journals:
            journal_type = JournalType.CASH
        else:
            journal_type = JournalType.GENERAL

        journal = await self.posting.journals.get_by_type(organization_id, journal_type)
        if journal is None:
            raise BusinessRuleError(
                f"This organization has no {journal_type} journal configured. "
                "Set up the chart of accounts first.",
                code="no_transfer_journal",
            )

        from app.modules.accounting.schemas import JournalEntryCreate, JournalEntryLineInput

        narration = (description or "").strip() or (
            f"Transfer from {source.name} to {destination.name}"
        )

        entry = await self.posting.create_entry(
            organization_id,
            JournalEntryCreate(
                journal_id=journal.id,
                entry_date=entry_date,
                narration=narration,
                reference=reference,
                # The counterparty of a transfer is the organization itself. Naming the
                # destination rather than leaving it blank keeps the trial balance's
                # "dealt with" column meaningful for these rows.
                counterparty=destination.name,
                lines=[
                    JournalEntryLineInput(account_id=destination.id, debit=amount, credit=ZERO),
                    JournalEntryLineInput(account_id=source.id, debit=ZERO, credit=amount),
                ],
                post=True,
            ),
            actor,
            ctx,
            source_type=TRANSFER_SOURCE_TYPE,
        )

        log.info(
            "transfer recorded",
            extra={
                "amount": str(amount),
                "from": source.code,
                "to": destination.code,
                "entry_number": entry.entry_number,
            },
        )

        return Transfer(
            entry_id=entry.id,
            entry_number=entry.entry_number,
            date=entry_date,
            amount=amount,
            description=narration,
            from_account_id=source.id,
            from_account_name=source.name,
            to_account_id=destination.id,
            to_account_name=destination.name,
        )

    @staticmethod
    def _journal_for(money: Account) -> JournalType:
        """Which book a movement through this account belongs in.

        A card charge is neither a cash-book nor a bank-book entry - no cash moved and no
        bank was involved - so it lands in the general journal. Filing it under the bank
        book would make "show me every bank transaction" return things that never touched
        one, which is the question that book exists to answer.
        """
        if money.subtype is AccountSubtype.CASH:
            return JournalType.CASH
        if money.subtype is AccountSubtype.BANK:
            return JournalType.BANK
        return JournalType.GENERAL

    async def _spendable_account_ids(self, organization_id: uuid.UUID) -> set[uuid.UUID]:
        """Accounts money is allowed to move through on this screen.

        Cash equivalents, plus the liability account behind each credit card. The card
        accounts have to be enumerated from the card table rather than recognised by
        subtype: ``other_current_liability`` also covers salaries payable and customer
        advances, and letting a payment be recorded against those would post nonsense
        that balances.
        """
        rows = await self.accounts.list_for_org(organization_id, postable_only=True)
        spendable = {a.id for a in rows if a.subtype.is_cash_equivalent}
        spendable.update(
            card.account_id
            for card in await self.cards(organization_id)
            if card.kind is CardKind.CREDIT
        )
        return spendable

    async def _resolve_money_account(
        self, organization_id: uuid.UUID, money_account_id: uuid.UUID | None
    ) -> Account:
        rows = await self.accounts.list_for_org(organization_id, postable_only=True)
        cash = [a for a in rows if a.subtype.is_cash_equivalent]

        if money_account_id is not None:
            allowed = await self._spendable_account_ids(organization_id)
            match = next((a for a in rows if a.id == money_account_id), None)
            if match is None or match.id not in allowed:
                raise ValidationError(
                    "That is not one of your cash, bank, or card accounts, so money "
                    "cannot move through it."
                )
            return match

        if not cash:
            raise BusinessRuleError(
                "No cash or bank account exists yet. Set up the chart of accounts first.",
                code="no_money_account",
            )
        return next(
            (a for a in cash if a.system_key == SystemAccount.CASH),
            cash[0],
        )

    async def ensure_books(self, organization_id: uuid.UUID, on: dt.date | None = None) -> None:
        """Make sure this organization has a chart of accounts and a fiscal period.

        Called before every read and every write on this screen, and it is a **repair
        path**, not just a convenience. Organizations created through registration before
        that path seeded the books have no chart at all, so the first thing their owner
        saw here was "no income accounts exist yet" with two empty dropdowns and no way
        forward. Seeding on demand fixes those accounts the moment someone opens the
        screen, with no migration and nothing for the user to do.

        Both halves are idempotent - ``seed_defaults`` skips entirely when any account
        exists, ``ensure_year_for`` returns the existing year - so the common case costs
        one cheap existence check.
        """
        start_month = (
            await self.session.execute(
                select(Organization.fiscal_year_start_month).where(
                    Organization.id == organization_id
                )
            )
        ).scalar_one_or_none() or 4

        # `sync_template` rather than `seed_defaults`: it seeds when there is nothing,
        # and tops up by code when there is. Organizations created against an earlier
        # template would otherwise never see categories added since - which is exactly
        # what happened when the household and expanded expense lists landed.
        await self.chart.sync_template(organization_id)
        await self.calendar.ensure_year_for(
            organization_id, fiscal_year_start_month=start_month, on=on
        )

    # -----------------------------------------------------------------------
    # Reading back
    # -----------------------------------------------------------------------
    def _entry_query(self, organization_id: uuid.UUID) -> Select[tuple[JournalEntry]]:
        """Posted billing entries, excluding the reversals that cancel them.

        ``reverse_entry`` copies ``source_type`` onto the mirror entry it creates, so
        without the ``reverses_id`` filter a cancelled ₹5,000 expense shows up here
        twice: once struck through, and once as a phantom ₹5,000 *receipt* - because
        reversing a payment debits cash. Two rows that cancel each other is precisely
        the wrong thing to show the audience this screen exists for.

        The ledger keeps both entries, as it must. This is a view over them, and the
        original already carries ``is_reversed``, which says everything the user needs.
        """
        return (
            select(JournalEntry)
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.source_type == SOURCE_TYPE,
                JournalEntry.status.in_(POSTED_STATUSES),
                JournalEntry.reverses_id.is_(None),
            )
            .options(selectinload(JournalEntry.lines).selectinload(JournalEntryLine.account))
        )

    async def get(self, organization_id: uuid.UUID, entry_id: uuid.UUID) -> Entry:
        row = (
            await self.session.execute(
                self._entry_query(organization_id).where(JournalEntry.id == entry_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Entry")
        return self._to_entry(row)

    async def paginate(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        *,
        direction: Direction | None = None,
        from_date: dt.date | None = None,
        to_date: dt.date | None = None,
        q: str | None = None,
    ) -> tuple[list[Entry], int]:
        """Most recent first - a day book is read backwards from today."""
        query = self._entry_query(organization_id)

        if from_date is not None:
            query = query.where(JournalEntry.entry_date >= from_date)
        if to_date is not None:
            query = query.where(JournalEntry.entry_date <= to_date)
        if q:
            # The party is searched alongside the description: "Airtel" is at least as
            # likely a search as the note someone typed next to it.
            pattern = f"%{q.strip()}%"
            query = query.where(
                or_(
                    JournalEntry.narration.ilike(pattern),
                    JournalEntry.counterparty.ilike(pattern),
                )
            )

        counted = query.options().order_by(None).subquery()
        total = (await self.session.execute(select(func.count()).select_from(counted))).scalar_one()

        rows = (
            (
                await self.session.execute(
                    query.order_by(JournalEntry.entry_date.desc(), JournalEntry.created_at.desc())
                )
            )
            .scalars()
            .all()
        )

        entries = [self._to_entry(row) for row in rows]

        # Direction is a property of the reconstructed entry rather than a column, so
        # it is filtered here rather than in SQL. Acceptable: the query is already
        # narrowed by organization, source type, and date, and a hand-kept day book is
        # hundreds of rows a year, not millions.
        if direction is not None:
            entries = [entry for entry in entries if entry.direction is direction]
            total = len(entries)

        start = params.offset
        return entries[start : start + params.limit], total

    def _to_entry(self, row: JournalEntry) -> Entry:
        """Reconstruct the simple view from the two-line ledger entry.

        Exact, not heuristic: this module writes exactly two lines, one on an income or
        expense account and one on a money account. Anything else under this
        ``source_type`` would be corrupt data, so it fails loudly rather than guessing.

        **The category line is identified, and the money line is whatever is left.** That
        is the other way round from the obvious reading, and it is deliberate: "money
        account" now covers cash, a bank, *and* the liability account behind a credit
        card, so a positive test for it would have to enumerate subtypes and would be
        wrong again the next time the set grew. Which it was - looking for a
        cash-equivalent line meant a card charge posted successfully and then could not be
        read back at all. Exactly one line is on the P&L, so naming that one is both
        stable and stricter.
        """
        if len(row.lines) != 2:  # pragma: no cover - only reachable via manual SQL
            raise BusinessRuleError(
                f"Entry {row.entry_number} does not have the two lines a billing entry "
                "must have. It may have been edited outside the application.",
                code="billing_entry_malformed",
            )

        category_lines = [
            line
            for line in row.lines
            if line.account.account_type in (AccountType.INCOME, AccountType.EXPENSE)
        ]
        if len(category_lines) != 1:  # pragma: no cover - corrupt data only
            raise BusinessRuleError(
                f"Entry {row.entry_number} is not shaped like a billing entry: it should "
                "have exactly one income or expense line.",
                code="billing_entry_malformed",
            )

        category_line = category_lines[0]
        money_line = next(line for line in row.lines if line is not category_line)

        # The money leg being credited means it left; debited means it arrived. True for a
        # card as well as for cash: a charge credits the card, growing what is owed.
        direction = Direction.OUT if money_line.credit > 0 else Direction.IN

        return Entry(
            id=row.id,
            entry_number=row.entry_number,
            date=row.entry_date,
            direction=direction,
            amount=money_line.credit if direction is Direction.OUT else money_line.debit,
            description=row.narration,
            reference=row.reference,
            party=row.counterparty,
            category_id=category_line.account_id,
            category_name=category_line.account.name,
            money_account_id=money_line.account_id,
            money_account_name=money_line.account.name,
            created_at=row.created_at,
            is_reversed=row.status is EntryStatus.REVERSED,
        )

    async def summary(
        self, organization_id: uuid.UUID, *, from_date: dt.date, to_date: dt.date
    ) -> Summary:
        """Money in, money out, and the net, for a window.

        Counts only what this module recorded, so it answers "what have I logged"
        rather than "what did the business earn" - the second question is the P&L's,
        and it includes invoices too.
        """
        entries, _ = await self.paginate(
            organization_id,
            PageParams(page=1, page_size=200),
            from_date=from_date,
            to_date=to_date,
        )
        # Reversed entries are excluded from the totals but stay in the list: the
        # cancellation is part of the record, its effect on the balance is not.
        live = [entry for entry in entries if not entry.is_reversed]

        return Summary(
            from_date=from_date,
            to_date=to_date,
            money_in=sum((e.amount for e in live if e.direction is Direction.IN), start=ZERO),
            money_out=sum((e.amount for e in live if e.direction is Direction.OUT), start=ZERO),
            entry_count=len(live),
        )

    # -----------------------------------------------------------------------
    # Undo
    # -----------------------------------------------------------------------
    async def reverse(
        self,
        organization_id: uuid.UUID,
        entry_id: uuid.UUID,
        actor: User,
        *,
        reason: str | None = None,
        ctx: RequestContext | None = None,
    ) -> Entry:
        """Cancel an entry by posting its mirror image.

        Not a delete, and not an edit. A posted ledger entry is immutable in this
        system, so the only honest undo is an opposite entry that nets it to zero -
        which is also what an auditor expects to see. Both rows survive.
        """
        entry = await self.get(organization_id, entry_id)
        if entry.is_reversed:
            raise BusinessRuleError(
                "This entry has already been reversed.", code="already_reversed"
            )

        await self.posting.reverse_entry(
            organization_id,
            entry_id,
            actor,
            narration=reason or f"Reversal of {entry.description}",
            ctx=ctx,
        )
        return await self.get(organization_id, entry_id)


__all__ = [
    "TRANSFER_SOURCE_TYPE",
    "BillingService",
    "Card",
    "Category",
    "Direction",
    "Entry",
    "MoneyAccount",
    "MoneyAccountKind",
    "MoneyKind",
    "Summary",
    "Transfer",
]
