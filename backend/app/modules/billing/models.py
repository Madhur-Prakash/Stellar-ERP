"""Payment cards and bank-account details - identifying where money moved.

Two tables, and the contrast between them is the thing to understand before editing
either. :class:`BankAccountDetail` stores an account number **in full**, encrypted;
:class:`PaymentCard` stores no card number at all, in any form. That is not an
inconsistency - a bank account number is what you quote to get paid and match against a
statement, so an ERP cannot do its job without it, whereas a card number has no use here
once the last four digits are known, and keeping one would bring the whole database into
PCI DSS scope.


**The full card number is never stored, and this module is written so that it cannot
be.** A Primary Account Number is the one field that brings an entire database into PCI
DSS scope, and this is self-hosted software with no key management, no tokenisation
service, and no obligation to have either. What a shopkeeper actually needs is to tell
*which* card a payment went on, and the last four digits plus the network do that
completely - it is what the card itself prints on a receipt, and what every bank
statement shows.

So the API accepts a number, validates it, derives :attr:`PaymentCard.network` and
:attr:`PaymentCard.last4`, and throws the rest away before anything is written or
logged. There is no column that could hold a PAN, which is a stronger guarantee than a
rule saying not to put one there.

**A credit card is a liability, not cash - and that distinction is the whole reason
this module exists rather than reusing "add a bank account".** Paying by credit card
does not move money; it creates a debt to the issuer. Modelling it as a cash-equivalent
asset would put a card balance inside the dashboard's "Cash and bank" figure and inside
the cash flow statement's definition of cash, and both would then be wrong in a way that
looks plausible. So a credit card gets its own liability account under Current
Liabilities, and paying it off from a bank account is an ordinary transfer.

**A debit card is not an account at all**, and pretending otherwise would double-count
the money. The card is a way of touching a bank account that already exists, so adding
one attaches a label and last four digits *to that account* rather than creating a
second one. The picker can then offer "HDFC Bank ··4242" while every posting still lands
on the single real account.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import enum_column

if TYPE_CHECKING:
    from app.modules.accounting.models import Account
    from app.modules.organizations.models import Organization


class CardKind(StrEnum):
    """What kind of card, which decides where its postings land.

    Not cosmetic. The two are different accounting objects, and the difference is the
    reason this enum exists rather than a single "card" concept.
    """

    #: Spending creates a debt to the issuer. Gets its own liability account.
    CREDIT = "credit"
    #: Spending moves money out of a bank account that already exists. Gets no account
    #: of its own - it is a label on that one.
    DEBIT = "debit"

    @property
    def label(self) -> str:
        return "Credit card" if self is CardKind.CREDIT else "Debit card"

    @property
    def has_own_account(self) -> bool:
        return self is CardKind.CREDIT


class CardNetwork(StrEnum):
    """The scheme the number belongs to, derived from its leading digits.

    Stored rather than re-derived because the number it was derived from is deliberately
    gone. Kept as a closed set so the UI can show a recognisable name; anything the
    prefix table does not recognise is :attr:`OTHER`, which is honest and harmless -
    the card still works, the software just does not claim to know the scheme.
    """

    VISA = "visa"
    MASTERCARD = "mastercard"
    RUPAY = "rupay"
    AMEX = "amex"
    DISCOVER = "discover"
    DINERS = "diners"
    JCB = "jcb"
    MAESTRO = "maestro"
    OTHER = "other"

    @property
    def label(self) -> str:
        return _NETWORK_LABELS[self]


_NETWORK_LABELS: Final[dict[CardNetwork, str]] = {
    CardNetwork.VISA: "Visa",
    CardNetwork.MASTERCARD: "Mastercard",
    CardNetwork.RUPAY: "RuPay",
    CardNetwork.AMEX: "American Express",
    CardNetwork.DISCOVER: "Discover",
    CardNetwork.DINERS: "Diners Club",
    CardNetwork.JCB: "JCB",
    CardNetwork.MAESTRO: "Maestro",
    CardNetwork.OTHER: "Card",
}


class PaymentCard(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """A card money moves on, identified by its last four digits.

    **There is no column for a card number**, and that absence is the design. See the
    module docstring.
    """

    #: What the user calls it - "Business Amex", "HDFC debit". Free text, because the
    #: scheme and the last four are not enough to tell two HDFC cards apart.
    label: Mapped[str] = mapped_column(String(80), nullable=False)

    kind: Mapped[CardKind] = mapped_column(enum_column(CardKind, length=10), nullable=False)

    network: Mapped[CardNetwork] = mapped_column(
        enum_column(CardNetwork, length=12), nullable=False
    )

    #: Exactly four digits, as a string. A string rather than an integer because a card
    #: ending 0042 is not the number 42, and the leading zeros are the point.
    last4: Mapped[str] = mapped_column(String(4), nullable=False)

    #: The ledger account this card's postings land on.
    #:
    #: A credit card owns its account, which was created alongside it. A debit card
    #: points at a bank account that already existed and is shared with every other way
    #: of touching that account. ``RESTRICT`` rather than ``CASCADE``: an account with
    #: postings against it is not deletable anyway, and silently removing a card because
    #: something happened to an account would lose the record of which card was used.
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    #: Whose name is embossed on the card. Optional, because on a sole proprietor's own
    #: card it is simply their own name and typing it adds nothing.
    #:
    #: **Stored in the clear, unlike the number.** Under PCI DSS a cardholder name is
    #: cardholder data and may be retained; it is the PAN and the authentication data
    #: (CVV, PIN, magnetic stripe) that may not. A name on its own cannot be used to
    #: transact, so protecting it like a credential would be theatre - what makes this
    #: safe is the absence of everything it would need to be paired with.
    holder_name: Mapped[str | None] = mapped_column(String(120))

    #: Archived rather than deleted, for the same reason a product is: entries already
    #: reference the account, and the card is how someone recognises them.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped[Organization] = relationship(lazy="raise")
    account: Mapped[Account] = relationship(lazy="raise")

    __table_args__ = (
        # The same scheme and last four twice in one organization is almost always a
        # double-entry rather than two genuinely different cards. Scoped by kind as
        # well, because a bank issuing a debit and a credit card on the same account
        # range can legitimately produce a collision.
        UniqueConstraint(
            "organization_id",
            "network",
            "last4",
            "kind",
            name="uq_payment_card_org_network_last4_kind",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PaymentCard {self.label} {self.network.value} ··{self.last4}>"

    @property
    def display_name(self) -> str:
        """What the picker shows: "Business Amex ··4242"."""
        return f"{self.label} ··{self.last4}"


class BankAccountDetail(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """The human facts about a bank account, which the chart of accounts has no room for.

    An :class:`~app.modules.accounting.models.Account` knows a code, a name, and a
    subtype - everything the ledger needs and nothing a person does. "Which of my three
    HDFC accounts is 1120?" is not answerable from the chart, and it is the question
    someone reconciling a statement actually has.

    Its own table rather than columns on ``account``, for two reasons. Every field here is
    nullable and meaningless for the great majority of accounts - revenue, expenses,
    receivables - so putting them on ``account`` would add four permanently-empty columns
    to the one table every posting joins. And it keeps the accounting core free of a
    dependency on what the billing module happens to want, which is the same reason
    :class:`PaymentCard` lives here.

    **Cash in hand gets no row.** There is no bank, no number, and no holder.
    """

    #: One row per account, enforced by the unique constraint below. ``CASCADE`` here
    #: rather than ``RESTRICT`` as on :attr:`PaymentCard.account_id`: this row is a
    #: description of an account, not a record of something that happened, so if the
    #: account can be deleted at all then its description should go with it.
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: "HDFC Bank", "State Bank of India". Free text on purpose - a fixed list of banks is
    #: a list that is wrong the week a new one launches or two merge.
    bank_name: Mapped[str | None] = mapped_column(String(120))

    #: Whose account it is. Usually the business or its proprietor, but a partner's petty
    #: cash float or a director's account used for expenses are both ordinary.
    holder_name: Mapped[str | None] = mapped_column(String(120))

    #: **Fernet-encrypted at rest**, like a TOTP secret - see
    #: :func:`app.core.security.encrypt_secret`.
    #:
    #: Unlike a card number this *is* stored in full, and the difference is not
    #: inconsistency. A bank account number is what you must quote to be paid, print on an
    #: invoice, and match against a statement, so an ERP that discarded it would be unable
    #: to do the job. It also carries none of a PAN's scheme obligations. But it is still
    #: the kind of thing that should not be legible in a stolen dump or a database
    #: screenshot, and the key material for this already exists and is mandatory in
    #: production, so there is no cost to encrypting it.
    account_number_encrypted: Mapped[str | None] = mapped_column(String(500))

    #: The last four digits, in the clear, so a list can show "··4321" without decrypting
    #: a row per line. The same trick as :attr:`PaymentCard.last4`, and for the same
    #: reason: the tail of the number is what people recognise it by, and it is what a
    #: statement header prints.
    account_number_last4: Mapped[str | None] = mapped_column(String(4))

    organization: Mapped[Organization] = relationship(lazy="raise")
    account: Mapped[Account] = relationship(lazy="raise")

    __table_args__ = (UniqueConstraint("account_id", name="uq_bank_account_detail_account"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<BankAccountDetail {self.bank_name} ··{self.account_number_last4}>"
