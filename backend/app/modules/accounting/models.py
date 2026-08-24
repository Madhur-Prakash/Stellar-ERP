"""Accounting core - the double-entry ledger.

This is the module every later stage writes into: Stage 3 invoices post to the
sales journal, Stage 4 goods receipts post inventory and COGS, Stage 5 OCR
produces draft entries, Stage 8 reports read the ledger. Getting it wrong here is
not recoverable later, so the invariants are enforced at three levels - Python
service, database ``CHECK`` constraint, and test - rather than trusted to
application code alone.

**The invariants.**

1. *Every entry balances.* ``sum(debit) == sum(credit)``, enforced by a
   ``CHECK`` on the stored totals, so even a raw SQL insert cannot create an
   unbalanced entry.
2. *A line is debit or credit, never both.* Enforced by ``CHECK``.
3. *Posted entries are immutable.* Correcting a posted entry means posting a
   reversal, not editing history. There is no update path.
4. *Nothing posts into a closed period.* Otherwise last quarter's filed numbers
   change after the fact.
5. *Only leaf accounts receive postings.* A parent account's balance is the sum
   of its children; letting it hold its own postings makes that sum ambiguous.

**Why debit/credit columns rather than one signed amount.** A signed column is
more compact, but it forces every reader to remember the sign convention per
account type, and it makes "total debits" - the number an accountant reconciles
against - a conditional aggregate instead of a plain ``SUM``. The two-column form
matches how the domain is actually taught and audited.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, OrgScopedMixin, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import ZERO, CurrencyCode, LedgerDate, Money, enum_column

if TYPE_CHECKING:
    from app.modules.organizations.models import Organization
    from app.modules.users.models import User


# =============================================================================
# Enumerations
# =============================================================================
class AccountType(StrEnum):
    """The five fundamental account types.

    These are not a taxonomy choice - they are the accounting equation itself:
    ``Assets = Liabilities + Equity + (Income - Expenses)``. Everything else in
    this module derives from which side of that equation an account sits on.
    """

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"

    @property
    def normal_balance(self) -> BalanceSide:
        """The side on which this type normally carries a positive balance.

        Assets and expenses increase with debits; liabilities, equity, and income
        increase with credits. This single property drives balance signing across
        every report, so the convention is stated once.
        """
        if self in (AccountType.ASSET, AccountType.EXPENSE):
            return BalanceSide.DEBIT
        return BalanceSide.CREDIT

    @property
    def is_balance_sheet(self) -> bool:
        """Balance-sheet accounts carry forward; P&L accounts reset each year."""
        return self in (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY)

    @property
    def is_profit_and_loss(self) -> bool:
        return self in (AccountType.INCOME, AccountType.EXPENSE)


class BalanceSide(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class AccountSubtype(StrEnum):
    """Report-line grouping within a type.

    A balance sheet does not list "assets" flat - it separates current from
    fixed, and orders them by liquidity. The subtype is what lets the report
    builder do that without hard-coding account codes.
    """

    # Assets
    CASH = "cash"
    BANK = "bank"
    ACCOUNTS_RECEIVABLE = "accounts_receivable"
    INVENTORY = "inventory"
    OTHER_CURRENT_ASSET = "other_current_asset"
    FIXED_ASSET = "fixed_asset"
    ACCUMULATED_DEPRECIATION = "accumulated_depreciation"
    OTHER_ASSET = "other_asset"

    # Liabilities
    ACCOUNTS_PAYABLE = "accounts_payable"
    TAX_PAYABLE = "tax_payable"
    OTHER_CURRENT_LIABILITY = "other_current_liability"
    LONG_TERM_LIABILITY = "long_term_liability"

    # Equity
    CAPITAL = "capital"
    DRAWINGS = "drawings"
    RETAINED_EARNINGS = "retained_earnings"

    # Income
    OPERATING_REVENUE = "operating_revenue"
    OTHER_INCOME = "other_income"

    # Expenses
    COST_OF_GOODS_SOLD = "cost_of_goods_sold"
    OPERATING_EXPENSE = "operating_expense"
    PAYROLL_EXPENSE = "payroll_expense"
    DEPRECIATION_EXPENSE = "depreciation_expense"
    TAX_EXPENSE = "tax_expense"
    OTHER_EXPENSE = "other_expense"

    @property
    def is_current_asset(self) -> bool:
        return self in (
            AccountSubtype.CASH,
            AccountSubtype.BANK,
            AccountSubtype.ACCOUNTS_RECEIVABLE,
            AccountSubtype.INVENTORY,
            AccountSubtype.OTHER_CURRENT_ASSET,
        )

    @property
    def is_current_liability(self) -> bool:
        return self in (
            AccountSubtype.ACCOUNTS_PAYABLE,
            AccountSubtype.TAX_PAYABLE,
            AccountSubtype.OTHER_CURRENT_LIABILITY,
        )

    @property
    def is_cash_equivalent(self) -> bool:
        """Drives the cash flow statement's definition of "cash"."""
        return self in (AccountSubtype.CASH, AccountSubtype.BANK)


class JournalType(StrEnum):
    """Which book an entry belongs to.

    Separating the books by source is what makes "show me all sales postings"
    answerable without inspecting every line, and gives later stages a
    deterministic target: an invoice always posts to the sales journal.
    """

    GENERAL = "general"
    SALES = "sales"
    PURCHASE = "purchase"
    CASH = "cash"
    BANK = "bank"
    OPENING = "opening"


class EntryStatus(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"
    #: A posted entry that has been reversed. Its lines still exist and still
    #: affect no balance, because the reversal cancels them out.
    REVERSED = "reversed"


class PeriodStatus(StrEnum):
    OPEN = "open"
    #: Soft close: no new postings, but an administrator can reopen.
    CLOSED = "closed"
    #: Hard close, after filing. Reopening requires a deliberate override.
    LOCKED = "locked"


# =============================================================================
# Chart of accounts
# =============================================================================
class Account(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, SoftDeleteMixin):
    """A single line in the chart of accounts.

    Accounts form a tree: ``1000 Assets`` → ``1100 Current Assets`` →
    ``1110 Cash``. Only leaves are postable; a parent's balance is the sum of its
    subtree. Soft-deleted rather than deleted, because an account referenced by a
    posted entry can never truly go away without breaking the audit trail.
    """

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    account_type: Mapped[AccountType] = mapped_column(
        enum_column(AccountType, length=20),
        nullable=False,
        index=True,
    )
    subtype: Mapped[AccountSubtype] = mapped_column(
        enum_column(AccountSubtype, length=40),
        nullable=False,
    )

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="RESTRICT"), index=True
    )
    #: True when this account has children. Group accounts are headers: they
    #: appear on reports but cannot receive postings.
    is_group: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Depth in the tree, 0 for roots. Denormalised purely for cheap indented
    #: rendering of the chart without a recursive query per row.
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Seeded by the default chart. Protected from deletion because later stages
    #: resolve them by role (e.g. "the receivables account") rather than by id.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: Stable identifier for the *default* account of a given role, e.g.
    #: ``accounts_receivable``. An organization may have many receivable accounts;
    #: exactly one carries the key, and that is the one Stage 3 posts an invoice
    #: to without having to guess from the subtype. Unique per organization.
    system_key: Mapped[str | None] = mapped_column(String(50))

    #: Bank/cash accounts get reconciliation workflows in a later stage.
    is_reconcilable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    currency: Mapped[CurrencyCode | None] = mapped_column(default=None)

    # --- Relationships ---
    organization: Mapped[Organization] = relationship()
    parent: Mapped[Account | None] = relationship(
        remote_side="Account.id", back_populates="children"
    )
    children: Mapped[list[Account]] = relationship(back_populates="parent")

    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_account_org_code"),
        Index("ix_account_org_type", "organization_id", "account_type"),
        Index(
            "ix_account_postable",
            "organization_id",
            postgresql_where=text("is_group IS FALSE AND is_active IS TRUE AND deleted_at IS NULL"),
        ),
        # At most one account per organization may claim a given system role.
        # Partial, so the many accounts with no key do not collide on NULL.
        Index(
            "uq_account_org_system_key",
            "organization_id",
            "system_key",
            unique=True,
            postgresql_where=text("system_key IS NOT NULL"),
        ),
    )

    @property
    def is_postable(self) -> bool:
        """Whether a journal line may reference this account."""
        return not self.is_group and self.is_active and not self.is_deleted

    @property
    def normal_balance(self) -> BalanceSide:
        return self.account_type.normal_balance

    @property
    def label(self) -> str:
        return f"{self.code} - {self.name}"

    def signed_balance(self, total_debit: Decimal, total_credit: Decimal) -> Decimal:
        """Convert raw debit/credit totals into a balance in the account's own terms.

        A positive result always means "more of what this account is for": more
        cash in an asset, more owed on a liability. Without this the sign of a
        balance depends on the account type and every caller has to remember it.
        """
        if self.normal_balance is BalanceSide.DEBIT:
            return total_debit - total_credit
        return total_credit - total_debit


# =============================================================================
# Fiscal calendar
# =============================================================================
class FiscalYear(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """One financial year, e.g. 2026-27 running April to March.

    The year is the boundary at which P&L accounts reset and their net result
    rolls into retained earnings. Its start month comes from
    ``Organization.fiscal_year_start_month`` (April in India, January elsewhere).
    """

    name: Mapped[str] = mapped_column(String(50), nullable=False)
    start_date: Mapped[LedgerDate] = mapped_column(nullable=False)
    end_date: Mapped[LedgerDate] = mapped_column(nullable=False)

    status: Mapped[PeriodStatus] = mapped_column(
        enum_column(PeriodStatus, length=20),
        nullable=False,
        default=PeriodStatus.OPEN,
    )
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship()
    periods: Mapped[list[AccountingPeriod]] = relationship(
        back_populates="fiscal_year",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AccountingPeriod.start_date",
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_fiscal_year_org_name"),
        CheckConstraint("end_date > start_date", name="dates_ordered"),
    )

    def contains(self, on: dt.date) -> bool:
        return self.start_date <= on <= self.end_date


class AccountingPeriod(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """A month within a fiscal year - the unit that opens and closes.

    Monthly rather than yearly granularity because GST returns are filed monthly:
    once a month's numbers are filed they must stop changing, while the rest of
    the year stays open for business.
    """

    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fiscal_year.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    start_date: Mapped[LedgerDate] = mapped_column(nullable=False)
    end_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)

    status: Mapped[PeriodStatus] = mapped_column(
        enum_column(PeriodStatus, length=20),
        nullable=False,
        default=PeriodStatus.OPEN,
    )
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship()
    fiscal_year: Mapped[FiscalYear] = relationship(back_populates="periods")

    __table_args__ = (
        UniqueConstraint("fiscal_year_id", "name", name="uq_period_year_name"),
        CheckConstraint("end_date >= start_date", name="dates_ordered"),
        Index("ix_period_org_range", "organization_id", "start_date", "end_date"),
    )

    @property
    def accepts_postings(self) -> bool:
        return self.status is PeriodStatus.OPEN

    def contains(self, on: dt.date) -> bool:
        return self.start_date <= on <= self.end_date


# =============================================================================
# Journals
# =============================================================================
class Journal(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """A book of entries, grouped by source."""

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    journal_type: Mapped[JournalType] = mapped_column(
        enum_column(JournalType, length=20),
        nullable=False,
        index=True,
    )

    #: Prefix for generated entry numbers, e.g. ``SAL`` -> ``SAL-2026-0001``.
    number_prefix: Mapped[str] = mapped_column(String(10), nullable=False, default="JV")

    #: For cash and bank journals: the account the journal's contra side hits by
    #: default. Lets later stages record a payment without naming the bank twice.
    default_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="RESTRICT")
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    organization: Mapped[Organization] = relationship()
    default_account: Mapped[Account | None] = relationship()

    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_journal_org_code"),)


class NumberSequence(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """Gap-free per-scope document numbering.

    ``MAX(number) + 1`` is the obvious approach and it is wrong: two concurrent
    posts read the same maximum and produce duplicate numbers. This table is
    incremented under ``SELECT … FOR UPDATE``, which serialises exactly the
    contending transactions and nothing else.

    Statutory numbering must also be *gap-free*, which rules out a PostgreSQL
    ``SEQUENCE`` - sequences deliberately do not roll back, so a failed
    transaction burns a number permanently. Here the increment is part of the
    transaction, so a rollback returns the number.

    ``scope`` is a free-form key (``"journal:<id>:2026"``, later
    ``"invoice:2026"``), so Stage 3 reuses this table rather than inventing a
    second numbering scheme.
    """

    scope: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    padding: Mapped[int] = mapped_column(Integer, nullable=False, default=4)

    organization: Mapped[Organization] = relationship()

    __table_args__ = (
        UniqueConstraint("organization_id", "scope", name="uq_number_sequence_org_scope"),
        CheckConstraint("next_value >= 1", name="positive_next_value"),
    )

    def format(self, value: int) -> str:
        return (
            f"{self.prefix}{value:0{self.padding}d}" if self.prefix else f"{value:0{self.padding}d}"
        )


# =============================================================================
# Journal entries
# =============================================================================
class JournalEntry(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """One balanced transaction.

    ``total_debit`` and ``total_credit`` are stored rather than derived. Two
    reasons: a trial balance over a year of entries becomes an index-only scan of
    this table instead of an aggregate over every line, and - more importantly -
    storing them lets the balance invariant become a database ``CHECK``. A
    computed value cannot be constrained.

    There is no soft-delete and no update path for posted entries. History is
    corrected by posting a reversal.
    """

    journal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounting_period.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    #: Assigned at posting time, not creation: a draft that is never posted must
    #: not consume a statutory number.
    entry_number: Mapped[str | None] = mapped_column(String(50), index=True)

    entry_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)
    narration: Mapped[str] = mapped_column(Text, nullable=False)
    #: External document reference - cheque number, supplier invoice number.
    reference: Mapped[str | None] = mapped_column(String(100), index=True)

    #: Who the money came from, or went to. Free text, and deliberately not a foreign
    #: key to a customer or supplier.
    #:
    #: This is the "particulars" column of a traditional day book. Most entries in a
    #: small business name a party that will never be a master record - the auto driver,
    #: the electricity board, a walk-in buyer - and forcing a customer row into existence
    #: to write down who paid you is exactly the friction the billing screen exists to
    #: remove. Indexed because "everything I paid Airtel" is a question people ask.
    #:
    #: Invoices and bills leave this empty: they already have a real party on the
    #: document itself, and duplicating it here would give two answers to one question.
    counterparty: Mapped[str | None] = mapped_column(String(200), index=True)

    status: Mapped[EntryStatus] = mapped_column(
        enum_column(EntryStatus, length=20),
        nullable=False,
        default=EntryStatus.DRAFT,
        index=True,
    )

    total_debit: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    total_credit: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    currency: Mapped[CurrencyCode] = mapped_column(nullable=False, default="INR")

    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    # --- Reversal linkage ---
    reversed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    #: On the reversal entry: the entry it cancels.
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="RESTRICT")
    )

    # --- Provenance for later stages ---
    #: What produced this entry - ``"invoice"``, ``"payment"``, ``"stock_move"``.
    #: A loose string pair rather than a polymorphic FK: the accounting module
    #: must not depend on modules that do not exist yet, and inverting that
    #: dependency is what keeps this layer replaceable.
    source_type: Mapped[str | None] = mapped_column(String(50), index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    # --- Relationships ---
    organization: Mapped[Organization] = relationship()
    journal: Mapped[Journal] = relationship()
    period: Mapped[AccountingPeriod] = relationship()
    lines: Mapped[list[JournalEntryLine]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="JournalEntryLine.line_number",
    )
    posted_by: Mapped[User | None] = relationship(foreign_keys=[posted_by_id])
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    reverses: Mapped[JournalEntry | None] = relationship(
        remote_side="JournalEntry.id", foreign_keys=[reverses_id]
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "entry_number", name="uq_entry_org_number"),
        # The central invariant, enforced where nothing can bypass it.
        CheckConstraint("total_debit = total_credit", name="balanced"),
        CheckConstraint("total_debit >= 0 AND total_credit >= 0", name="totals_non_negative"),
        # A posted entry must carry a number and a timestamp.
        CheckConstraint(
            "status = 'draft' OR (entry_number IS NOT NULL AND posted_at IS NOT NULL)",
            name="posted_has_number",
        ),
        # Drives the ledger and report queries: one org, a date range.
        Index("ix_entry_org_date_status", "organization_id", "entry_date", "status"),
    )

    @property
    def is_posted(self) -> bool:
        return self.status in (EntryStatus.POSTED, EntryStatus.REVERSED)

    @property
    def is_editable(self) -> bool:
        return self.status is EntryStatus.DRAFT

    @property
    def is_balanced(self) -> bool:
        return self.total_debit == self.total_credit and self.total_debit > 0


class JournalEntryLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One side of one leg of a transaction.

    Not org-scoped: a line reaches its tenant through its entry, and duplicating
    ``organization_id`` here would create a second source of truth that could
    disagree with the parent.
    """

    entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    line_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str | None] = mapped_column(String(500))

    debit: Mapped[Money] = mapped_column(nullable=False, default=ZERO)
    credit: Mapped[Money] = mapped_column(nullable=False, default=ZERO)

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()

    __table_args__ = (
        CheckConstraint("debit >= 0 AND credit >= 0", name="amounts_non_negative"),
        # Exactly one side carries a value. A line with both, or neither, is
        # meaningless and would silently distort the trial balance.
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)",
            name="single_sided",
        ),
        # The general-ledger query: one account, chronological.
        Index("ix_line_account_entry", "account_id", "entry_id"),
    )

    @property
    def amount(self) -> Decimal:
        return self.debit if self.debit > 0 else self.credit

    @property
    def side(self) -> BalanceSide:
        return BalanceSide.DEBIT if self.debit > 0 else BalanceSide.CREDIT
