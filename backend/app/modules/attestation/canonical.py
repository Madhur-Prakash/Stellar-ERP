"""The canonical encoding - a frozen, versioned byte form for one journal entry.

**This module is a one-way door, and it is the reason the third ledger can be
trusted at all.**

A Merkle root is only meaningful if the same journal entry hashes to the same 32
bytes forever - across a Python upgrade, a schema migration, a refactor, a
different machine, and a re-implementation in TypeScript written by somebody who
has never read this file. Once a root is on chain, every proof issued against it
depends on this encoding being reproducible. Change one byte of it and every
historical proof silently stops verifying, with no error anywhere to say so.

So the rules here are deliberately paranoid.

**Nothing is derived from the ORM.** Not the field order, not the field set, not
the types. :data:`FIELD_ORDER` is written out by hand. If it were built from
``JournalEntry.__table__.columns`` then a later migration adding a column would
silently enter the hash - and a migration is exactly the kind of change nobody
reviews for cryptographic consequences.

**Every value is length-prefixed and every absent value is distinct from an empty
one.** ``narration=""`` and ``narration=None`` must not produce the same bytes,
because otherwise two different entries could share a leaf, and the difference
between them is exactly the sort of thing a dispute turns on.

**Money is an integer.** ``NUMERIC(18, 4)`` is scaled by 10,000 into minor units
and encoded as a fixed-width 16-byte signed big-endian integer - the same
``i128`` the contract stores its control total in. There is no decimal string
anywhere in the hash input, because ``Decimal("100.00")`` and
``Decimal("100.0000")`` are the same number and different strings, and a
round-trip through the database can change which one you are holding.

**Leaf and node hashes are domain-separated.** Leaves are prefixed ``0x00`` and
interior nodes ``0x01`` (see :mod:`app.modules.attestation.merkle`). Without
that, an interior node's 64-byte preimage could be presented as a leaf's data and
a second preimage would be constructible for free.

**A version byte leads the encoding.** If a v2 is ever needed - a new field, a
different money scale - it gets a new version byte and a new
:data:`FIELD_ORDER`, and v1 leaves keep verifying against v1 roots forever. That
is the only safe way to evolve this file: additively, never in place.

**A golden-vector test pins the output.** ``tests/test_attestation_canonical.py``
asserts the exact hex of a known entry. Any change to this module that alters a
hash fails the build rather than quietly invalidating every proof ever issued -
which is the whole point, and is why that test carries a comment telling you not
to update the expected value.

What is deliberately *not* in the hash
--------------------------------------
**An entry's status.** A leaf commits to what was recorded, not to what later
happened to it. This ledger corrects by reversal, so ``posted`` becoming
``reversed`` is the normal path for any entry - and hashing the status would mean
that taking that path invalidated the entry's own proof. See the note in
:data:`FIELD_ORDER`; it was a real bug, caught by the reversal test.

Account **codes** and **names** are not hashed; account **ids** are. A code is a
label a business is free to renumber, and a name is one it is free to reword. If
either were hashed, renaming ``1100 Accounts Receivable`` to
``1150 Trade Receivables`` next year would invalidate every proof issued for
every entry that ever touched it. The id is the immutable fact and it is what
gets committed; the code and name travel in the proof bundle as display metadata,
clearly marked as not covered by the proof.

That is not a weakness. What the proof establishes is that *these amounts, on
these accounts, on this date, under this entry number* have not been altered.
What a business chose to call account ``1100`` is not a fact about the money.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from app.core.exceptions import BusinessRuleError

# =============================================================================
# Version
# =============================================================================
#: Leading byte of every canonical encoding.
#:
#: **Never change this value in place.** A new encoding gets a new version, both
#: implementations learn to produce it for new leaves, and both keep the old one
#: for verifying old ones.
CANONICAL_VERSION: Final = 1

#: Decimal places in :data:`app.db.types.Money`. Money is scaled by
#: ``10 ** MONEY_SCALE`` to reach whole minor units.
#:
#: Read from nowhere else on purpose. Importing the column definition to derive
#: it would mean a future widening of the money column - to 6dp, say - silently
#: rescaling every hash. If that widening ever happens it needs a new
#: :data:`CANONICAL_VERSION`, and the compiler cannot tell you that; this
#: constant sitting here, unshared, is what forces the conversation.
MONEY_SCALE: Final = 4

#: Width of an encoded integer, in bytes. 16 bytes signed is ``i128`` - the same
#: type the contract stores ``debits`` in, so the control total that reaches the
#: chain is the same width as the figures that built it.
INT_WIDTH: Final = 16

#: Sentinel length marking an absent value, so ``None`` and ``""`` differ.
#: ``0xFFFFFFFF`` is not a plausible real length (4 GiB in one column) and it is
#: the maximum a ``u32`` can hold, which makes it unmistakable in a hex dump.
ABSENT: Final = 0xFFFFFFFF

#: Domain-separation prefix for a leaf. Kept here rather than in ``merkle`` so
#: the one function that builds a leaf preimage owns both halves of it.
LEAF_PREFIX: Final = b"\x00"

#: Tag naming what kind of record this is, hashed into every leaf.
#:
#: Present so that a future second leaf type - an audit-trail leaf, a stock-movement
#: leaf - can never collide with a journal-entry leaf even if their encodings happened
#: to agree byte for byte.
KIND_JOURNAL_ENTRY: Final = "journal_entry.v1"


# =============================================================================
# Primitive encoders
# =============================================================================
def enc_u32(value: int) -> bytes:
    """A 4-byte unsigned big-endian integer."""
    if not 0 <= value <= 0xFFFFFFFF:
        raise BusinessRuleError(f"Value {value} does not fit a u32 in the canonical encoding")
    return value.to_bytes(4, "big", signed=False)


def enc_u64(value: int) -> bytes:
    """An 8-byte unsigned big-endian integer."""
    if not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise BusinessRuleError(f"Value {value} does not fit a u64 in the canonical encoding")
    return value.to_bytes(8, "big", signed=False)


def enc_int(value: int) -> bytes:
    """A 16-byte signed big-endian integer - ``i128``, two's complement.

    Fixed width rather than length-prefixed: a variable-width integer would let
    ``1`` be encoded as one byte or as sixteen, and two encoders that disagreed
    about which would produce different hashes for the same number while both
    looking correct in isolation.
    """
    try:
        return value.to_bytes(INT_WIDTH, "big", signed=True)
    except OverflowError as exc:
        raise BusinessRuleError(
            f"Value {value} does not fit an i128 in the canonical encoding"
        ) from exc


def enc_bytes(value: bytes | None) -> bytes:
    """Length-prefixed bytes; :data:`ABSENT` when ``None``."""
    if value is None:
        return enc_u32(ABSENT)
    return enc_u32(len(value)) + value


def enc_str(value: str | None) -> bytes:
    """Length-prefixed UTF-8.

    **No normalisation, no trimming, no case folding.** The bytes stored in the
    database are the bytes hashed. Normalising here would mean the hash depended
    on a Unicode table version, and NFC in Python 3.13 is not guaranteed to be
    NFC in whatever runs this in 2032.
    """
    if value is None:
        return enc_u32(ABSENT)
    return enc_bytes(value.encode("utf-8"))


def enc_uuid(value: uuid.UUID | None) -> bytes:
    """16 raw bytes, or :data:`ABSENT`.

    The raw bytes rather than the string form: a UUID has two textual spellings
    (hyphenated and not) and two cases, and only one byte form.
    """
    if value is None:
        return enc_u32(ABSENT)
    return enc_bytes(value.bytes)


def enc_date(value: dt.date | None) -> bytes:
    """A date as ``YYYYMMDD`` in a u32 - e.g. 2026-03-31 -> ``20260331``.

    Chosen over a day count from an epoch because it is legible in a hex dump and
    in a test failure, and because "which epoch" is one more thing two
    implementations can disagree about. An accounting date has no time and no
    timezone; attaching either would be the classic off-by-one that lands an
    evening entry in the wrong month.
    """
    if value is None:
        return enc_u32(ABSENT)
    return enc_u32(value.year * 10_000 + value.month * 100 + value.day)


def enc_instant(value: dt.datetime | None) -> bytes:
    """A timestamp as milliseconds since the Unix epoch, UTC.

    Milliseconds, not microseconds: PostgreSQL stores microsecond precision but a
    JavaScript ``Date`` cannot represent it, and the browser has to reach the same
    hash as the server. Truncating to a precision both sides can hold exactly is
    the only honest option - and the alternative, hashing microseconds and hoping
    the verifier's parser keeps them, would fail silently on entries posted at an
    unlucky moment.

    A naive datetime is rejected rather than assumed to be UTC. Guessing is how a
    5.5-hour shift enters a hash.
    """
    if value is None:
        return enc_u32(ABSENT)
    if value.tzinfo is None:
        raise BusinessRuleError(
            "A naive datetime cannot be canonically encoded - its timezone is a guess"
        )
    millis = int(value.astimezone(dt.UTC).timestamp() * 1000)
    return enc_u64(millis)


def money_minor(value: Decimal | int | str | None) -> int:
    """Convert a money amount to whole minor units, exactly.

    Raises rather than rounding. A value with more than
    :data:`MONEY_SCALE` decimal places cannot have come from the money column, so
    it is a bug somewhere upstream - and silently rounding it would put a figure
    on chain that does not match the figure in the books, which is the one thing
    this whole subsystem exists to prevent.

    ``None`` is zero: a nullable money column means "nothing", and nothing is
    ``0``, not absent. This is the one place a null collapses, and it is safe
    because no money column in the ledger distinguishes the two.
    """
    if value is None:
        return 0
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation as exc:
        raise BusinessRuleError(f"{value!r} is not a decimal amount") from exc

    scaled = amount.scaleb(MONEY_SCALE)
    whole = scaled.to_integral_value()
    if scaled != whole:
        raise BusinessRuleError(
            f"Amount {amount} carries more than {MONEY_SCALE} decimal places and "
            "cannot be encoded without loss"
        )
    return int(whole)


def enc_money(value: Decimal | int | str | None) -> bytes:
    """Money as an ``i128`` of minor units."""
    return enc_int(money_minor(value))


# =============================================================================
# The entry encoding
# =============================================================================
#: The exact fields hashed into a journal-entry leaf, in the exact order.
#:
#: **Hand-written, and the order is part of the contract.** Reordering two lines
#: here changes every hash. Adding one changes every hash. Both need a new
#: :data:`CANONICAL_VERSION`.
#:
#: Each entry is ``(name, encoder-key)``; the encoder is resolved in
#: :func:`encode_entry` so this stays a plain data declaration that can be read,
#: diffed, and compared against the TypeScript twin without following any code.
FIELD_ORDER: Final[tuple[tuple[str, str], ...]] = (
    ("organization_id", "uuid"),
    ("entry_id", "uuid"),
    ("entry_number", "str"),
    ("entry_date", "date"),
    ("currency", "str"),
    # `status` is deliberately **not** hashed, and this is the most important
    # omission in the file.
    #
    # A leaf commits to *what was recorded*: the entry number, the date, the
    # amounts, the accounts, the narration. An entry's status is not a fact about
    # what was recorded - it is a fact about what happened to it **later**.
    #
    # Hashing it was a real bug, found by the test that reverses a sealed entry.
    # This ledger corrects by reversal, so `posted` becoming `reversed` is the
    # normal, correct, expected path for any entry - and with `status` in the hash,
    # taking that path silently invalidated the entry's own proof. A business would
    # have sealed its March books, issued a credit note in May, and discovered that
    # its March invoice no longer verified. The subsystem would have accused it of
    # tampering for doing the right thing.
    #
    # Nothing is lost. A reversal is itself a journal entry: it gets its own leaf,
    # lands in a later batch, and is sealed in its turn. The fact that entry A was
    # reversed is recorded on chain as the existence of mirror entry B, whose
    # `reverses_id` points at A - which is committed to, below. So the reversal is
    # still provable; it is just proved by the reversal's own leaf rather than by
    # retroactively editing the original's.
    ("total_debit", "money"),
    ("total_credit", "money"),
    ("narration", "str"),
    ("reference", "str"),
    ("counterparty", "str"),
    ("source_type", "str"),
    ("source_id", "uuid"),
    ("reverses_id", "uuid"),
    ("posted_at", "instant"),
)

#: The fields of one line, in order. Lines are hashed in ``line_number`` order.
LINE_FIELD_ORDER: Final[tuple[tuple[str, str], ...]] = (
    ("line_number", "u32"),
    ("account_id", "uuid"),
    ("debit", "money"),
    ("credit", "money"),
    ("description", "str"),
)

_ENCODERS: Final[dict[str, Any]] = {
    "u32": enc_u32,
    "u64": enc_u64,
    "int": enc_int,
    "str": enc_str,
    "uuid": enc_uuid,
    "date": enc_date,
    "instant": enc_instant,
    "money": enc_money,
}


class EntryPayload(dict[str, Any]):
    """A plain mapping of the canonical fields of one entry, plus its lines.

    A ``dict`` subclass rather than a dataclass or a Pydantic model, and that is
    deliberate: this is the object that gets serialised into a proof bundle, sent
    to a verifier's browser, and re-encoded there. Keeping it a mapping means the
    JSON the verifier receives is structurally identical to what the server
    hashed, with no field renaming, no alias, and no ``model_dump`` behaviour
    sitting between the two.
    """


def payload_from_entry(entry: Any) -> EntryPayload:
    """Extract the canonical payload from a :class:`JournalEntry` ORM object.

    Reads only the attributes named in :data:`FIELD_ORDER` and
    :data:`LINE_FIELD_ORDER`, so an unrelated column added to the table later
    cannot drift into the hash.

    Lines are sorted here by ``line_number`` rather than trusted to arrive in
    order. The relationship declares an ``order_by``, but this function is also
    handed reconstructed payloads from a proof bundle, and a bundle's line order
    is whatever the JSON happened to hold.
    """
    lines = sorted(entry.lines, key=lambda line: line.line_number)

    payload = EntryPayload(
        organization_id=entry.organization_id,
        entry_id=entry.id,
        entry_number=entry.entry_number,
        entry_date=entry.entry_date,
        currency=entry.currency,
        total_debit=entry.total_debit,
        total_credit=entry.total_credit,
        narration=entry.narration,
        reference=entry.reference,
        counterparty=entry.counterparty,
        source_type=entry.source_type,
        source_id=entry.source_id,
        reverses_id=entry.reverses_id,
        posted_at=entry.posted_at,
        lines=[
            {
                "line_number": line.line_number,
                "account_id": line.account_id,
                "debit": line.debit,
                "credit": line.credit,
                "description": line.description,
            }
            for line in lines
        ],
    )
    return payload


def _coerce(kind: str, value: Any) -> Any:
    """Bring a JSON-shaped value back to the type its encoder expects.

    The server hashes straight from the ORM, where a UUID is a
    :class:`uuid.UUID` and a date is a :class:`datetime.date`. A payload that has
    been through a proof bundle carries strings. Both must reach the same bytes,
    so the round trip is normalised here rather than in each encoder - one place
    to read, and no encoder that quietly accepts two representations.
    """
    if value is None:
        return None
    if kind == "uuid" and not isinstance(value, uuid.UUID):
        return uuid.UUID(str(value))
    if kind == "date" and not isinstance(value, dt.date):
        return dt.date.fromisoformat(str(value))
    if kind == "instant" and not isinstance(value, dt.datetime):
        text = str(value)
        # `fromisoformat` in 3.11+ handles a trailing `Z`, but a value that has
        # been through JavaScript's `toISOString` always has one, and being
        # explicit costs nothing.
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise BusinessRuleError(f"Timestamp {text!r} carries no timezone")
        return parsed
    if kind == "money" and not isinstance(value, Decimal):
        return Decimal(str(value))
    if kind in ("u32", "u64", "int") and not isinstance(value, int):
        return int(value)
    return value


def encode_entry(payload: EntryPayload | dict[str, Any]) -> bytes:
    """The canonical bytes of one journal entry.

    Structure, in order::

        version    u8      = CANONICAL_VERSION
        kind       str     = KIND_JOURNAL_ENTRY
        <fields>           in FIELD_ORDER
        line_count u32
        <lines>            each in LINE_FIELD_ORDER, ordered by line_number

    The line count is encoded before the lines even though every field inside
    them is length-prefixed. Without it, an entry with two lines and an entry
    whose second line happened to encode as the concatenation of two others could
    not be distinguished - and prefixing the count is one cheap byte against a
    whole class of ambiguity.
    """
    parts: list[bytes] = [
        bytes([CANONICAL_VERSION]),
        enc_str(KIND_JOURNAL_ENTRY),
    ]

    for name, kind in FIELD_ORDER:
        if name not in payload:
            raise BusinessRuleError(f"Canonical payload is missing {name!r}")
        parts.append(_ENCODERS[kind](_coerce(kind, payload[name])))

    raw_lines = payload.get("lines") or []
    lines: Sequence[dict[str, Any]] = sorted(raw_lines, key=lambda line: int(line["line_number"]))
    if not lines:
        raise BusinessRuleError("A journal entry with no lines cannot be sealed")

    parts.append(enc_u32(len(lines)))
    for line in lines:
        for name, kind in LINE_FIELD_ORDER:
            if name not in line:
                raise BusinessRuleError(f"Canonical line payload is missing {name!r}")
            parts.append(_ENCODERS[kind](_coerce(kind, line[name])))

    return b"".join(parts)


def leaf_hash(payload: EntryPayload | dict[str, Any]) -> bytes:
    """The 32-byte Merkle leaf for one entry: ``SHA-256(0x00 ‖ canonical)``.

    The ``0x00`` prefix is the leaf half of the domain separation described in
    the module docstring; :func:`app.modules.attestation.merkle.node_hash` owns
    the ``0x01`` half.
    """
    return hashlib.sha256(LEAF_PREFIX + encode_entry(payload)).digest()


def leaf_hash_hex(payload: EntryPayload | dict[str, Any]) -> str:
    """:func:`leaf_hash` as lowercase hex, which is how it is stored and sent."""
    return leaf_hash(payload).hex()


# =============================================================================
# Serialisation for a proof bundle
# =============================================================================
def payload_to_json(payload: EntryPayload | dict[str, Any]) -> dict[str, Any]:
    """Render a payload as JSON-safe values for a proof bundle.

    Money becomes a **string**, never a JSON number. A JSON number is a double in
    every JavaScript client, so ``1234567.89`` would arrive in the verifier's
    browser as ``1234567.8899999999`` and hash to something the chain has never
    seen. This is the same rule the rest of the API follows for money, and here
    it is not a nicety - it is the difference between a proof that verifies and
    one that does not.
    """

    def render(kind: str, value: Any) -> Any:
        if value is None:
            return None
        if kind == "money":
            # Fixed to the money scale so the string is unambiguous, and so a
            # verifier reading `100.0000` cannot wonder whether trailing zeroes
            # were significant.
            return f"{Decimal(str(value)):.{MONEY_SCALE}f}"
        if kind == "uuid":
            return str(value)
        if kind == "date":
            return value.isoformat() if isinstance(value, dt.date) else str(value)
        if kind == "instant":
            if isinstance(value, dt.datetime):
                return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
            return str(value)
        if kind in ("u32", "u64", "int"):
            return int(value)
        return value

    out: dict[str, Any] = {name: render(kind, payload.get(name)) for name, kind in FIELD_ORDER}
    out["lines"] = [
        {name: render(kind, line.get(name)) for name, kind in LINE_FIELD_ORDER}
        for line in sorted(payload.get("lines") or [], key=lambda line: int(line["line_number"]))
    ]
    return out


#: Everything a re-implementation needs, exported so the verifier's TypeScript and
#: this module can be diffed against one shared declaration rather than two
#: hand-kept lists.
#:
#: Served by ``GET /attestation/spec`` so the browser bundle can assert at runtime
#: that it agrees with the server it is *not* trusting - a mismatch means one side
#: was deployed without the other, which would otherwise show up as every proof
#: failing for no visible reason.
CANONICAL_SPEC: Final[dict[str, Any]] = {
    "version": CANONICAL_VERSION,
    "kind": KIND_JOURNAL_ENTRY,
    "money_scale": MONEY_SCALE,
    "int_width": INT_WIDTH,
    "absent": ABSENT,
    "leaf_prefix": LEAF_PREFIX.hex(),
    "node_prefix": "01",
    "hash": "sha256",
    "merkle": "rfc6962",
    "fields": [{"name": name, "type": kind} for name, kind in FIELD_ORDER],
    "line_fields": [{"name": name, "type": kind} for name, kind in LINE_FIELD_ORDER],
}
