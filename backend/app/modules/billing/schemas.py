"""Billing API contracts."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated

from pydantic import Field, StringConstraints

from app.core.schemas import BaseSchema, ResponseSchema
from app.modules.billing.models import CardKind, CardNetwork
from app.modules.billing.service import Direction, MoneyAccountKind, MoneyKind

#: A money amount on the way in. Positive only - a correction is a reversal, not a
#: negative entry, because a ledger records what happened rather than the net of it.
#: `decimal_places=2` because a person typing an amount by hand means rupees and
#: paise; the column keeps 4 for computed figures.
Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]

Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
#: Who the money was with. Stripped, so a field holding only spaces is rejected rather
#: than stored as whitespace that looks filled in on screen.
Party = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class RecordEntryRequest(BaseSchema):
    """What the form sends.

    Only three fields are required: which way, when, and how much - plus a note,
    because an amount with no description is unidentifiable a month later and the
    ledger's narration cannot be blank.

    `category_id` and `money_account_id` are optional and fall back to sensible
    defaults, so a first entry needs no understanding of the chart of accounts.
    """

    direction: Direction
    amount: Amount
    description: Description
    #: Defaults to today, resolved in the organization's own timezone.
    entry_date: dt.date | None = None
    category_id: uuid.UUID | None = None
    money_account_id: uuid.UUID | None = None
    reference: str | None = Field(default=None, max_length=100)
    #: Who it came from, or who it went to. **Required.**
    #:
    #: Free text, not a foreign key: most parties in a small business are never worth a
    #: master record, and forcing one is the friction this screen exists to remove. But
    #: an amount with no counterparty is nearly as unidentifiable a month later as one
    #: with no description, so it is asked for rather than offered.
    #:
    #: Enforced here and not only in the form, because a rule the browser keeps and the
    #: API does not is not a rule. The form is the only thing that creates these entries,
    #: so there is no import or scanning path that this locks out.
    party: Party


class ReverseEntryRequest(BaseSchema):
    reason: str | None = Field(default=None, max_length=500)


class CategoryRead(ResponseSchema):
    id: uuid.UUID
    code: str
    name: str
    #: Which way this category applies. Income categories cannot take money out.
    direction: Direction
    #: The parent group's name, so the dropdown can use `optgroup`. A flat list of
    #: nearly eighty categories is one nobody reads to the end of.
    group: str
    is_default: bool


class CreateCategoryRequest(BaseSchema):
    """Add a category of your own.

    Only a name and a direction. The code, parent group, and subtype are derived -
    asking someone to pick an account code and a subtype in order to record a payment
    would defeat the purpose of this screen.
    """

    name: Annotated[str, Field(min_length=1, max_length=150)]
    direction: Direction


#: A bank's name, an account holder's name. Stripped, so a field holding only spaces is
#: treated as blank rather than stored as whitespace that looks filled in on screen.
PartyName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]

#: An account number as typed. Spaces and dashes are how people write these down, so they
#: are accepted and stripped; letters are not, because an account number has none.
AccountNumber = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=4, max_length=34, pattern=r"^[\d\s-]+$"),
]


class BankDetailsFields(BaseSchema):
    """The facts about a bank account that the ledger has no use for.

    All optional, because cash in hand has none of them and a first entry should not be
    blocked on paperwork. Shared by the create and update requests so the two cannot drift.
    """

    #: "HDFC Bank", "State Bank of India". Free text - a fixed list of banks is a list that
    #: is wrong the week a new one launches or two merge.
    bank_name: PartyName | None = None
    #: Whose account it is. Usually the business or its proprietor.
    holder_name: PartyName | None = None
    #: **Stored encrypted, and stored in full** - unlike a card number. It is what you
    #: quote to be paid and match against a statement, so keeping only four digits would
    #: make it useless. See `billing/models.py` for the contrast.
    account_number: AccountNumber | None = None


class CreateMoneyAccountRequest(BankDetailsFields):
    """Add a cash box or bank account.

    A name and which of the two it behaves like are all that is required. Everything else -
    the account code, the parent group, the subtype - is derived, for the same reason the
    category form derives them: nobody should need the chart of accounts to add a UPI
    wallet.

    The inherited bank fields are **ignored for a cash account**, which has no bank, no
    number and no holder.
    """

    name: Annotated[str, Field(min_length=1, max_length=150)]
    kind: MoneyKind = MoneyKind.BANK


class UpdateBankDetailsRequest(BankDetailsFields):
    """Fill in or correct an account's details after the fact.

    Needed because the seeded chart creates "Primary Bank Account" before anyone has said
    which bank that is - so without this, the one account most organizations actually use
    would be the only one that could never carry its own details.
    """

    #: A new name for the account. Optional; omitting it leaves the name alone.
    #:
    #: **Renaming a seeded account is allowed**, unlike archiving or deleting one. "Primary
    #: Bank Account" is a placeholder the chart template wrote before anyone was asked, and
    #: the software finds that account by its `system_key`, not by its name - so calling it
    #: "HDFC Current" breaks nothing and is the first thing most people want to do.
    name: Annotated[str | None, Field(default=None, min_length=1, max_length=150)] = None


class BankDetailsRead(ResponseSchema):
    """One account's details, **with the number in full.**

    Its own response rather than part of `MoneyAccountRead`, so that reading a full account
    number is a deliberate request against its own route, instead of something that rides
    along on every load of the recording screen.
    """

    account_id: uuid.UUID
    #: The account's own name, so a rename is reflected without a second request.
    name: str
    bank_name: str | None = None
    holder_name: str | None = None
    account_number: str | None = None
    account_number_last4: str | None = None


class MoneyAccountRead(ResponseSchema):
    id: uuid.UUID
    code: str
    name: str
    is_default: bool
    #: What this place actually is. `credit_card` is a **liability**, not cash, and the
    #: client uses this to say so rather than showing a card beside a bank balance as
    #: though the two meant the same thing.
    kind: MoneyAccountKind = MoneyAccountKind.CASH
    #: Set when a card is what identifies this option. A debit card shares its `id` with
    #: the bank account it draws on, so the card id is what tells the two entries apart.
    card_id: uuid.UUID | None = None
    card_last4: str | None = None
    card_network: str | None = None

    #: Who the account belongs to and which bank it is at, for the line under the name.
    bank_name: str | None = None
    holder_name: str | None = None
    #: The tail only. **Never the full number** on this list - it fills a picker, and a
    #: client that just needs to tell two accounts apart has no use for the whole thing.
    account_number_last4: str | None = None

    #: False once archived. Archived accounts are left out of the picker entirely and only
    #: appear on the accounts screen when it asks for them.
    is_active: bool = True
    #: Whether deleting is allowed: nothing posted to it, not seeded, and no card drawing on
    #: it. Archiving is the answer when this is false, which is why they are two flags.
    can_delete: bool = False
    #: Why deleting is refused, or null. Phrased for a person - it goes into a tooltip, so
    #: the control can be shown and explained rather than silently missing.
    delete_blocked_reason: str | None = None
    #: Whether archiving is allowed. A seeded account cannot be deactivated - later modules
    #: post to it by role - so the server answers the question rather than leaving the
    #: client to re-derive a rule it would get wrong.
    can_archive: bool = False


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
class AddCardRequest(BaseSchema):
    """Put a card on file.

    **The number is used and discarded; only the last four digits and the network are
    stored.** Constrained here to shape only - digits, spaces and dashes, within the
    lengths ISO/IEC 7812 allows - with the check digit and the scheme worked out in the
    service. The pattern rejects letters without quoting the value back, which matters:
    the 422 handler forwards messages and never inputs, and a message that echoed the
    digits would undo that.
    """

    label: Annotated[str, Field(min_length=1, max_length=80)]
    kind: CardKind

    #: As typed or pasted. Spaces and dashes are fine.
    card_number: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=12,
            # 19 digits plus the separators a person types between groups of four.
            max_length=25,
            pattern=r"^[\d\s-]+$",
        ),
    ]

    #: The name embossed on the card. Optional - on a sole proprietor's own card it is
    #: simply their own name and typing it adds nothing.
    #:
    #: **Kept in the clear, unlike the number.** PCI DSS permits retaining a cardholder
    #: name; it is the PAN and the authentication data that may not be kept. A name alone
    #: cannot be used to transact.
    holder_name: PartyName | None = None

    #: Required for a debit card, ignored for a credit card. A debit card is a way of
    #: using a bank account you already have, so it names that account rather than
    #: creating one - which would double-count the same money.
    bank_account_id: uuid.UUID | None = None


class UpdateCardRequest(BaseSchema):
    """Correct a card's name, its holder, or its number.

    **No `kind`.** A credit card owns a liability account; a debit card points at a bank
    account that already existed. Switching would either orphan an account with postings
    against it or start filing card spending as money leaving a bank account that never lost
    it. The honest correction is a new card and an archive of the wrong one.

    Every field is optional and omitting one leaves it alone - unlike the bank-details `PUT`,
    which replaces the whole set. Sending `holder_name: ""` clears it.
    """

    label: Annotated[str | None, Field(default=None, min_length=1, max_length=80)] = None
    holder_name: PartyName | None = None

    #: A corrected number. Read, reduced to a network and four digits, and discarded - the
    #: same handling as on create. Worth allowing: a mistyped number leaves the wrong four
    #: digits on screen, and those digits are the whole reason anything is stored.
    card_number: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True, min_length=12, max_length=25, pattern=r"^[\d\s-]+$"
            ),
        ]
        | None
    ) = None


class CardRead(ResponseSchema):
    """A card on file. **There is no field for a number, by design.**"""

    id: uuid.UUID
    label: str
    kind: CardKind
    network: CardNetwork
    #: Four digits, as a string - a card ending 0042 is not the number 42.
    last4: str
    #: The ledger account this card's postings land on. Its own liability account for a
    #: credit card; the bank account it draws on for a debit card.
    account_id: uuid.UUID
    account_name: str
    is_active: bool
    #: The name on the card, if it was given.
    holder_name: str | None = None
    #: Whether deleting is allowed - false once anything has been recorded on the card's
    #: account. Archive it instead; the entries name it.
    can_delete: bool = False
    #: Why deleting is refused, or null. See `MoneyAccountRead.delete_blocked_reason`.
    delete_blocked_reason: str | None = None


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------
class TransferRequest(BaseSchema):
    """Move money between two of your own accounts.

    No category, and that is not an omission: moving your own money is neither earning
    nor spending it, so there is no income or expense line to file it against.
    """

    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount: Amount
    #: Defaults to today, resolved in the organization's own timezone.
    entry_date: dt.date | None = None
    #: Optional. Left blank, the ledger narration names both accounts.
    description: Annotated[str | None, Field(default=None, max_length=500)] = None
    reference: str | None = Field(default=None, max_length=100)


class TransferRead(ResponseSchema):
    entry_id: uuid.UUID
    entry_number: str | None
    date: dt.date
    amount: Decimal
    description: str
    from_account_id: uuid.UUID
    from_account_name: str
    to_account_id: uuid.UUID
    to_account_name: str


class BillingOptions(ResponseSchema):
    """Everything the form needs to render, in one request.

    Served rather than hard-coded so the categories follow the organization's actual
    chart of accounts - including any account it has added itself.
    """

    categories: list[CategoryRead]
    money_accounts: list[MoneyAccountRead]
    #: Cards on file, for the accounts panel. Separate from `money_accounts` because the
    #: two answer different questions: that list is "where can this payment go", this one
    #: is "what have I registered" - and an archived card belongs in neither.
    cards: list[CardRead]
    #: Today in the organization's timezone, so the date field opens on the right day
    #: rather than on the server's UTC date.
    today: dt.date
    currency: str


class EntryRead(ResponseSchema):
    id: uuid.UUID
    #: The ledger's own number for this entry, so it can be found in the journal.
    entry_number: str | None
    date: dt.date
    direction: Direction
    amount: Decimal
    description: str
    reference: str | None
    party: str | None

    category_id: uuid.UUID
    category_name: str
    money_account_id: uuid.UUID
    money_account_name: str

    created_at: dt.datetime
    #: Cancelled by a reversal. Still listed - the cancellation is part of the record.
    is_reversed: bool


class BillingSummary(ResponseSchema):
    from_date: dt.date
    to_date: dt.date
    money_in: Decimal
    money_out: Decimal
    net: Decimal
    entry_count: int
