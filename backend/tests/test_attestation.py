"""The proof ledger, against a real database and a faithful fake chain.

Why a fake chain rather than testnet
------------------------------------
The hardest logic in this subsystem is what happens when a submission's outcome
is **unknown** - the transaction left the process and no verdict came back. That
is not a condition you can wait for on a real network, and a test that hopes for
it is a test that passes for the wrong reason. So :class:`FakeChain` reimplements
the contract's rules in memory and exposes knobs to make submission time out,
diverge, or be refused on demand.

The fake is only worth anything if it refuses what the real contract refuses, so
:class:`TestTheFakeIsFaithful` pins its behaviour against the error codes in
``contracts/proof_ledger/src/lib.rs``. The real contract's own 28 adversarial
tests live next to it in Rust; these are about how *this* application behaves
when the contract says no.

Everything else here runs against real PostgreSQL, because the parts worth
testing - a partial unique index that lets a failed seal's sequence be reused,
``SELECT … FOR UPDATE`` allocating gap-free leaf sequences - are exactly what a
substitute engine implements differently.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessRuleError, ConflictError
from app.modules.accounting.coa_template import SystemAccount
from app.modules.accounting.models import JournalType, PeriodStatus
from app.modules.accounting.repository import AccountRepository, JournalRepository
from app.modules.accounting.schemas import JournalEntryCreate, JournalEntryLineInput
from app.modules.accounting.service import (
    ChartOfAccountsService,
    FiscalCalendarService,
    PostingService,
)
from app.modules.attestation import canonical as canon
from app.modules.attestation import merkle as mk
from app.modules.attestation.models import (
    AttestationSetting,
    Seal,
    SealCadence,
    SealLeaf,
    SealStatus,
    SealTrigger,
)
from app.modules.attestation.repository import (
    SealLeafRepository,
    SealRepository,
)
from app.modules.attestation.service import (
    AttestationService,
    SealService,
    VerifyService,
    namespace_for,
)
from app.modules.attestation.stellar import GENESIS_ROOT, BookView, SealView, SubmitOutcome
from app.modules.organizations.models import Organization
from app.modules.users.models import User

pytestmark = pytest.mark.integration

TODAY = dt.date.today()
CONTRACT = "CTESTCONTRACT000000000000000000000000000000000000000000000"


# =============================================================================
# The fake chain
# =============================================================================
@dataclasses.dataclass
class _Book:
    admin: str
    head: int = 0
    root: str = GENESIS_ROOT
    sealed_at: dt.datetime | None = None
    covered_to: int = 0
    entries: int = 0
    seals: dict[int, SealView] = dataclasses.field(default_factory=dict)


class FakeChain:
    """An in-memory ``proof_ledger``, enforcing the contract's rules.

    Error names match :data:`app.modules.attestation.stellar.CONTRACT_ERRORS`, so
    a test asserting on ``sequence_out_of_order`` is asserting on the same string
    the real RPC would produce.
    """

    def __init__(self) -> None:
        self.books: dict[str, _Book] = {}
        self.now = dt.datetime(2026, 4, 1, 10, 0, 0, tzinfo=dt.UTC)
        self.submissions: list[dict[str, Any]] = []

        # Knobs. Each fires once and clears, so a test says "the next submission
        # times out" rather than having to reset state afterwards.
        self.unknown_once = False
        self.unreachable = False
        self.reject_once: str | None = None

    def tick(self, seconds: int = 5) -> None:
        self.now += dt.timedelta(seconds=seconds)

    # -- contract behaviour ------------------------------------------------
    def register(self, namespace: str, admin: str) -> str | None:
        if namespace in self.books:
            return "already_registered"
        self.books[namespace] = _Book(admin=admin)
        return None

    def seal(
        self,
        namespace: str,
        seq: int,
        root: str,
        prev: str,
        count: int,
        debits: int,
        covered_from: int,
        covered_to: int,
    ) -> str | None:
        book = self.books.get(namespace)
        if book is None:
            return "not_registered"
        if root == GENESIS_ROOT:
            return "root_is_sentinel"
        if count == 0:
            return "empty_seal"
        if covered_to < covered_from:
            return "period_out_of_order"
        if seq != book.head + 1:
            return "sequence_out_of_order"
        if prev != book.root:
            return "chain_broken"
        if book.head > 0 and covered_from < book.covered_to:
            return "period_out_of_order"

        self.tick()
        book.seals[seq] = SealView(
            seq=seq,
            root=root,
            prev=prev,
            count=count,
            debits=debits,
            period_from=covered_from,
            period_to=covered_to,
            at=self.now,
        )
        book.head = seq
        book.root = root
        book.sealed_at = self.now
        book.covered_to = covered_to
        book.entries += count
        return None


class FakeSorobanClient:
    """The :class:`SorobanClient` surface, backed by a :class:`FakeChain`."""

    def __init__(self, network: str | None = None, *, chain: FakeChain) -> None:
        self.network = network or "testnet"
        self.chain = chain
        self._keys = 0

    # -- keys --------------------------------------------------------------
    def generate_keypair(self) -> tuple[str, str]:
        self._keys += 1
        suffix = f"{self._keys:02d}"
        return (f"G{'A' * 53}{suffix}", f"S{'A' * 53}{suffix}")

    def public_key_of(self, secret: str) -> str:
        return "G" + secret[1:]

    async def account_exists(self, public_key: str) -> bool:
        return True

    async def fund_testnet_account(self, public_key: str) -> bool:
        return True

    # -- writes ------------------------------------------------------------
    async def register_book(
        self, *, contract_id: str, namespace: str, secret: str
    ) -> SubmitOutcome:
        self._guard()
        error = self.chain.register(namespace, self.public_key_of(secret))
        if error:
            return SubmitOutcome(status="rejected", error_name=error, message=error)
        self.chain.tick()
        return SubmitOutcome(
            status="confirmed", tx_hash="tx" + "a" * 62, network_time=self.chain.now
        )

    async def rotate_admin(
        self, *, contract_id: str, namespace: str, secret: str, new_admin: str
    ) -> SubmitOutcome:
        self._guard()
        book = self.chain.books.get(namespace)
        if book is None:
            return SubmitOutcome(status="rejected", error_name="not_registered")
        book.admin = new_admin
        return SubmitOutcome(status="confirmed", tx_hash="tx" + "b" * 62)

    async def submit_seal(
        self,
        *,
        contract_id: str,
        namespace: str,
        secret: str,
        seq: int,
        root: str,
        prev: str,
        count: int,
        debit_minor: int,
        covered_from: int,
        covered_to: int,
    ) -> SubmitOutcome:
        self._guard()
        self.chain.submissions.append({"seq": seq, "root": root, "prev": prev, "count": count})

        if self.chain.reject_once:
            error = self.chain.reject_once
            self.chain.reject_once = None
            return SubmitOutcome(status="rejected", error_name=error, message=error)

        error = self.chain.seal(
            namespace, seq, root, prev, count, debit_minor, covered_from, covered_to
        )
        if error:
            return SubmitOutcome(status="rejected", error_name=error, message=error)

        if self.chain.unknown_once:
            # The critical case: it *landed*, but we never learned that it did.
            self.chain.unknown_once = False
            return SubmitOutcome(
                status="unknown", tx_hash="tx" + "c" * 62, message="no result in time"
            )

        return SubmitOutcome(
            status="confirmed",
            tx_hash="tx" + f"{seq:062d}",
            ledger=1000 + seq,
            network_time=self.chain.now,
        )

    # -- reads -------------------------------------------------------------
    async def read_book(self, *, contract_id: str, namespace: str) -> BookView | None:
        self._guard()
        book = self.chain.books.get(namespace)
        if book is None:
            return None
        return BookView(
            admin=book.admin,
            head=book.head,
            root=book.root,
            sealed_at=book.sealed_at,
            covered_to=book.covered_to,
            entries=book.entries,
        )

    async def read_seal(self, *, contract_id: str, namespace: str, seq: int) -> SealView | None:
        self._guard()
        book = self.chain.books.get(namespace)
        return None if book is None else book.seals.get(seq)

    async def is_registered(self, *, contract_id: str, namespace: str) -> bool:
        self._guard()
        return namespace in self.chain.books

    async def health(self) -> dict[str, Any]:
        return {"reachable": not self.chain.unreachable, "network": self.network}

    def _guard(self) -> None:
        if self.chain.unreachable:
            from app.modules.attestation.stellar import SorobanUnavailable

            raise SorobanUnavailable("fake chain is unreachable")


@pytest.fixture
def chain(monkeypatch: pytest.MonkeyPatch) -> FakeChain:
    """Replace the Soroban boundary everywhere the services reach for it."""
    fake = FakeChain()

    def factory(network: str | None = None) -> FakeSorobanClient:
        return FakeSorobanClient(network, chain=fake)

    monkeypatch.setattr("app.modules.attestation.service.SorobanClient", factory, raising=True)
    monkeypatch.setattr("app.core.config.settings.soroban_contract_id", CONTRACT, raising=False)
    return fake


# =============================================================================
# Books and posting fixtures
# =============================================================================
@pytest.fixture
async def books(db: AsyncSession, organization: Organization) -> Organization:
    await ChartOfAccountsService(db).seed_defaults(organization.id)
    await FiscalCalendarService(db).ensure_year_for(organization.id, fiscal_year_start_month=4)
    await db.flush()
    return organization


@pytest.fixture
async def hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install the accounting subscriptions, and remove them afterwards.

    Explicit rather than autouse. `clear_hooks` exists precisely because a leaked
    subscription would make an unrelated accounting test start writing leaves, and
    one failure would look like ten.
    """
    from app.modules.accounting.hooks import clear_hooks
    from app.modules.attestation.hooks import install_attestation_hooks

    clear_hooks()
    install_attestation_hooks()
    yield
    clear_hooks()


@pytest.fixture
async def posting_ctx(
    db: AsyncSession, books: Organization
) -> tuple[PostingService, uuid.UUID, dict[str, uuid.UUID]]:
    journal = await JournalRepository(db).get_by_type(books.id, JournalType.GENERAL)
    assert journal is not None
    repo = AccountRepository(db)
    wanted = (SystemAccount.CASH, SystemAccount.SALES_REVENUE, SystemAccount.ACCOUNTS_RECEIVABLE)
    accounts: dict[str, uuid.UUID] = {}
    for key in wanted:
        account = await repo.get_by_system_key(books.id, key)
        assert account is not None
        accounts[key] = account.id
    return PostingService(db), journal.id, accounts


async def post_entry(
    ctx: tuple[PostingService, uuid.UUID, dict[str, uuid.UUID]],
    org: Organization,
    user: User,
    amount: str,
    *,
    on: dt.date | None = None,
    narration: str = "Sale",
) -> Any:
    """Post one balanced two-line entry, the plain way an invoice would."""
    posting, journal_id, accounts = ctx
    payload = JournalEntryCreate(
        journal_id=journal_id,
        entry_date=on or TODAY,
        narration=narration,
        lines=[
            JournalEntryLineInput(account_id=accounts[SystemAccount.CASH], debit=Decimal(amount)),
            JournalEntryLineInput(
                account_id=accounts[SystemAccount.SALES_REVENUE], credit=Decimal(amount)
            ),
        ],
    )
    entry = await posting.create_entry(org.id, payload, user)
    return await posting.post_entry(org.id, entry.id, user)


@pytest.fixture
async def enabled(
    db: AsyncSession, books: Organization, user: User, chain: FakeChain
) -> AttestationSetting:
    """Sealing switched on, with the on-chain book opened."""
    return await AttestationService(db).enable(books.id, user, cadence=SealCadence.DAILY)


# =============================================================================
# The fake's fidelity
# =============================================================================
class TestTheFakeIsFaithful:
    """The fake refuses what the real contract refuses.

    Pinned against ``contracts/proof_ledger/src/lib.rs``. A fake that were more
    permissive would let every test below pass while the real thing rejected the
    same submission in production.
    """

    def test_it_refuses_a_second_registration(self) -> None:
        c = FakeChain()
        assert c.register("ab" * 32, "GADMIN") is None
        assert c.register("ab" * 32, "GOTHER") == "already_registered"

    def test_it_refuses_an_out_of_order_sequence(self) -> None:
        c = FakeChain()
        c.register("ab" * 32, "GADMIN")
        assert c.seal("ab" * 32, 2, "aa" * 32, GENESIS_ROOT, 1, 0, 0, 1) == (
            "sequence_out_of_order"
        )

    def test_it_refuses_a_duplicate_sequence(self) -> None:
        c = FakeChain()
        c.register("ab" * 32, "GADMIN")
        assert c.seal("ab" * 32, 1, "aa" * 32, GENESIS_ROOT, 1, 0, 0, 1) is None
        assert c.seal("ab" * 32, 1, "aa" * 32, GENESIS_ROOT, 1, 0, 0, 1) == (
            "sequence_out_of_order"
        )

    def test_it_refuses_a_broken_chain(self) -> None:
        c = FakeChain()
        c.register("ab" * 32, "GADMIN")
        c.seal("ab" * 32, 1, "aa" * 32, GENESIS_ROOT, 1, 0, 0, 1)
        assert c.seal("ab" * 32, 2, "bb" * 32, "cc" * 32, 1, 0, 2, 3) == "chain_broken"

    def test_it_refuses_an_empty_seal_and_the_sentinel_root(self) -> None:
        c = FakeChain()
        c.register("ab" * 32, "GADMIN")
        assert c.seal("ab" * 32, 1, "aa" * 32, GENESIS_ROOT, 0, 0, 0, 1) == "empty_seal"
        assert c.seal("ab" * 32, 1, GENESIS_ROOT, GENESIS_ROOT, 1, 0, 0, 1) == ("root_is_sentinel")

    def test_it_refuses_backwards_windows(self) -> None:
        c = FakeChain()
        c.register("ab" * 32, "GADMIN")
        c.seal("ab" * 32, 1, "aa" * 32, GENESIS_ROOT, 1, 0, 100, 200)
        assert c.seal("ab" * 32, 2, "bb" * 32, "aa" * 32, 1, 0, 150, 300) == ("period_out_of_order")

    def test_the_network_sets_the_timestamp(self) -> None:
        c = FakeChain()
        c.register("ab" * 32, "GADMIN")
        before = c.now
        c.seal("ab" * 32, 1, "aa" * 32, GENESIS_ROOT, 1, 0, 0, 1)
        assert c.books["ab" * 32].seals[1].at > before


# =============================================================================
# Leaves
# =============================================================================
class TestLeaves:
    async def test_posting_records_a_leaf(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
    ) -> None:
        entry = await post_entry(posting_ctx, books, user, "100.00")

        leaf = await SealLeafRepository(db).for_entry(entry.id)
        assert leaf is not None
        assert leaf.organization_id == books.id
        assert leaf.leaf_seq == 1
        assert leaf.seal_id is None
        assert leaf.leaf_index is None
        assert leaf.canonical_version == canon.CANONICAL_VERSION

    async def test_the_leaf_hash_is_the_canonical_hash_of_the_entry(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
    ) -> None:
        """The whole chain of trust starts here, so it is checked directly rather
        than inferred from a later proof verifying."""
        entry = await post_entry(posting_ctx, books, user, "250.50")
        await db.refresh(entry, ["lines"])

        leaf = await SealLeafRepository(db).for_entry(entry.id)
        assert leaf is not None
        assert leaf.leaf_hash == canon.leaf_hash_hex(canon.payload_from_entry(entry))

    async def test_leaf_sequences_are_gap_free(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
    ) -> None:
        """A batch is a half-open *range* of sequences, so a gap would either be
        swept into a later batch - changing a published root - or skipped."""
        for index in range(5):
            await post_entry(posting_ctx, books, user, f"{index + 1}.00")

        rows = (
            (
                await db.execute(
                    select(SealLeaf.leaf_seq)
                    .where(SealLeaf.organization_id == books.id)
                    .order_by(SealLeaf.leaf_seq)
                )
            )
            .scalars()
            .all()
        )
        assert list(rows) == [1, 2, 3, 4, 5]

    async def test_no_leaf_is_recorded_without_the_hook(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
    ) -> None:
        """The proof ledger is genuinely removable.

        No `hooks` fixture here, so nothing is subscribed - and the accounting core
        behaves exactly as it did before this module existed.
        """
        from app.modules.accounting.hooks import clear_hooks

        clear_hooks()
        entry = await post_entry(posting_ctx, books, user, "10.00")
        assert await SealLeafRepository(db).for_entry(entry.id) is None

    async def test_a_failing_hook_cannot_fail_the_posting(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The posting is the statutory act; the leaf is a commentary on it.

        A bug in the proof ledger must not be able to stop a business invoicing.
        """
        from app.modules.accounting.hooks import clear_hooks, on_entry_posted

        clear_hooks()

        async def explode(session: Any, entry: Any) -> None:
            raise RuntimeError("the proof ledger is on fire")

        on_entry_posted(explode)
        try:
            entry = await post_entry(posting_ctx, books, user, "77.00")
            assert entry.entry_number is not None
            assert entry.is_posted
        finally:
            clear_hooks()


# =============================================================================
# Enabling
# =============================================================================
class TestEnabling:
    async def test_enabling_configures_and_registers(
        self, db: AsyncSession, books: Organization, user: User, chain: FakeChain
    ) -> None:
        setting = await AttestationService(db).enable(books.id, user)

        assert setting.enabled
        assert setting.contract_id == CONTRACT
        assert setting.network == "testnet"
        assert setting.registered_at is not None
        assert setting.signer_public_key
        assert setting.is_ready
        assert setting.org_namespace in chain.books

    async def test_the_namespace_is_a_salted_hash(
        self, db: AsyncSession, books: Organization, user: User, chain: FakeChain
    ) -> None:
        """Unlinkable to a named business until the business discloses it."""
        setting = await AttestationService(db).enable(books.id, user)
        assert setting.org_namespace == namespace_for(books.id)
        assert len(setting.org_namespace) == 64
        assert str(books.id) not in setting.org_namespace

    async def test_the_secret_is_encrypted_at_rest(
        self, db: AsyncSession, books: Organization, user: User, chain: FakeChain
    ) -> None:
        from app.core.security import decrypt_secret

        setting = await AttestationService(db).enable(books.id, user)
        assert setting.signer_secret_encrypted
        # Not the plaintext seed.
        assert not setting.signer_secret_encrypted.startswith("S")
        assert decrypt_secret(setting.signer_secret_encrypted).startswith("S")

    async def test_enabling_twice_converges(
        self, db: AsyncSession, books: Organization, user: User, chain: FakeChain
    ) -> None:
        """Onboarding gets retried - a closed tab, a refreshed page - so a second
        run must converge rather than fail on `already_registered`."""
        service = AttestationService(db)
        first = await service.enable(books.id, user)
        second = await service.enable(books.id, user)

        assert first.id == second.id
        assert second.enabled
        assert len(chain.books) == 1

    async def test_disabling_keeps_everything_already_sealed(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        chain: FakeChain,
        enabled: AttestationSetting,
    ) -> None:
        await post_entry(posting_ctx, books, user, "100.00")
        sealer = SealService(db)
        seal = await sealer.seal_now(books.id, user)
        assert seal is not None and seal.status is SealStatus.CONFIRMED

        await AttestationService(db).disable(books.id, user)

        # The seal is untouched and still on chain.
        assert (await SealRepository(db).latest_confirmed(books.id)) is not None
        assert chain.books[enabled.org_namespace].head == 1

    async def test_sealing_is_refused_without_a_contract(
        self, db: AsyncSession, books: Organization, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.core.config.settings.soroban_contract_id", None, raising=False)
        with pytest.raises(BusinessRuleError, match="contract"):
            await AttestationService(db).enable(books.id, user)


# =============================================================================
# Batching
# =============================================================================
class TestBatching:
    async def test_a_seal_covers_the_whole_backlog(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
    ) -> None:
        for amount in ("100.00", "250.00", "75.50"):
            await post_entry(posting_ctx, books, user, amount)

        seal = await SealService(db).create_seal(books.id, trigger=SealTrigger.MANUAL, actor=user)
        assert seal is not None
        assert seal.seq == 1
        assert seal.prev_root == GENESIS_ROOT
        assert seal.entry_count == 3
        assert seal.first_leaf_seq == 1
        assert seal.last_leaf_seq == 3
        # 425.50 in minor units at 4dp.
        assert int(seal.debit_minor) == 4_255_000

    async def test_the_root_is_the_merkle_root_of_the_leaves(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
    ) -> None:
        for amount in ("10.00", "20.00", "30.00", "40.00", "50.00"):
            await post_entry(posting_ctx, books, user, amount)

        seal = await SealService(db).create_seal(books.id, trigger=SealTrigger.MANUAL, actor=user)
        assert seal is not None

        leaves = await SealLeafRepository(db).for_seal(seal.id)
        digests = [bytes.fromhex(item.leaf_hash) for item in leaves]
        assert seal.merkle_root == mk.merkle_root(digests).hex()
        # And the tree order is pinned, not re-derived.
        assert [item.leaf_index for item in leaves] == [0, 1, 2, 3, 4]

    async def test_nothing_to_seal_returns_none(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        hooks: None,
        enabled: AttestationSetting,
    ) -> None:
        """A normal outcome of a scheduled pass, not an error."""
        assert (await SealService(db).create_seal(books.id, trigger=SealTrigger.SCHEDULE)) is None

    async def test_only_one_seal_may_be_in_flight(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """Two pending seals would both chain from the same confirmed head, so both
        would carry the same `seq` and `prev`, and the second would be refused -
        after the leaves had already been split between them."""
        await post_entry(posting_ctx, books, user, "100.00")
        sealer = SealService(db)
        first = await sealer.create_seal(books.id, trigger=SealTrigger.MANUAL, actor=user)
        assert first is not None and first.status is SealStatus.PENDING

        await post_entry(posting_ctx, books, user, "200.00")
        assert (await sealer.create_seal(books.id, trigger=SealTrigger.MANUAL, actor=user)) is None

    async def test_a_batch_is_capped(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A large backlog drains over several seals rather than failing as one."""
        monkeypatch.setattr("app.core.config.settings.seal_max_batch", 2, raising=False)
        for index in range(5):
            await post_entry(posting_ctx, books, user, f"{index + 1}.00")

        seal = await SealService(db).create_seal(books.id, trigger=SealTrigger.MANUAL, actor=user)
        assert seal is not None
        assert seal.entry_count == 2
        assert await SealLeafRepository(db).unsealed_count(books.id) == 3

    async def test_closing_a_period_prepares_a_seal(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
    ) -> None:
        """And it stays a millisecond database transaction - no network call."""
        await post_entry(posting_ctx, books, user, "500.00")

        calendar = FiscalCalendarService(db)
        # The *earliest* period, not today's. Periods must close in order - closing
        # August while April is open is refused, correctly, because the comparatives
        # would be unreconcilable. Reaching for `resolve_open_period(TODAY)` here
        # made this test fail for an accounting reason unrelated to sealing.
        from app.modules.accounting.models import AccountingPeriod

        period = (
            await db.execute(
                select(AccountingPeriod)
                .where(AccountingPeriod.organization_id == books.id)
                .order_by(AccountingPeriod.start_date)
                .limit(1)
            )
        ).scalar_one()
        assert period.status is PeriodStatus.OPEN

        await calendar.close_period(books.id, period.id, user)

        seals = await SealRepository(db).list_all(Seal.organization_id == books.id)
        assert len(seals) == 1
        assert seals[0].trigger is SealTrigger.PERIOD_CLOSE
        assert seals[0].accounting_period_id == period.id
        assert seals[0].status is SealStatus.PENDING

    async def test_covered_windows_tile_forwards(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """The contract refuses a window that starts before the last one ended, so
        consecutive batches must never overlap."""
        sealer = SealService(db)

        await post_entry(posting_ctx, books, user, "100.00")
        first = await sealer.seal_now(books.id, user)
        assert first is not None and first.status is SealStatus.CONFIRMED

        await post_entry(posting_ctx, books, user, "200.00")
        second = await sealer.seal_now(books.id, user)
        assert second is not None and second.status is SealStatus.CONFIRMED

        assert second.covered_from >= first.covered_to
        assert second.covered_to >= second.covered_from
        assert second.prev_root == first.merkle_root


# =============================================================================
# Submission, and the ambiguous failure
# =============================================================================
class TestSubmission:
    async def test_a_confirmed_seal_records_the_networks_timestamp(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """Not our clock. This field is the entire basis of the claim that a
        business cannot back-date its books, so it comes from the only party with
        no interest in it."""
        await post_entry(posting_ctx, books, user, "100.00")
        seal = await SealService(db).seal_now(books.id, user)

        assert seal is not None
        assert seal.status is SealStatus.CONFIRMED
        assert seal.tx_hash
        assert seal.sealed_at == chain.books[enabled.org_namespace].seals[1].at
        assert seal.explorer_url and "testnet" in seal.explorer_url

    async def test_an_unknown_outcome_parks_the_seal_for_the_reconciler(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """**The case this whole subsystem is designed around.**

        The transaction landed, but no verdict came back in time. Treating that as
        failure and resubmitting is how a double seal happens; treating it as
        success is how a gap happens. So it is parked.
        """
        await post_entry(posting_ctx, books, user, "100.00")
        chain.unknown_once = True

        sealer = SealService(db)
        seal = await sealer.seal_now(books.id, user)
        assert seal is not None
        assert seal.status is SealStatus.SUBMITTED
        assert seal.sealed_at is None  # we do not know, so we do not claim

        # It did land, and the next pass finds it.
        assert chain.books[enabled.org_namespace].head == 1
        result = await sealer.reconcile(books.id)
        assert result["reconciled"] is True
        assert result["chain_head"] == 1

        await db.refresh(seal)
        assert seal.status is SealStatus.CONFIRMED
        assert seal.sealed_at is not None
        # And it was never submitted twice.
        assert len(chain.submissions) == 1

    async def test_a_duplicate_rejection_is_resolved_not_retried(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """`sequence_out_of_order` on a submission we believed was needed means a
        previous attempt landed. The correct response is to reconcile."""
        await post_entry(posting_ctx, books, user, "100.00")
        sealer = SealService(db)
        seal = await sealer.create_seal(books.id, trigger=SealTrigger.MANUAL, actor=user)
        assert seal is not None

        # Somebody else's worker got there first with the same batch.
        chain.seal(
            enabled.org_namespace,
            seal.seq,
            seal.merkle_root,
            seal.prev_root,
            seal.entry_count,
            int(seal.debit_minor),
            int(seal.covered_from.timestamp()),
            int(seal.covered_to.timestamp()),
        )

        after = await sealer.submit(seal)
        assert after.status is SealStatus.CONFIRMED
        assert after.sealed_at is not None

    async def test_a_diverged_chain_fails_loudly_and_releases_the_leaves(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """Our sequence number is taken by a different root. No retry fixes that."""
        await post_entry(posting_ctx, books, user, "100.00")
        sealer = SealService(db)
        seal = await sealer.create_seal(books.id, trigger=SealTrigger.MANUAL, actor=user)
        assert seal is not None

        # Something else sealed seq 1 with a root that is not ours.
        chain.seal(
            enabled.org_namespace,
            1,
            "ff" * 32,
            GENESIS_ROOT,
            9,
            0,
            int(seal.covered_from.timestamp()),
            int(seal.covered_to.timestamp()),
        )

        after = await sealer.submit(seal)
        assert after.status is SealStatus.FAILED
        assert after.last_error and "authority" in after.last_error

        # The leaves are back in the backlog so a rebuilt seal can cover them.
        assert await SealLeafRepository(db).unsealed_count(books.id) == 1

    async def test_an_unreachable_chain_does_not_burn_an_attempt(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """A network outage must not march a perfectly good seal towards FAILED."""
        await post_entry(posting_ctx, books, user, "100.00")
        sealer = SealService(db)
        seal = await sealer.create_seal(books.id, trigger=SealTrigger.MANUAL, actor=user)
        assert seal is not None

        chain.unreachable = True
        after = await sealer.submit(seal)

        assert after.status is SealStatus.PENDING
        assert after.attempts == 0
        assert after.last_error and "unreachable" in after.last_error

        # And it succeeds once the network is back.
        chain.unreachable = False
        recovered = await sealer.submit(after)
        assert recovered.status is SealStatus.CONFIRMED

    async def test_a_failed_seal_lets_its_sequence_be_reused(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """The partial unique index earns its keep here.

        A permanent failure at sequence 1 must not block the replacement, because
        the contract's `head` never moved and 1 is still the only number it will
        accept.
        """
        await post_entry(posting_ctx, books, user, "100.00")
        sealer = SealService(db)
        seal = await sealer.create_seal(books.id, trigger=SealTrigger.MANUAL, actor=user)
        assert seal is not None

        await sealer._fail(seal, "deliberate, for the test")
        assert seal.status is SealStatus.FAILED

        replacement = await sealer.create_seal(books.id, trigger=SealTrigger.MANUAL, actor=user)
        assert replacement is not None
        assert replacement.seq == 1, "the failed sequence number must be reused"
        assert replacement.id != seal.id

        confirmed = await sealer.submit(replacement)
        assert confirmed.status is SealStatus.CONFIRMED

    async def test_retries_are_bounded(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """Failures worth retrying clear in seconds; the ones that are not do not
        clear at all, and retrying them forever buries the log line that says why."""
        from app.modules.attestation.service import MAX_SEAL_ATTEMPTS

        await post_entry(posting_ctx, books, user, "100.00")
        sealer = SealService(db)
        seal = await sealer.create_seal(books.id, trigger=SealTrigger.MANUAL, actor=user)
        assert seal is not None

        for _ in range(MAX_SEAL_ATTEMPTS):
            chain.reject_once = "empty_seal"
            seal = await sealer.submit(seal)
            if seal.status is SealStatus.FAILED:
                break

        assert seal.status is SealStatus.FAILED
        assert seal.attempts >= MAX_SEAL_ATTEMPTS

    async def test_drain_reports_what_it_did(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        await post_entry(posting_ctx, books, user, "100.00")
        sealer = SealService(db)
        await sealer.create_seal(books.id, trigger=SealTrigger.SCHEDULE)

        tally = await sealer.drain()
        assert tally["processed"] == 1
        assert tally["confirmed"] == 1
        assert tally["failed"] == 0


# =============================================================================
# Reversal semantics - the promise the contract's design rests on
# =============================================================================
class TestReversalSemantics:
    async def test_reversing_an_entry_does_not_change_its_own_leaf(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
    ) -> None:
        """Regression test for a real bug: `status` used to be hashed.

        This ledger corrects by reversal, so `posted` becoming `reversed` is the
        normal path for any entry. With the status in the hash, taking that path
        silently invalidated the entry's own proof - a business would seal its
        March books, issue a credit note in May, and find its March invoice no
        longer verified. The subsystem accused it of tampering for doing the right
        thing.

        A leaf commits to what was *recorded*, not to what later happened to it.
        """
        posting, _, _ = posting_ctx
        original = await post_entry(posting_ctx, books, user, "500.00", on=dt.date(2026, 4, 6))

        leaf = await SealLeafRepository(db).for_entry(original.id)
        assert leaf is not None
        before = leaf.leaf_hash

        await posting.reverse_entry(books.id, original.id, user, reversal_date=dt.date(2026, 5, 4))
        await db.refresh(original, ["lines"])

        # The entry is now REVERSED, and it still hashes to what was sealed.
        assert original.status.value == "reversed"
        assert canon.leaf_hash_hex(canon.payload_from_entry(original)) == before

    async def test_a_sealed_period_stays_sealed_when_an_entry_is_reversed_later(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """The test the idea submission promised.

        Post in one period, seal it, reverse in a later period, seal again. The
        first seal must still verify - a reversal is a *new leaf in a new batch*,
        never an edit to a sealed one. A naive design would want to update the
        first root; the contract makes that impossible on purpose, and this proves
        the application does not want to.
        """
        posting, _, _ = posting_ctx
        sealer = SealService(db)

        # March: post and seal.
        original = await post_entry(
            posting_ctx, books, user, "1000.00", on=dt.date(2026, 4, 6), narration="March sale"
        )
        first = await sealer.seal_now(books.id, user)
        assert first is not None and first.status is SealStatus.CONFIRMED
        first_root = first.merkle_root
        assert first.entry_count == 1

        # May: reverse it. The reversal is dated later, as it must be when the
        # original's month has closed.
        reversal = await posting.reverse_entry(
            books.id, original.id, user, reversal_date=dt.date(2026, 5, 4)
        )
        assert reversal.reverses_id == original.id

        second = await sealer.seal_now(books.id, user)
        assert second is not None and second.status is SealStatus.CONFIRMED

        # The first seal is untouched, and still what the chain holds at seq 1.
        await db.refresh(first)
        assert first.merkle_root == first_root
        assert chain.books[enabled.org_namespace].seals[1].root == first_root

        # The reversal is a new leaf in the second batch.
        assert second.seq == 2
        assert second.prev_root == first_root
        assert second.entry_count == 1

        # And the first period's proof still verifies.
        bundle = await VerifyService(db).bundle_for_entry(books.id, original.id)
        result = await VerifyService(db).verify_bundle(bundle.to_json(), check_chain=False)
        assert result.valid, result.reason

    async def test_control_totals_across_both_seals_net_to_zero(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """A reversal mirrors its original, so the two seals' debits sum to twice
        the amount - the entry and its cancellation both counted, which is what a
        ledger that corrects by reversal actually contains."""
        posting, _, _ = posting_ctx
        sealer = SealService(db)

        original = await post_entry(posting_ctx, books, user, "1000.00", on=dt.date(2026, 4, 6))
        first = await sealer.seal_now(books.id, user)
        assert first is not None

        await posting.reverse_entry(books.id, original.id, user, reversal_date=dt.date(2026, 5, 4))
        second = await sealer.seal_now(books.id, user)
        assert second is not None

        # Both the original and the mirror are on chain, each at 1000.0000.
        assert int(first.debit_minor) == 10_000_000
        assert int(second.debit_minor) == 10_000_000
        assert chain.books[enabled.org_namespace].entries == 2


# =============================================================================
# Proof bundles
# =============================================================================
class TestProofBundles:
    async def test_a_bundle_verifies_against_the_chain(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        entries = [
            await post_entry(posting_ctx, books, user, f"{(index + 1) * 10}.00")
            for index in range(7)
        ]
        seal = await SealService(db).seal_now(books.id, user)
        assert seal is not None and seal.status is SealStatus.CONFIRMED

        verifier = VerifyService(db)
        for entry in entries:
            bundle = await verifier.bundle_for_entry(books.id, entry.id)
            result = await verifier.verify_bundle(bundle.to_json())
            assert result.valid, f"{entry.entry_number}: {result.reason}"
            assert result.chain_checked
            assert result.on_chain_root == seal.merkle_root

    async def test_a_bundle_is_self_contained_and_labels_what_is_not_proven(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        entry = await post_entry(posting_ctx, books, user, "100.00")
        await SealService(db).seal_now(books.id, user)

        payload = (await VerifyService(db).bundle_for_entry(books.id, entry.id)).to_json()

        assert payload["format"] == "stellar-erp.proof.v1"
        assert payload["chain"]["org_namespace"] == enabled.org_namespace
        assert payload["seal"]["merkle_root"]
        assert payload["leaf"]["hash"]
        assert "path" in payload
        assert payload["spec"]["version"] == canon.CANONICAL_VERSION
        assert payload["how_to_verify"]
        # Account codes are labels, not facts about the money - and the bundle says so.
        assert "_note" in payload["display"]
        assert "Not covered by the proof" in payload["display"]["_note"]

    async def test_money_in_a_bundle_is_a_string(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """A JSON number is a double in the verifier's browser, and a double would
        hash to something no chain has ever seen."""
        entry = await post_entry(posting_ctx, books, user, "1234567.89")
        await SealService(db).seal_now(books.id, user)

        payload = (await VerifyService(db).bundle_for_entry(books.id, entry.id)).to_json()
        assert payload["entry"]["total_debit"] == "1234567.8900"
        assert isinstance(payload["seal"]["debit_minor"], str)

    async def test_a_tampered_amount_is_caught(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """The point of the whole exercise."""
        entry = await post_entry(posting_ctx, books, user, "100.00")
        await SealService(db).seal_now(books.id, user)

        verifier = VerifyService(db)
        payload = (await verifier.bundle_for_entry(books.id, entry.id)).to_json()
        payload["entry"]["total_debit"] = "9999.0000"

        result = await verifier.verify_bundle(payload, check_chain=False)
        assert not result.valid
        assert "altered" in result.reason

    async def test_a_tampered_root_is_caught_by_the_chain(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """A forged bundle can be internally consistent. Only the chain settles it."""
        entries = [await post_entry(posting_ctx, books, user, "100.00") for _ in range(4)]
        await SealService(db).seal_now(books.id, user)

        verifier = VerifyService(db)
        payload = (await verifier.bundle_for_entry(books.id, entries[0].id)).to_json()

        # Build a wholly fabricated single-leaf tree and present it as the seal.
        forged_leaf = canon.leaf_hash_hex(
            {**payload["entry"], "total_debit": "5000.0000", "total_credit": "5000.0000"}
        )
        payload["entry"]["total_debit"] = "5000.0000"
        payload["entry"]["total_credit"] = "5000.0000"
        payload["leaf"] = {"index": 0, "hash": forged_leaf, "canonical_version": 1}
        payload["path"] = []
        payload["seal"]["merkle_root"] = forged_leaf

        # Internally consistent...
        offline = await verifier.verify_bundle(payload, check_chain=False)
        assert offline.valid

        # ...and refused the moment the chain is consulted.
        online = await verifier.verify_bundle(payload, check_chain=True)
        assert not online.valid
        assert "not the books that were sealed" in online.reason

    async def test_an_unsealed_entry_has_no_proof(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
    ) -> None:
        entry = await post_entry(posting_ctx, books, user, "100.00")
        with pytest.raises(BusinessRuleError, match="not been sealed"):
            await VerifyService(db).bundle_for_entry(books.id, entry.id)

    async def test_an_entry_edited_after_sealing_refuses_to_export(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """Posted entries are immutable, so this can only happen if somebody edited
        the database directly - which is exactly what this subsystem exists to
        detect. Shipping a bundle that cannot verify would be worse than shipping
        none, because the business would learn it was broken from its bank.
        """
        from sqlalchemy import text

        entry = await post_entry(posting_ctx, books, user, "100.00")
        await SealService(db).seal_now(books.id, user)

        # Straight past the ORM, the way a `psql` session would.
        await db.execute(
            text("UPDATE journal_entry SET narration = :n WHERE id = :id"),
            {"n": "Edited after sealing", "id": entry.id},
        )
        # `refresh`, not `expire_all`. Expiring everything leaves the fixtures'
        # objects needing a lazy reload, which under async SQLAlchemy happens
        # outside greenlet context and raises `MissingGreenlet` - a crash that has
        # nothing to do with what this test is checking.
        await db.refresh(entry)

        with pytest.raises(ConflictError, match="no longer matches"):
            await VerifyService(db).bundle_for_entry(books.id, entry.id)

    async def test_one_invoice_proves_without_revealing_the_others(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """Selective disclosure, which is the reason for a tree rather than one hash."""
        entries = [
            await post_entry(
                posting_ctx, books, user, f"{index + 1}.00", narration=f"Customer {index}"
            )
            for index in range(12)
        ]
        await SealService(db).seal_now(books.id, user)

        payload = (await VerifyService(db).bundle_for_entry(books.id, entries[5].id)).to_json()

        # Only the disclosed entry's narration appears anywhere in the bundle.
        blob = str(payload)
        assert "Customer 5" in blob
        for index in (0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11):
            assert f"Customer {index}" not in blob
        # And the path is logarithmic, not the whole period.
        assert len(payload["path"]) <= mk.tree_depth(12)


# =============================================================================
# Status and the public chain
# =============================================================================
class TestStatus:
    async def test_status_reports_the_backlog_and_the_chain(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        await post_entry(posting_ctx, books, user, "100.00")
        await SealService(db).seal_now(books.id, user)
        await post_entry(posting_ctx, books, user, "200.00")

        status = await AttestationService(db).status(books.id)

        assert status.enabled and status.ready
        assert status.seals_confirmed == 1
        assert status.entries_sealed == 1
        assert status.unsealed_entries == 1
        assert status.chain.reachable
        assert status.chain.head == 1
        assert status.chain.agrees_with_local is True

    async def test_a_divergence_is_the_first_warning(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """Ordered by consequence: the chain disagreeing outranks everything else."""
        await post_entry(posting_ctx, books, user, "100.00")
        await SealService(db).seal_now(books.id, user)

        # Move the chain on without us.
        chain.books[enabled.org_namespace].head = 9
        chain.books[enabled.org_namespace].root = "ee" * 32

        status = await AttestationService(db).status(books.id)
        assert status.chain.agrees_with_local is False
        assert status.warnings
        assert "disagree" in status.warnings[0]

    async def test_an_unreachable_chain_still_yields_a_status(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """A business whose network is down still needs to see its backlog."""
        chain.unreachable = True
        status = await AttestationService(db).status(books.id)
        assert status.enabled
        assert status.chain.reachable is False
        assert status.chain.error

    async def test_a_server_held_key_is_disclosed_as_a_limitation(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        """Stated on the screen rather than buried in documentation, because it is
        the honest limit of what the seal proves."""
        status = await AttestationService(db).status(books.id)
        assert not status.external_signer
        assert any("co-signer" in warning for warning in status.warnings)

    async def test_the_public_chain_asserts_continuity(
        self,
        db: AsyncSession,
        books: Organization,
        user: User,
        posting_ctx: Any,
        hooks: None,
        enabled: AttestationSetting,
        chain: FakeChain,
    ) -> None:
        sealer = SealService(db)
        for amount in ("100.00", "200.00", "300.00"):
            await post_entry(posting_ctx, books, user, amount)
            await sealer.seal_now(books.id, user)

        view = await VerifyService(db).public_chain(enabled.org_namespace)

        assert view["head"] == 3
        assert view["continuous"] is True
        assert len(view["seals"]) == 3
        # Nothing identifying: no organization name, no entry, no party.
        blob = str(view)
        assert books.name not in blob
        assert "Acme" not in blob

    async def test_an_unknown_namespace_is_not_found(
        self, db: AsyncSession, chain: FakeChain
    ) -> None:
        from app.core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await VerifyService(db).public_chain("ab" * 32)


# =============================================================================
# Permissions
# =============================================================================
class TestPermissions:
    def test_sealing_is_not_implied_by_posting(self) -> None:
        """The same reasoning as `period:close`: a bookkeeper posts daily and
        should not be able to publish a commitment to a public network on the
        organization's behalf."""
        from app.modules.rbac.permissions import (
            SYSTEM_ROLE_PERMISSIONS,
            Permission,
            SystemRole,
            expand_grants,
        )

        sales = expand_grants(list(SYSTEM_ROLE_PERMISSIONS[SystemRole.SALES]))
        assert Permission.SEAL_READ in sales
        assert Permission.SEAL_WRITE not in sales
        assert Permission.SEAL_CONFIGURE not in sales
        assert Permission.PROOF_EXPORT not in sales

    def test_only_owners_and_admins_may_configure(self) -> None:
        """Disabling sealing is what makes a business stop being checkable, so it
        is separated from `seal:write`."""
        from app.modules.rbac.permissions import (
            SYSTEM_ROLE_PERMISSIONS,
            Permission,
            SystemRole,
            expand_grants,
        )

        for role in (SystemRole.OWNER, SystemRole.ADMIN):
            assert Permission.SEAL_CONFIGURE in expand_grants(list(SYSTEM_ROLE_PERMISSIONS[role]))
        for role in (SystemRole.ACCOUNTANT, SystemRole.SALES, SystemRole.VIEWER):
            assert Permission.SEAL_CONFIGURE not in expand_grants(
                list(SYSTEM_ROLE_PERMISSIONS[role])
            )

    def test_the_accountant_can_seal_and_export(self) -> None:
        """The accountant is the party the co-signing model constrains, not the one
        it locks out - they still do the monthly work."""
        from app.modules.rbac.permissions import (
            SYSTEM_ROLE_PERMISSIONS,
            Permission,
            SystemRole,
            expand_grants,
        )

        granted = expand_grants(list(SYSTEM_ROLE_PERMISSIONS[SystemRole.ACCOUNTANT]))
        assert Permission.SEAL_WRITE in granted
        assert Permission.PROOF_EXPORT in granted
        assert Permission.SEAL_CONFIGURE not in granted


class TestThePublicSurface:
    """`/verify/*` is the only unauthenticated router in the application.

    What makes that safe is not a permission check - it is that there is nothing
    behind it. These tests pin that, because "it happens not to query anything
    today" is a property a later refactor removes silently.
    """

    async def test_the_public_endpoints_issue_no_sql(
        self,
        client: AsyncClient,
        api: str,
    ) -> None:
        """A handler reachable without a token must not be able to reach a tenant row.

        The dependency hands it a session because the service is shared with the
        authenticated routes, and that is exactly why this is asserted rather than
        assumed: the day somebody adds a lookup here, an unauthenticated caller
        gains a database query, and nothing else in the suite would notice.

        Savepoint traffic from the test fixture's own commit is excluded - it is the
        harness, not the handler.
        """
        statements: list[str] = []

        def _record(
            _conn: Any,
            _cursor: Any,
            statement: str,
            _params: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            head = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
            if head in {"SELECT", "INSERT", "UPDATE", "DELETE"}:
                statements.append(statement)

        event.listen(Engine, "before_cursor_execute", _record)
        try:
            for path in (f"{api}/verify/network", f"{api}/verify/spec"):
                response = await client.get(path)
                assert response.status_code == 200, path

            response = await client.post(
                f"{api}/verify/bundle",
                json={"bundle": {"format": "nonsense"}, "check_chain": False},
            )
            assert response.status_code in {200, 422}
        finally:
            event.remove(Engine, "before_cursor_execute", _record)

        assert statements == [], f"the public verifier queried the database: {statements}"

    async def test_the_public_endpoints_need_no_token(
        self,
        client: AsyncClient,
        api: str,
    ) -> None:
        """A bank's credit officer has no account here, and requiring one would
        defeat the whole design."""
        for path in (f"{api}/verify/network", f"{api}/verify/spec"):
            assert (await client.get(path)).status_code == 200

    async def test_a_malformed_bundle_is_rejected_rather_than_crashing(
        self,
        client: AsyncClient,
        api: str,
    ) -> None:
        """The caller is a stranger holding a file they did not create. A 500 here
        reads as "their proof broke our server", which is the worst possible answer
        to give somebody deciding whether to trust us."""
        response = await client.post(
            f"{api}/verify/bundle",
            json={"bundle": {"format": "stellar-erp.proof.v1", "entry": {}}},
        )
        assert response.status_code in {200, 422}
        if response.status_code == 200:
            assert response.json()["valid"] is False

    async def test_a_namespace_must_be_32_bytes_of_hex(
        self,
        client: AsyncClient,
        api: str,
    ) -> None:
        """The namespace is the capability. Accepting a short or non-hex value would
        turn a typo into a scan of the contract's key space."""
        for bad in ("abc", "z" * 64, "0" * 63, "0" * 65):
            response = await client.get(f"{api}/verify/chain/{bad}")
            assert response.status_code == 422, bad
