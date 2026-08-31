"""The canonical encoding and the Merkle tree.

Pure unit tests - no database, no chain, no fixtures. These two modules are the
only part of the proof ledger that can silently invalidate *every proof ever
issued*, so they are tested harder than anything else in the subsystem and they
are tested in isolation.

Read the golden-vector test first. It is the one that matters.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.core.exceptions import BusinessRuleError
from app.modules.attestation import canonical as c
from app.modules.attestation import merkle as m

# =============================================================================
# A fixed entry, and the hash it must always produce
# =============================================================================
#: Deliberately exercises every awkward case at once: a null field
#: (``counterparty``), an empty-string field (``reference``), a value with
#: trailing zeroes (``100.0000``), a non-ASCII narration, a fractional amount,
#: three lines so the line count matters, and an aware timestamp.
GOLDEN_ENTRY: dict[str, Any] = {
    "organization_id": uuid.UUID("0192f3a4-5b6c-7d8e-9f01-234567890abc"),
    "entry_id": uuid.UUID("0192f3a4-5b6c-7d8e-9f01-234567890def"),
    "entry_number": "JV-2026-27-0001",
    "entry_date": dt.date(2026, 3, 31),
    "currency": "INR",
    "total_debit": Decimal("100.0000"),
    "total_credit": Decimal("100.0000"),
    "narration": "Sale to Sharma & Sons - ₹100",
    "reference": "",
    "counterparty": None,
    "source_type": "invoice",
    "source_id": uuid.UUID("0192f3a4-5b6c-7d8e-9f01-2345678901ab"),
    "reverses_id": None,
    "posted_at": dt.datetime(2026, 3, 31, 9, 15, 30, tzinfo=dt.UTC),
    "lines": [
        {
            "line_number": 1,
            "account_id": uuid.UUID("0192f3a4-5b6c-7d8e-9f01-100000000001"),
            "debit": Decimal("100.0000"),
            "credit": Decimal("0.0000"),
            "description": "Receivable",
        },
        {
            "line_number": 2,
            "account_id": uuid.UUID("0192f3a4-5b6c-7d8e-9f01-200000000002"),
            "debit": Decimal("0.0000"),
            "credit": Decimal("84.7500"),
            "description": None,
        },
        {
            "line_number": 3,
            "account_id": uuid.UUID("0192f3a4-5b6c-7d8e-9f01-300000000003"),
            "debit": Decimal("0.0000"),
            "credit": Decimal("15.2500"),
            "description": "IGST 18%",
        },
    ],
}

#: The leaf hash of :data:`GOLDEN_ENTRY`.
#:
#: ############################################################################
#: DO NOT UPDATE THIS VALUE TO MAKE A FAILING TEST PASS.
#:
#: If this assertion fails, the canonical encoding has changed - and every proof
#: ever issued against every seal already on chain has just become unverifiable.
#: The encoding is append-only: a new field, a new money scale, a reordering all
#: require a new `CANONICAL_VERSION` and a new golden vector *alongside* this one,
#: never a replacement for it.
#:
#: The only legitimate reason to change this line is if it was wrong on the day it
#: was written, before any seal existed.
#: ############################################################################
GOLDEN_LEAF = "6d86fbb3e6bbd2357897b39bf872145d2abf2c1c3e27448a4cf2f4d80600b605"

#: Length of the encoding of :data:`GOLDEN_ENTRY`, pinned alongside the hash.
#:
#: A second, coarser tripwire. If a field is dropped from the encoding *and* the
#: hash is carelessly updated to match, this still fails - and 412 bytes is a
#: number somebody has to think about before overwriting.
GOLDEN_LENGTH = 412


class TestGoldenVector:
    """The hash of a known entry, pinned forever."""

    def test_the_encoding_is_stable(self) -> None:
        """A change to `canonical.py` that alters this hash must fail the build.

        This is the test the whole subsystem's credibility rests on. Read the
        comment above `GOLDEN_LEAF` before touching it.
        """
        assert c.leaf_hash_hex(GOLDEN_ENTRY) == GOLDEN_LEAF

    def test_the_encoded_length_is_stable(self) -> None:
        """A coarser tripwire than the hash, and it catches a different mistake:
        a field dropped from the encoding while the hash was updated to match."""
        assert len(c.encode_entry(GOLDEN_ENTRY)) == GOLDEN_LENGTH

    def test_hashing_is_deterministic_across_calls(self) -> None:
        first = c.leaf_hash_hex(GOLDEN_ENTRY)
        second = c.leaf_hash_hex(GOLDEN_ENTRY)
        assert first == second

    def test_the_version_byte_leads_the_encoding(self) -> None:
        """A verifier reads the version first to choose a decoder."""
        raw = c.encode_entry(GOLDEN_ENTRY)
        assert raw[0] == c.CANONICAL_VERSION

    def test_the_leaf_is_domain_separated(self) -> None:
        """`SHA-256(0x00 || canonical)`, not `SHA-256(canonical)`.

        Without the prefix an interior node's 64-byte preimage could be presented
        as leaf data, and a second preimage for the root would be free.
        """
        raw = c.encode_entry(GOLDEN_ENTRY)
        assert c.leaf_hash(GOLDEN_ENTRY) == hashlib.sha256(b"\x00" + raw).digest()
        assert c.leaf_hash(GOLDEN_ENTRY) != hashlib.sha256(raw).digest()

    def test_a_json_round_trip_reaches_the_same_hash(self) -> None:
        """The server hashes ORM objects; the browser hashes JSON. Same bytes.

        This is what makes a proof bundle verifiable by somebody who never saw the
        database: the payload is rendered to JSON, shipped, and re-encoded, and
        the hash has to survive the trip. If it did not, the TypeScript verifier
        could never agree with the Python that sealed it.
        """
        as_json = c.payload_to_json(GOLDEN_ENTRY)
        assert c.leaf_hash_hex(as_json) == GOLDEN_LEAF

    def test_money_survives_json_as_a_string(self) -> None:
        """Money is a string on the wire, and that is load-bearing here.

        A JSON number is a double in every JavaScript client, so `1234567.89`
        would arrive in the browser as `1234567.8899999999` and hash to something
        no chain has ever seen.
        """
        as_json = c.payload_to_json(GOLDEN_ENTRY)
        assert as_json["total_debit"] == "100.0000"
        assert isinstance(as_json["total_debit"], str)
        assert as_json["lines"][2]["credit"] == "15.2500"


# =============================================================================
# Sensitivity - every field must actually be committed to
# =============================================================================
class TestEveryFieldIsCommitted:
    """Changing any hashed field must change the hash.

    A field silently excluded from the encoding is the worst failure mode
    available: the proof still verifies, so nobody notices, and that field can be
    altered after sealing with no trace. So every one is checked.
    """

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("organization_id", uuid.UUID(int=99)),
            ("entry_id", uuid.UUID(int=98)),
            ("entry_number", "JV-2026-27-0002"),
            ("entry_date", dt.date(2026, 4, 1)),
            ("currency", "USD"),
            ("total_debit", Decimal("100.0001")),
            ("total_credit", Decimal("100.0001")),
            ("narration", "Sale to Sharma & Sons - ₹101"),
            ("reference", "CHQ-1"),
            ("counterparty", "Sharma & Sons"),
            ("source_type", "billing"),
            ("source_id", uuid.UUID(int=97)),
            ("reverses_id", uuid.UUID(int=96)),
            ("posted_at", dt.datetime(2026, 3, 31, 9, 15, 31, tzinfo=dt.UTC)),
        ],
    )
    def test_changing_a_header_field_changes_the_hash(self, field: str, value: Any) -> None:
        tampered = {**GOLDEN_ENTRY, field: value}
        assert c.leaf_hash_hex(tampered) != GOLDEN_LEAF

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("line_number", 9),
            ("account_id", uuid.UUID(int=95)),
            ("debit", Decimal("100.0001")),
            ("credit", Decimal("0.0001")),
            ("description", "Something else"),
        ],
    )
    def test_changing_a_line_field_changes_the_hash(self, field: str, value: Any) -> None:
        lines = [dict(line) for line in GOLDEN_ENTRY["lines"]]
        lines[0][field] = value
        tampered = {**GOLDEN_ENTRY, "lines": lines}
        assert c.leaf_hash_hex(tampered) != GOLDEN_LEAF

    def test_null_and_empty_string_are_different(self) -> None:
        """The single most likely encoding bug, and the reason for the ABSENT sentinel.

        `reference=""` and `reference=None` must not collide, or two entries that
        differ only in whether a field was filled in would share a leaf - and
        "was this reference blank or was it never entered?" is exactly the sort of
        thing a dispute turns on.
        """
        empty = {**GOLDEN_ENTRY, "reference": ""}
        absent = {**GOLDEN_ENTRY, "reference": None}
        assert c.leaf_hash_hex(empty) != c.leaf_hash_hex(absent)

    def test_reordering_lines_does_not_change_the_hash(self) -> None:
        """Lines are sorted by `line_number` before hashing.

        The encoder is handed payloads from two places - the ORM, where a
        relationship's `order_by` guarantees the order, and a proof bundle, where
        the order is whatever the JSON held. Both must reach the same hash.
        """
        reversed_lines = list(reversed(GOLDEN_ENTRY["lines"]))
        shuffled = {**GOLDEN_ENTRY, "lines": reversed_lines}
        assert c.leaf_hash_hex(shuffled) == GOLDEN_LEAF

    def test_dropping_a_line_changes_the_hash(self) -> None:
        fewer = {**GOLDEN_ENTRY, "lines": GOLDEN_ENTRY["lines"][:2]}
        assert c.leaf_hash_hex(fewer) != GOLDEN_LEAF

    def test_the_line_count_is_committed_separately(self) -> None:
        """Prefixing the count removes a class of ambiguity for one byte.

        Without it, an entry with two lines and an entry whose second line
        happened to encode as the concatenation of two others could not be
        distinguished.
        """
        raw = c.encode_entry(GOLDEN_ENTRY)
        assert c.enc_u32(len(GOLDEN_ENTRY["lines"])) in raw


# =============================================================================
# Money
# =============================================================================
class TestMoneyEncoding:
    """Money is an integer count of minor units, exactly."""

    @pytest.mark.parametrize(
        ("amount", "minor"),
        [
            ("0", 0),
            ("0.0000", 0),
            ("1", 10_000),
            ("1.0000", 10_000),
            ("100.00", 1_000_000),
            ("100.0000", 1_000_000),
            ("0.0001", 1),
            ("-42.5000", -425_000),
            ("99999999999999.9999", 999_999_999_999_999_999),
        ],
    )
    def test_scaling_is_exact(self, amount: str, minor: int) -> None:
        assert c.money_minor(Decimal(amount)) == minor

    def test_trailing_zeroes_do_not_change_the_encoding(self) -> None:
        """`Decimal("100.00")` and `Decimal("100.0000")` are the same number.

        They are different *strings*, and a round trip through the database can
        change which one you are holding - which is precisely why no decimal
        string appears anywhere in the hash input.
        """
        assert c.enc_money(Decimal("100.00")) == c.enc_money(Decimal("100.0000"))
        assert c.enc_money(Decimal("100")) == c.enc_money(Decimal("100.0000"))

    def test_none_is_zero(self) -> None:
        assert c.money_minor(None) == 0

    def test_more_precision_than_the_column_holds_is_refused(self) -> None:
        """Rounding here would put a figure on chain that is not the figure in the
        books, which is the one thing this subsystem exists to prevent."""
        with pytest.raises(BusinessRuleError, match="decimal places"):
            c.money_minor(Decimal("1.00001"))

    def test_encoded_width_is_fixed(self) -> None:
        """Fixed width, so `1` cannot be encoded as one byte by one implementation
        and sixteen by another."""
        assert len(c.enc_money(Decimal("0"))) == c.INT_WIDTH
        assert len(c.enc_money(Decimal("-1"))) == c.INT_WIDTH
        assert len(c.enc_money(Decimal("99999999999999.9999"))) == c.INT_WIDTH

    def test_negatives_are_twos_complement(self) -> None:
        assert c.enc_int(-1) == b"\xff" * 16
        assert c.enc_int(0) == b"\x00" * 16


class TestTimestampEncoding:
    def test_a_naive_datetime_is_refused(self) -> None:
        """Guessing a timezone is how a 5.5-hour shift enters a hash."""
        with pytest.raises(BusinessRuleError, match="timezone"):
            c.enc_instant(dt.datetime(2026, 3, 31, 9, 15, 30))

    def test_equivalent_instants_in_different_zones_agree(self) -> None:
        utc = dt.datetime(2026, 3, 31, 9, 15, 30, tzinfo=dt.UTC)
        ist = utc.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30)))
        assert c.enc_instant(utc) == c.enc_instant(ist)

    def test_sub_millisecond_precision_is_truncated(self) -> None:
        """Milliseconds, because a JavaScript `Date` cannot hold microseconds and
        the browser has to reach the same hash as the server."""
        a = dt.datetime(2026, 3, 31, 9, 15, 30, 123_000, tzinfo=dt.UTC)
        b = dt.datetime(2026, 3, 31, 9, 15, 30, 123_400, tzinfo=dt.UTC)
        assert c.enc_instant(a) == c.enc_instant(b)


class TestStructuralGuards:
    def test_a_missing_field_is_refused(self) -> None:
        """Silently defaulting a missing field would let two different entries
        share a leaf."""
        incomplete = {k: v for k, v in GOLDEN_ENTRY.items() if k != "currency"}
        with pytest.raises(BusinessRuleError, match="currency"):
            c.encode_entry(incomplete)

    def test_an_entry_with_no_lines_is_refused(self) -> None:
        with pytest.raises(BusinessRuleError, match="no lines"):
            c.encode_entry({**GOLDEN_ENTRY, "lines": []})

    def test_the_field_order_is_not_derived_from_anything(self) -> None:
        """The order is part of the contract, so it is asserted literally.

        If a migration adds a column and somebody adds it to `FIELD_ORDER`
        without bumping `CANONICAL_VERSION`, this fails - which is the point.
        """
        assert [name for name, _ in c.FIELD_ORDER] == [
            "organization_id",
            "entry_id",
            "entry_number",
            "entry_date",
            "currency",
            "total_debit",
            "total_credit",
            "narration",
            "reference",
            "counterparty",
            "source_type",
            "source_id",
            "reverses_id",
            "posted_at",
        ]
        assert [name for name, _ in c.LINE_FIELD_ORDER] == [
            "line_number",
            "account_id",
            "debit",
            "credit",
            "description",
        ]

    def test_the_published_spec_matches_the_implementation(self) -> None:
        """`/attestation/spec` is what the browser checks itself against.

        A spec that drifted from the code would make the verifier reject every
        proof with no visible cause.
        """
        spec = c.CANONICAL_SPEC
        assert spec["version"] == c.CANONICAL_VERSION
        assert spec["money_scale"] == c.MONEY_SCALE
        assert spec["leaf_prefix"] == "00"
        assert spec["node_prefix"] == m.NODE_PREFIX.hex()
        assert spec["hash"] == "sha256"
        assert spec["merkle"] == "rfc6962"
        assert [f["name"] for f in spec["fields"]] == [n for n, _ in c.FIELD_ORDER]
        assert [f["name"] for f in spec["line_fields"]] == [n for n, _ in c.LINE_FIELD_ORDER]


# =============================================================================
# The Merkle tree
# =============================================================================
def leaf(index: int) -> bytes:
    """A distinct 32-byte digest per index."""
    return hashlib.sha256(b"\x00" + index.to_bytes(4, "big")).digest()


def reference_mth(leaves: list[bytes]) -> bytes:
    """RFC 6962 section 2.1, written independently of the implementation.

    A second implementation rather than a table of expected values: the property
    that matters is agreement with the specification for *every* leaf count, and
    the ones that disagree are the counts nobody thinks to tabulate.
    """
    if len(leaves) == 1:
        return leaves[0]
    split = 1
    while split * 2 < len(leaves):
        split *= 2
    return hashlib.sha256(
        b"\x01" + reference_mth(leaves[:split]) + reference_mth(leaves[split:])
    ).digest()


def reference_path(index: int, leaves: list[bytes]) -> list[tuple[str, str]]:
    """RFC 6962 ``PATH``, also written independently.

    Note the order: the recursive result comes *first*, so the innermost sibling
    leads. Getting this backwards produces a path of the right length with the
    right hashes that fails only at the final comparison - which reads as "the
    books were altered". It is the single easiest way to make this subsystem
    accuse an honest business of fraud.
    """
    if len(leaves) == 1:
        return []
    split = 1
    while split * 2 < len(leaves):
        split *= 2
    if index < split:
        return [
            *reference_path(index, leaves[:split]),
            ("right", reference_mth(leaves[split:]).hex()),
        ]
    return [
        *reference_path(index - split, leaves[split:]),
        ("left", reference_mth(leaves[:split]).hex()),
    ]


class TestMerkleTree:
    @pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 7, 8, 9, 16, 17, 31, 32, 33, 64, 100])
    def test_root_matches_an_independent_rfc6962_implementation(self, count: int) -> None:
        leaves = [leaf(i) for i in range(count)]
        assert m.merkle_root(leaves) == reference_mth(leaves)

    @pytest.mark.parametrize("count", [1, 2, 3, 5, 8, 13, 17, 32, 33])
    def test_paths_match_an_independent_rfc6962_implementation(self, count: int) -> None:
        leaves = [leaf(i) for i in range(count)]
        for index in range(count):
            mine = [(s["side"], s["hash"]) for s in m.inclusion_proof(leaves, index)]
            assert mine == reference_path(index, leaves)

    @pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 7, 8, 9, 15, 16, 17, 40])
    def test_every_leaf_proves_inclusion(self, count: int) -> None:
        leaves = [leaf(i) for i in range(count)]
        root = m.merkle_root(leaves)
        for index in range(count):
            path = m.inclusion_proof(leaves, index)
            assert m.verify_inclusion(leaves[index], path, root), (
                f"leaf {index} of {count} failed to verify"
            )

    def test_it_is_not_the_bitcoin_construction(self) -> None:
        """RFC 6962 splits at the largest power of two; Bitcoin duplicates the last
        node on an odd level. They disagree for every count that is not a power of
        two, and a pairwise fold here would produce roots the browser could never
        reproduce - visibly only for periods with an odd number of entries.
        """
        leaves = [leaf(i) for i in range(3)]
        rfc = m.node_hash(m.node_hash(leaves[0], leaves[1]), leaves[2])
        bitcoin = m.node_hash(m.node_hash(leaves[0], leaves[1]), m.node_hash(leaves[2], leaves[2]))
        assert m.merkle_root(leaves) == rfc
        assert m.merkle_root(leaves) != bitcoin

    def test_nodes_are_domain_separated_from_leaves(self) -> None:
        left, right = leaf(0), leaf(1)
        assert m.node_hash(left, right) == hashlib.sha256(b"\x01" + left + right).digest()
        assert m.node_hash(left, right) != hashlib.sha256(left + right).digest()

    def test_a_single_leaf_is_its_own_root(self) -> None:
        assert m.merkle_root([leaf(0)]) == leaf(0)
        assert m.inclusion_proof([leaf(0)], 0) == []

    def test_order_matters(self) -> None:
        """Two entries swapped must produce a different root, or a business could
        reorder its books after sealing."""
        assert m.merkle_root([leaf(0), leaf(1)]) != m.merkle_root([leaf(1), leaf(0)])


class TestMerkleRefusals:
    def test_an_empty_tree_is_refused(self) -> None:
        """`SHA-256("")` is a perfectly valid-looking 32 bytes that would sail onto
        the chain as a root committing to nothing."""
        with pytest.raises(BusinessRuleError, match="at least one leaf"):
            m.merkle_root([])

    def test_a_wrong_length_leaf_is_refused(self) -> None:
        with pytest.raises(BusinessRuleError, match="bytes"):
            m.merkle_root([b"too short"])

    def test_an_out_of_range_index_is_refused(self) -> None:
        with pytest.raises(BusinessRuleError, match="outside a tree"):
            m.inclusion_proof([leaf(0), leaf(1)], 5)

    def test_a_forged_leaf_does_not_verify(self) -> None:
        leaves = [leaf(i) for i in range(8)]
        root = m.merkle_root(leaves)
        path = m.inclusion_proof(leaves, 3)
        assert not m.verify_inclusion(leaf(999), path, root)

    def test_flipping_a_side_does_not_verify(self) -> None:
        """`side` decides concatenation order, so an implementation that ignored it
        would verify paths it should reject."""
        leaves = [leaf(i) for i in range(8)]
        root = m.merkle_root(leaves)
        path = m.inclusion_proof(leaves, 3)
        flipped = [
            {"side": "left" if s["side"] == "right" else "right", "hash": s["hash"]} for s in path
        ]
        assert not m.verify_inclusion(leaves[3], flipped, root)

    def test_a_truncated_path_does_not_verify(self) -> None:
        leaves = [leaf(i) for i in range(8)]
        root = m.merkle_root(leaves)
        path = m.inclusion_proof(leaves, 3)
        assert not m.verify_inclusion(leaves[3], path[:-1], root)

    def test_a_malformed_path_step_is_rejected_not_raised(self) -> None:
        """A hand-edited bundle should show a red tick, not a stack trace."""
        leaves = [leaf(i) for i in range(4)]
        root = m.merkle_root(leaves)
        assert not m.verify_inclusion(leaves[0], [{"side": "up", "hash": "00"}], root)  # type: ignore[list-item]
        assert not m.verify_inclusion(leaves[0], [{"side": "left", "hash": "zz"}], root)

    def test_a_path_from_another_tree_does_not_verify(self) -> None:
        mine = [leaf(i) for i in range(8)]
        theirs = [leaf(i) for i in range(100, 108)]
        assert not m.verify_inclusion(mine[2], m.inclusion_proof(theirs, 2), m.merkle_root(mine))


class TestTreeDepth:
    @pytest.mark.parametrize(
        ("count", "depth"), [(1, 0), (2, 1), (3, 2), (4, 2), (5, 3), (8, 3), (9, 4), (412, 9)]
    )
    def test_depth_bounds_the_path_length(self, count: int, depth: int) -> None:
        """Shown on the Trust screen: "one invoice proves with 9 sibling hashes"
        says something meaningful about disclosure that "412 entries" does not."""
        assert m.tree_depth(count) == depth
        leaves = [leaf(i) for i in range(count)]
        for index in range(min(count, 20)):
            assert len(m.inclusion_proof(leaves, index)) <= depth


class TestSelectiveDisclosure:
    def test_a_proof_reveals_only_opaque_hashes(self) -> None:
        """The point of a tree rather than one hash over the period: a business can
        prove one invoice without revealing the other four hundred, and the
        siblings carry no information about them."""
        leaves = [leaf(i) for i in range(400)]
        path = m.inclusion_proof(leaves, 137)

        assert len(path) <= m.tree_depth(400)
        # Every sibling is a digest, and none of them is another leaf's plaintext.
        for step in path:
            assert len(bytes.fromhex(step["hash"])) == m.DIGEST_BYTES
        assert m.verify_inclusion(leaves[137], path, m.merkle_root(leaves))
