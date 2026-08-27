"""API contracts for the proof ledger.

Two conventions carried over from the rest of the API, both for the same reason -
**JavaScript's only numeric type is a double**:

* Money crosses the wire as a string. Here that applies to ``debit_minor``, which
  is a count of paise and can legitimately exceed 2^53 for a business's lifetime
  turnover.
* So do the 32-byte hashes, obviously, but also the on-chain ``ledger`` sequence,
  which is small today and has no reason to be a number the client does arithmetic
  on.

One convention specific to this module: **nothing here is optional out of
laziness.** A field is nullable only where the null means something a client has
to render differently - ``sealed_at`` is null while a seal is awaiting
confirmation, and showing a made-up time there would undermine the one claim the
whole subsystem makes.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator

from app.core.schemas import BaseSchema, ResponseSchema
from app.modules.attestation.models import SealCadence, SealStatus, SealTrigger

#: "The client did not mention a time", distinct from an explicit ``null`` meaning
#: "go back to the server's default". The two are genuinely different instructions,
#: and collapsing them would make it impossible to stop overriding once you had
#: started. -1 stands in, outside the 0-1439 a real value occupies.
UNSET_MINUTE = -1

#: ``HH:MM`` on a 24-hour clock. Anchored at both ends so ``"9:00am"`` is rejected
#: rather than half-parsed into something plausible and wrong.
TIME_OF_DAY = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def minute_of_day(text: str) -> int:
    """``"01:30"`` -> ``90``. Assumes :data:`TIME_OF_DAY` has already matched."""
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def time_of_day(minute: int) -> str:
    """``90`` -> ``"01:30"``. The inverse of :func:`minute_of_day`."""
    return f"{minute // 60:02d}:{minute % 60:02d}"

# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------
#: A 32-byte hash as lowercase hex. Constrained rather than a bare ``str`` so a
#: truncated root is refused at the edge instead of failing inside an XDR encoder.
Hex32 = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[0-9a-f]{64}$"),
]

#: A Stellar account - ``G`` followed by 55 base32 characters.
StellarAccount = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^G[A-Z2-7]{55}$"),
]

#: A Stellar secret seed. Never echoed back in any response in this module.
StellarSecret = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^S[A-Z2-7]{55}$"),
]


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class EnableAttestationRequest(BaseSchema):
    """Switch sealing on for this organization."""

    cadence: SealCadence = SealCadence.DAILY

    #: Bring your own signing account. Omit and one is generated.
    #:
    #: Write-only: it is encrypted on arrival and no response in this module ever
    #: returns it, not even redacted. A secret that appears in a response body
    #: appears in a browser's network log, and from there in a screenshot.
    secret_key: StellarSecret | None = None

    #: Ask Friendbot for testnet XLM. Ignored on mainnet, where there is no
    #: friendbot and pretending otherwise would leave an operator waiting for funds
    #: that were never coming.
    fund_on_testnet: bool = True


class SetCadenceRequest(BaseSchema):
    cadence: SealCadence

    #: ``HH:MM`` in the organization's own timezone - any minute of the day, not a
    #: shortlist of hours. The useful sealing time is whenever nobody is posting,
    #: and that is 01:00 for one business and 03:30 for another.
    #:
    #: Omitted leaves whatever is stored; ``null`` clears it back to the install's
    #: ``SEAL_DAILY_HOUR``. Told apart by ``model_fields_set`` rather than by a
    #: sentinel on the wire, so the API stays honest about what it accepts.
    seal_time: str | None = None

    @field_validator("seal_time")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not TIME_OF_DAY.match(text):
            raise ValueError("seal_time must be HH:MM on a 24-hour clock, for example 01:30")
        return text

    @property
    def seal_minute(self) -> int | None:
        """Minutes past midnight, ``None`` to clear, :data:`UNSET_MINUTE` if omitted."""
        if "seal_time" not in self.model_fields_set:
            return UNSET_MINUTE
        return None if self.seal_time is None else minute_of_day(self.seal_time)


class RotateSignerRequest(BaseSchema):
    """Hand the book to a different Stellar account - the 2-of-3 upgrade path."""

    #: The destination account. The contract requires it to authorise the rotation
    #: too, so this server's key must be one of its signers or the call is refused.
    new_admin: StellarAccount


class VerifyBundleRequest(BaseSchema):
    """Check a proof bundle.

    Unauthenticated, and the bundle arrives as a free-form object on purpose: it
    was produced by some other install of this software, possibly an older version,
    and validating it field-by-field here would reject bundles this endpoint could
    perfectly well check. The verifier reads ``format`` and refuses what it does
    not recognise.
    """

    bundle: dict[str, Any]

    #: Whether to confirm the root against the chain as well as internally.
    #:
    #: Default true, because an internally-consistent bundle that no chain has ever
    #: seen is worth nothing, and a caller who did not think about this should get
    #: the meaningful answer.
    check_chain: bool = True


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class SealRead(ResponseSchema):
    """One seal, as the Trust screen and the seal history show it."""

    id: uuid.UUID
    seq: int
    status: SealStatus
    trigger: SealTrigger

    merkle_root: str
    prev_root: str
    entry_count: int
    #: Total debits covered, in minor units, as a string. See the module docstring.
    debit_minor: str

    #: The posting-time window that went on chain. Tiles forward by construction.
    covered_from: dt.datetime
    covered_to: dt.datetime
    #: The accounting dates the batch touches. Local, for display - these do *not*
    #: tile, because a bill dated in March can arrive in April.
    entry_date_from: dt.date
    entry_date_to: dt.date

    network: str | None
    contract_id: str | None
    tx_hash: str | None
    ledger_sequence: str | None

    #: **The network's** timestamp, not ours. Null until the chain confirms, and
    #: the UI must say "awaiting confirmation" rather than invent one - this field
    #: is the entire basis of the claim that a business cannot back-date its books.
    sealed_at: dt.datetime | None
    submitted_at: dt.datetime | None
    confirmed_at: dt.datetime | None

    attempts: int
    last_error: str | None
    explorer_url: str | None

    #: Sibling hashes needed to prove one entry from this seal - ``log2(n)``.
    #: Shown because "one invoice proves with 9 hashes" says something meaningful
    #: about disclosure that "412 entries sealed" does not.
    tree_depth: int


class ChainRead(ResponseSchema):
    """What the contract says, independent of what we believe."""

    reachable: bool
    head: int | None = None
    root: str | None = None
    entries: int | None = None
    sealed_at: dt.datetime | None = None
    admin: str | None = None

    #: Null when there is nothing to compare yet. ``False`` is the condition worth
    #: shouting about: the chain and the database disagree, and the chain wins.
    agrees_with_local: bool | None = None
    error: str | None = None


class AttestationStatusRead(ResponseSchema):
    """Everything the Trust screen renders, in one response."""

    enabled: bool
    configured: bool
    ready: bool

    network: str | None
    contract_id: str | None
    contract_url: str | None
    org_namespace: str | None
    cadence: SealCadence

    #: The organization's chosen sealing time as ``HH:MM``, and the one actually in
    #: force. The second is never null, so a screen can state a time without having
    #: to know the server's configuration - and the first being null is worth
    #: showing, because "following the server default" is a real answer.
    seal_time: str | None
    effective_seal_time: str
    timezone: str

    signer_public_key: str | None
    #: True when the key is held outside this server. Distinguished from "not
    #: configured" because they look identical from a null column and mean opposite
    #: things about how much the seal proves.
    external_signer: bool
    registered_at: dt.datetime | None

    seals_confirmed: int
    entries_sealed: int
    unsealed_entries: int
    oldest_unsealed_at: dt.datetime | None
    #: Age of the oldest unsealed entry. The number that matters: a growing backlog
    #: is what sealing silently breaking looks like.
    days_unsealed: float | None

    last_seal: SealRead | None
    open_seal: SealRead | None
    chain: ChainRead

    #: Reasons to act, worst first, already written for a human to read.
    warnings: list[str] = Field(default_factory=list)


class SealPage(ResponseSchema):
    items: list[SealRead]
    next_cursor: str | None = None
    has_more: bool = False

    #: Whether every seal returned chains to its predecessor. Asserted server-side
    #: rather than left to the client: a break in the chain is the single most
    #: important thing this list can report, and three clients computing it three
    #: ways would eventually disagree.
    continuous: bool = True


class ProofBundleRead(ResponseSchema):
    """A proof bundle.

    Returned as an opaque object rather than a typed model. The bundle's shape is
    defined by its own ``format`` tag and is read by third-party verifiers,
    including ones written against a future version - describing it twice, here and
    in the builder, would give two answers to the question "what is in a bundle?".
    """

    bundle: dict[str, Any]


class VerifyResultRead(ResponseSchema):
    valid: bool
    reason: str
    leaf_hash: str | None = None
    computed_root: str | None = None
    expected_root: str | None = None
    on_chain_root: str | None = None
    seal_seq: int | None = None
    #: Whether the chain was actually consulted. A ``valid`` that did not check the
    #: chain means only "internally consistent", and the difference matters enough
    #: to be its own field rather than a footnote in ``reason``.
    chain_checked: bool = False


class PublicSealRead(ResponseSchema):
    """A seal as an unauthenticated verifier sees it.

    Note what is absent: no organization name, no entry, no local ids. Everything
    here is already on a public ledger.
    """

    seq: int
    root: str
    prev: str
    entry_count: int
    debit_minor: str
    covered_from: dt.datetime
    covered_to: dt.datetime
    sealed_at: dt.datetime


class PublicChainRead(ResponseSchema):
    namespace: str
    network: str
    contract_id: str
    head: int
    root: str | None = None
    entries: int | None = None
    sealed_at: dt.datetime | None = None
    continuous: bool = True
    seals: list[PublicSealRead] = Field(default_factory=list)


class CanonicalSpecRead(ResponseSchema):
    """The canonical encoding, published so a verifier can assert agreement.

    Served because the browser implements this encoding independently. If the two
    ever drift, every proof fails with no visible cause - so the client fetches
    this and refuses to render a verdict when its own spec version disagrees.
    """

    spec: dict[str, Any]


class DrainResultRead(ResponseSchema):
    """Outcome of one worker pass."""

    processed: int
    confirmed: int
    failed: int
    waiting: int


class ReconcileResultRead(ResponseSchema):
    reconciled: bool
    reason: str | None = None
    chain_head: int | None = None
    chain_root: str | None = None
    local_head: int | None = None
    adjusted: int | None = None
    agrees: bool | None = None


class SealNowResponse(ResponseSchema):
    """Result of pressing "Seal now"."""

    #: Null when there was nothing to seal, which is a normal answer and not an
    #: error - the button is enabled whenever the screen might be stale.
    seal: SealRead | None = None
    message: str


class NetworkInfoRead(ResponseSchema):
    """What the client needs to talk to the chain itself.

    Served so the web and desktop clients do not hard-code a network, and so the
    verifier can be pointed at an RPC of the reader's choosing - a scheme whose
    claim is "you need not trust us" should not require trusting one hosted RPC.
    """

    enabled: bool
    network: Literal["testnet", "public"] | None
    contract_id: str | None
    rpc_url: str
    explorer_base: str
    spec_version: int


class AdoptionRowRead(ResponseSchema):
    """One organization's on-chain footprint, install-wide.

    Every figure here is either configuration or already public on the Stellar
    ledger. The point of the shape is that a reader can leave this response and
    confirm it: `signer_public_key` and `head_tx_hash` resolve on any explorer,
    which is what separates a checkable claim from a number we assert about
    ourselves.
    """

    organization_id: uuid.UUID
    organization_name: str
    organization_slug: str

    enabled: bool
    network: Literal["testnet", "public"] | None
    contract_id: str | None
    org_namespace: Hex32

    #: The `G...` account that signs this organization's seals. Public by
    #: definition - it is the address the transactions come from.
    signer_public_key: str | None
    #: True when the key is held outside this server, which is the stronger
    #: posture and looks identical to "not configured" from a null column.
    external_signer: bool

    registered_at: dt.datetime | None
    registration_tx: str | None
    cadence: SealCadence

    #: Confirmed seals only. A submitted-but-unconfirmed seal is not evidence of
    #: anything yet, and counting it here would inflate exactly the figure this
    #: endpoint exists to make checkable.
    seals: int
    entries_sealed: int
    #: A string, because a lifetime control total in paise outgrows a double.
    debit_minor: str

    first_sealed_at: dt.datetime | None
    last_sealed_at: dt.datetime | None
    head_seq: int | None
    #: The transaction that wrote `head_seq`. Paste it into an explorer.
    head_tx_hash: str | None


class AdoptionRead(ResponseSchema):
    """Who on this install is actually using the third ledger."""

    #: Organizations with a book, most active first.
    organizations: list[AdoptionRowRead]
    #: Organizations that have written at least one confirmed seal. The headline
    #: figure, and deliberately not the same as `len(organizations)` - switching
    #: sealing on is not the same as having sealed.
    sealing: int
    total_seals: int
    total_entries_sealed: int
    network: Literal["testnet", "public"] | None
    contract_id: str | None
    explorer_base: str
