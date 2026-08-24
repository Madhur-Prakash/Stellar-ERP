"""The Merkle tree - RFC 6962, so a business can prove one invoice without
revealing the other four hundred.

A single hash over a whole period would be cheaper and useless. It would let a
business prove "these are all my March entries" only by handing over all of
them, which is precisely the disclosure the design exists to avoid. A tree lets
one entry be proven with the entry itself plus about ``log2(n)`` sibling hashes,
and the siblings are opaque - a verifier learns that the other entries exist and
nothing whatsoever about what they say.

**RFC 6962, not the Bitcoin construction.** The obvious tree duplicates the last
node when a level has an odd count, and that is a real flaw: two different leaf
lists can produce the same root, so a proof for one can be presented as a proof
for the other. RFC 6962 instead splits at the largest power of two below ``n``,
which is unambiguous for every ``n``. It is also the construction behind
Certificate Transparency, so it is specified precisely enough that two
independent implementations - the Python here and the TypeScript in the verifier -
can be checked against each other rather than against each other's bugs.

**Domain separation.** A leaf is ``SHA-256(0x00 ‖ data)`` and an interior node is
``SHA-256(0x01 ‖ left ‖ right)``. Without the prefixes, an attacker could present
an interior node's 64-byte preimage as leaf data and construct a second preimage
for the root for free. The leaf half lives in
:mod:`app.modules.attestation.canonical`, next to the encoding it prefixes; this
module owns the node half.

This module is pure: no database, no network, no ORM. It takes lists of 32-byte
digests and returns 32-byte digests, which is what makes it exhaustively testable
and what lets the same logic be re-derived in the browser.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Final, Literal, TypedDict

from app.core.exceptions import BusinessRuleError

#: Domain-separation prefix for an interior node.
NODE_PREFIX: Final = b"\x01"

#: Length of every digest this module handles.
DIGEST_BYTES: Final = 32


class ProofStep(TypedDict):
    """One sibling on the path from a leaf to the root.

    ``side`` says where the sibling sits, which the verifier needs in order to
    concatenate in the right order - ``H(0x01 ‖ sibling ‖ acc)`` when the sibling
    is on the left, ``H(0x01 ‖ acc ‖ sibling)`` when it is on the right. Encoding
    the side explicitly rather than deriving it from the leaf index is what lets a
    verifier check a path without being told how large the tree was.
    """

    side: Literal["left", "right"]
    hash: str


def node_hash(left: bytes, right: bytes) -> bytes:
    """Combine two child digests into their parent."""
    return hashlib.sha256(NODE_PREFIX + left + right).digest()


def _largest_power_of_two_below(n: int) -> int:
    """The largest power of two strictly less than ``n``, for ``n >= 2``.

    RFC 6962's split point. ``1 << (n - 1).bit_length() - 1`` is the same thing
    without a loop: for ``n = 5``, ``(4).bit_length()`` is 3, so ``1 << 2 == 4``.
    """
    if n < 2:  # pragma: no cover - callers guard this
        raise BusinessRuleError("A split point is only defined for two or more leaves")
    return 1 << ((n - 1).bit_length() - 1)


def _validate(leaves: Sequence[bytes]) -> None:
    if not leaves:
        # The contract refuses a seal covering no entries, so an empty tree can
        # only arise from a bug here. Failing loudly beats returning
        # `SHA-256("")`, which is a perfectly valid-looking 32 bytes that would
        # sail onto the chain as a root committing to nothing.
        raise BusinessRuleError("A Merkle tree needs at least one leaf")
    for index, leaf in enumerate(leaves):
        if len(leaf) != DIGEST_BYTES:
            raise BusinessRuleError(
                f"Leaf {index} is {len(leaf)} bytes; every leaf must be {DIGEST_BYTES}"
            )


def merkle_root(leaves: Sequence[bytes]) -> bytes:
    """The root over ``leaves``, which are already leaf hashes.

    ``MTH`` from RFC 6962 section 2.1, with the leaf hashing already applied by
    :func:`app.modules.attestation.canonical.leaf_hash`::

        MTH({h})    = h
        MTH(D[n])   = H(0x01 ‖ MTH(D[0:k]) ‖ MTH(D[k:n])),  k = largest 2^x < n

    Note what this is *not*: a plain pairwise fold over successive levels. That
    is the Bitcoin shape, and it disagrees with RFC 6962 for every ``n`` that is
    not a power of two - so an implementation that folded pairwise would produce
    roots the verifier's browser could never reproduce, and the disagreement
    would only show up for periods with an odd number of entries.

    Because the split is at the largest power of two below ``n``, the left
    subtree is always perfect and only the right one is ragged. That makes the
    left side a straight fold (:func:`_perfect_root`) and only the right side
    recursive - so the recursion depth is bounded by ``log2(n)``, not by ``n``.
    """
    _validate(leaves)

    if len(leaves) == 1:
        return leaves[0]

    split = _largest_power_of_two_below(len(leaves))
    return node_hash(_perfect_root(leaves[:split]), merkle_root(leaves[split:]))


def _perfect_root(leaves: Sequence[bytes]) -> bytes:
    """Root of a slice whose length is a power of two - a plain pairwise fold.

    Safe to fold pairwise here, and only here: with a power-of-two count every
    level is even, so the ambiguity RFC 6962's split rule exists to remove cannot
    arise.
    """
    level = list(leaves)
    while len(level) > 1:
        level = [node_hash(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def inclusion_proof(leaves: Sequence[bytes], index: int) -> list[ProofStep]:
    """The sibling path from ``leaves[index]`` to the root, innermost first.

    Innermost first because that is the order a verifier consumes them: start
    with the leaf, fold in the nearest sibling, then the next, and finish holding
    the root. Returning them root-first would force every verifier to reverse the
    list, and one that forgot would produce a wrong answer rather than an error.
    """
    _validate(leaves)
    if not 0 <= index < len(leaves):
        raise BusinessRuleError(f"Leaf index {index} is outside a tree of {len(leaves)} leaves")

    # Walking top-down means the first sibling discovered is the *outermost* one -
    # the last a verifier folds in. RFC 6962's PATH is defined the other way
    # round (`PATH(m, D[0:k]) : MTH(D[k:n])` puts the recursive result first), so
    # the list is reversed before returning.
    #
    # This is not a stylistic detail. A path in the wrong order still has the
    # right length and the right hashes, so it looks correct in a debugger and
    # fails only at the final comparison - which reads as "the books were
    # altered". Getting it backwards is the single easiest way to make this
    # subsystem accuse an honest business of fraud.
    outermost_first: list[ProofStep] = []
    working = list(leaves)
    position = index

    while len(working) > 1:
        split = _largest_power_of_two_below(len(working))
        if position < split:
            # The leaf is in the left subtree, so its sibling subtree is on the right.
            outermost_first.append({"side": "right", "hash": merkle_root(working[split:]).hex()})
            working = working[:split]
        else:
            outermost_first.append({"side": "left", "hash": merkle_root(working[:split]).hex()})
            working = working[split:]
            position -= split

    outermost_first.reverse()
    return outermost_first


def verify_inclusion(leaf: bytes, path: Sequence[ProofStep], root: bytes) -> bool:
    """Whether folding ``leaf`` through ``path`` reproduces ``root``.

    The server's copy of what the verifier's browser does. It exists here so the
    test suite can assert that the two agree on the same vectors, and so the
    export endpoint can refuse to hand out a bundle it has not itself checked -
    shipping a proof that does not verify would be worse than shipping none,
    because the business would learn it was broken from the bank rather than from
    us.
    """
    if len(leaf) != DIGEST_BYTES or len(root) != DIGEST_BYTES:
        return False

    acc = leaf
    for step in path:
        try:
            sibling = bytes.fromhex(str(step["hash"]))
        except (ValueError, KeyError, TypeError):
            return False
        if len(sibling) != DIGEST_BYTES:
            return False

        # `str(...)` rather than trusting the declared `Literal`. The annotation
        # describes what this module *produces*; this function's whole job is to
        # check what somebody else *sent*, which arrives as JSON and can hold
        # anything. Narrowing on the Literal would let mypy prove the final branch
        # unreachable - and it is very much reachable from a hand-edited bundle.
        side = str(step.get("side", ""))
        if side == "left":
            acc = node_hash(sibling, acc)
        elif side == "right":
            acc = node_hash(acc, sibling)
        else:
            return False
    return acc == root


def tree_depth(count: int) -> int:
    """Number of hashes on the longest path, for display and cost estimates.

    Shown on the Trust screen next to a period, because "one invoice proves with
    9 sibling hashes" is a more meaningful statement about disclosure than
    "412 entries sealed".
    """
    if count <= 1:
        return 0
    return (count - 1).bit_length()
