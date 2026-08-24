"""Financial statements.

Five reports, all derived from the same source - posted journal lines - so they
cannot disagree with each other:

* **Trial balance** - every account's debits and credits. Total debits must equal
  total credits; if they do not, the ledger is corrupt and every other report is
  meaningless.
* **General ledger** - one account's movements with a running balance.
* **Profit & loss** - income minus expenses over a *period*.
* **Balance sheet** - assets, liabilities, and equity *as at a date*.
* **Cash flow** - movement in cash and bank accounts over a period.

**The one subtlety that makes a balance sheet balance.** P&L accounts reset each
fiscal year; their net result rolls into retained earnings only when the year is
closed. So mid-year, ``assets != liabilities + equity`` - the difference is
exactly the profit earned so far. This module computes that figure and presents it
as a distinct equity line, ``current_period_earnings``. Omitting it is the classic
reason a hand-rolled balance sheet fails to balance.

Every report is a pure read. Nothing here writes, so a report can never corrupt
the data it describes.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.types import ZERO
from app.modules.accounting.models import (
    Account,
    AccountSubtype,
    AccountType,
    BalanceSide,
    EntryStatus,
    JournalEntry,
    JournalEntryLine,
)
from app.modules.accounting.repository import (
    POSTED_STATUSES,
    AccountRepository,
    FiscalCalendarRepository,
    JournalEntryRepository,
)
from app.modules.accounting.schemas import (
    AccountLedger,
    AccountRead,
    BalanceSheet,
    CashFlowStatement,
    LedgerLine,
    ProfitAndLoss,
    ReportLine,
    TrialBalance,
    TrialBalanceRow,
)

log = get_logger(__name__)


class _Totals(NamedTuple):
    debit: Decimal
    credit: Decimal


def signed_balance(account_type: AccountType, debit: Decimal, credit: Decimal) -> Decimal:
    """Balance in the account's own direction.

    Positive always means "more of what this account represents": more cash in an
    asset, more owed on a liability, more earned in an income account. Defined
    once here so no report has to re-derive the sign convention.

    Public because analytics needs it too. A dashboard tile that signs balances
    differently from the P&L would show a different revenue figure on the same data,
    and there would be no way to tell which one was right.
    """
    if account_type.normal_balance is BalanceSide.DEBIT:
        return debit - credit
    return credit - debit


class ReportingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.accounts = AccountRepository(session)
        self.entries = JournalEntryRepository(session)
        self.calendar = FiscalCalendarRepository(session)

    # -------------------------------------------------------------------------
    # Trial balance
    # -------------------------------------------------------------------------
    async def trial_balance(
        self,
        organization_id: uuid.UUID,
        *,
        as_of: dt.date,
        from_date: dt.date | None = None,
        include_zero: bool = False,
    ) -> TrialBalance:
        """Debits and credits per account.

        Only postable (leaf) accounts appear: including groups would double-count,
        since a group's balance is its children's.

        A balance is reported on the side it naturally falls on - a net debit
        balance in the debit column - which is what makes the two column totals
        equal.
        """
        accounts = await self.accounts.list_for_org(
            organization_id, include_inactive=True, postable_only=True
        )
        balances = await self.accounts.balances(organization_id, to_date=as_of, from_date=from_date)
        parties = await self._counterparties(organization_id, as_of=as_of, from_date=from_date)

        rows: list[TrialBalanceRow] = []
        total_debit = ZERO
        total_credit = ZERO

        for account in accounts:
            balance = balances.get(account.id)
            if balance is None and not include_zero:
                continue

            debit = balance.total_debit if balance else ZERO
            credit = balance.total_credit if balance else ZERO
            net = debit - credit

            # An account that had movement stays on the report even when it nets to
            # nothing. Dropping it is how a reversed ₹100 charge vanishes without trace:
            # the journal shows the entry and its reversal, and the trial balance shows
            # neither, which makes the two impossible to reconcile.
            had_activity = debit != 0 or credit != 0
            if net == 0 and not had_activity and not include_zero:
                continue

            # Present the net on whichever side it falls, not the gross of both.
            row_debit = net if net > 0 else ZERO
            row_credit = -net if net < 0 else ZERO

            rows.append(
                TrialBalanceRow(
                    account_id=account.id,
                    code=account.code,
                    name=account.name,
                    account_type=account.account_type,
                    debit=row_debit,
                    credit=row_credit,
                    gross_debit=debit,
                    gross_credit=credit,
                    parties=parties[account.id],
                )
            )
            total_debit += row_debit
            total_credit += row_credit

        is_balanced = total_debit == total_credit
        if not is_balanced:
            # Should be impossible: every entry is balanced by CHECK constraint.
            # If it happens, something has written to the database outside the
            # application, and that is worth shouting about.
            log.critical(
                "trial balance does not balance - ledger integrity compromised",
                extra={
                    "organization_id": str(organization_id),
                    "total_debit": str(total_debit),
                    "total_credit": str(total_credit),
                    "difference": str(total_debit - total_credit),
                },
            )

        return TrialBalance(
            as_of=as_of,
            from_date=from_date,
            rows=rows,
            total_debit=total_debit,
            total_credit=total_credit,
            is_balanced=is_balanced,
            reversed_entry_count=await self._reversed_count(
                organization_id, as_of=as_of, from_date=from_date
            ),
        )

    async def _counterparties(
        self, organization_id: uuid.UUID, *, as_of: dt.date, from_date: dt.date | None
    ) -> dict[uuid.UUID, list[str]]:
        """The parties each account has dealt with.

        A trial-balance row is one account aggregated over every entry that touched it, so
        unlike a journal entry it has no single counterparty - hence a list.

        **One list, not a from/to pair.** An account that both received from and paid the
        same person then showed that name in both columns, which reads as a contradiction
        even though it is exactly what happened. Direction is a property of a transaction;
        this row is a balance. The Billing day book and the journal are where a single
        movement's direction belongs, and both already state it.

        **Only what someone actually typed.** These are ``counterparty`` values and nothing
        else; an entry that named nobody contributes no name. An earlier version fell back
        to the account on the other side of the entry, which filled the column with "Cash
        on Hand" and "Salaries & Wages" - the chart of accounts restated, not an answer to
        "who was this with".

        Aggregated in SQL rather than by walking the entries: one grouped query whatever
        the ledger's size, against pulling every line in the window into Python.
        """
        query = (
            select(JournalEntryLine.account_id, JournalEntry.counterparty)
            .join(JournalEntry, JournalEntry.id == JournalEntryLine.entry_id)
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.status.in_(POSTED_STATUSES),
                JournalEntry.entry_date <= as_of,
                JournalEntry.counterparty.is_not(None),
            )
            # Distinct pairs only: an account paid by the same party fifty times should
            # name them once.
            .group_by(JournalEntryLine.account_id, JournalEntry.counterparty)
            .order_by(JournalEntryLine.account_id, JournalEntry.counterparty)
        )
        if from_date is not None:
            query = query.where(JournalEntry.entry_date >= from_date)

        result: dict[uuid.UUID, list[str]] = defaultdict(list)
        for account_id, party in await self.session.execute(query):
            result[account_id].append(party)
        return result

    async def _reversed_count(
        self, organization_id: uuid.UUID, *, as_of: dt.date, from_date: dt.date | None
    ) -> int:
        """How many entries in this window were cancelled by a reversal."""
        query = (
            select(func.count())
            .select_from(JournalEntry)
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.status == EntryStatus.REVERSED,
                JournalEntry.entry_date <= as_of,
            )
        )
        if from_date is not None:
            query = query.where(JournalEntry.entry_date >= from_date)
        return (await self.session.execute(query)).scalar_one()

    # -------------------------------------------------------------------------
    # General ledger
    # -------------------------------------------------------------------------
    async def account_ledger(
        self,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        *,
        from_date: dt.date,
        to_date: dt.date,
    ) -> AccountLedger:
        """One account's movements, with an opening balance and running total."""
        if to_date < from_date:
            raise ValidationError("to_date cannot be before from_date")

        account = await self.accounts.get(account_id)
        if account is None or account.organization_id != organization_id:
            raise NotFoundError("Account")

        # Everything before the window, collapsed into one figure.
        opening = await self.accounts.balance_for(
            account_id, to_date=from_date - dt.timedelta(days=1)
        )
        opening_balance = signed_balance(
            account.account_type, opening.total_debit, opening.total_credit
        )

        rows = await self.entries.ledger_lines(
            organization_id, account_id, from_date=from_date, to_date=to_date
        )

        lines: list[LedgerLine] = []
        running = opening_balance
        total_debit = ZERO
        total_credit = ZERO

        for line, entry, journal in rows:
            total_debit += line.debit
            total_credit += line.credit
            # The running balance moves in the account's own direction, so the
            # final figure matches the closing balance without a sign flip.
            if account.normal_balance is BalanceSide.DEBIT:
                running += line.debit - line.credit
            else:
                running += line.credit - line.debit

            lines.append(
                LedgerLine(
                    entry_id=entry.id,
                    entry_number=entry.entry_number,
                    entry_date=entry.entry_date,
                    narration=entry.narration,
                    reference=entry.reference,
                    journal_code=journal.code,
                    debit=line.debit,
                    credit=line.credit,
                    running_balance=running,
                )
            )

        return AccountLedger(
            account=AccountRead.model_validate(account),
            from_date=from_date,
            to_date=to_date,
            opening_balance=opening_balance,
            closing_balance=running,
            total_debit=total_debit,
            total_credit=total_credit,
            lines=lines,
        )

    # -------------------------------------------------------------------------
    # Profit & loss
    # -------------------------------------------------------------------------
    async def profit_and_loss(
        self,
        organization_id: uuid.UUID,
        *,
        from_date: dt.date,
        to_date: dt.date,
    ) -> ProfitAndLoss:
        """Income and expenses for a period.

        Cost of goods sold is separated from operating expenses so gross profit is
        meaningful - for a trading business that is the number that matters, and it
        cannot be recovered from a single lumped expense total.
        """
        if to_date < from_date:
            raise ValidationError("to_date cannot be before from_date")

        accounts = await self.accounts.list_for_org(
            organization_id, include_inactive=True, postable_only=True
        )
        # from_date is supplied: a P&L measures movement in a window, unlike a
        # balance sheet which is cumulative since inception.
        balances = await self.accounts.balances(
            organization_id, to_date=to_date, from_date=from_date
        )

        income_lines: list[ReportLine] = []
        expense_lines: list[ReportLine] = []
        total_income = ZERO
        total_expenses = ZERO
        cogs = ZERO

        for account in sorted(accounts, key=lambda a: a.code):
            if not account.account_type.is_profit_and_loss:
                continue
            balance = balances.get(account.id)
            if balance is None:
                continue

            amount = signed_balance(account.account_type, balance.total_debit, balance.total_credit)
            if amount == 0:
                continue

            line = ReportLine(
                label=account.name,
                amount=amount,
                level=1,
                account_id=account.id,
                account_code=account.code,
            )
            if account.account_type is AccountType.INCOME:
                income_lines.append(line)
                total_income += amount
            else:
                expense_lines.append(line)
                total_expenses += amount
                if account.subtype is AccountSubtype.COST_OF_GOODS_SOLD:
                    cogs += amount

        return ProfitAndLoss(
            from_date=from_date,
            to_date=to_date,
            income=income_lines,
            expenses=expense_lines,
            total_income=total_income,
            total_expenses=total_expenses,
            cost_of_goods_sold=cogs,
            gross_profit=total_income - cogs,
            net_profit=total_income - total_expenses,
        )

    # -------------------------------------------------------------------------
    # Balance sheet
    # -------------------------------------------------------------------------
    async def balance_sheet(self, organization_id: uuid.UUID, *, as_of: dt.date) -> BalanceSheet:
        """Assets, liabilities, and equity at a point in time.

        Balance-sheet balances are cumulative since inception, so no ``from_date``
        is passed. Current-year P&L is computed separately over the fiscal year and
        surfaced as an equity line - see the module docstring for why that is what
        makes the statement balance.
        """
        accounts = await self.accounts.list_for_org(
            organization_id, include_inactive=True, postable_only=True
        )
        balances = await self.accounts.balances(organization_id, to_date=as_of)

        grouped: dict[AccountType, list[ReportLine]] = defaultdict(list)
        totals: dict[AccountType, Decimal] = defaultdict(lambda: ZERO)
        earnings = ZERO

        for account in sorted(accounts, key=lambda a: a.code):
            balance = balances.get(account.id)
            if balance is None:
                continue
            amount = signed_balance(account.account_type, balance.total_debit, balance.total_credit)

            if account.account_type.is_profit_and_loss:
                # Income increases earnings, expenses reduce them.
                earnings += amount if account.account_type is AccountType.INCOME else -amount
                continue

            if amount == 0:
                continue
            grouped[account.account_type].append(
                ReportLine(
                    label=account.name,
                    amount=amount,
                    level=1,
                    account_id=account.id,
                    account_code=account.code,
                )
            )
            totals[account.account_type] += amount

        # Earnings above cover all time. Anything before the current fiscal year
        # should already sit in retained earnings via year-end closing, so only
        # this year's movement is shown as unappropriated.
        fiscal_year = await self.calendar.year_containing(organization_id, as_of)
        if fiscal_year is not None:
            current = await self.accounts.balances(
                organization_id, to_date=as_of, from_date=fiscal_year.start_date
            )
            earnings = ZERO
            for account in accounts:
                if not account.account_type.is_profit_and_loss:
                    continue
                balance = current.get(account.id)
                if balance is None:
                    continue
                amount = signed_balance(
                    account.account_type, balance.total_debit, balance.total_credit
                )
                earnings += amount if account.account_type is AccountType.INCOME else -amount

        equity_lines = list(grouped[AccountType.EQUITY])
        if earnings != 0:
            equity_lines.append(
                ReportLine(label="Current period earnings", amount=earnings, level=1)
            )

        total_assets = totals[AccountType.ASSET]
        total_liabilities = totals[AccountType.LIABILITY]
        total_equity = totals[AccountType.EQUITY] + earnings

        return BalanceSheet(
            as_of=as_of,
            assets=grouped[AccountType.ASSET],
            liabilities=grouped[AccountType.LIABILITY],
            equity=equity_lines,
            total_assets=total_assets,
            total_liabilities=total_liabilities,
            total_equity=total_equity,
            current_period_earnings=earnings,
            is_balanced=total_assets == total_liabilities + total_equity,
        )

    # -------------------------------------------------------------------------
    # Cash flow
    # -------------------------------------------------------------------------
    async def cash_flow(
        self,
        organization_id: uuid.UUID,
        *,
        from_date: dt.date,
        to_date: dt.date,
    ) -> CashFlowStatement:
        """Movement in cash and bank accounts over a period.

        **Direct method**, built from actual cash-account movements and grouped by
        the counter-account they came from or went to. The indirect method (net
        profit adjusted for non-cash items and working-capital changes) is the
        statutory presentation for larger entities and is deferred - for a small
        business, "where did the money actually go" is both more useful and
        verifiable line by line against the bank statement.
        """
        if to_date < from_date:
            raise ValidationError("to_date cannot be before from_date")

        accounts = await self.accounts.list_for_org(
            organization_id, include_inactive=True, postable_only=True
        )
        cash_accounts = [a for a in accounts if a.subtype.is_cash_equivalent]
        by_id = {a.id: a for a in accounts}

        if not cash_accounts:
            return CashFlowStatement(
                from_date=from_date,
                to_date=to_date,
                opening_cash=ZERO,
                closing_cash=ZERO,
                net_change=ZERO,
                inflows=[],
                outflows=[],
                total_inflows=ZERO,
                total_outflows=ZERO,
                reconciles=True,
            )

        opening_cash = ZERO
        for account in cash_accounts:
            prior = await self.accounts.balance_for(
                account.id, to_date=from_date - dt.timedelta(days=1)
            )
            opening_cash += signed_balance(
                account.account_type, prior.total_debit, prior.total_credit
            )

        # Walk each cash account's movements and attribute them to the other side
        # of the same entry - that counter-account is the reason the cash moved.
        inflow_totals: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
        outflow_totals: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
        total_inflows = ZERO
        total_outflows = ZERO

        cash_ids = {account.id for account in cash_accounts}

        def is_cash(account_id: uuid.UUID) -> bool:
            return account_id in cash_ids

        # One query for every entry touching cash in the window, lines included.
        entries = await self.entries.entries_touching_accounts(
            organization_id, list(cash_ids), from_date=from_date, to_date=to_date
        )

        for entry in entries:
            cash_lines = [line for line in entry.lines if is_cash(line.account_id)]
            # Counter-lines explain why the cash moved. Cash-to-cash transfers
            # (bank to petty cash) have no counter-line and are skipped: they net
            # to zero and must not appear as both an inflow and an outflow.
            counter = [line for line in entry.lines if not is_cash(line.account_id)]
            if not counter:
                continue

            counter_total = sum((line.amount for line in counter), ZERO)
            if counter_total == 0:
                continue

            movement = sum((line.debit - line.credit for line in cash_lines), ZERO)
            if movement == 0:
                continue

            for other in counter:
                # Split proportionally when one cash movement offsets several
                # counter-lines, so the parts sum back to the whole.
                share = (abs(movement) * other.amount) / counter_total
                if movement > 0:
                    inflow_totals[other.account_id] += share
                    total_inflows += share
                else:
                    outflow_totals[other.account_id] += share
                    total_outflows += share

        def to_lines(totals_map: dict[uuid.UUID, Decimal]) -> list[ReportLine]:
            lines = [
                ReportLine(
                    label=by_id[account_id].name if account_id in by_id else "Unknown",
                    amount=amount,
                    level=1,
                    account_id=account_id,
                    account_code=by_id[account_id].code if account_id in by_id else None,
                )
                for account_id, amount in totals_map.items()
                if amount != 0
            ]
            return sorted(lines, key=lambda line: line.amount, reverse=True)

        closing_cash = ZERO
        for account in cash_accounts:
            current = await self.accounts.balance_for(account.id, to_date=to_date)
            closing_cash += signed_balance(
                account.account_type, current.total_debit, current.total_credit
            )

        net_change = closing_cash - opening_cash
        reconciles = (total_inflows - total_outflows) == net_change

        return CashFlowStatement(
            from_date=from_date,
            to_date=to_date,
            opening_cash=opening_cash,
            closing_cash=closing_cash,
            net_change=net_change,
            inflows=to_lines(inflow_totals),
            outflows=to_lines(outflow_totals),
            total_inflows=total_inflows,
            total_outflows=total_outflows,
            reconciles=reconciles,
        )

    # -------------------------------------------------------------------------
    # Chart with balances
    # -------------------------------------------------------------------------
    async def chart_with_balances(
        self, organization_id: uuid.UUID, *, as_of: dt.date
    ) -> list[Account]:
        """Accounts ordered by code, for the chart view."""
        return list(await self.accounts.list_for_org(organization_id, include_inactive=True))
