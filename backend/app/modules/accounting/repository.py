"""Accounting data access.

One subtlety dominates this module, and getting it wrong silently corrupts every
report:

**A reversed entry still counts toward balances.**

Reversing entry *A* creates a mirror entry *B* and marks *A* as ``REVERSED``. The
two cancel out arithmetically. So a balance query must include **both**
``POSTED`` and ``REVERSED`` entries - excluding ``REVERSED`` would leave *B*'s
mirror lines counted with nothing to cancel them, flipping the sign of every
reversed transaction. Only ``DRAFT`` is excluded, because a draft is not yet in
the books.

:data:`POSTED_STATUSES` exists so that rule is stated once and reused, rather
than re-derived (and eventually mis-derived) at each call site.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Final, NamedTuple

from sqlalchemy import ColumnElement, Select, and_, func, select
from sqlalchemy.orm import selectinload

from app.core.pagination import CursorParams, PageParams
from app.db.repository import BaseRepository
from app.db.types import ZERO
from app.modules.accounting.models import (
    Account,
    AccountingPeriod,
    AccountType,
    EntryStatus,
    FiscalYear,
    Journal,
    JournalEntry,
    JournalEntryLine,
    NumberSequence,
    PeriodStatus,
)

#: Statuses whose lines affect account balances. See the module docstring - the
#: inclusion of REVERSED is load-bearing, not an oversight.
POSTED_STATUSES: Final = (EntryStatus.POSTED, EntryStatus.REVERSED)


class AccountBalance(NamedTuple):
    account_id: uuid.UUID
    total_debit: Decimal
    total_credit: Decimal


# =============================================================================
# Accounts
# =============================================================================
class AccountRepository(BaseRepository[Account]):
    model = Account
    sortable_fields = frozenset({"code", "name", "account_type", "created_at"})
    default_sort = "code"

    async def get_by_code(self, organization_id: uuid.UUID, code: str) -> Account | None:
        return await self.get_by(organization_id=organization_id, code=code)

    async def get_by_system_key(self, organization_id: uuid.UUID, key: str) -> Account | None:
        """Resolve the default account for a role, e.g. ``accounts_receivable``.

        The lookup later stages use to post without hard-coding an account code.
        """
        return await self.get_by(organization_id=organization_id, system_key=key)

    async def list_for_org(
        self,
        organization_id: uuid.UUID,
        *,
        account_type: AccountType | None = None,
        include_inactive: bool = False,
        postable_only: bool = False,
    ) -> Sequence[Account]:
        query = (
            self._base_query()
            .where(Account.organization_id == organization_id)
            .order_by(Account.code)
        )
        if account_type is not None:
            query = query.where(Account.account_type == account_type)
        if not include_inactive:
            query = query.where(Account.is_active.is_(True))
        if postable_only:
            query = query.where(Account.is_group.is_(False))
        return (await self.session.execute(query)).scalars().all()

    async def has_children(self, account_id: uuid.UUID) -> bool:
        return await self.exists(parent_id=account_id)

    async def has_postings(self, account_id: uuid.UUID) -> bool:
        """Whether any journal line references this account.

        Gate for deletion: an account with history cannot be removed without
        orphaning entries, so it may only be deactivated.
        """
        query = (
            select(func.count())
            .select_from(JournalEntryLine)
            .where(JournalEntryLine.account_id == account_id)
            .limit(1)
        )
        return bool((await self.session.execute(query)).scalar_one())

    async def balances(
        self,
        organization_id: uuid.UUID,
        *,
        to_date: dt.date,
        from_date: dt.date | None = None,
    ) -> dict[uuid.UUID, AccountBalance]:
        """Aggregate debit/credit totals per account, as one query.

        Returned as a dict so report builders can walk the account tree in Python
        without a query per node - the difference between one round trip and
        fifty-two for a chart of accounts.

        ``from_date`` omitted means "since inception", which is what balance-sheet
        accounts need. Supplying it gives period movement, which is what the P&L
        needs.
        """
        query = (
            select(
                JournalEntryLine.account_id,
                func.coalesce(func.sum(JournalEntryLine.debit), ZERO).label("total_debit"),
                func.coalesce(func.sum(JournalEntryLine.credit), ZERO).label("total_credit"),
            )
            .join(JournalEntry, JournalEntry.id == JournalEntryLine.entry_id)
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.status.in_(POSTED_STATUSES),
                JournalEntry.entry_date <= to_date,
            )
            .group_by(JournalEntryLine.account_id)
        )
        if from_date is not None:
            query = query.where(JournalEntry.entry_date >= from_date)

        rows = (await self.session.execute(query)).all()
        return {
            row.account_id: AccountBalance(row.account_id, row.total_debit, row.total_credit)
            for row in rows
        }

    async def balance_for(
        self,
        account_id: uuid.UUID,
        *,
        to_date: dt.date,
        from_date: dt.date | None = None,
    ) -> AccountBalance:
        """Debit/credit totals for a single account."""
        query = (
            select(
                func.coalesce(func.sum(JournalEntryLine.debit), ZERO),
                func.coalesce(func.sum(JournalEntryLine.credit), ZERO),
            )
            .join(JournalEntry, JournalEntry.id == JournalEntryLine.entry_id)
            .where(
                JournalEntryLine.account_id == account_id,
                JournalEntry.status.in_(POSTED_STATUSES),
                JournalEntry.entry_date <= to_date,
            )
        )
        if from_date is not None:
            query = query.where(JournalEntry.entry_date >= from_date)

        debit, credit = (await self.session.execute(query)).one()
        return AccountBalance(account_id, debit, credit)


# =============================================================================
# Fiscal calendar
# =============================================================================
class FiscalCalendarRepository(BaseRepository[FiscalYear]):
    model = FiscalYear
    default_sort = "-start_date"

    async def list_years(self, organization_id: uuid.UUID) -> Sequence[FiscalYear]:
        query = (
            select(FiscalYear)
            .where(FiscalYear.organization_id == organization_id)
            .options(selectinload(FiscalYear.periods))
            .order_by(FiscalYear.start_date.desc())
        )
        return (await self.session.execute(query)).scalars().all()

    async def year_containing(self, organization_id: uuid.UUID, on: dt.date) -> FiscalYear | None:
        query = select(FiscalYear).where(
            FiscalYear.organization_id == organization_id,
            FiscalYear.start_date <= on,
            FiscalYear.end_date >= on,
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def overlapping_year(
        self, organization_id: uuid.UUID, start: dt.date, end: dt.date
    ) -> FiscalYear | None:
        """Find any year overlapping ``[start, end]``.

        Fiscal years must not overlap, or an entry date would resolve to two
        periods and its ledger placement would be ambiguous.
        """
        query = select(FiscalYear).where(
            FiscalYear.organization_id == organization_id,
            FiscalYear.start_date <= end,
            FiscalYear.end_date >= start,
        )
        return (await self.session.execute(query.limit(1))).scalar_one_or_none()

    async def period_containing(
        self, organization_id: uuid.UUID, on: dt.date
    ) -> AccountingPeriod | None:
        query = select(AccountingPeriod).where(
            AccountingPeriod.organization_id == organization_id,
            AccountingPeriod.start_date <= on,
            AccountingPeriod.end_date >= on,
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def get_period(
        self, organization_id: uuid.UUID, period_id: uuid.UUID
    ) -> AccountingPeriod | None:
        query = select(AccountingPeriod).where(
            AccountingPeriod.organization_id == organization_id,
            AccountingPeriod.id == period_id,
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    async def add_period(self, period: AccountingPeriod) -> AccountingPeriod:
        self.session.add(period)
        await self.session.flush()
        return period

    async def periods_in_range(
        self, organization_id: uuid.UUID, start: dt.date, end: dt.date
    ) -> Sequence[AccountingPeriod]:
        query = (
            select(AccountingPeriod)
            .where(
                AccountingPeriod.organization_id == organization_id,
                AccountingPeriod.start_date <= end,
                AccountingPeriod.end_date >= start,
            )
            .order_by(AccountingPeriod.start_date)
        )
        return (await self.session.execute(query)).scalars().all()

    async def has_open_period_before(self, organization_id: uuid.UUID, before: dt.date) -> bool:
        """Whether an earlier period is still open.

        Closing out of order (locking March while February is open) produces
        comparative reports nobody can reconcile.
        """
        query = (
            select(func.count())
            .select_from(AccountingPeriod)
            .where(
                AccountingPeriod.organization_id == organization_id,
                AccountingPeriod.end_date < before,
                AccountingPeriod.status == PeriodStatus.OPEN,
            )
            .limit(1)
        )
        return bool((await self.session.execute(query)).scalar_one())


# =============================================================================
# Journals
# =============================================================================
class JournalRepository(BaseRepository[Journal]):
    model = Journal
    default_sort = "code"

    async def list_for_org(
        self, organization_id: uuid.UUID, *, include_inactive: bool = False
    ) -> Sequence[Journal]:
        query = (
            select(Journal).where(Journal.organization_id == organization_id).order_by(Journal.code)
        )
        if not include_inactive:
            query = query.where(Journal.is_active.is_(True))
        return (await self.session.execute(query)).scalars().all()

    async def get_by_type(self, organization_id: uuid.UUID, journal_type: str) -> Journal | None:
        """Resolve the journal a given source posts to - Stage 3/4 use this."""
        query = (
            select(Journal)
            .where(
                Journal.organization_id == organization_id,
                Journal.journal_type == journal_type,
                Journal.is_active.is_(True),
            )
            .order_by(Journal.created_at)
        )
        return (await self.session.execute(query.limit(1))).scalar_one_or_none()


# =============================================================================
# Numbering
# =============================================================================
class SequenceRepository(BaseRepository[NumberSequence]):
    model = NumberSequence

    async def next_number(
        self,
        organization_id: uuid.UUID,
        scope: str,
        *,
        prefix: str = "",
        padding: int = 4,
    ) -> str:
        """Reserve and format the next number for ``scope``.

        Locks the sequence row with ``FOR UPDATE``, so concurrent posts serialise
        on this one row rather than colliding. Because the increment is part of the
        caller's transaction, a rollback returns the number - which a PostgreSQL
        ``SEQUENCE`` cannot do, and statutory numbering requires.
        """
        query = (
            select(NumberSequence)
            .where(
                NumberSequence.organization_id == organization_id,
                NumberSequence.scope == scope,
            )
            .with_for_update()
        )
        sequence = (await self.session.execute(query)).scalar_one_or_none()

        if sequence is None:
            sequence = NumberSequence(
                organization_id=organization_id,
                scope=scope,
                prefix=prefix,
                next_value=1,
                padding=padding,
            )
            self.session.add(sequence)
            await self.session.flush()

        value = sequence.next_value
        sequence.next_value = value + 1
        await self.session.flush()
        return sequence.format(value)


# =============================================================================
# Journal entries
# =============================================================================
class JournalEntryRepository(BaseRepository[JournalEntry]):
    model = JournalEntry
    sortable_fields = frozenset({"entry_date", "entry_number", "created_at", "total_debit"})
    default_sort = "-entry_date"

    def _with_lines(self) -> Select[tuple[JournalEntry]]:
        """Eager-load lines and their accounts.

        ``selectinload`` rather than ``joinedload``: an entry has few lines, and a
        JOIN would multiply the parent row per line, forcing SQLAlchemy to
        de-duplicate. Two clean queries beat one cartesian product.
        """
        return select(JournalEntry).options(
            selectinload(JournalEntry.lines).selectinload(JournalEntryLine.account),
            selectinload(JournalEntry.journal),
        )

    async def get_with_lines(
        self, organization_id: uuid.UUID, entry_id: uuid.UUID
    ) -> JournalEntry | None:
        query = self._with_lines().where(
            JournalEntry.organization_id == organization_id,
            JournalEntry.id == entry_id,
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    def _filters(
        self,
        organization_id: uuid.UUID,
        *,
        journal_id: uuid.UUID | None = None,
        status: EntryStatus | None = None,
        from_date: dt.date | None = None,
        to_date: dt.date | None = None,
        account_id: uuid.UUID | None = None,
        source_type: str | None = None,
        source_id: uuid.UUID | None = None,
    ) -> list[ColumnElement[bool]]:
        clauses: list[ColumnElement[bool]] = [JournalEntry.organization_id == organization_id]
        if journal_id is not None:
            clauses.append(JournalEntry.journal_id == journal_id)
        if status is not None:
            clauses.append(JournalEntry.status == status)
        if from_date is not None:
            clauses.append(JournalEntry.entry_date >= from_date)
        if to_date is not None:
            clauses.append(JournalEntry.entry_date <= to_date)
        if source_type is not None:
            clauses.append(JournalEntry.source_type == source_type)
        if source_id is not None:
            clauses.append(JournalEntry.source_id == source_id)
        if account_id is not None:
            # EXISTS rather than a JOIN: a JOIN would duplicate the entry row once
            # per matching line, breaking both the count and the page size.
            clauses.append(
                select(JournalEntryLine.id)
                .where(
                    JournalEntryLine.entry_id == JournalEntry.id,
                    JournalEntryLine.account_id == account_id,
                )
                .exists()
            )
        return clauses

    async def paginate_entries(
        self,
        organization_id: uuid.UUID,
        params: PageParams,
        **filters: object,
    ) -> tuple[Sequence[JournalEntry], int]:
        clauses = self._filters(organization_id, **filters)  # type: ignore[arg-type]

        count_query = select(func.count()).select_from(JournalEntry).where(and_(*clauses))
        total = int((await self.session.execute(count_query)).scalar_one())

        query = (
            self._with_lines()
            .where(and_(*clauses))
            .order_by(JournalEntry.entry_date.desc(), JournalEntry.created_at.desc())
            .offset(params.offset)
            .limit(params.limit)
        )
        rows = (await self.session.execute(query)).scalars().all()
        return rows, total

    async def ledger_lines(
        self,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        *,
        from_date: dt.date,
        to_date: dt.date,
    ) -> Sequence[tuple[JournalEntryLine, JournalEntry, Journal]]:
        """Chronological lines for one account, for the general ledger.

        Ordered by date then entry number so the running balance is deterministic
        - two entries on the same day must always appear in the same order, or the
        printed ledger changes between runs.
        """
        query = (
            select(JournalEntryLine, JournalEntry, Journal)
            .join(JournalEntry, JournalEntry.id == JournalEntryLine.entry_id)
            .join(Journal, Journal.id == JournalEntry.journal_id)
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntryLine.account_id == account_id,
                JournalEntry.status.in_(POSTED_STATUSES),
                JournalEntry.entry_date >= from_date,
                JournalEntry.entry_date <= to_date,
            )
            .order_by(
                JournalEntry.entry_date,
                JournalEntry.entry_number,
                JournalEntryLine.line_number,
            )
        )
        return [tuple(row) for row in (await self.session.execute(query)).all()]

    async def entries_touching_accounts(
        self,
        organization_id: uuid.UUID,
        account_ids: Sequence[uuid.UUID],
        *,
        from_date: dt.date,
        to_date: dt.date,
    ) -> Sequence[JournalEntry]:
        """Every posted entry with a line on any of ``account_ids``, lines loaded.

        Exists for the cash flow statement, which needs *both* sides of each entry:
        the cash movement and the counter-account explaining it. Fetching per line
        would be an N+1 over the whole period, so the entries come back whole in
        one round trip and are grouped in Python.
        """
        if not account_ids:
            return []

        touches = (
            select(JournalEntryLine.entry_id)
            .where(JournalEntryLine.account_id.in_(list(account_ids)))
            .scalar_subquery()
        )
        query = (
            self._with_lines()
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.status.in_(POSTED_STATUSES),
                JournalEntry.entry_date >= from_date,
                JournalEntry.entry_date <= to_date,
                JournalEntry.id.in_(touches),
            )
            .order_by(JournalEntry.entry_date, JournalEntry.entry_number)
        )
        return (await self.session.execute(query)).scalars().unique().all()

    async def cursor_entries(
        self, organization_id: uuid.UUID, params: CursorParams, **filters: object
    ) -> Sequence[JournalEntry]:
        clauses = self._filters(organization_id, **filters)  # type: ignore[arg-type]
        return await self.paginate_cursor(params, and_(*clauses))

    async def counts_by_status(self, organization_id: uuid.UUID) -> dict[str, int]:
        query = (
            select(JournalEntry.status, func.count())
            .where(JournalEntry.organization_id == organization_id)
            .group_by(JournalEntry.status)
        )
        return {str(status): count for status, count in (await self.session.execute(query)).all()}
