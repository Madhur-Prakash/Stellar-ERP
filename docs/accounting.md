<div align="center">

# Accounting core

**The double-entry ledger: its invariants, exact money, reversals, and the fiscal calendar.**

![Money](https://img.shields.io/badge/money-NUMERIC(18,4)_never_float-2EA043?style=flat-square)
![Entries](https://img.shields.io/badge/posted_entries-immutable-DA3633?style=flat-square)
![Correction](https://img.shields.io/badge/correction-by_reversal-D29922?style=flat-square)

<!-- nav:start -->
[Docs](README.md) · [Spec](spec.md) · [Architecture](architecture.md) · [Database](database.md) · **Accounting** · [Proof ledger](attestation.md) · [API](api.md) · [Security](security.md) · [Audit](security-audit.md) · [Development](development.md) · [Deployment](deployment.md)
<!-- nav:end -->

</div>

---

The double-entry ledger. Every commercial module posts into it: invoices hit the
sales journal, goods receipts move inventory and recognise COGS, scanned documents
produce a draft a human confirms, and every report reads back out of it.

Getting this wrong is not recoverable later, so each invariant is enforced in
three places - Pydantic schema, service, and database constraint.

---

## The invariants

| # | Invariant | Enforced by |
| --- | --- | --- |
| 1 | Every entry balances (`Σ debit = Σ credit`) | schema validator, service check, **`CHECK` constraint** |
| 2 | A line is debit **or** credit, never both | schema validator, **`CHECK` constraint** |
| 3 | Posted entries are immutable | no update code path exists |
| 4 | Nothing posts into a closed period | service, at every create *and* post |
| 5 | Only leaf accounts receive postings | service |
| 6 | No cross-tenant posting | service, per-account ownership check |

Invariant 1 is constrained at the database level deliberately: a bad data
migration or a manual `UPDATE` in psql cannot create an unbalanced entry. A test
proves it by attempting exactly that with raw SQL.

Invariant 3 is worth dwelling on. There is no "edit posted entry" endpoint, and no
service method that would allow it - correction happens by posting a **reversal**,
which leaves both the original and its mirror in the ledger. The audit trail then
shows what happened *and* what corrected it, which is the whole point of an audit
trail.

---

## Money is never a float

`0.1 + 0.2 != 0.3` in binary floating point. In a ledger that error compounds: a
trial balance that should sum to zero comes out at `-0.000000001`, the balance
sheet fails to balance, and the cause is invisible.

Amounts are `NUMERIC(18, 4)` → `decimal.Decimal`, defined once in
[`db/types.py`](../backend/app/db/types.py):

- **4 decimal places**, not 2, so unit prices and tax rates keep sub-paisa
  precision during calculation. Rounding happens once, at presentation.
- **18 total digits** leaves 14 for the integer part.

Money also crosses the wire as a JSON **string**. A JSON number is a double in
every JavaScript client, so `1234567.89` would arrive as `1234567.8899999999`.

Two tests pin this: `0.1 + 0.2` must equal exactly `0.3000`, and thirty entries of
`33.3333` must total exactly `999.9990`.

---

## Debit/credit columns, not one signed amount

A signed column is more compact but forces every reader to remember the sign
convention per account type, and makes "total debits" - the figure an accountant
reconciles against - a conditional aggregate instead of a plain `SUM`.

`Account.signed_balance()` converts raw totals into a balance in the account's own
terms, so *positive always means more of what this account is for*: more cash in
an asset, more owed on a liability. The convention lives in one place
(`AccountType.normal_balance`) and every report derives from it.

---

## A reversed entry still counts

This is the subtlety most likely to be broken by a well-meaning change.

Reversing entry *A* creates mirror entry *B* and marks *A* as `REVERSED`. The two
cancel arithmetically. So balance queries must include **both** `POSTED` and
`REVERSED`:

```python
POSTED_STATUSES = (EntryStatus.POSTED, EntryStatus.REVERSED)
```

Excluding `REVERSED` would leave *B*'s mirror lines counted with nothing to cancel
them, flipping the sign of every reversed transaction. Only `DRAFT` is excluded,
because a draft is not yet in the books. `test_reversal_mirrors_and_nets_to_zero`
guards this.

---

## Why a balance sheet balances

P&L accounts reset each fiscal year; their net result rolls into retained earnings
only when the year is closed. So mid-year, `assets != liabilities + equity` - the
difference is exactly the profit earned so far.

The report computes that figure over the fiscal year to date and presents it as a
distinct equity line, `current_period_earnings`. **Omitting it is the classic
reason a hand-rolled balance sheet fails to balance.** A test asserts it equals
the P&L's net profit over the same range - the two statements read the same ledger
and must agree.

---

## Numbering

Entry numbers are assigned **at posting time**, not creation, so an abandoned
draft consumes no statutory number.

`NumberSequence` is incremented under `SELECT … FOR UPDATE`. Two alternatives were
rejected:

- **`MAX(number) + 1`** - two concurrent posts read the same maximum and produce
  duplicates.
- **A PostgreSQL `SEQUENCE`** - sequences deliberately do not roll back, so a
  failed transaction burns a number permanently. Statutory numbering must be
  gap-free.

Because the increment is part of the caller's transaction, a rollback returns the
number. `scope` is a free-form key (`journal:<id>:2026-27`), so Stage 3 reuses this
table for invoice numbering rather than inventing a second scheme.

---

## The fiscal calendar

A **fiscal year** is the boundary at which P&L accounts reset. Its start month
comes from `Organization.fiscal_year_start_month` (April in India, January
elsewhere), so a date in February 2026 belongs to the year beginning April 2025.

**Periods are monthly**, because GST returns are filed monthly: once a month's
numbers are filed they must stop changing while the rest of the year stays open.

| Status | Meaning |
| --- | --- |
| `open` | accepts postings |
| `closed` | soft close - no postings, an administrator can reopen |
| `locked` | hard close after filing - the API will not reopen it |

Periods must close **in order**. Closing March while February is open produces
comparatives nobody can reconcile.

Creating an organization seeds the current fiscal year and its twelve periods.
Without that, `resolve_open_period` refuses every posting and a new organization
would have books it could not write to.

---

## The seam for later stages

Stage 3 and 4 must not need to know the chart's shape. They post by naming
**roles**:

```python
await posting.post_simple(
    org_id, actor,
    journal_type=JournalType.SALES,
    entry_date=invoice.date,
    narration=f"Invoice {invoice.number}",
    debit_key=SystemAccount.ACCOUNTS_RECEIVABLE,
    credit_key=SystemAccount.SALES_REVENUE,
    amount=invoice.total,
    source_type="invoice", source_id=invoice.id,
)
```

`Account.system_key` marks the single default account for each role. An
organization may have many receivable accounts; exactly one carries the key.
`source_type`/`source_id` are a loose string pair rather than a polymorphic FK -
the accounting module must not depend on modules that do not exist yet, and
inverting that dependency is what keeps this layer replaceable.

---

## Cards and transfers

Two things the billing module posts deserve stating here, because both are places where
the intuitive treatment is the wrong one.

**A credit card is a liability.** Registering one creates an account under Current
Liabilities (parent `2100`, subtype `other_current_liability`), not a cash-equivalent
asset. Spending ₹5,000 on it is *debit expense, credit the card* - what you owe went up,
not what you hold went down. Filing it as cash would inflate the cash balance and
understate the debt at the same time, and both errors would flow into the dashboard, the
cash flow statement, and the balance sheet.

A **debit** card gets no account at all. It names a bank account that already exists,
because a debit card is a way of using that account - a second account would double-count
the same money. This is why a debit card arrives from the API sharing its bank account's
id.

**A transfer between your own accounts is neither income nor an expense.** It posts as
*debit the destination, credit the source*, with **no income or expense line** - there is
nothing earned or spent to file against. It is tagged `source_type="transfer"` rather than
the day book's own tag, which is what keeps it out of the money-in and money-out totals;
counting it would report income that never arrived from anywhere and an expense that
bought nothing. Paying off a credit card is exactly this: money out of a bank account and
into the card's liability account, reducing the debt.

**Which bank an account is at is not the ledger's business.** An `account` row knows a code,
a name and a subtype - everything a posting needs. The bank name, the holder, and the account
number live in `bank_account_detail`, keyed one-to-one to the account, because every one of
those fields is nullable and meaningless for the great majority of accounts: revenue,
expenses, receivables. Putting them on `account` would add four permanently-empty columns to
the one table every posting joins, and would make the accounting core depend on what the
billing module happens to want.

That tagging is also what makes the day book's reconstruction work. `BillingService`
rebuilds its simple two-line view by finding the **income or expense line** and treating
whatever is left as the money leg - not the other way round. Looking for a cash-equivalent
line instead would mean a card charge posted successfully and then could not be read back,
because its money leg is a liability.

---

## Reports

| Report | Window | Note |
| --- | --- | --- |
| Trial balance | cumulative, or a range | `is_balanced` must be true |
| General ledger | a range | opening balance + running total |
| Profit & loss | a range | COGS separated so gross profit is meaningful |
| Balance sheet | as at a date | includes `current_period_earnings` |
| Cash flow | a range | **direct method** |

Cash flow uses the direct method - actual cash movements grouped by the
counter-account that explains them. The indirect method (net profit adjusted for
non-cash items) is the statutory presentation for larger entities and is deferred;
for a small business, "where did the money actually go" is more useful and
verifiable line-by-line against a bank statement.

Cash-to-cash transfers (bank → petty cash) are excluded: they net to zero and must
not appear as both an inflow and an outflow.

Reports that must reconcile expose a boolean (`is_balanced`, `reconciles`) rather
than raising. A corrupted ledger should be *visible in the UI*, not a 500 on an
otherwise-useful report. The trial balance also logs at `critical` if it ever fails
to balance, since that implies something wrote to the database outside the
application.

---

## Enum storage

Every enum column goes through `enum_column()` in
[`db/types.py`](../backend/app/db/types.py), never `sqlalchemy.Enum` directly.

Two SQLAlchemy defaults make the obvious spelling quietly wrong:

1. **It stores the member *name*, not the value.** `EntryStatus.DRAFT` persists as
   `'DRAFT'` while the API serialises `'draft'`. Worse, any SQL predicate written
   against the value silently never matches - a partial index
   `WHERE status = 'pending'` exists but enforces nothing.
2. **`create_constraint` defaults to False**, so there is no `CHECK` at all and the
   column accepts any string.

Both were live defects, found while building this stage: the Stage 1 index
`uq_invitation_pending_email` had never fired. Migration `a1f2e3d4c5b6` remaps the
data and adds the constraints; two regression tests cover the index firing and the
stored casing.

<!-- related:start -->

---

## Related reading

- [Database](database.md) - the tables and constraints behind these invariants
- [API](api.md) - the endpoints that post into the ledger
- [Spec](spec.md) - the product requirements this implements
- [Proof ledger](attestation.md) - how these entries are committed to Stellar, and why an entry's status is deliberately not hashed

[All documentation](README.md)
<!-- related:end -->
