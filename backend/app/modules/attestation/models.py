"""Ledger 3 - the proof ledger's local half.

Three tables, and the shape of them is the whole design:

* :class:`SealLeaf` - one row per posted journal entry, holding the 32-byte
  canonical hash of that entry. Written in the **same transaction as the
  posting**, so a leaf cannot go missing for an entry that made it into the
  books.
* :class:`Seal` - one row per batch of leaves committed to the chain. **This row
  is also the outbox.** A `pending` seal is the intent; the worker drains it and
  moves it to `confirmed`.
* :class:`AttestationSetting` - one row per organization: whether sealing is on,
  which namespace and contract, and the signer.

Why the seal row *is* the outbox
--------------------------------
The obvious design is a `seal` table plus a separate `seal_outbox` table of work
to do. That is two records of the same fact, and this codebase has already been
bitten by a figure stored twice - it is why the billing module has no billing
table and analytics has no metrics table. A separate outbox would make "has
period 7 been sealed?" answerable two ways, and the day they disagree is the day
nobody can tell whether the chain is behind or the database is lying.

So there is one row per seal, created in the same transaction as the event that
triggered it, and its ``status`` is how far along it is. The chain is still the
authority - :meth:`SealStatus` is a local belief that the reconciler corrects
against ``latest()`` - but there is only one place that belief is written down.

Why sealing is batched by leaf sequence, not by accounting period
-----------------------------------------------------------------
The intuitive unit is the month. It is the wrong one, for a reason that only
shows up in real books: **a journal entry can be posted into a period after that
period has already been sealed.** A daily seal covers an open month; the next day
brings three more entries dated inside it. And a bill for March genuinely arrives
on 3 April.

If the sealing unit were the month, either the March root would have to change -
which the contract forbids, correctly - or those entries would never be sealed at
all.

So the unit is a **batch**: leaves ``(last_sealed, cutoff]`` in per-organization
posting order (:attr:`SealLeaf.leaf_seq`). Batches are consecutive and
non-overlapping by construction, which is also what makes the contract's
"periods tile forwards" check hold without any special casing - the on-chain
``from``/``to`` are the batch's **posting-time** window, which only ever moves
forward. The accounting-date span the batch happens to touch is recorded locally
(:attr:`Seal.entry_date_from`) for display, because it is what a human wants to
read, but it is not what tiles.

The consequence for a verifier is stated plainly on the Trust screen: a seal
attests *"these entries were in the books at this moment"*, not *"these are all
the entries for March"*. The second claim is the one a naive design accidentally
makes and cannot keep.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import LedgerDate, Money, enum_column

if TYPE_CHECKING:
    from app.modules.accounting.models import AccountingPeriod, JournalEntry
    from app.modules.organizations.models import Organization
    from app.modules.users.models import User


# =============================================================================
# Enums
# =============================================================================
class SealStatus(StrEnum):
    """How far a seal has got towards the chain.

    ``PENDING`` -> ``SUBMITTED`` -> ``CONFIRMED``, with ``FAILED`` as a terminal
    parking spot for something a human needs to look at.

    ``SUBMITTED`` exists as its own state rather than being folded into
    ``PENDING`` because of the ambiguous failure this whole subsystem is built
    around: once a transaction has left the process, we do **not** know whether it
    landed. A row in ``SUBMITTED`` is one the worker must ask the chain about
    before it does anything else, and collapsing it into ``PENDING`` would mean
    resubmitting blind.
    """

    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (SealStatus.CONFIRMED, SealStatus.FAILED)

    @property
    def is_open(self) -> bool:
        """Whether the worker still owes this row an outcome."""
        return self in (SealStatus.PENDING, SealStatus.SUBMITTED)


class SealTrigger(StrEnum):
    """What caused a seal to be created. Recorded because it is the first
    question asked of an unexpected seal, and because a business showing a
    verifier its chain should be able to say which entries it chose to seal and
    which the system sealed for it."""

    PERIOD_CLOSE = "period_close"
    SCHEDULE = "schedule"
    MANUAL = "manual"
    #: The very first seal after sealing is switched on, covering the backlog of
    #: entries posted before then.
    BACKFILL = "backfill"


class SealCadence(StrEnum):
    """How often an organization seals.

    ``DAILY`` is the recommended setting and the reason Stellar was chosen: at
    well under a cent per operation, sealing every day costs less than the
    electricity the server draws computing the root, and it narrows the window in
    which history could be rewritten from a year to a day.
    """

    #: Seal when an accounting period is closed, and no more often.
    ON_PERIOD_CLOSE = "on_period_close"
    #: Seal on a daily schedule, and on period close.
    DAILY = "daily"
    #: Only when somebody presses the button.
    MANUAL = "manual"


# =============================================================================
# Per-organization configuration
# =============================================================================
class AttestationSetting(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """One organization's proof-ledger configuration.

    Its own table rather than columns on ``organization`` or keys in that table's
    ``settings`` JSONB. Two reasons, and the second is the real one:

    1. Eight mostly-null columns on the row every request already loads is a poor
       trade for a feature not every install turns on.
    2. **The signer's secret key lives here.** A secret does not belong in a
       free-form JSONB blob that other features write to by key, where a careless
       ``settings.update(payload)`` in some unrelated endpoint could echo it back
       to a client. It belongs in a typed column, in a table nothing else writes,
       whose only reader is the one service that needs to sign.
    """

    #: Whether this organization seals at all. Off by default: the ERP is fully
    #: usable without the third ledger, and a business that has not chosen to
    #: publish commitments should not be publishing them.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: ``SHA-256(organization_id ‖ install_salt)``, hex. The organization's
    #: identity on chain.
    #:
    #: Salted, and this is the privacy story: without the salt an observer who
    #: guessed an organization id could confirm it by hashing. With it, the
    #: on-chain record is unlinkable to a named business until the business itself
    #: discloses the namespace - which is exactly what handing a verifier a proof
    #: bundle does, deliberately and one counterparty at a time.
    #:
    #: Unique across the install, because two organizations sharing a namespace
    #: would interleave their seals into one chain and each would break the
    #: other's sequence.
    org_namespace: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    #: Which contract instance this organization's chain lives in. Stored per
    #: organization rather than read from configuration at verification time: a
    #: proof issued against the testnet contract must keep verifying against the
    #: testnet contract after the install moves to mainnet, or every proof ever
    #: exported silently becomes unverifiable on the day of the cutover.
    contract_id: Mapped[str | None] = mapped_column(String(64))

    #: ``testnet`` or ``public``. Same reasoning as :attr:`contract_id`, and shown
    #: to the verifier so a testnet proof can never be mistaken for a mainnet one.
    network: Mapped[str | None] = mapped_column(String(16))

    #: The Stellar account authorised to seal - ``G...``.
    signer_public_key: Mapped[str | None] = mapped_column(String(56))

    #: The signer's secret seed, **Fernet-encrypted at rest** with the same key
    #: material as a TOTP secret and a bank account number
    #: (:func:`app.core.security.encrypt_secret`).
    #:
    #: Null when the organization signs externally - a browser wallet, or a
    #: multisig account whose signers are elsewhere. That is the stronger posture
    #: and the one the roadmap moves towards; a server-held key is what makes
    #: *unattended* sealing possible, and unattended sealing is what makes the
    #: cadence daily instead of whenever-somebody-remembers.
    #:
    #: The honest limitation, stated in the docs and on the screen: a server-held
    #: key means the operator could doctor the books before sealing. What the seal
    #: then proves is that nothing changed *after* it - which is still the claim
    #: that matters, because retroactive editing is how books are actually cooked.
    #: 2-of-3 co-signing with the business's accountant is what closes the rest of
    #: the gap.
    signer_secret_encrypted: Mapped[str | None] = mapped_column(String(500))

    #: True when the key is held outside this server. Derived from
    #: :attr:`signer_secret_encrypted` being null, but stored explicitly so the UI
    #: can say "external signer" rather than "not configured" - two very different
    #: states that look identical from a null column.
    external_signer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    cadence: Mapped[SealCadence] = mapped_column(
        enum_column(SealCadence, length=20),
        nullable=False,
        default=SealCadence.DAILY,
    )

    #: The time of day at which a ``DAILY`` seal may fire, as minutes past
    #: midnight in the organization's own timezone (0-1439). Null means "use the
    #: install's ``SEAL_DAILY_HOUR``".
    #:
    #: **Minutes, not an hour.** The useful sealing time is whenever nobody is
    #: posting, and that is 01:00 for one business and 03:30 for another whose
    #: night shift ends at 03:00. An hour column would have made "half past"
    #: unrepresentable, and widening it later means guessing what a stored 3 meant.
    #:
    #: **Nullable rather than defaulted, and the distinction earns its keep.** A
    #: stored 1 and an unset hour look the same until the operator changes
    #: ``SEAL_DAILY_HOUR``, at which point every organization that never expressed a
    #: preference should follow and every organization that did should not. Writing
    #: the default in at enable time would silently pin thousands of tenants to
    #: whatever the value happened to be that afternoon.
    #:
    #: **The organization's clock, not UTC.** An owner who picks 01:30 means 01:30
    #: where the business is; comparing against a server time makes one setting
    #: fire at a different wall-clock moment for every tenant. The worker resolves
    #: it through :func:`app.modules.organizations.clock.organization_now`.
    seal_minute: Mapped[int | None] = mapped_column(SmallInteger)

    #: When ``register`` was confirmed on chain, and the transaction that did it.
    #: Null means the namespace exists locally but the book does not exist on
    #: chain yet, so nothing can be sealed.
    registered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    registration_tx: Mapped[str | None] = mapped_column(String(64))

    organization: Mapped[Organization] = relationship()

    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_attestation_setting_organization_id"),
        # Enabled without a contract, a network, and a registration is a
        # configuration that would fail on every seal attempt. Caught here so the
        # failure is at the moment of switching it on, where somebody is looking.
        CheckConstraint(
            "enabled = false OR (contract_id IS NOT NULL AND network IS NOT NULL)",
            name="enabled_is_configured",
        ),
        # A value outside 0-1439 is not a preference, it is one that would never
        # match and so would stop sealing altogether - silently, which is the one
        # failure mode this subsystem must not have.
        CheckConstraint(
            "seal_minute IS NULL OR (seal_minute >= 0 AND seal_minute <= 1439)",
            name="seal_minute_is_a_time_of_day",
        ),
    )

    @property
    def is_ready(self) -> bool:
        """Whether a seal could actually be submitted right now."""
        return bool(
            self.enabled
            and self.contract_id
            and self.network
            and self.registered_at
            and self.signer_public_key
        )


# =============================================================================
# Leaves
# =============================================================================
class SealLeaf(Base, UUIDPrimaryKeyMixin, OrgScopedMixin):
    """The canonical hash of one posted journal entry.

    Deliberately **without** :class:`~app.db.base.TimestampMixin`. An
    ``updated_at`` column on a leaf would imply a leaf can change, and it cannot:
    a leaf is a hash of an immutable entry, so the only correct lifecycle is
    "written once, then assigned to a seal". :attr:`created_at` is declared on its
    own below because the batching does need to know when the leaf appeared.

    There is no update path for :attr:`leaf_hash` anywhere in the module, for the
    same reason there is none for a posted entry.
    """

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    #: The entry this commits to. ``RESTRICT`` rather than ``CASCADE``: a posted
    #: journal entry is never deleted, so a cascade would only ever fire on
    #: something that should have been impossible, and it would quietly remove the
    #: evidence.
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal_entry.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )

    #: Position in this organization's posting order, from 1. Assigned under the
    #: same lock as the accounting module's statutory numbering, so it is gap-free
    #: and total.
    #:
    #: **Gap-free matters here more than it does for an invoice number.** A batch
    #: is defined as a half-open range of these, so a gap would either be sealed
    #: as part of a later batch - changing a root that was already published - or
    #: skipped entirely. Both are silent.
    leaf_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: ``SHA-256(0x00 ‖ canonical(entry))``, lowercase hex.
    #:
    #: Hex in a ``String`` rather than raw ``BYTEA``. Every consumer - the API, the
    #: proof bundle, the verifier's browser, a support engineer reading a row -
    #: wants hex, and a ``BYTEA`` would be encoded and decoded at every one of
    #: those boundaries for a 32-byte saving per row.
    leaf_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    #: Which canonical encoding produced :attr:`leaf_hash`. Stored per leaf, not
    #: globally, because a future v2 encoding must not retroactively change how an
    #: old leaf is interpreted - the version that hashed it is a property of the
    #: leaf, and a verifier needs it to pick the right decoder.
    canonical_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    #: Denormalised from the entry so a batch's control totals and date span can be
    #: computed without joining ``journal_entry`` - which for a month of a busy
    #: business is the difference between one index scan and a join over every
    #: line.
    entry_date: Mapped[LedgerDate] = mapped_column(nullable=False, index=True)
    total_debit: Mapped[Money] = mapped_column(nullable=False)

    #: The seal that covers this leaf; null until it is sealed. Nullable **and**
    #: indexed, because "what is not yet sealed?" is the query the worker runs.
    seal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("seal.id", ondelete="RESTRICT"),
        index=True,
    )

    #: Index of this leaf within its seal's Merkle tree, from 0. Set when the seal
    #: is created.
    #:
    #: **Pinned rather than re-derived at export time.** The tree could be rebuilt
    #: by re-sorting the batch's leaves, and it would give the same answer today.
    #: Storing the index means it gives the same answer in five years even if the
    #: sort key, the query, or the default ordering of anything upstream changes -
    #: and if any of those did change, a re-derived tree would produce valid-looking
    #: proofs that fail against a published root, with nothing to point at.
    leaf_index: Mapped[int | None] = mapped_column(Integer)

    organization: Mapped[Organization] = relationship()
    journal_entry: Mapped[JournalEntry] = relationship(lazy="raise")
    seal: Mapped[Seal | None] = relationship(back_populates="leaves", lazy="raise")

    __table_args__ = (
        UniqueConstraint("organization_id", "leaf_seq", name="uq_seal_leaf_org_seq"),
        # A sealed leaf must know where it sits in the tree, and an unsealed one
        # must not claim to. Constrained rather than assumed, because a half-filled
        # pair here produces an inclusion proof for the wrong position - which
        # verifies against nothing and looks like tampering.
        CheckConstraint(
            "(seal_id IS NULL AND leaf_index IS NULL) OR "
            "(seal_id IS NOT NULL AND leaf_index IS NOT NULL)",
            name="sealed_leaf_has_index",
        ),
        CheckConstraint("leaf_seq > 0", name="leaf_seq_positive"),
        CheckConstraint("leaf_index IS NULL OR leaf_index >= 0", name="leaf_index_non_negative"),
        # The worker's hot query: this organization's unsealed leaves, in posting
        # order. Partial on ``seal_id IS NULL`` so the index holds only the
        # backlog rather than every leaf ever written - which for a business in
        # its fifth year is a few hundred rows instead of a few hundred thousand,
        # and the backlog is the only part ever scanned.
        Index(
            "ix_seal_leaf_unsealed",
            "organization_id",
            "leaf_seq",
            postgresql_where=text("seal_id IS NULL"),
        ),
        # Rebuilding one seal's tree: every leaf it covers, in tree order.
        Index("ix_seal_leaf_seal_index", "seal_id", "leaf_index"),
    )


# =============================================================================
# Seals
# =============================================================================
class Seal(Base, UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin):
    """One batch of leaves, committed to the chain.

    **This row is the outbox.** It is created in the same database transaction as
    whatever triggered it - closing a period, pressing the button - and it commits
    in milliseconds, so a chain outage can never block a month-end close. A
    separate worker moves it to the chain afterwards.
    """

    #: Position in the organization's on-chain chain, from 1. The same number the
    #: contract enforces as ``head + 1``.
    #:
    #: Assigned locally when the row is created, which means a *local* gap is
    #: possible if a seal fails permanently. That is intentional and survivable:
    #: the contract's ``head`` does not move for a failed submission, so the next
    #: attempt reuses the number rather than skipping it. See
    #: :attr:`superseded_by_id`.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Merkle root over the batch's leaf hashes, lowercase hex.
    merkle_root: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    #: The previous confirmed seal's root, or 64 zeroes for the first. What makes
    #: the seals a chain rather than a pile.
    prev_root: Mapped[str] = mapped_column(String(64), nullable=False)

    entry_count: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Sum of the batch's debits in **minor units** - an integer, matching the
    #: ``i128`` the contract stores.
    #:
    #: ``NUMERIC(38, 0)`` rather than ``BIGINT``: ₹92,23,37,20,368 exhausts a
    #: signed 64-bit integer in paise, which is a plausible lifetime turnover for
    #: a business this product is aimed at, and a control total that silently wraps
    #: is worse than no control total.
    #: Annotated ``Decimal`` rather than ``int`` because that is what asyncpg
    #: actually returns for ``NUMERIC`` - the value is a whole number, but the
    #: Python type is not, and an ``int`` annotation here would be a lie mypy
    #: cannot catch and every call site would trip over. Use :attr:`debits` to
    #: get the integer the contract wants.
    debit_minor: Mapped[Decimal] = mapped_column(Numeric(38, 0), nullable=False)

    #: The batch's half-open leaf range, ``(first-1, last]`` in
    #: :attr:`SealLeaf.leaf_seq`. Stored so a batch's membership is a fact on the
    #: row rather than something inferred by re-running the query that built it.
    first_leaf_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_leaf_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)

    #: The batch's **posting-time** window - the ``from``/``to`` that go on chain.
    #: These tile forward by construction, which is what satisfies the contract's
    #: ordering check without special casing. See the module docstring.
    covered_from: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    covered_to: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: The **accounting-date** span the batch happens to touch. Local only, for
    #: display: it is what a human reads ("March and one April bill"), and it does
    #: not tile, because a bill for March can arrive in April.
    entry_date_from: Mapped[dt.date] = mapped_column(nullable=False)
    entry_date_to: Mapped[dt.date] = mapped_column(nullable=False)

    trigger: Mapped[SealTrigger] = mapped_column(
        enum_column(SealTrigger, length=20), nullable=False
    )

    #: The period whose closing triggered this seal, when one did.
    accounting_period_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounting_period.id", ondelete="SET NULL"), index=True
    )

    status: Mapped[SealStatus] = mapped_column(
        enum_column(SealStatus, length=20),
        nullable=False,
        default=SealStatus.PENDING,
        index=True,
    )

    # --- Chain coordinates -------------------------------------------------
    #: Copied onto the seal rather than only held in settings, so a proof bundle
    #: exported years later names the contract and network it was actually
    #: written to - and keeps verifying after a mainnet cutover changes the
    #: organization's current configuration.
    network: Mapped[str | None] = mapped_column(String(16))
    contract_id: Mapped[str | None] = mapped_column(String(64))
    tx_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    ledger_sequence: Mapped[int | None] = mapped_column(BigInteger)

    #: The **network's** timestamp for this seal, read back from the contract
    #: after confirmation - not our own clock.
    #:
    #: This is the field a verifier's whole claim rests on, so it is deliberately
    #: not defaulted, not set at submission, and not derived from
    #: :attr:`submitted_at`. It is null until the chain tells us, and the UI shows
    #: "awaiting confirmation" rather than a time we made up.
    sealed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    #: Submission attempts. Bounded retries with backoff; a seal that exhausts
    #: them lands in ``FAILED`` for a human rather than retrying forever against a
    #: contract that is refusing it for a reason retrying cannot fix.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    #: When a seal fails permanently, the replacement that covers the same leaves.
    #: The failed row is kept, because "we tried to seal on the 3rd and the
    #: submission was refused" is exactly the kind of thing an auditor is entitled
    #: to see rather than have tidied away.
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("seal.id", ondelete="SET NULL")
    )

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    organization: Mapped[Organization] = relationship()
    accounting_period: Mapped[AccountingPeriod | None] = relationship(lazy="raise")
    created_by: Mapped[User | None] = relationship(lazy="raise")
    leaves: Mapped[list[SealLeaf]] = relationship(
        back_populates="seal",
        lazy="raise",
        order_by="SealLeaf.leaf_index",
    )

    __table_args__ = (
        # One live seal per sequence number. Partial, so a failed attempt at
        # sequence 7 does not block the retry that must reuse it: the contract's
        # `head` never moved, so 7 is still the only number the chain will accept.
        Index(
            "uq_seal_org_seq_live",
            "organization_id",
            "seq",
            unique=True,
            postgresql_where="status <> 'failed'",
        ),
        # One live seal per leaf range, and **partial for the same reason as the
        # index above**. An unconditional constraint here would let a failed seal
        # reserve its leaf range forever: the replacement covers exactly the same
        # leaves - that is the whole point of releasing them - so it would collide
        # on `last_leaf_seq` and the organization could never seal again.
        #
        # Found by the test that asserts a failed sequence can be reused, which is
        # why that test exists.
        Index(
            "uq_seal_org_last_leaf",
            "organization_id",
            "last_leaf_seq",
            unique=True,
            postgresql_where=text("status <> 'failed'"),
        ),
        CheckConstraint("seq > 0", name="seq_positive"),
        CheckConstraint("entry_count > 0", name="count_positive"),
        CheckConstraint("last_leaf_seq >= first_leaf_seq", name="leaf_range_ordered"),
        CheckConstraint("covered_to >= covered_from", name="covered_window_ordered"),
        CheckConstraint("entry_date_to >= entry_date_from", name="entry_dates_ordered"),
        # A confirmed seal must carry what makes it independently checkable: the
        # contract it lives in, and the network timestamp it was accepted at.
        #
        # `tx_hash` is deliberately **not** required. A seal can legitimately be
        # confirmed without one: when a submission times out and the reconciler
        # later finds the seal on chain, the transaction that carried it is not
        # recoverable without an event scan, and the seal is fully verifiable
        # regardless - a verifier checks `verify(namespace, seq, root)` against the
        # contract, never a transaction hash. Requiring it here would mean the
        # reconciler could not record a seal that demonstrably exists.
        CheckConstraint(
            "status <> 'confirmed' OR (sealed_at IS NOT NULL AND contract_id IS NOT NULL)",
            name="confirmed_is_checkable",
        ),
        # The Trust screen's query: this org's seals, newest first.
        Index("ix_seal_org_seq_desc", "organization_id", "seq"),
        # The worker's query: everything still owed an outcome.
        Index("ix_seal_status_created", "status", "created_at"),
    )

    @property
    def is_confirmed(self) -> bool:
        return self.status is SealStatus.CONFIRMED

    @property
    def debits(self) -> int:
        """:attr:`debit_minor` as the ``i128`` the contract stores.

        One conversion, in one place. Scattering ``int(seal.debit_minor)`` across
        call sites is how one of them ends up passing a ``Decimal`` into an XDR
        encoder that accepts it and produces something subtly different.
        """
        return int(self.debit_minor)

    @property
    def explorer_url(self) -> str | None:
        """A link to this seal's transaction on a public explorer.

        Built here rather than in the client so the web app, the desktop app, and
        an exported proof bundle all point at the same place - and so the network
        recorded on the row, not the one currently configured, decides which
        network's explorer is linked.
        """
        if not self.tx_hash or not self.network:
            return None
        segment = "public" if self.network == "public" else "testnet"
        return f"https://stellar.expert/explorer/{segment}/tx/{self.tx_hash}"
