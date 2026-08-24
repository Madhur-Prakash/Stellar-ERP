"""Accounting core tests.

Organised around the invariants rather than the classes, because the invariants
are what actually matter: a violated one produces books that cannot be corrected.

The reports get property-style assertions - a trial balance must sum to zero, a
balance sheet must balance - checked against generated activity rather than
hand-computed expected values. Those identities have to hold for *any* set of
valid entries, so asserting them is stronger than asserting one arithmetic result.
"""

from __future__ import annotations

import datetime as dt
import itertools
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.modules.accounting.coa_template import (
    DEFAULT_CHART,
    DEFAULT_JOURNALS,
    SystemAccount,
    validate_template,
)
from app.modules.accounting.models import (
    Account,
    AccountType,
    EntryStatus,
    JournalType,
    PeriodStatus,
)
from app.modules.accounting.reports import ReportingService
from app.modules.accounting.repository import AccountRepository, JournalRepository
from app.modules.accounting.schemas import (
    JournalEntryCreate,
    JournalEntryLineInput,
    JournalEntryUpdate,
)
from app.modules.accounting.service import (
    ChartOfAccountsService,
    FiscalCalendarService,
    PostingService,
)
from app.modules.organizations.models import Organization
from app.modules.users.models import User

pytestmark = pytest.mark.integration

TODAY = dt.date.today()


# =============================================================================
# Fixtures
# =============================================================================
@pytest.fixture
async def books(db: AsyncSession, organization: Organization) -> Organization:
    """Seed the chart, journals, and the current fiscal year.

    The shared ``organization`` fixture constructs the row directly rather than
    going through ``OrganizationService.create``, so it has no books. Accounting
    tests need them, and specifically need the fiscal year - without an open
    period nothing can be posted at all.
    """
    await ChartOfAccountsService(db).seed_defaults(organization.id)
    await FiscalCalendarService(db).ensure_year_for(organization.id, fiscal_year_start_month=4)
    await db.flush()
    return organization


@pytest.fixture
async def accounts(db: AsyncSession, books: Organization) -> dict[str, Account]:
    """The seeded accounts, keyed by their system key."""
    repo = AccountRepository(db)
    keys = (
        SystemAccount.CASH,
        SystemAccount.BANK,
        SystemAccount.ACCOUNTS_RECEIVABLE,
        SystemAccount.ACCOUNTS_PAYABLE,
        SystemAccount.SALES_REVENUE,
        SystemAccount.COST_OF_GOODS_SOLD,
        SystemAccount.INVENTORY,
        SystemAccount.OWNER_CAPITAL,
    )
    resolved: dict[str, Account] = {}
    for key in keys:
        account = await repo.get_by_system_key(books.id, key)
        assert account is not None, f"system account {key} was not seeded"
        resolved[key] = account
    return resolved


@pytest.fixture
async def general_journal(db: AsyncSession, books: Organization) -> uuid.UUID:
    journal = await JournalRepository(db).get_by_type(books.id, JournalType.GENERAL)
    assert journal is not None
    return journal.id


@pytest.fixture
def posting(db: AsyncSession) -> PostingService:
    return PostingService(db)


@pytest.fixture
def reporting(db: AsyncSession) -> ReportingService:
    return ReportingService(db)


def entry_payload(
    journal_id: uuid.UUID,
    debit_account: uuid.UUID,
    credit_account: uuid.UUID,
    amount: str,
    *,
    entry_date: dt.date | None = None,
    narration: str = "Test entry",
    post: bool = True,
) -> JournalEntryCreate:
    return JournalEntryCreate(
        journal_id=journal_id,
        entry_date=entry_date or TODAY,
        narration=narration,
        post=post,
        lines=[
            JournalEntryLineInput(account_id=debit_account, debit=Decimal(amount)),
            JournalEntryLineInput(account_id=credit_account, credit=Decimal(amount)),
        ],
    )


# =============================================================================
# The chart of accounts template
# =============================================================================
class TestChartTemplate:
    def test_template_is_internally_consistent(self) -> None:
        """Parents defined first, no duplicate codes, no orphan system keys."""
        validate_template()

    def test_every_account_type_is_represented(self) -> None:
        types = {spec.account_type for spec in DEFAULT_CHART}
        assert types == set(AccountType), f"missing: {set(AccountType) - types}"

    def test_group_accounts_are_never_postable(self) -> None:
        assert all(spec.system_key is None for spec in DEFAULT_CHART if spec.is_group)

    async def test_seeding_creates_the_full_chart(
        self, db: AsyncSession, books: Organization
    ) -> None:
        accounts = await AccountRepository(db).list_for_org(books.id, include_inactive=True)
        assert len(accounts) == len(DEFAULT_CHART)

        journals = await JournalRepository(db).list_for_org(books.id)
        assert len(journals) == len(DEFAULT_JOURNALS)

    async def test_seeding_is_idempotent(self, db: AsyncSession, books: Organization) -> None:
        """A second seed must not duplicate the chart.

        Organization creation seeds; a retried or replayed request must not double
        it, and unique account codes would make a partial re-seed fail loudly
        mid-transaction.
        """
        created, journals = await ChartOfAccountsService(db).seed_defaults(books.id)
        assert (created, journals) == (0, 0)

        accounts = await AccountRepository(db).list_for_org(books.id, include_inactive=True)
        assert len(accounts) == len(DEFAULT_CHART)

    async def test_depth_reflects_hierarchy(self, db: AsyncSession, books: Organization) -> None:
        repo = AccountRepository(db)
        root = await repo.get_by_code(books.id, "1000")
        current = await repo.get_by_code(books.id, "1100")
        cash = await repo.get_by_code(books.id, "1110")
        assert root and current and cash
        assert (root.depth, current.depth, cash.depth) == (0, 1, 2)
        assert root.is_group and current.is_group and not cash.is_group

    async def test_normal_balance_follows_account_type(self, accounts: dict[str, Account]) -> None:
        """Assets/expenses are debit-normal; the rest are credit-normal."""
        assert accounts[SystemAccount.CASH].normal_balance == "debit"
        assert accounts[SystemAccount.COST_OF_GOODS_SOLD].normal_balance == "debit"
        assert accounts[SystemAccount.SALES_REVENUE].normal_balance == "credit"
        assert accounts[SystemAccount.ACCOUNTS_PAYABLE].normal_balance == "credit"
        assert accounts[SystemAccount.OWNER_CAPITAL].normal_balance == "credit"


# =============================================================================
# Invariant 1 - every entry balances
# =============================================================================
class TestBalanceInvariant:
    async def test_balanced_entry_posts(
        self,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        entry = await posting.create_entry(
            books.id,
            entry_payload(
                general_journal,
                accounts[SystemAccount.CASH].id,
                accounts[SystemAccount.OWNER_CAPITAL].id,
                "100000.00",
            ),
            user,
        )
        assert entry.status is EntryStatus.POSTED
        assert entry.total_debit == entry.total_credit == Decimal("100000.0000")
        assert entry.entry_number is not None
        assert entry.posted_at is not None

    def test_unbalanced_payload_rejected_by_schema(
        self, accounts: dict[str, Account], general_journal: uuid.UUID
    ) -> None:
        """Caught at the edge, with the difference named in the message."""
        with pytest.raises(ValueError, match="does not balance"):
            JournalEntryCreate(
                journal_id=general_journal,
                entry_date=TODAY,
                narration="Lopsided",
                lines=[
                    JournalEntryLineInput(
                        account_id=accounts[SystemAccount.CASH].id, debit=Decimal("100")
                    ),
                    JournalEntryLineInput(
                        account_id=accounts[SystemAccount.SALES_REVENUE].id,
                        credit=Decimal("90"),
                    ),
                ],
            )

    def test_zero_total_rejected(
        self, accounts: dict[str, Account], general_journal: uuid.UUID
    ) -> None:
        """Balanced at zero is still meaningless."""
        with pytest.raises(ValueError, match=r"cannot be zero|must have either"):
            JournalEntryCreate(
                journal_id=general_journal,
                entry_date=TODAY,
                narration="Nothing",
                lines=[
                    JournalEntryLineInput(
                        account_id=accounts[SystemAccount.CASH].id, debit=Decimal("0")
                    ),
                    JournalEntryLineInput(
                        account_id=accounts[SystemAccount.SALES_REVENUE].id,
                        credit=Decimal("0"),
                    ),
                ],
            )

    def test_line_cannot_be_both_debit_and_credit(self, accounts: dict[str, Account]) -> None:
        with pytest.raises(ValueError, match="both a debit and a credit"):
            JournalEntryLineInput(
                account_id=accounts[SystemAccount.CASH].id,
                debit=Decimal("10"),
                credit=Decimal("10"),
            )

    def test_line_must_have_one_side(self, accounts: dict[str, Account]) -> None:
        with pytest.raises(ValueError, match="must have either"):
            JournalEntryLineInput(account_id=accounts[SystemAccount.CASH].id)

    def test_single_account_entry_rejected(
        self, accounts: dict[str, Account], general_journal: uuid.UUID
    ) -> None:
        """Debiting and crediting one account nets to nothing."""
        cash = accounts[SystemAccount.CASH].id
        with pytest.raises(ValueError, match="two different accounts"):
            JournalEntryCreate(
                journal_id=general_journal,
                entry_date=TODAY,
                narration="Circular",
                lines=[
                    JournalEntryLineInput(account_id=cash, debit=Decimal("50")),
                    JournalEntryLineInput(account_id=cash, credit=Decimal("50")),
                ],
            )

    async def test_database_rejects_unbalanced_entry(
        self, db: AsyncSession, books: Organization, general_journal: uuid.UUID
    ) -> None:
        """The CHECK constraint is real, not just an application rule.

        Proves defence in depth: even a direct SQL insert - a bad data migration,
        a manual fix in psql - cannot create an unbalanced entry.
        """
        period = await FiscalCalendarService(db).resolve_open_period(books.id, TODAY)

        with pytest.raises(IntegrityError, match="balanced"):
            async with db.begin_nested():  # savepoint, so the session survives
                await db.execute(
                    text(
                        "INSERT INTO journal_entry "
                        "(id, organization_id, journal_id, period_id, entry_date, "
                        " narration, status, total_debit, total_credit, currency, "
                        " created_at, updated_at) "
                        "VALUES (gen_random_uuid(), :org, :journal, :period, :date, "
                        " 'Forced imbalance', 'draft', 100, 90, 'INR', now(), now())"
                    ),
                    {
                        "org": books.id,
                        "journal": general_journal,
                        "period": period.id,
                        "date": TODAY,
                    },
                )


# =============================================================================
# Invariant 2 - only postable accounts receive entries
# =============================================================================
class TestPostableAccounts:
    async def test_group_account_cannot_receive_postings(
        self,
        db: AsyncSession,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        group = await AccountRepository(db).get_by_code(books.id, "1000")
        assert group is not None and group.is_group

        with pytest.raises(BusinessRuleError, match="group account"):
            await posting.create_entry(
                books.id,
                entry_payload(
                    general_journal,
                    group.id,
                    accounts[SystemAccount.OWNER_CAPITAL].id,
                    "500",
                ),
                user,
            )

    async def test_inactive_account_cannot_receive_postings(
        self,
        db: AsyncSession,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        target = await AccountRepository(db).get_by_code(books.id, "5210")  # Rent
        assert target is not None
        target.is_active = False
        await db.flush()

        with pytest.raises(BusinessRuleError, match="inactive"):
            await posting.create_entry(
                books.id,
                entry_payload(general_journal, target.id, accounts[SystemAccount.CASH].id, "500"),
                user,
            )

    async def test_another_organizations_account_is_rejected(
        self,
        db: AsyncSession,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        """Cross-tenant posting must be impossible.

        The single most damaging bug a multi-tenant ledger can have.
        """
        other = Organization(name="Rival Co", slug=f"rival-{uuid.uuid4().hex[:6]}")
        db.add(other)
        await db.flush()
        await ChartOfAccountsService(db).seed_defaults(other.id)
        await db.flush()

        foreign = await AccountRepository(db).get_by_system_key(other.id, SystemAccount.CASH)
        assert foreign is not None

        with pytest.raises(ValidationError, match="does not exist"):
            await posting.create_entry(
                books.id,
                entry_payload(
                    general_journal,
                    foreign.id,
                    accounts[SystemAccount.OWNER_CAPITAL].id,
                    "500",
                ),
                user,
            )


# =============================================================================
# Invariant 3 - posted entries are immutable
# =============================================================================
class TestImmutability:
    async def test_posted_entry_cannot_be_edited(
        self,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        entry = await posting.create_entry(
            books.id,
            entry_payload(
                general_journal,
                accounts[SystemAccount.CASH].id,
                accounts[SystemAccount.SALES_REVENUE].id,
                "1000",
            ),
            user,
        )
        with pytest.raises(BusinessRuleError, match="reversing entry"):
            await posting.update_entry(
                books.id,
                entry.id,
                JournalEntryUpdate(narration="Rewriting history"),
                user,
            )

    async def test_posted_entry_cannot_be_deleted(
        self,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        entry = await posting.create_entry(
            books.id,
            entry_payload(
                general_journal,
                accounts[SystemAccount.CASH].id,
                accounts[SystemAccount.SALES_REVENUE].id,
                "1000",
            ),
            user,
        )
        with pytest.raises(BusinessRuleError, match="Reverse the entry"):
            await posting.delete_draft(books.id, entry.id, user)

    async def test_draft_can_be_edited_and_deleted(
        self,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        draft = await posting.create_entry(
            books.id,
            entry_payload(
                general_journal,
                accounts[SystemAccount.CASH].id,
                accounts[SystemAccount.SALES_REVENUE].id,
                "1000",
                post=False,
            ),
            user,
        )
        assert draft.status is EntryStatus.DRAFT
        # A draft has consumed no number - that is the point of deferring them.
        assert draft.entry_number is None

        updated = await posting.update_entry(
            books.id, draft.id, JournalEntryUpdate(narration="Corrected"), user
        )
        assert updated.narration == "Corrected"

        await posting.delete_draft(books.id, draft.id, user)
        with pytest.raises(NotFoundError):
            await posting.get_entry(books.id, draft.id)


# =============================================================================
# Invariant 4 - closed periods reject postings
# =============================================================================
class TestPeriodControl:
    async def test_posting_into_closed_period_is_refused(
        self,
        db: AsyncSession,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        calendar = FiscalCalendarService(db)
        period = await calendar.resolve_open_period(books.id, TODAY)
        period.status = PeriodStatus.CLOSED
        await db.flush()

        with pytest.raises(BusinessRuleError, match="cannot accept new entries"):
            await posting.create_entry(
                books.id,
                entry_payload(
                    general_journal,
                    accounts[SystemAccount.CASH].id,
                    accounts[SystemAccount.SALES_REVENUE].id,
                    "100",
                ),
                user,
            )

    async def test_date_outside_any_period_is_refused(
        self,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        with pytest.raises(BusinessRuleError, match="No accounting period"):
            await posting.create_entry(
                books.id,
                entry_payload(
                    general_journal,
                    accounts[SystemAccount.CASH].id,
                    accounts[SystemAccount.SALES_REVENUE].id,
                    "100",
                    entry_date=dt.date(1990, 1, 1),
                ),
                user,
            )

    async def test_periods_must_close_in_order(
        self, db: AsyncSession, books: Organization, user: User
    ) -> None:
        """Closing March while February is open makes comparatives unreconcilable."""
        calendar = FiscalCalendarService(db)
        years = await calendar.list_years(books.id)
        periods = sorted(years[0].periods, key=lambda p: p.start_date)
        assert len(periods) >= 3

        with pytest.raises(BusinessRuleError, match="earlier period is still open"):
            await calendar.close_period(books.id, periods[2].id, user)

    async def test_locked_period_cannot_be_reopened(
        self, db: AsyncSession, books: Organization, user: User
    ) -> None:
        calendar = FiscalCalendarService(db)
        years = await calendar.list_years(books.id)
        first = sorted(years[0].periods, key=lambda p: p.start_date)[0]

        await calendar.close_period(books.id, first.id, user, lock=True)
        with pytest.raises(BusinessRuleError, match="locked"):
            await calendar.reopen_period(books.id, first.id, user)

    async def test_fiscal_year_generates_monthly_periods(
        self, db: AsyncSession, books: Organization
    ) -> None:
        """Twelve contiguous months with no gap and no overlap.

        A gap means a date resolves to no period and cannot be posted; an overlap
        means it resolves to two and its ledger placement is ambiguous.
        """
        years = await FiscalCalendarService(db).list_years(books.id)
        assert len(years) == 1
        periods = sorted(years[0].periods, key=lambda p: p.start_date)
        assert len(periods) == 12

        assert periods[0].start_date == years[0].start_date
        assert periods[-1].end_date == years[0].end_date
        for earlier, later in itertools.pairwise(periods):
            assert later.start_date == earlier.end_date + dt.timedelta(days=1)


# =============================================================================
# Numbering
# =============================================================================
class TestEntryNumbering:
    async def test_numbers_are_sequential_and_gap_free(
        self,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        numbers = []
        for index in range(5):
            entry = await posting.create_entry(
                books.id,
                entry_payload(
                    general_journal,
                    accounts[SystemAccount.CASH].id,
                    accounts[SystemAccount.SALES_REVENUE].id,
                    "100",
                    narration=f"Sale {index}",
                ),
                user,
            )
            numbers.append(entry.entry_number)

        suffixes = [int(str(n).rsplit("-", 1)[-1]) for n in numbers]
        assert suffixes == list(range(1, 6)), numbers

    async def test_number_includes_journal_prefix_and_year(
        self,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        entry = await posting.create_entry(
            books.id,
            entry_payload(
                general_journal,
                accounts[SystemAccount.CASH].id,
                accounts[SystemAccount.SALES_REVENUE].id,
                "100",
            ),
            user,
        )
        assert entry.entry_number is not None
        assert entry.entry_number.startswith("JV-")


# =============================================================================
# Reversal
# =============================================================================
class TestReversal:
    async def test_reversal_mirrors_and_nets_to_zero(
        self,
        posting: PostingService,
        reporting: ReportingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        """The reversed pair must leave every balance exactly as it was.

        This is what makes including REVERSED entries in balance queries
        load-bearing - excluding them would leave the mirror uncancelled.
        """
        cash = accounts[SystemAccount.CASH]
        revenue = accounts[SystemAccount.SALES_REVENUE]

        original = await posting.create_entry(
            books.id,
            entry_payload(general_journal, cash.id, revenue.id, "7500"),
            user,
        )
        before = await reporting.accounts.balance_for(cash.id, to_date=TODAY)
        assert before.total_debit == Decimal("7500.0000")

        reversal = await posting.reverse_entry(books.id, original.id, user)

        assert reversal.status is EntryStatus.POSTED
        assert reversal.reverses_id == original.id
        # Debits and credits swapped.
        assert reversal.total_debit == original.total_credit
        for line in reversal.lines:
            assert (line.debit > 0) != (line.credit > 0)

        refreshed = await posting.get_entry(books.id, original.id)
        assert refreshed.status is EntryStatus.REVERSED
        assert refreshed.reversed_at is not None

        after = await reporting.accounts.balance_for(cash.id, to_date=TODAY)
        assert after.total_debit - after.total_credit == Decimal("0")

    async def test_cannot_reverse_twice(
        self,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        entry = await posting.create_entry(
            books.id,
            entry_payload(
                general_journal,
                accounts[SystemAccount.CASH].id,
                accounts[SystemAccount.SALES_REVENUE].id,
                "100",
            ),
            user,
        )
        await posting.reverse_entry(books.id, entry.id, user)
        with pytest.raises(ConflictError, match="already been reversed"):
            await posting.reverse_entry(books.id, entry.id, user)

    async def test_cannot_reverse_a_draft(
        self,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        draft = await posting.create_entry(
            books.id,
            entry_payload(
                general_journal,
                accounts[SystemAccount.CASH].id,
                accounts[SystemAccount.SALES_REVENUE].id,
                "100",
                post=False,
            ),
            user,
        )
        with pytest.raises(BusinessRuleError, match="still a draft"):
            await posting.reverse_entry(books.id, draft.id, user)

    async def test_reversal_cannot_predate_the_original(
        self,
        posting: PostingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        entry = await posting.create_entry(
            books.id,
            entry_payload(
                general_journal,
                accounts[SystemAccount.CASH].id,
                accounts[SystemAccount.SALES_REVENUE].id,
                "100",
            ),
            user,
        )
        with pytest.raises(ValidationError, match="cannot be dated before"):
            await posting.reverse_entry(
                books.id,
                entry.id,
                user,
                reversal_date=entry.entry_date - dt.timedelta(days=1),
            )


# =============================================================================
# Reports
# =============================================================================
@pytest.fixture
async def activity(
    posting: PostingService,
    books: Organization,
    user: User,
    accounts: dict[str, Account],
    general_journal: uuid.UUID,
) -> Organization:
    """A small but realistic set of posted transactions.

    Capital in, a cash sale, a credit sale, inventory bought on credit, and cost
    of sales recognised - enough to exercise every report path.
    """
    moves = [
        (SystemAccount.CASH, SystemAccount.OWNER_CAPITAL, "500000"),
        (SystemAccount.CASH, SystemAccount.SALES_REVENUE, "120000"),
        (SystemAccount.ACCOUNTS_RECEIVABLE, SystemAccount.SALES_REVENUE, "80000"),
        (SystemAccount.INVENTORY, SystemAccount.ACCOUNTS_PAYABLE, "150000"),
        (SystemAccount.COST_OF_GOODS_SOLD, SystemAccount.INVENTORY, "90000"),
    ]
    for debit_key, credit_key, amount in moves:
        await posting.create_entry(
            books.id,
            entry_payload(
                general_journal,
                accounts[debit_key].id,
                accounts[credit_key].id,
                amount,
                narration=f"{debit_key} / {credit_key}",
            ),
            user,
        )
    return books


class TestTrialBalance:
    async def test_trial_balance_balances(
        self, reporting: ReportingService, activity: Organization
    ) -> None:
        """Total debits equal total credits. If this fails, nothing else matters."""
        tb = await reporting.trial_balance(activity.id, as_of=TODAY)
        assert tb.is_balanced
        assert tb.total_debit == tb.total_credit
        assert tb.total_debit > 0

    async def test_rows_carry_net_balance_on_one_side(
        self, reporting: ReportingService, activity: Organization
    ) -> None:
        """A row shows its net on the natural side, never gross on both."""
        tb = await reporting.trial_balance(activity.id, as_of=TODAY)
        for row in tb.rows:
            assert not (row.debit > 0 and row.credit > 0), row

    async def test_empty_books_balance_trivially(
        self, reporting: ReportingService, books: Organization
    ) -> None:
        tb = await reporting.trial_balance(books.id, as_of=TODAY)
        assert tb.is_balanced
        assert tb.total_debit == tb.total_credit == Decimal("0")


class TestProfitAndLoss:
    async def test_net_profit_is_income_minus_expenses(
        self, reporting: ReportingService, activity: Organization
    ) -> None:
        pl = await reporting.profit_and_loss(
            activity.id, from_date=TODAY - dt.timedelta(days=1), to_date=TODAY
        )
        # Revenue 120000 + 80000; COGS 90000.
        assert pl.total_income == Decimal("200000.0000")
        assert pl.cost_of_goods_sold == Decimal("90000.0000")
        assert pl.total_expenses == Decimal("90000.0000")
        assert pl.net_profit == pl.total_income - pl.total_expenses
        assert pl.gross_profit == pl.total_income - pl.cost_of_goods_sold

    async def test_capital_contribution_is_not_income(
        self, reporting: ReportingService, activity: Organization
    ) -> None:
        """Owner's capital is equity, not revenue - a classic misclassification."""
        pl = await reporting.profit_and_loss(
            activity.id, from_date=TODAY - dt.timedelta(days=1), to_date=TODAY
        )
        assert pl.total_income == Decimal("200000.0000")  # not 700000

    async def test_rejects_inverted_range(
        self, reporting: ReportingService, books: Organization
    ) -> None:
        with pytest.raises(ValidationError):
            await reporting.profit_and_loss(
                books.id, from_date=TODAY, to_date=TODAY - dt.timedelta(days=5)
            )


class TestBalanceSheet:
    async def test_balance_sheet_balances(
        self, reporting: ReportingService, activity: Organization
    ) -> None:
        """Assets == liabilities + equity, including current-period earnings.

        Omitting current-year profit from equity is the classic reason a
        hand-rolled balance sheet fails to balance.
        """
        bs = await reporting.balance_sheet(activity.id, as_of=TODAY)
        assert bs.is_balanced, (
            f"assets {bs.total_assets} != liabilities {bs.total_liabilities} "
            f"+ equity {bs.total_equity}"
        )
        assert bs.total_assets == bs.total_liabilities + bs.total_equity

    async def test_current_period_earnings_matches_profit_and_loss(
        self, reporting: ReportingService, activity: Organization
    ) -> None:
        """The two statements must agree - they read the same ledger."""
        bs = await reporting.balance_sheet(activity.id, as_of=TODAY)
        years = await reporting.calendar.list_years(activity.id)
        pl = await reporting.profit_and_loss(
            activity.id, from_date=years[0].start_date, to_date=TODAY
        )
        assert bs.current_period_earnings == pl.net_profit

    async def test_empty_books_balance(
        self, reporting: ReportingService, books: Organization
    ) -> None:
        bs = await reporting.balance_sheet(books.id, as_of=TODAY)
        assert bs.is_balanced


class TestLedger:
    async def test_running_balance_ends_at_closing_balance(
        self, reporting: ReportingService, activity: Organization, accounts: dict[str, Account]
    ) -> None:
        ledger = await reporting.account_ledger(
            activity.id,
            accounts[SystemAccount.CASH].id,
            from_date=TODAY - dt.timedelta(days=7),
            to_date=TODAY,
        )
        assert ledger.lines
        assert ledger.lines[-1].running_balance == ledger.closing_balance
        # 500000 capital + 120000 cash sale.
        assert ledger.closing_balance == Decimal("620000.0000")

    async def test_opening_balance_excludes_the_window(
        self, reporting: ReportingService, activity: Organization, accounts: dict[str, Account]
    ) -> None:
        ledger = await reporting.account_ledger(
            activity.id,
            accounts[SystemAccount.CASH].id,
            from_date=TODAY,
            to_date=TODAY,
        )
        assert ledger.opening_balance == Decimal("0")

    async def test_rejects_inverted_range(
        self, reporting: ReportingService, books: Organization, accounts: dict[str, Account]
    ) -> None:
        with pytest.raises(ValidationError):
            await reporting.account_ledger(
                books.id,
                accounts[SystemAccount.CASH].id,
                from_date=TODAY,
                to_date=TODAY - dt.timedelta(days=1),
            )


class TestCashFlow:
    async def test_cash_flow_reconciles(
        self, reporting: ReportingService, activity: Organization
    ) -> None:
        """opening + inflows - outflows == closing."""
        cf = await reporting.cash_flow(
            activity.id, from_date=TODAY - dt.timedelta(days=7), to_date=TODAY
        )
        assert cf.reconciles, (
            f"opening {cf.opening_cash} + in {cf.total_inflows} "
            f"- out {cf.total_outflows} != closing {cf.closing_cash}"
        )
        assert cf.closing_cash == Decimal("620000.0000")
        assert cf.net_change == cf.closing_cash - cf.opening_cash

    async def test_non_cash_transactions_are_excluded(
        self, reporting: ReportingService, activity: Organization
    ) -> None:
        """The credit sale and the inventory purchase moved no cash."""
        cf = await reporting.cash_flow(
            activity.id, from_date=TODAY - dt.timedelta(days=7), to_date=TODAY
        )
        # Only capital (500000) and the cash sale (120000) touched cash.
        assert cf.total_inflows == Decimal("620000.0000")
        assert cf.total_outflows == Decimal("0")


# =============================================================================
# Programmatic posting - the seam later stages use
# =============================================================================
class TestProgrammaticPosting:
    async def test_post_simple_resolves_accounts_by_role(
        self, posting: PostingService, books: Organization, user: User
    ) -> None:
        """Stage 3+ post by naming roles, not account ids."""
        entry = await posting.post_simple(
            books.id,
            user,
            journal_type=JournalType.SALES,
            entry_date=TODAY,
            narration="Invoice INV-001",
            debit_key=SystemAccount.ACCOUNTS_RECEIVABLE,
            credit_key=SystemAccount.SALES_REVENUE,
            amount=Decimal("25000"),
            source_type="invoice",
            source_id=uuid.uuid4(),
        )
        assert entry.status is EntryStatus.POSTED
        assert entry.entry_number is not None
        assert entry.entry_number.startswith("SI-")  # sales journal prefix
        assert entry.source_type == "invoice"
        assert entry.total_debit == Decimal("25000.0000")

    async def test_unknown_system_key_is_a_clear_error(
        self, posting: PostingService, books: Organization, user: User
    ) -> None:
        with pytest.raises(BusinessRuleError, match="No account is configured"):
            await posting.post_simple(
                books.id,
                user,
                journal_type=JournalType.SALES,
                entry_date=TODAY,
                narration="Bad mapping",
                debit_key="nonexistent_role",
                credit_key=SystemAccount.SALES_REVENUE,
                amount=Decimal("100"),
            )

    async def test_negative_amount_rejected(
        self, posting: PostingService, books: Organization, user: User
    ) -> None:
        with pytest.raises(ValidationError, match="must be positive"):
            await posting.post_simple(
                books.id,
                user,
                journal_type=JournalType.SALES,
                entry_date=TODAY,
                narration="Negative",
                debit_key=SystemAccount.ACCOUNTS_RECEIVABLE,
                credit_key=SystemAccount.SALES_REVENUE,
                amount=Decimal("-100"),
            )


# =============================================================================
# Money precision
# =============================================================================
class TestMoneyPrecision:
    async def test_amounts_survive_the_round_trip_exactly(
        self,
        posting: PostingService,
        reporting: ReportingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        """The float-error case: 0.1 + 0.2 must be exactly 0.3 in the ledger."""
        for amount in ("0.1000", "0.2000"):
            await posting.create_entry(
                books.id,
                entry_payload(
                    general_journal,
                    accounts[SystemAccount.CASH].id,
                    accounts[SystemAccount.SALES_REVENUE].id,
                    amount,
                ),
                user,
            )
        balance = await reporting.accounts.balance_for(
            accounts[SystemAccount.CASH].id, to_date=TODAY
        )
        assert balance.total_debit == Decimal("0.3000")
        assert balance.total_debit != Decimal("0.30000000000000004")

    async def test_trial_balance_stays_exact_over_many_entries(
        self,
        posting: PostingService,
        reporting: ReportingService,
        books: Organization,
        user: User,
        accounts: dict[str, Account],
        general_journal: uuid.UUID,
    ) -> None:
        """Accumulated float error would show up here as a non-zero difference."""
        for _ in range(30):
            await posting.create_entry(
                books.id,
                entry_payload(
                    general_journal,
                    accounts[SystemAccount.CASH].id,
                    accounts[SystemAccount.SALES_REVENUE].id,
                    "33.3333",
                ),
                user,
            )
        tb = await reporting.trial_balance(books.id, as_of=TODAY)
        assert tb.is_balanced
        assert tb.total_debit == Decimal("999.9990")
