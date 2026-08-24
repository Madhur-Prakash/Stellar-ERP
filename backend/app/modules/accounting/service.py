"""Accounting services - chart, calendar, and the posting engine.

The posting engine is the part worth reading carefully. Every rule it enforces
exists because violating it produces books that cannot be corrected later:

* **Balance.** Enforced in the schema, re-checked here, and constrained in the
  database. Three layers, because an unbalanced entry makes every downstream
  report wrong and there is no way to tell which side was right.
* **Postable accounts only.** A posting to a group account makes its subtree sum
  ambiguous; a posting to another tenant's account is a cross-tenant leak.
* **Open period only.** A posting into a filed month changes numbers already
  submitted to the tax authority.
* **Immutability after posting.** Correction is by reversal. There is no code path
  that edits a posted entry, which is a stronger guarantee than a permission check.
* **Numbering at posting time.** A draft that is discarded must not consume a
  statutory number, so numbers are assigned when the entry enters the books.
"""

from __future__ import annotations

import datetime as dt
import uuid
from calendar import monthrange
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.db.types import ZERO
from app.modules.accounting.coa_template import (
    DEFAULT_CHART,
    DEFAULT_JOURNALS,
    SystemAccount,
)
from app.modules.accounting.models import (
    Account,
    AccountingPeriod,
    AccountType,
    EntryStatus,
    FiscalYear,
    Journal,
    JournalEntry,
    JournalEntryLine,
    JournalType,
    PeriodStatus,
)
from app.modules.accounting.repository import (
    AccountRepository,
    FiscalCalendarRepository,
    JournalEntryRepository,
    JournalRepository,
    SequenceRepository,
)
from app.modules.accounting.schemas import (
    AccountCreate,
    AccountUpdate,
    FiscalYearCreate,
    JournalCreate,
    JournalEntryCreate,
    JournalEntryUpdate,
)
from app.modules.audit.models import AuditAction, AuditSeverity
from app.modules.audit.service import AuditService
from app.modules.organizations.clock import organization_today
from app.modules.users.models import User

log = get_logger(__name__)

MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _audit_ctx(ctx: RequestContext | None) -> dict[str, Any]:
    """Map a :class:`RequestContext` onto the audit recorder's keyword arguments.

    ``RequestContext`` is a frozen dataclass, not a mapping, so it cannot be
    splatted directly. Converting in one place keeps every call site identical.
    """
    if ctx is None:
        return {}
    return {"ip_address": ctx.ip_address, "user_agent": ctx.user_agent}


# =============================================================================
# Chart of accounts
# =============================================================================
class ChartOfAccountsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.accounts = AccountRepository(session)
        self.journals = JournalRepository(session)
        self.audit = AuditService(session)

    # -- Seeding -------------------------------------------------------------
    async def seed_defaults(self, organization_id: uuid.UUID) -> tuple[int, int]:
        """Create the default chart and journals for a new organization.

        Idempotent: if any account already exists the seed is skipped entirely
        rather than partially re-applied, so calling it twice cannot produce
        duplicate codes or a half-built chart.

        Returns ``(accounts_created, journals_created)``.
        """
        if await self.accounts.exists(organization_id=organization_id):
            log.info(
                "chart of accounts already seeded, skipping",
                extra={"organization_id": str(organization_id)},
            )
            return 0, 0

        by_code: dict[str, Account] = {}
        for spec in DEFAULT_CHART:
            parent = by_code.get(spec.parent) if spec.parent else None
            account = Account(
                organization_id=organization_id,
                code=spec.code,
                name=spec.name,
                account_type=spec.account_type,
                subtype=spec.subtype,
                parent_id=parent.id if parent else None,
                depth=(parent.depth + 1) if parent else 0,
                is_group=spec.is_group,
                is_system=True,
                is_reconcilable=spec.reconcilable,
                system_key=spec.system_key,
            )
            self.session.add(account)
            # Flush per account: the next spec may reference this one as its
            # parent and needs its generated id.
            await self.session.flush()
            by_code[spec.code] = account

        journals_created = 0
        for jspec in DEFAULT_JOURNALS:
            self.session.add(
                Journal(
                    organization_id=organization_id,
                    code=jspec.code,
                    name=jspec.name,
                    journal_type=JournalType(jspec.journal_type),
                    number_prefix=jspec.number_prefix,
                    is_system=True,
                )
            )
            journals_created += 1
        await self.session.flush()

        log.info(
            "seeded chart of accounts",
            extra={
                "organization_id": str(organization_id),
                "accounts": len(by_code),
                "journals": journals_created,
            },
        )
        return len(by_code), journals_created

    async def sync_template(self, organization_id: uuid.UUID) -> int:
        """Add template accounts this organization is missing, by code.

        ``seed_defaults`` skips entirely once any account exists, which is right for
        seeding but means an organization set up against an older template never gains
        anything added later. That is not hypothetical: the household and expanded
        expense categories arrived after the first organizations were created, and
        without this their owners would only see the original list.

        Matching is by **code**, and existing rows are never touched - so an account
        the user renamed keeps its name, and one they deactivated stays inactive.
        Adding is the only operation. Nothing is renamed, re-parented, or deleted,
        because a code already in use may have postings against it.

        Returns the number of accounts created.
        """
        existing = {
            account.code: account
            for account in await self.accounts.list_for_org(organization_id, include_inactive=True)
        }
        if not existing:
            # Nothing at all: this is a seed, not a top-up.
            created, _ = await self.seed_defaults(organization_id)
            return created

        added = 0
        for spec in DEFAULT_CHART:
            if spec.code in existing:
                continue

            parent = existing.get(spec.parent) if spec.parent else None
            if spec.parent and parent is None:
                # The parent group is itself missing and comes later in the template;
                # skipping keeps the tree consistent, and the next call picks it up
                # once the parent exists. Ordering means this converges in one pass in
                # practice, because parents always precede their children.
                log.warning(
                    "skipping template account with an unresolved parent",
                    extra={"code": spec.code, "parent": spec.parent},
                )
                continue

            account = Account(
                organization_id=organization_id,
                code=spec.code,
                name=spec.name,
                account_type=spec.account_type,
                subtype=spec.subtype,
                parent_id=parent.id if parent else None,
                depth=(parent.depth + 1) if parent else 0,
                is_group=spec.is_group,
                is_system=True,
                is_reconcilable=spec.reconcilable,
                system_key=spec.system_key,
            )
            self.session.add(account)
            await self.session.flush()
            existing[spec.code] = account
            added += 1

        if added:
            log.info(
                "topped up chart of accounts from the template",
                extra={"organization_id": str(organization_id), "added": added},
            )
        return added

    # -- Reads ---------------------------------------------------------------
    async def list_accounts(
        self,
        organization_id: uuid.UUID,
        *,
        account_type: AccountType | None = None,
        include_inactive: bool = False,
        postable_only: bool = False,
    ) -> list[Account]:
        return list(
            await self.accounts.list_for_org(
                organization_id,
                account_type=account_type,
                include_inactive=include_inactive,
                postable_only=postable_only,
            )
        )

    async def get_account(self, organization_id: uuid.UUID, account_id: uuid.UUID) -> Account:
        account = await self.accounts.get(account_id)
        if account is None or account.organization_id != organization_id:
            # Same error whether it is missing or another tenant's: distinguishing
            # them would confirm the existence of other organizations' records.
            raise NotFoundError("Account")
        return account

    async def resolve_system_account(self, organization_id: uuid.UUID, key: str) -> Account:
        """Look up the default account for a role. Used by later stages."""
        account = await self.accounts.get_by_system_key(organization_id, key)
        if account is None:
            raise BusinessRuleError(
                f"No account is configured for '{key}'. Set one in the chart of accounts.",
                details={"system_key": key},
            )
        return account

    # -- Writes --------------------------------------------------------------
    async def create_account(
        self,
        organization_id: uuid.UUID,
        data: AccountCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Account:
        if await self.accounts.get_by_code(organization_id, data.code):
            raise ConflictError(
                f"Account code {data.code} is already in use",
                details={"code": data.code},
            )

        parent: Account | None = None
        depth = 0
        if data.parent_id is not None:
            parent = await self.get_account(organization_id, data.parent_id)
            if parent.account_type is not data.account_type:
                raise ValidationError(
                    "An account must have the same type as its parent",
                    details={
                        "account_type": str(data.account_type),
                        "parent_type": str(parent.account_type),
                    },
                )
            depth = parent.depth + 1

            # A parent that already holds postings cannot become a group: its own
            # balance would stop being attributable to anything.
            if not parent.is_group:
                if await self.accounts.has_postings(parent.id):
                    raise BusinessRuleError(
                        "This account already has journal entries, so it cannot "
                        "become a group. Create the new account elsewhere."
                    )
                parent.is_group = True

        account = await self.accounts.add(
            Account(
                organization_id=organization_id,
                code=data.code,
                name=data.name,
                description=data.description,
                account_type=data.account_type,
                subtype=data.subtype,
                parent_id=parent.id if parent else None,
                depth=depth,
                is_group=data.is_group,
                is_reconcilable=data.is_reconcilable,
            )
        )

        await self.audit.record(
            AuditAction.ACCOUNT_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="account",
            resource_id=account.id,
            summary=f"Created account {account.label}",
            **_audit_ctx(ctx),
        )
        return account

    async def update_account(
        self,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        data: AccountUpdate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Account:
        account = await self.get_account(organization_id, account_id)
        changes: dict[str, Any] = {}

        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "parent_id":
                continue  # re-parenting is handled separately below
            current = getattr(account, field)
            if current != value:
                changes[field] = {"before": current, "after": value}
                setattr(account, field, value)

        if data.is_active is False and account.is_system:
            raise BusinessRuleError(
                "System accounts cannot be deactivated - later modules post to them."
            )

        await self.session.flush()

        if changes:
            await self.audit.record(
                AuditAction.ACCOUNT_UPDATED,
                actor=actor,
                organization_id=organization_id,
                resource_type="account",
                resource_id=account.id,
                summary=f"Updated account {account.label}",
                changes=changes,
                **_audit_ctx(ctx),
            )
        return account

    async def delete_account(
        self,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> None:
        """Soft-delete an account, if nothing depends on it."""
        account = await self.get_account(organization_id, account_id)

        if account.is_system:
            raise BusinessRuleError("System accounts cannot be deleted, only deactivated.")
        if await self.accounts.has_children(account.id):
            raise BusinessRuleError("Reassign or delete the child accounts first.")
        if await self.accounts.has_postings(account.id):
            raise BusinessRuleError(
                "This account has journal entries and cannot be deleted. "
                "Deactivate it instead - the history must remain intact."
            )

        await self.accounts.soft_delete(account)
        await self.audit.record(
            AuditAction.ACCOUNT_DELETED,
            actor=actor,
            organization_id=organization_id,
            resource_type="account",
            resource_id=account.id,
            summary=f"Deleted account {account.label}",
            severity=AuditSeverity.WARNING,
            **_audit_ctx(ctx),
        )


# =============================================================================
# Fiscal calendar
# =============================================================================
class FiscalCalendarService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.calendar = FiscalCalendarRepository(session)
        self.audit = AuditService(session)

    async def list_years(self, organization_id: uuid.UUID) -> list[FiscalYear]:
        return list(await self.calendar.list_years(organization_id))

    async def create_year(
        self,
        organization_id: uuid.UUID,
        data: FiscalYearCreate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> FiscalYear:
        clash = await self.calendar.overlapping_year(
            organization_id, data.start_date, data.end_date
        )
        if clash is not None:
            raise ConflictError(
                f"This range overlaps fiscal year {clash.name} "
                f"({clash.start_date} to {clash.end_date})",
            )

        year = FiscalYear(
            organization_id=organization_id,
            name=data.name,
            start_date=data.start_date,
            end_date=data.end_date,
        )
        self.session.add(year)
        await self.session.flush()

        if data.generate_periods:
            await self._generate_monthly_periods(year)

        await self.audit.record(
            AuditAction.FISCAL_YEAR_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="fiscal_year",
            resource_id=year.id,
            summary=f"Created fiscal year {year.name}",
            **_audit_ctx(ctx),
        )
        return year

    async def _generate_monthly_periods(self, year: FiscalYear) -> list[AccountingPeriod]:
        """Tile the fiscal year with calendar months.

        Periods are clipped to the year's bounds, so a year starting mid-month
        yields a short first period rather than one that overruns the year - which
        would let an entry fall in two periods at once.
        """
        periods: list[AccountingPeriod] = []
        cursor = year.start_date

        while cursor <= year.end_date:
            last_day = monthrange(cursor.year, cursor.month)[1]
            month_end = dt.date(cursor.year, cursor.month, last_day)
            period_end = min(month_end, year.end_date)

            period = AccountingPeriod(
                organization_id=year.organization_id,
                fiscal_year_id=year.id,
                name=f"{MONTH_NAMES[cursor.month - 1]} {cursor.year}",
                start_date=cursor,
                end_date=period_end,
            )
            self.session.add(period)
            periods.append(period)

            cursor = period_end + dt.timedelta(days=1)

        await self.session.flush()
        return periods

    async def ensure_year_for(
        self,
        organization_id: uuid.UUID,
        *,
        fiscal_year_start_month: int,
        on: dt.date | None = None,
    ) -> FiscalYear:
        """Create the fiscal year containing ``on``, if it does not exist.

        Called when an organization is created. Without a fiscal year - and the
        periods inside it - every posting fails, so a new organization would have
        working books it could not write to. Seeding the current year makes the
        ledger usable immediately.

        The year is derived from the organization's own start month: with April
        (the Indian convention), a date in February 2026 belongs to the year
        beginning April 2025.
        """
        today = on or await organization_today(self.session, organization_id)

        start_year = today.year if today.month >= fiscal_year_start_month else today.year - 1
        start = dt.date(start_year, fiscal_year_start_month, 1)
        end = dt.date(start_year + 1, fiscal_year_start_month, 1) - dt.timedelta(days=1)

        existing = await self.calendar.year_containing(organization_id, today)
        if existing is not None:
            return existing

        # "2026-27" for a split year, plain "2026" when it matches the calendar.
        name = (
            str(start_year)
            if fiscal_year_start_month == 1
            else f"{start_year}-{str(end.year)[-2:]}"
        )

        year = FiscalYear(
            organization_id=organization_id,
            name=name,
            start_date=start,
            end_date=end,
        )
        self.session.add(year)
        await self.session.flush()
        await self._generate_monthly_periods(year)

        log.info(
            "seeded fiscal year",
            extra={
                "organization_id": str(organization_id),
                "fiscal_year": name,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        return year

    async def resolve_open_period(
        self, organization_id: uuid.UUID, on: dt.date
    ) -> AccountingPeriod:
        """Find the period for a date and assert it accepts postings.

        The single gate every posting passes through.
        """
        period = await self.calendar.period_containing(organization_id, on)
        if period is None:
            raise BusinessRuleError(
                f"No accounting period covers {on.isoformat()}. Create the fiscal year first.",
                details={"entry_date": on.isoformat()},
            )
        if not period.accepts_postings:
            raise BusinessRuleError(
                f"{period.name} is {period.status} and cannot accept new entries.",
                details={"period": period.name, "status": str(period.status)},
            )
        return period

    async def close_period(
        self,
        organization_id: uuid.UUID,
        period_id: uuid.UUID,
        actor: User,
        *,
        lock: bool = False,
        ctx: RequestContext | None = None,
    ) -> AccountingPeriod:
        period = await self.calendar.get_period(organization_id, period_id)
        if period is None:
            raise NotFoundError("Accounting period")
        if period.status is PeriodStatus.LOCKED:
            raise BusinessRuleError("This period is locked and cannot be changed.")

        if await self.calendar.has_open_period_before(organization_id, period.start_date):
            raise BusinessRuleError(
                "An earlier period is still open. Close periods in order, "
                "or comparative reports cannot be reconciled."
            )

        period.status = PeriodStatus.LOCKED if lock else PeriodStatus.CLOSED
        period.closed_at = dt.datetime.now(dt.UTC)
        period.closed_by_id = actor.id
        await self.session.flush()

        await self.audit.record(
            AuditAction.PERIOD_CLOSED,
            actor=actor,
            organization_id=organization_id,
            resource_type="accounting_period",
            resource_id=period.id,
            summary=f"{'Locked' if lock else 'Closed'} {period.name}",
            severity=AuditSeverity.WARNING,
            **_audit_ctx(ctx),
        )
        return period

    async def reopen_period(
        self,
        organization_id: uuid.UUID,
        period_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> AccountingPeriod:
        period = await self.calendar.get_period(organization_id, period_id)
        if period is None:
            raise NotFoundError("Accounting period")
        if period.status is PeriodStatus.LOCKED:
            raise BusinessRuleError(
                "This period is locked. Locking is intended to be final - "
                "reopening requires a database-level override."
            )
        if period.status is PeriodStatus.OPEN:
            return period

        period.status = PeriodStatus.OPEN
        period.closed_at = None
        period.closed_by_id = None
        await self.session.flush()

        await self.audit.record(
            AuditAction.PERIOD_REOPENED,
            actor=actor,
            organization_id=organization_id,
            resource_type="accounting_period",
            resource_id=period.id,
            summary=f"Reopened {period.name}",
            severity=AuditSeverity.WARNING,
            **_audit_ctx(ctx),
        )
        return period


# =============================================================================
# Journals
# =============================================================================
class JournalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.journals = JournalRepository(session)
        self.accounts = AccountRepository(session)

    async def list_journals(self, organization_id: uuid.UUID) -> list[Journal]:
        return list(await self.journals.list_for_org(organization_id))

    async def get_journal(self, organization_id: uuid.UUID, journal_id: uuid.UUID) -> Journal:
        journal = await self.journals.get(journal_id)
        if journal is None or journal.organization_id != organization_id:
            raise NotFoundError("Journal")
        return journal

    async def create_journal(self, organization_id: uuid.UUID, data: JournalCreate) -> Journal:
        if await self.journals.get_by(organization_id=organization_id, code=data.code):
            raise ConflictError(f"Journal code {data.code} is already in use")

        if data.default_account_id is not None:
            account = await self.accounts.get(data.default_account_id)
            if account is None or account.organization_id != organization_id:
                raise NotFoundError("Account")
            if not account.is_postable:
                raise ValidationError("A journal's default account must be postable")

        return await self.journals.add(
            Journal(
                organization_id=organization_id,
                code=data.code,
                name=data.name,
                journal_type=data.journal_type,
                number_prefix=data.number_prefix,
                default_account_id=data.default_account_id,
            )
        )


# =============================================================================
# The posting engine
# =============================================================================
class PostingService:
    """Creates, posts, and reverses journal entries."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.entries = JournalEntryRepository(session)
        self.accounts = AccountRepository(session)
        self.journals = JournalRepository(session)
        self.sequences = SequenceRepository(session)
        self.calendar = FiscalCalendarService(session)
        self.audit = AuditService(session)

    # -- Validation ----------------------------------------------------------
    async def _load_postable_accounts(
        self, organization_id: uuid.UUID, account_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Account]:
        """Fetch and validate every account an entry references.

        One query for all of them, then validate in Python - a per-line query
        would turn a 20-line entry into 20 round trips.
        """
        unique_ids = list(dict.fromkeys(account_ids))
        found = await self.accounts.list_all(Account.id.in_(unique_ids))
        by_id = {account.id: account for account in found}

        for account_id in unique_ids:
            account = by_id.get(account_id)
            if account is None or account.organization_id != organization_id:
                raise ValidationError(
                    "An account on this entry does not exist",
                    details={"account_id": str(account_id)},
                )
            if account.is_group:
                raise BusinessRuleError(
                    f"{account.label} is a group account and cannot receive postings. "
                    "Post to one of its sub-accounts.",
                    details={"account_id": str(account_id)},
                )
            if not account.is_active:
                raise BusinessRuleError(
                    f"{account.label} is inactive.",
                    details={"account_id": str(account_id)},
                )
        return by_id

    @staticmethod
    def _totals(lines: list[JournalEntryLine]) -> tuple[Decimal, Decimal]:
        debit = sum((line.debit for line in lines), ZERO)
        credit = sum((line.credit for line in lines), ZERO)
        return debit, credit

    # -- Create --------------------------------------------------------------
    async def create_entry(
        self,
        organization_id: uuid.UUID,
        data: JournalEntryCreate,
        actor: User,
        ctx: RequestContext | None = None,
        *,
        source_type: str | None = None,
        source_id: uuid.UUID | None = None,
    ) -> JournalEntry:
        """Create a draft or posted entry.

        ``source_type``/``source_id`` are for programmatic callers - Stage 3's
        invoice posting passes them so the entry can be traced back to its
        originating document.
        """
        journal = await self.journals.get(data.journal_id)
        if journal is None or journal.organization_id != organization_id:
            raise NotFoundError("Journal")
        if not journal.is_active:
            raise BusinessRuleError(f"Journal {journal.code} is inactive.")

        period = await self.calendar.resolve_open_period(organization_id, data.entry_date)
        await self._load_postable_accounts(
            organization_id, [line.account_id for line in data.lines]
        )

        entry = JournalEntry(
            organization_id=organization_id,
            journal_id=journal.id,
            period_id=period.id,
            entry_date=data.entry_date,
            narration=data.narration,
            reference=data.reference,
            counterparty=data.counterparty,
            status=EntryStatus.DRAFT,
            created_by_id=actor.id,
            source_type=source_type,
            source_id=source_id,
        )
        entry.lines = [
            JournalEntryLine(
                line_number=index,
                account_id=line.account_id,
                debit=line.debit,
                credit=line.credit,
                description=line.description,
            )
            for index, line in enumerate(data.lines, start=1)
        ]
        entry.total_debit, entry.total_credit = self._totals(entry.lines)

        # Defence in depth: the schema already validated this, but a programmatic
        # caller constructs the payload directly and bypasses that layer.
        if entry.total_debit != entry.total_credit:
            raise BusinessRuleError(
                f"Entry does not balance: debits {entry.total_debit} != "
                f"credits {entry.total_credit}"
            )

        self.session.add(entry)
        await self.session.flush()

        if data.post:
            return await self.post_entry(organization_id, entry.id, actor, ctx)

        await self.audit.record(
            AuditAction.JOURNAL_ENTRY_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="journal_entry",
            resource_id=entry.id,
            summary=f"Created draft entry for {entry.total_debit} {entry.currency}",
            **_audit_ctx(ctx),
        )
        return entry

    # -- Update (drafts only) ------------------------------------------------
    async def update_entry(
        self,
        organization_id: uuid.UUID,
        entry_id: uuid.UUID,
        data: JournalEntryUpdate,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> JournalEntry:
        entry = await self._get_entry(organization_id, entry_id)

        if not entry.is_editable:
            raise BusinessRuleError(
                f"This entry is {entry.status} and cannot be edited. "
                "Post a reversing entry instead - the books are a record, not a draft.",
                details={"status": str(entry.status), "entry_number": entry.entry_number},
            )

        if data.entry_date is not None and data.entry_date != entry.entry_date:
            period = await self.calendar.resolve_open_period(organization_id, data.entry_date)
            entry.entry_date = data.entry_date
            entry.period_id = period.id

        if data.narration is not None:
            entry.narration = data.narration
        if data.reference is not None:
            entry.reference = data.reference

        if data.lines is not None:
            await self._load_postable_accounts(
                organization_id, [line.account_id for line in data.lines]
            )
            entry.lines = [
                JournalEntryLine(
                    line_number=index,
                    account_id=line.account_id,
                    debit=line.debit,
                    credit=line.credit,
                    description=line.description,
                )
                for index, line in enumerate(data.lines, start=1)
            ]
            entry.total_debit, entry.total_credit = self._totals(entry.lines)
            if entry.total_debit != entry.total_credit:
                raise BusinessRuleError("Entry does not balance")

        await self.session.flush()
        return entry

    # -- Post ----------------------------------------------------------------
    async def post_entry(
        self,
        organization_id: uuid.UUID,
        entry_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> JournalEntry:
        """Move a draft into the books.

        Assigns the statutory number here rather than at creation, so abandoned
        drafts leave no gap in the sequence.
        """
        entry = await self._get_entry(organization_id, entry_id)

        if entry.is_posted:
            raise ConflictError(
                f"Entry {entry.entry_number} is already posted",
                details={"entry_number": entry.entry_number},
            )
        if len(entry.lines) < 2:
            raise BusinessRuleError("An entry needs at least two lines")
        if not entry.is_balanced:
            raise BusinessRuleError(
                f"Entry does not balance: debits {entry.total_debit} != "
                f"credits {entry.total_credit}"
            )

        # Re-check the period: it may have closed while the draft sat unposted.
        period = await self.calendar.resolve_open_period(organization_id, entry.entry_date)
        entry.period_id = period.id

        # Re-check accounts: one may have been deactivated since the draft was saved.
        await self._load_postable_accounts(
            organization_id, [line.account_id for line in entry.lines]
        )

        journal = await self.journals.get(entry.journal_id)
        assert journal is not None  # noqa: S101 - FK guarantees it
        fiscal_year = await self.calendar.calendar.year_containing(
            organization_id, entry.entry_date
        )
        year_label = fiscal_year.name if fiscal_year else str(entry.entry_date.year)

        entry.entry_number = await self.sequences.next_number(
            organization_id,
            scope=f"journal:{journal.id}:{year_label}",
            prefix=f"{journal.number_prefix}-{year_label}-",
        )
        entry.status = EntryStatus.POSTED
        entry.posted_at = dt.datetime.now(dt.UTC)
        entry.posted_by_id = actor.id
        await self.session.flush()

        await self.audit.record(
            AuditAction.JOURNAL_ENTRY_POSTED,
            actor=actor,
            organization_id=organization_id,
            resource_type="journal_entry",
            resource_id=entry.id,
            summary=f"Posted {entry.entry_number} for {entry.total_debit} {entry.currency}",
            context={
                "entry_number": entry.entry_number,
                "entry_date": entry.entry_date.isoformat(),
                "amount": str(entry.total_debit),
            },
            **_audit_ctx(ctx),
        )
        log.info(
            "journal entry posted",
            extra={
                "entry_number": entry.entry_number,
                "amount": str(entry.total_debit),
                "organization_id": str(organization_id),
            },
        )
        return entry

    # -- Reverse -------------------------------------------------------------
    async def reverse_entry(
        self,
        organization_id: uuid.UUID,
        entry_id: uuid.UUID,
        actor: User,
        *,
        reversal_date: dt.date | None = None,
        narration: str | None = None,
        ctx: RequestContext | None = None,
    ) -> JournalEntry:
        """Post a mirror entry that cancels ``entry_id``.

        The only way to undo a posted entry. The original is left untouched and
        marked ``REVERSED``; both remain in the ledger and sum to zero, so the
        audit trail shows what happened *and* what corrected it.

        The reversal may carry a later date than the original - if the original's
        month has since closed, the correction belongs in an open one.
        """
        original = await self._get_entry(organization_id, entry_id)

        if original.status is EntryStatus.DRAFT:
            raise BusinessRuleError(
                "This entry is still a draft. Edit or delete it instead of reversing."
            )
        if original.status is EntryStatus.REVERSED:
            raise ConflictError(
                f"Entry {original.entry_number} has already been reversed",
            )

        effective_date = reversal_date or original.entry_date
        if effective_date < original.entry_date:
            raise ValidationError(
                "A reversal cannot be dated before the entry it reverses",
                details={
                    "reversal_date": effective_date.isoformat(),
                    "original_date": original.entry_date.isoformat(),
                },
            )

        period = await self.calendar.resolve_open_period(organization_id, effective_date)

        reversal = JournalEntry(
            organization_id=organization_id,
            journal_id=original.journal_id,
            period_id=period.id,
            entry_date=effective_date,
            narration=narration or f"Reversal of {original.entry_number}: {original.narration}",
            reference=original.reference,
            status=EntryStatus.DRAFT,
            created_by_id=actor.id,
            reverses_id=original.id,
            source_type=original.source_type,
            source_id=original.source_id,
        )
        # The mirror: every debit becomes a credit and vice versa.
        reversal.lines = [
            JournalEntryLine(
                line_number=line.line_number,
                account_id=line.account_id,
                debit=line.credit,
                credit=line.debit,
                description=line.description,
            )
            for line in original.lines
        ]
        reversal.total_debit, reversal.total_credit = self._totals(reversal.lines)

        self.session.add(reversal)
        await self.session.flush()

        posted = await self.post_entry(organization_id, reversal.id, actor, ctx)

        original.status = EntryStatus.REVERSED
        original.reversed_at = dt.datetime.now(dt.UTC)
        await self.session.flush()

        await self.audit.record(
            AuditAction.JOURNAL_ENTRY_REVERSED,
            actor=actor,
            organization_id=organization_id,
            resource_type="journal_entry",
            resource_id=original.id,
            summary=f"Reversed {original.entry_number} via {posted.entry_number}",
            severity=AuditSeverity.WARNING,
            context={
                "original": original.entry_number,
                "reversal": posted.entry_number,
            },
            **_audit_ctx(ctx),
        )
        return posted

    # -- Delete (drafts only) ------------------------------------------------
    async def delete_draft(
        self,
        organization_id: uuid.UUID,
        entry_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> None:
        entry = await self._get_entry(organization_id, entry_id)
        if entry.is_posted:
            raise BusinessRuleError(
                "Posted entries cannot be deleted. Reverse the entry instead.",
                details={"entry_number": entry.entry_number},
            )

        await self.entries.hard_delete(entry)
        await self.audit.record(
            AuditAction.JOURNAL_ENTRY_DELETED,
            actor=actor,
            organization_id=organization_id,
            resource_type="journal_entry",
            resource_id=entry_id,
            summary="Deleted a draft entry",
            **_audit_ctx(ctx),
        )

    # -- Helpers -------------------------------------------------------------
    async def _get_entry(self, organization_id: uuid.UUID, entry_id: uuid.UUID) -> JournalEntry:
        entry = await self.entries.get_with_lines(organization_id, entry_id)
        if entry is None:
            raise NotFoundError("Journal entry")
        return entry

    async def get_entry(self, organization_id: uuid.UUID, entry_id: uuid.UUID) -> JournalEntry:
        return await self._get_entry(organization_id, entry_id)

    # -- Programmatic posting, for later stages ------------------------------
    async def post_simple(
        self,
        organization_id: uuid.UUID,
        actor: User,
        *,
        journal_type: JournalType,
        entry_date: dt.date,
        narration: str,
        debit_key: str,
        credit_key: str,
        amount: Decimal,
        reference: str | None = None,
        source_type: str | None = None,
        source_id: uuid.UUID | None = None,
    ) -> JournalEntry:
        """Post a two-line entry between two system accounts.

        The seam Stage 3 and 4 post through: they name the *roles*
        (``accounts_receivable``, ``sales_revenue``) rather than resolving account
        ids, so the sales and inventory modules never need to know the chart's
        shape.
        """
        if amount <= 0:
            raise ValidationError("Amount must be positive")

        chart = ChartOfAccountsService(self.session)
        debit_account = await chart.resolve_system_account(organization_id, debit_key)
        credit_account = await chart.resolve_system_account(organization_id, credit_key)

        journal = await self.journals.get_by_type(organization_id, journal_type)
        if journal is None:
            raise BusinessRuleError(
                f"No {journal_type} journal is configured for this organization"
            )

        from app.modules.accounting.schemas import (
            JournalEntryCreate as Payload,
        )
        from app.modules.accounting.schemas import (
            JournalEntryLineInput as LineInput,
        )

        return await self.create_entry(
            organization_id,
            Payload(
                journal_id=journal.id,
                entry_date=entry_date,
                narration=narration,
                reference=reference,
                post=True,
                lines=[
                    LineInput(account_id=debit_account.id, debit=amount),
                    LineInput(account_id=credit_account.id, credit=amount),
                ],
            ),
            actor,
            source_type=source_type,
            source_id=source_id,
        )


async def provision_books(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    fiscal_year_start_month: int = 4,
) -> None:
    """Give an organization books it can actually write to.

    The default chart of accounts, the standard journals, and the fiscal year with its
    monthly periods. **This is setup, not convenience**: without a chart there is
    nothing to post against, and without a fiscal year every posting fails with "no
    accounting period covers this date".

    **It exists as one function because having it in two places went wrong.**
    ``POST /organizations`` seeded the books; the registration path, written before
    accounting existed, seeded only roles and was never updated. So every user who
    signed up with an organization name got books they could not write to, and the first
    thing they saw on the billing screen was "no income accounts exist yet". Two call
    sites that must stay identical will eventually not be, so now there is one.

    Idempotent on both halves - ``seed_defaults`` skips entirely if any account exists,
    and ``ensure_year_for`` returns the existing year - so it is safe to call
    defensively from anywhere that needs the books to be usable.
    """
    await ChartOfAccountsService(session).seed_defaults(organization_id)
    await FiscalCalendarService(session).ensure_year_for(
        organization_id, fiscal_year_start_month=fiscal_year_start_month
    )


__all__ = [
    "ChartOfAccountsService",
    "FiscalCalendarService",
    "JournalService",
    "PostingService",
    "SystemAccount",
    "provision_books",
]
