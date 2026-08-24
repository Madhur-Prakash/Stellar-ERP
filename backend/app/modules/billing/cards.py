"""Reading a card number, and forgetting it.

Pure functions, in their own module and importing nothing from the rest of the app, so
the one piece of code that ever holds a full card number is small enough to read in one
sitting and testable without a database.

**Everything here takes a number and returns something that is not one.** There is no
function that stores, echoes, or logs the digits it was given, and
:func:`inspect_card_number` returns exactly the two facts that get persisted - the
network and the last four. That is the whole contract: the PAN exists as a local
variable for the length of one call and then it is gone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from app.modules.billing.models import CardNetwork

#: Separators people type or paste. Stripped before anything else looks at the value.
_SEPARATORS: Final = re.compile(r"[\s\-]")

#: The range real card numbers fall in. ISO/IEC 7812 allows up to 19 digits; the
#: shortest scheme still in circulation is 12 (some Maestro).
MIN_DIGITS: Final = 12
MAX_DIGITS: Final = 19


@dataclass(frozen=True, slots=True)
class CardIdentity:
    """What is safe to keep about a card.

    Deliberately does *not* carry the number it came from - there is nowhere for a
    caller to accidentally read it back out of. ``checksum_ok`` is a verdict *about* the
    number, not a piece of it.
    """

    network: CardNetwork
    last4: str

    #: Whether the number passed its Luhn check digit.
    #:
    #: **A card that fails this is not a valid card, and the service refuses it.** Carried as
    #: a field rather than folded into a `None` return so the caller can tell the two kinds
    #: of failure apart and say which happened: "that is the wrong number of digits" and
    #: "those digits do not check out" are different mistakes with different fixes, and one
    #: generic message for both is the kind of thing that leaves someone re-typing a correct
    #: number.
    checksum_ok: bool


def normalise_card_number(raw: str) -> str:
    """Strip spaces and dashes. Returns digits, or whatever else was in there."""
    return _SEPARATORS.sub("", raw.strip())


def is_plausible_card_number(digits: str) -> bool:
    """Length and character check, before the arithmetic.

    Separate from the Luhn test so a caller can tell "that is not a card number" from
    "that is a card number with a typo in it", which are different messages to show.
    """
    return digits.isdigit() and MIN_DIGITS <= len(digits) <= MAX_DIGITS


def passes_luhn(digits: str) -> bool:
    """The check digit, as every issuer computes it.

    Worth doing rather than accepting any 16 digits: Luhn catches every single-digit
    typo and almost every transposition, which is the entire class of mistake someone
    makes copying a number off a card. It says nothing about whether the card exists -
    that is not knowable here and not needed, because nothing is charged.
    """
    if not digits.isdigit():
        return False

    total = 0
    # Doubling every second digit from the right, so the parity depends on the length.
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        value = int(character)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


#: Leading-digit ranges, longest prefix first so a more specific rule wins.
#:
#: **RuPay is here and near the front on purpose.** This product is built for Indian
#: small businesses, where RuPay is the most common domestic scheme - and several of its
#: ranges (``60``, ``65``, ``81``) overlap Discover and Maestro, so ordering decides the
#: answer. Getting it wrong is cosmetic rather than dangerous, but showing an Indian
#: shopkeeper "Discover" for their RuPay card reads as software that does not know where
#: it is.
_PREFIXES: Final[tuple[tuple[tuple[str, ...], CardNetwork], ...]] = (
    (("34", "37"), CardNetwork.AMEX),
    (("6521", "6522", "60", "81", "82", "508"), CardNetwork.RUPAY),
    (("4",), CardNetwork.VISA),
    (
        (
            "51",
            "52",
            "53",
            "54",
            "55",
            "2221",
            "2222",
            "2223",
            "2224",
            "2225",
            "2226",
            "2227",
            "2228",
            "2229",
            "223",
            "224",
            "225",
            "226",
            "227",
            "228",
            "229",
            "23",
            "24",
            "25",
            "26",
            "270",
            "271",
            "2720",
        ),
        CardNetwork.MASTERCARD,
    ),
    (("6011", "644", "645", "646", "647", "648", "649", "65"), CardNetwork.DISCOVER),
    (("300", "301", "302", "303", "304", "305", "3095", "36", "38", "39"), CardNetwork.DINERS),
    (
        ("3528", "3529", "353", "354", "355", "356", "357", "358"),
        CardNetwork.JCB,
    ),
    (("50", "56", "57", "58", "639", "67"), CardNetwork.MAESTRO),
)


def detect_network(digits: str) -> CardNetwork:
    """Which scheme a number belongs to, from its leading digits.

    Matched on the longest prefix so ``6521`` reads as RuPay rather than as Discover's
    ``65``. Anything unrecognised is :attr:`CardNetwork.OTHER` - the card is still
    perfectly usable, the software simply does not claim to know the scheme.
    """
    best: CardNetwork = CardNetwork.OTHER
    best_length = 0
    for prefixes, network in _PREFIXES:
        for prefix in prefixes:
            if digits.startswith(prefix) and len(prefix) > best_length:
                best = network
                best_length = len(prefix)
    return best


def inspect_card_number(raw: str) -> CardIdentity | None:
    """Everything the system keeps about a card, or ``None`` if it is not one.

    The single entry point, so there is exactly one place a full number is handled.
    Returning ``None`` rather than raising keeps this module free of the app's exception
    hierarchy - the caller owns the message, because the caller knows whether it is
    answering a form field or a script.

    **``None`` means the wrong shape.** A failed check digit comes back on
    :attr:`CardIdentity.checksum_ok` instead - both are refused by the caller, but they are
    reported apart so the message can say which one happened. "Twelve to nineteen digits" and
    "those digits do not check out" are different mistakes with different fixes.
    """
    digits = normalise_card_number(raw)
    if not is_plausible_card_number(digits):
        return None
    return CardIdentity(
        network=detect_network(digits),
        last4=digits[-4:],
        checksum_ok=passes_luhn(digits),
    )
