"""Ledger 3's business rules.

Three services, and the split is along the axis that matters - who is trusted:

* :class:`AttestationService` - configuration and onboarding. Generates the
  signer, funds it, opens the book on chain.
* :class:`SealService` - recording leaves, batching them, and getting them onto
  the chain. Everything here has to survive the chain being slow, unreachable, or
  ambiguous.
* :class:`VerifyService` - proof bundles. The only service whose output is read by
  somebody who does not trust this server.

Nothing in this module imports a router or touches a request. The Soroban
boundary is :mod:`app.modules.attestation.stellar`, so all of this is testable
against a fake chain - which matters, because the hardest logic here is what to do
when a submission's outcome is unknown, and that is not a condition you can wait
for.

The rule that governs the whole file
------------------------------------
**The chain is the authority on what has been sealed; the database is a cache of
that belief.** Every reconciliation reads ``latest()`` and corrects local state
from it, never the reverse. That is what makes the ambiguous failure survivable:
we never have to know whether our submission landed, only what the chain says now.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import uuid
from collections.abc import Sequence
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext
from app.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.security import decrypt_secret, encrypt_secret
from app.modules.attestation.canonical import (
    CANONICAL_SPEC,
    CANONICAL_VERSION,
    leaf_hash_hex,
    money_minor,
    payload_from_entry,
    payload_to_json,
)
from app.modules.attestation.merkle import (
    ProofStep,
    inclusion_proof,
    merkle_root,
    tree_depth,
    verify_inclusion,
)
from app.modules.attestation.models import (
    AttestationSetting,
    Seal,
    SealCadence,
    SealLeaf,
    SealStatus,
    SealTrigger,
)
from app.modules.attestation.repository import (
    AttestationSettingRepository,
    SealLeafRepository,
    SealRepository,
)
from app.modules.attestation.stellar import (
    GENESIS_ROOT,
    BookView,
    SorobanClient,
    SorobanUnavailable,
    SubmitOutcome,
)
from app.modules.audit.models import AuditAction, AuditSeverity
from app.modules.audit.service import AuditService
from app.modules.users.models import User

log = get_logger(__name__)

#: How many times a seal is submitted before it is parked as ``FAILED``.
#:
#: Bounded, because the failures worth retrying (a busy ledger, a flaky RPC) clear
#: in seconds and the ones that are not (a diverged chain, an unfunded account) do
#: not clear at all. Retrying the second kind forever would burn fees and bury the
#: one log line that says what is actually wrong.
MAX_SEAL_ATTEMPTS: Final = 5

#: The proof bundle's format tag. Versioned so a verifier written today can refuse
#: a bundle it does not understand instead of guessing at it.
BUNDLE_FORMAT: Final = "stellar-erp.proof.v1"


def _audit_ctx(ctx: RequestContext | None) -> dict[str, Any]:
    """``RequestContext`` -> audit recorder kwargs. Frozen dataclasses do not
    splat, and converting in one place keeps every call site identical."""
    if ctx is None:
        return {}
    return {"ip_address": ctx.ip_address, "user_agent": ctx.user_agent}


def namespace_for(organization_id: uuid.UUID) -> str:
    """The organization's on-chain namespace: ``SHA-256(org_id || salt)``.

    Deterministic, so it can be recomputed if a settings row is lost - but it is
    *stored* rather than recomputed on every use, because the salt may legitimately
    be rotated and a recomputed namespace would silently orphan an existing chain.
    """
    material = f"{organization_id}:{settings.namespace_salt}".encode()
    return hashlib.sha256(material).hexdigest()


# =============================================================================
# View models
# =============================================================================
@dataclasses.dataclass(frozen=True, slots=True)
class ChainHealth:
    """What the chain says, next to what we believe.

    Both are reported rather than reconciled into one number, deliberately. A
    single "sealed up to X" figure would hide the one condition worth surfacing:
    the two disagreeing.
    """

    reachable: bool
    head: int | None = None
    root: str | None = None
    entries: int | None = None
    sealed_at: dt.datetime | None = None
    admin: str | None = None
    agrees_with_local: bool | None = None
    error: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class AttestationStatus:
    """Everything the Trust screen shows, resolved in one call."""

    enabled: bool
    configured: bool
    ready: bool
    network: str | None
    contract_id: str | None
    org_namespace: str | None
    cadence: SealCadence
    signer_public_key: str | None
    external_signer: bool
    registered_at: dt.datetime | None

    seals_confirmed: int
    entries_sealed: int
    unsealed_entries: int
    oldest_unsealed_at: dt.datetime | None
    last_seal: Seal | None
    open_seal: Seal | None
    chain: ChainHealth

    contract_url: str | None
    #: Human-readable reasons the operator should act on, worst first.
    warnings: tuple[str, ...] = ()

    @property
    def days_unsealed(self) -> float | None:
        """Age of the oldest unsealed entry, in days.

        The number that matters on this screen. A growing backlog is what sealing
        silently breaking looks like, and it is indistinguishable from sealing
        being switched off unless somebody is looking at the age.
        """
        if self.oldest_unsealed_at is None:
            return None
        delta = dt.datetime.now(dt.UTC) - self.oldest_unsealed_at
        return round(delta.total_seconds() / 86_400, 2)


@dataclasses.dataclass(frozen=True, slots=True)
class ProofBundle:
    """A self-contained disclosure for one journal entry.

    This is the only artefact in the system designed to be read by somebody who
    does not trust the server that produced it. Everything in it is either
    (a) covered by the on-chain root, or (b) explicitly labelled as not covered.
    There is no third category, because a field whose status a verifier has to
    guess at is a field that will be misread.
    """

    payload: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return self.payload


@dataclasses.dataclass(frozen=True, slots=True)
class VerifyResult:
    """The server's own check of a bundle.

    Offered as a convenience - a business can confirm a bundle is good before
    emailing it - and never as the authority. The verifier's browser recomputes
    everything against a public RPC, because a verdict from this endpoint is only
    worth as much as the server giving it, which is precisely the trust this
    design removes.
    """

    valid: bool
    reason: str
    leaf_hash: str | None = None
    computed_root: str | None = None
    expected_root: str | None = None
    seal_seq: int | None = None
    on_chain_root: str | None = None
    chain_checked: bool = False


# =============================================================================
# Configuration and onboarding
# =============================================================================
class AttestationService:
    """Switching the third ledger on, and keeping its configuration honest."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings_repo = AttestationSettingRepository(session)
        self.seals = SealRepository(session)
        self.leaves = SealLeafRepository(session)
        self.audit = AuditService(session)

    # -- reads -------------------------------------------------------------
    async def get_setting(self, organization_id: uuid.UUID) -> AttestationSetting | None:
        return await self.settings_repo.for_organization(organization_id)

    async def ensure_setting(self, organization_id: uuid.UUID) -> AttestationSetting:
        """The organization's settings row, created disabled if absent.

        Created rather than returned as ``None`` so the namespace is allocated once
        and stays put. Allocating it lazily at enable time would work, but it would
        also mean the namespace depends on when sealing was switched on relative to
        a salt rotation - and a namespace that can change is a chain that can be
        orphaned.
        """
        existing = await self.settings_repo.for_organization(organization_id)
        if existing is not None:
            return existing

        setting = AttestationSetting(
            organization_id=organization_id,
            enabled=False,
            org_namespace=namespace_for(organization_id),
            cadence=SealCadence.DAILY,
        )
        return await self.settings_repo.add(setting)

    async def adoption(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Every organization with a book, and what it has actually put on chain.

        Install-wide and **superuser-only**, because the question it answers is
        about the deployment rather than about one business: how many organizations
        are genuinely sealing, and can somebody confirm that independently.

        That last clause is why this exists rather than a dashboard tile. Every row
        carries the signer's ``G...`` address and the transaction hash of the most
        recent seal, so the answer is checkable on a public explorer by somebody
        who does not have to believe this endpoint. A count we assert about
        ourselves is worth very little; a count that resolves to transactions on a
        public ledger is worth something.

        What it deliberately does not return: the signer's secret, which lives in
        the same row and is never selected here, and anything from the journal. The
        aggregate figures - entries sealed, control totals - are already public on
        chain.
        """
        from sqlalchemy import func, select
        from sqlalchemy.orm import joinedload

        confirmed = (
            select(
                Seal.organization_id.label("organization_id"),
                func.count(Seal.id).label("seals"),
                func.coalesce(func.sum(Seal.entry_count), 0).label("entries"),
                func.coalesce(func.sum(Seal.debit_minor), 0).label("debit_minor"),
                func.min(Seal.sealed_at).label("first_sealed_at"),
                func.max(Seal.sealed_at).label("last_sealed_at"),
                func.max(Seal.seq).label("head"),
            )
            .where(Seal.status == SealStatus.CONFIRMED)
            .group_by(Seal.organization_id)
            .subquery()
        )

        rows = (
            (
                await self.session.execute(
                    select(AttestationSetting, confirmed)
                    .outerjoin(
                        confirmed,
                        confirmed.c.organization_id == AttestationSetting.organization_id,
                    )
                    .options(joinedload(AttestationSetting.organization))
                    # Most active first: on an install with a long tail of
                    # organizations that switched sealing on and never used it, the
                    # rows that answer the question are the ones that sealed.
                    .order_by(confirmed.c.seals.desc().nullslast())
                    .limit(limit)
                )
            )
            .unique()
            .all()
        )

        # The head seal's transaction hash, in one query rather than one per row.
        # `max(tx_hash)` would be a different seal's hash than `max(seq)` in every
        # case where they disagree, which is most of them.
        heads = {
            (org_id, seq): tx
            for org_id, seq, tx in (
                await self.session.execute(
                    select(Seal.organization_id, Seal.seq, Seal.tx_hash).where(
                        Seal.status == SealStatus.CONFIRMED
                    )
                )
            ).tuples()
        }

        out: list[dict[str, Any]] = []
        for setting, org_id, seals, entries, debit_minor, first_at, last_at, head in rows:
            out.append(
                {
                    "organization_id": setting.organization_id,
                    "organization_name": setting.organization.name,
                    "organization_slug": setting.organization.slug,
                    "enabled": setting.enabled,
                    "network": setting.network,
                    "contract_id": setting.contract_id,
                    "org_namespace": setting.org_namespace,
                    "signer_public_key": setting.signer_public_key,
                    "external_signer": setting.external_signer,
                    "registered_at": setting.registered_at,
                    "registration_tx": setting.registration_tx,
                    "cadence": setting.cadence,
                    "seals": int(seals or 0),
                    "entries_sealed": int(entries or 0),
                    "debit_minor": str(int(debit_minor or 0)),
                    "first_sealed_at": first_at,
                    "last_sealed_at": last_at,
                    "head_seq": int(head) if head is not None else None,
                    "head_tx_hash": (
                        heads.get((org_id, head)) if org_id is not None and head else None
                    ),
                }
            )
        return out

    async def status(self, organization_id: uuid.UUID) -> AttestationStatus:
        """Everything the Trust screen needs, including the chain's own view.

        The chain read is best-effort: an unreachable RPC produces a status that
        says so, rather than an error that leaves the screen blank. A business
        whose network is down still needs to see its local backlog.
        """
        setting = await self.ensure_setting(organization_id)
        entries_sealed, seals_confirmed = await self.seals.totals(organization_id)
        unsealed = await self.leaves.unsealed_count(organization_id)
        oldest = await self.leaves.oldest_unsealed_at(organization_id)
        last_seal = await self.seals.latest_confirmed(organization_id)
        open_seals = list(
            await self.seals.list_all(
                Seal.organization_id == organization_id,
                Seal.status.in_((SealStatus.PENDING, SealStatus.SUBMITTED)),
                limit=1,
            )
        )
        open_seal = open_seals[0] if open_seals else None

        chain = ChainHealth(reachable=False)
        if setting.contract_id and setting.network:
            chain = await self._chain_health(setting, last_seal)

        warnings = self._warnings(setting, chain, unsealed, oldest, open_seal)

        contract_url = None
        if setting.contract_id and setting.network:
            segment = "public" if setting.network == "public" else "testnet"
            contract_url = (
                f"https://stellar.expert/explorer/{segment}/contract/{setting.contract_id}"
            )

        return AttestationStatus(
            enabled=setting.enabled,
            configured=bool(setting.contract_id and setting.network),
            ready=setting.is_ready,
            network=setting.network,
            contract_id=setting.contract_id,
            org_namespace=setting.org_namespace,
            cadence=setting.cadence,
            signer_public_key=setting.signer_public_key,
            external_signer=setting.external_signer,
            registered_at=setting.registered_at,
            seals_confirmed=seals_confirmed,
            entries_sealed=entries_sealed,
            unsealed_entries=unsealed,
            oldest_unsealed_at=oldest,
            last_seal=last_seal,
            open_seal=open_seal,
            chain=chain,
            contract_url=contract_url,
            warnings=warnings,
        )

    async def _chain_health(
        self, setting: AttestationSetting, last_seal: Seal | None
    ) -> ChainHealth:
        try:
            client = SorobanClient(setting.network)
            book = await client.read_book(
                contract_id=setting.contract_id or "",
                namespace=setting.org_namespace,
            )
        except SorobanUnavailable as exc:
            return ChainHealth(reachable=False, error=str(exc.message))
        except Exception as exc:  # pragma: no cover - transport-level surprises
            log.warning("chain health read failed", extra={"error": str(exc)})
            return ChainHealth(reachable=False, error=str(exc)[:200])

        if book is None:
            return ChainHealth(reachable=True, head=0, root=GENESIS_ROOT, entries=0)

        agrees = None
        if last_seal is not None:
            agrees = book.head == last_seal.seq and book.root == last_seal.merkle_root
        elif book.head == 0:
            agrees = True

        return ChainHealth(
            reachable=True,
            head=book.head,
            root=book.root,
            entries=book.entries,
            sealed_at=book.sealed_at,
            admin=book.admin,
            agrees_with_local=agrees,
        )

    @staticmethod
    def _warnings(
        setting: AttestationSetting,
        chain: ChainHealth,
        unsealed: int,
        oldest: dt.datetime | None,
        open_seal: Seal | None,
    ) -> tuple[str, ...]:
        """Reasons to act, worst first.

        Ordered by consequence rather than by category, because this list is read
        top-down by somebody deciding whether to do something today. A divergence
        between the chain and the database outranks a stale backlog, which outranks
        a configuration gap.
        """
        out: list[str] = []

        if chain.agrees_with_local is False:
            out.append(
                "The chain and this database disagree about the latest seal. "
                "Nothing has been lost, but no new seal can be added until they are "
                "reconciled - the chain is the authority."
            )

        if setting.enabled and not setting.registered_at:
            out.append(
                "Sealing is on but this organization's book has not been opened on "
                "chain yet, so nothing can be sealed."
            )

        if oldest is not None:
            age_days = (dt.datetime.now(dt.UTC) - oldest).total_seconds() / 86_400
            if age_days >= 7:
                out.append(
                    f"{unsealed} entries have been waiting {int(age_days)} days to be "
                    "sealed. Anything unsealed can still be edited without trace."
                )
            elif age_days >= 2 and setting.cadence is SealCadence.DAILY:
                out.append(
                    f"The daily seal has not run for {int(age_days)} days. Check that "
                    "the seal worker is running."
                )

        if open_seal is not None and open_seal.attempts >= 2:
            out.append(
                f"Seal #{open_seal.seq} has failed to submit {open_seal.attempts} times. "
                f"Last error: {(open_seal.last_error or 'unknown')[:160]}"
            )

        if setting.enabled and not chain.reachable and chain.error:
            out.append(f"The Stellar network could not be reached: {chain.error}")

        if setting.enabled and not setting.external_signer:
            out.append(
                "The signing key is held on this server, so a seal proves the books "
                "have not changed *since* it was written - not that they were correct "
                "when it was. Adding your accountant as a co-signer closes that gap."
            )

        return tuple(out)

    # -- writes ------------------------------------------------------------
    async def enable(
        self,
        organization_id: uuid.UUID,
        actor: User,
        *,
        cadence: SealCadence = SealCadence.DAILY,
        secret_key: str | None = None,
        fund_on_testnet: bool = True,
        ctx: RequestContext | None = None,
    ) -> AttestationSetting:
        """Switch sealing on: configure a signer, fund it, open the book on chain.

        Idempotent by design, because onboarding gets retried. Every step checks
        whether it has already been done - the account may exist, the book may be
        open - and a second run converges rather than failing. An onboarding flow
        that cannot be re-run is one that leaves a half-configured organization the
        first time a browser tab is closed at the wrong moment.

        ``secret_key`` lets a business bring its own account. Absent, one is
        generated. Either way the secret is Fernet-encrypted before it touches the
        database.
        """
        if not settings.attestation_enabled:
            raise BusinessRuleError(
                "The proof ledger is disabled on this server (ATTESTATION_ENABLED)."
            )
        if not settings.soroban_contract_id:
            raise BusinessRuleError(
                "No proof-ledger contract is configured on this server. Deploy "
                "contracts/proof_ledger and set SOROBAN_CONTRACT_ID."
            )

        setting = await self.ensure_setting(organization_id)
        client = SorobanClient(settings.stellar_network)

        # 1. Signer.
        if secret_key:
            try:
                public_key = client.public_key_of(secret_key.strip())
            except Exception as exc:
                raise ValidationError(
                    "That does not look like a Stellar secret key. It should start "
                    "with S and be 56 characters.",
                    details={"secret_key": "invalid"},
                ) from exc
            setting.signer_public_key = public_key
            setting.signer_secret_encrypted = encrypt_secret(secret_key.strip())
        elif not setting.signer_secret_encrypted:
            public_key, generated = client.generate_keypair()
            setting.signer_public_key = public_key
            setting.signer_secret_encrypted = encrypt_secret(generated)

        setting.external_signer = setting.signer_secret_encrypted is None
        setting.contract_id = settings.soroban_contract_id
        setting.network = settings.stellar_network
        setting.cadence = cadence

        # 2. Funding. Testnet only, and already-funded counts as success.
        if fund_on_testnet and settings.stellar_network == "testnet":
            assert setting.signer_public_key is not None  # noqa: S101 - set above
            if not await client.account_exists(setting.signer_public_key):
                await client.fund_testnet_account(setting.signer_public_key)

        # 3. Open the book. Already-open counts as success: `register` refuses a
        #    second book, and that refusal means the desired state already holds.
        if not setting.registered_at:
            already = await client.is_registered(
                contract_id=setting.contract_id,
                namespace=setting.org_namespace,
            )
            if already:
                setting.registered_at = dt.datetime.now(dt.UTC)
            else:
                secret = self._secret_of(setting)
                outcome = await client.register_book(
                    contract_id=setting.contract_id,
                    namespace=setting.org_namespace,
                    secret=secret,
                )
                if outcome.is_confirmed:
                    setting.registered_at = outcome.network_time or dt.datetime.now(dt.UTC)
                    setting.registration_tx = outcome.tx_hash
                elif outcome.error_name == "already_registered":
                    setting.registered_at = dt.datetime.now(dt.UTC)
                elif outcome.is_unknown:
                    # The registration may yet land. Leave it unregistered so the
                    # next attempt re-checks `is_registered` and converges, rather
                    # than recording a registration we cannot confirm.
                    raise ConflictError(
                        "The registration was submitted but not confirmed in time. "
                        "Try again in a moment - it will not be registered twice.",
                        details={"tx_hash": outcome.tx_hash},
                    )
                else:
                    raise BusinessRuleError(
                        f"Opening the on-chain book failed: {outcome.error_name or ''} "
                        f"{(outcome.message or '')[:300]}".strip()
                    )

        setting.enabled = True
        await self.session.flush()

        await self.audit.record(
            AuditAction.ATTESTATION_ENABLED,
            actor=actor,
            organization_id=organization_id,
            resource_type="attestation_setting",
            resource_id=setting.id,
            summary=(f"Enabled the proof ledger on {setting.network} (cadence: {cadence.value})"),
            context={
                "network": setting.network,
                "contract_id": setting.contract_id,
                "org_namespace": setting.org_namespace,
                "signer": setting.signer_public_key,
                "registration_tx": setting.registration_tx,
            },
            **_audit_ctx(ctx),
        )
        log.info(
            "attestation enabled",
            extra={
                "organization_id": str(organization_id),
                "network": setting.network,
                "namespace": setting.org_namespace[:12],
            },
        )
        return setting

    async def disable(
        self,
        organization_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> AttestationSetting:
        """Stop sealing.

        Nothing on chain is touched, and nothing local is deleted. Seals already
        written stay written and stay verifiable forever - which is the point of
        having put them there. What stops is new ones.

        Audited at **critical** severity, because a business that stops sealing
        stops being checkable, and the date it stopped is the first thing anyone
        reviewing the chain will want.
        """
        setting = await self.ensure_setting(organization_id)
        if not setting.enabled:
            return setting

        setting.enabled = False
        await self.session.flush()

        await self.audit.record(
            AuditAction.ATTESTATION_DISABLED,
            actor=actor,
            organization_id=organization_id,
            resource_type="attestation_setting",
            resource_id=setting.id,
            summary="Disabled the proof ledger - new entries will not be sealed",
            severity=AuditSeverity.CRITICAL,
            **_audit_ctx(ctx),
        )
        return setting

    async def set_cadence(
        self,
        organization_id: uuid.UUID,
        cadence: SealCadence,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> AttestationSetting:
        setting = await self.ensure_setting(organization_id)
        before = setting.cadence
        setting.cadence = cadence
        await self.session.flush()

        await self.audit.record(
            AuditAction.SETTINGS_UPDATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="attestation_setting",
            resource_id=setting.id,
            summary=f"Seal cadence changed from {before.value} to {cadence.value}",
            changes={"cadence": {"before": before.value, "after": cadence.value}},
            **_audit_ctx(ctx),
        )
        return setting

    async def rotate_signer(
        self,
        organization_id: uuid.UUID,
        new_admin: str,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> AttestationSetting:
        """Hand the book to a different Stellar account.

        The upgrade path to 2-of-3 co-signing: the business creates a multisig
        account whose signers are itself, its accountant, and a neutral third
        party, then rotates the book onto it.

        The contract requires **both** the outgoing and incoming accounts to
        authorise, so this only succeeds when this server's key is also a signer on
        the destination - which is the intended arrangement, and is what lets
        unattended daily sealing continue after the rotation.

        After a successful rotation the local secret is *kept*, because it is still
        one of the multisig's signers. What changes is that its signature alone is
        no longer sufficient, which is the entire point.
        """
        setting = await self.ensure_setting(organization_id)
        if not setting.is_ready:
            raise BusinessRuleError("Sealing is not configured for this organization.")
        if await self.seals.exists_open(organization_id):
            raise ConflictError(
                "A seal is still in flight. Wait for it to confirm before rotating "
                "the signer, or the in-flight seal will be signed by an account that "
                "no longer administers the book."
            )

        client = SorobanClient(setting.network)
        outcome = await client.rotate_admin(
            contract_id=setting.contract_id or "",
            namespace=setting.org_namespace,
            secret=self._secret_of(setting),
            new_admin=new_admin.strip(),
        )
        if not outcome.is_confirmed:
            raise BusinessRuleError(
                "The rotation was not accepted. Both the current and the new account "
                "must authorise it, so this server's key must be a signer on the new "
                f"account. {outcome.error_name or ''} "
                f"{(outcome.message or '')[:200]}".strip()
            )

        before = setting.signer_public_key
        setting.signer_public_key = new_admin.strip()
        # The destination is a multisig this server co-signs, so it is no longer
        # true that the key here alone controls the book.
        setting.external_signer = True
        await self.session.flush()

        await self.audit.record(
            AuditAction.ATTESTATION_SIGNER_ROTATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="attestation_setting",
            resource_id=setting.id,
            summary=f"Proof-ledger signer rotated to {new_admin[:8]}…",
            severity=AuditSeverity.CRITICAL,
            changes={"signer": {"before": before, "after": new_admin}},
            context={"tx_hash": outcome.tx_hash},
            **_audit_ctx(ctx),
        )
        return setting

    @staticmethod
    def _secret_of(setting: AttestationSetting) -> str:
        """Decrypt the signer's seed, or explain why there isn't one."""
        if not setting.signer_secret_encrypted:
            raise BusinessRuleError(
                "This organization's signing key is held outside this server, so the "
                "server cannot sign on its behalf."
            )
        return decrypt_secret(setting.signer_secret_encrypted)


# =============================================================================
# Sealing
# =============================================================================
class SealService:
    """Leaves, batches, and the chain."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings_repo = AttestationSettingRepository(session)
        self.leaves = SealLeafRepository(session)
        self.seals = SealRepository(session)
        self.audit = AuditService(session)

    # -- leaves ------------------------------------------------------------
    async def record_leaf(self, entry: Any) -> SealLeaf | None:
        """Hash a freshly posted entry and store its leaf.

        **Called from inside** ``PostingService.post_entry``'s transaction, so a
        leaf cannot go missing for an entry that reached the books.

        Two decisions worth stating:

        **Leaves are recorded whether or not this organization has sealing switched
        on** - only the server-wide ``ATTESTATION_ENABLED`` gates it. A leaf is
        about 200 bytes, and recording them from the start means a business that
        switches sealing on in its second year can seal its entire history in one
        batch instead of only what comes next. The alternative - start hashing at
        enable time - would make the first seal cover an arbitrary suffix of the
        books, which is a much less useful thing to hand a lender.

        **A failure here must never fail the posting.** The posting is the
        statutory act; the leaf is a commitment to it. If hashing raises - a money
        value that cannot be encoded, say - the entry still posts, the failure is
        logged loudly, and the entry simply has no leaf and will not be sealed.
        Getting this backwards would mean a bug in the attestation module could
        stop a business invoicing.
        """
        if not settings.attestation_enabled:
            return None

        try:
            existing = await self.leaves.for_entry(entry.id)
            if existing is not None:
                # Re-posting is impossible, so this means a retry inside one
                # transaction. Idempotent rather than a constraint violation.
                return existing

            payload = payload_from_entry(entry)
            digest = leaf_hash_hex(payload)
            seq = await self.leaves.next_leaf_seq(entry.organization_id)

            leaf = SealLeaf(
                organization_id=entry.organization_id,
                created_at=dt.datetime.now(dt.UTC),
                journal_entry_id=entry.id,
                leaf_seq=seq,
                leaf_hash=digest,
                canonical_version=CANONICAL_VERSION,
                entry_date=entry.entry_date,
                total_debit=entry.total_debit,
            )
            return await self.leaves.add(leaf)
        except Exception as exc:
            log.error(
                "failed to record an attestation leaf - the entry is posted but will not be sealed",
                extra={
                    "journal_entry_id": str(getattr(entry, "id", None)),
                    "organization_id": str(getattr(entry, "organization_id", None)),
                    "error": str(exc),
                },
            )
            return None

    # -- batching ----------------------------------------------------------
    async def create_seal(
        self,
        organization_id: uuid.UUID,
        *,
        trigger: SealTrigger,
        actor: User | None = None,
        accounting_period_id: uuid.UUID | None = None,
        ctx: RequestContext | None = None,
    ) -> Seal | None:
        """Select the unsealed backlog, compute its root, and write the intent.

        Returns ``None`` when there is nothing to seal, which is the normal
        outcome of a scheduled pass and not an error.

        **Everything here is local.** No network call, so this can be - and is -
        called inside the transaction that closes an accounting period. The
        transaction commits in milliseconds and a chain outage cannot block a
        month-end close. The worker takes it from there.
        """
        setting = await self.settings_repo.for_organization(organization_id)
        if setting is None or not setting.enabled:
            return None
        if not setting.registered_at:
            log.warning(
                "skipping seal: the on-chain book is not open yet",
                extra={"organization_id": str(organization_id)},
            )
            return None

        # One seal in flight at a time. Two pending seals would both be built
        # against the same confirmed head, so both would carry the same `seq` and
        # `prev`, and the second would be refused - after the leaves had already
        # been split between them.
        if await self.seals.exists_open(organization_id):
            return None

        batch = list(await self.leaves.unsealed(organization_id, limit=settings.seal_max_batch))
        if not batch:
            return None

        previous = await self.seals.latest_confirmed(organization_id)
        prev_root = previous.merkle_root if previous else GENESIS_ROOT
        seq = await self.seals.highest_live_seq(organization_id) + 1

        root = merkle_root([bytes.fromhex(leaf.leaf_hash) for leaf in batch])
        debit_minor = sum(money_minor(leaf.total_debit) for leaf in batch)

        covered_from, covered_to = self._covered_window(batch, previous)

        seal = Seal(
            organization_id=organization_id,
            seq=seq,
            merkle_root=root.hex(),
            prev_root=prev_root,
            entry_count=len(batch),
            debit_minor=debit_minor,
            first_leaf_seq=batch[0].leaf_seq,
            last_leaf_seq=batch[-1].leaf_seq,
            covered_from=covered_from,
            covered_to=covered_to,
            entry_date_from=min(leaf.entry_date for leaf in batch),
            entry_date_to=max(leaf.entry_date for leaf in batch),
            trigger=trigger,
            accounting_period_id=accounting_period_id,
            status=SealStatus.PENDING,
            network=setting.network,
            contract_id=setting.contract_id,
            created_by_id=actor.id if actor else None,
        )
        self.session.add(seal)
        await self.session.flush()
        await self.leaves.assign_to_seal(batch, seal.id)

        await self.audit.record(
            AuditAction.SEAL_CREATED,
            actor=actor,
            organization_id=organization_id,
            resource_type="seal",
            resource_id=seal.id,
            summary=(
                f"Prepared seal #{seq} over {len(batch)} entries "
                f"({seal.entry_date_from} to {seal.entry_date_to})"
            ),
            context={
                "seq": seq,
                "merkle_root": seal.merkle_root,
                "prev_root": prev_root,
                "entry_count": len(batch),
                "trigger": trigger.value,
            },
            **_audit_ctx(ctx),
        )
        log.info(
            "seal prepared",
            extra={
                "organization_id": str(organization_id),
                "seq": seq,
                "entries": len(batch),
                "root": seal.merkle_root[:16],
            },
        )
        return seal

    @staticmethod
    def _covered_window(
        batch: Sequence[SealLeaf], previous: Seal | None
    ) -> tuple[dt.datetime, dt.datetime]:
        """The batch's posting-time window, clamped so windows tile forwards.

        The contract refuses a seal whose ``from`` precedes the previous seal's
        ``to``, and it is right to: interleaving windows is the one shape in which a
        fabricated period could be slipped between two real ones.

        Batches are consecutive in ``leaf_seq``, so their windows already tile in
        the ordinary case. The clamp exists for the case they do not - a clock
        stepping backwards over an NTP correction, or two leaves written in the same
        millisecond at a boundary. Rather than submit a seal the contract will
        refuse, the window is nudged forward to start exactly where the last one
        ended. Nothing is lost: the window is metadata about *when* the batch was
        committed, and the leaves it covers are fixed by ``leaf_seq`` regardless.
        """
        start = batch[0].created_at
        end = batch[-1].created_at

        if previous is not None and start < previous.covered_to:
            start = previous.covered_to
        if end < start:
            end = start
        return start, end

    # -- submission --------------------------------------------------------
    async def submit(self, seal: Seal) -> Seal:
        """Get one prepared seal onto the chain, or work out what already happened.

        The order of operations is the whole point, and it is: **ask the chain
        first.** Only once we know the chain's head do we know whether this seal is
        needed, already landed, or impossible.
        """
        setting = await self.settings_repo.for_organization(seal.organization_id)
        if setting is None or not setting.is_ready:
            return await self._fail(seal, "Sealing is no longer configured for this organization.")

        client = SorobanClient(seal.network or setting.network)

        try:
            book = await client.read_book(
                contract_id=seal.contract_id or setting.contract_id or "",
                namespace=setting.org_namespace,
            )
        except SorobanUnavailable as exc:
            # Not a failure of the seal - a failure to reach the network. Leave the
            # row exactly as it is and try again next pass; burning an attempt on an
            # unreachable RPC would march a perfectly good seal towards FAILED.
            log.warning(
                "chain unreachable while submitting a seal",
                extra={"seal_id": str(seal.id), "error": str(exc.message)},
            )
            seal.last_error = f"chain unreachable: {exc.message}"
            await self.session.flush()
            return seal

        if book is None:
            return await self._fail(
                seal,
                "The on-chain book does not exist. Re-run onboarding to open it.",
            )

        # Already past us: either this seal landed, or a different one did.
        if book.head >= seal.seq:
            return await self._resolve_against_chain(seal, setting, client, book)

        # Not yet at us: an earlier seal has not confirmed. Do not skip ahead - the
        # contract would refuse it, and the fee would buy nothing.
        if book.head < seal.seq - 1:
            seal.last_error = (
                f"The chain is at seal #{book.head} but this is #{seal.seq}. "
                "An earlier seal must confirm first."
            )
            await self.session.flush()
            return seal

        # book.head == seal.seq - 1: our turn. Check the chain agrees about what we
        # are chaining from before spending a fee to find out.
        if book.root != seal.prev_root:
            return await self._fail(
                seal,
                "The chain's latest root does not match what this seal chains from, "
                "so the local history has diverged from the chain. The chain is the "
                "authority; this seal must be rebuilt.",
                severity=AuditSeverity.CRITICAL,
            )

        seal.attempts += 1
        seal.submitted_at = dt.datetime.now(dt.UTC)
        seal.status = SealStatus.SUBMITTED
        await self.session.flush()

        outcome = await client.submit_seal(
            contract_id=seal.contract_id or setting.contract_id or "",
            namespace=setting.org_namespace,
            secret=AttestationService._secret_of(setting),
            seq=seal.seq,
            root=seal.merkle_root,
            prev=seal.prev_root,
            count=seal.entry_count,
            debit_minor=seal.debits,
            covered_from=int(seal.covered_from.timestamp()),
            covered_to=int(seal.covered_to.timestamp()),
        )
        return await self._apply_outcome(seal, setting, client, outcome)

    async def _apply_outcome(
        self,
        seal: Seal,
        setting: AttestationSetting,
        client: SorobanClient,
        outcome: SubmitOutcome,
    ) -> Seal:
        if outcome.is_confirmed:
            return await self._confirm(seal, setting, client, outcome)

        if outcome.is_unknown:
            # The honest state. The transaction is out there; the next pass asks the
            # chain rather than resubmitting blind.
            seal.status = SealStatus.SUBMITTED
            seal.tx_hash = outcome.tx_hash
            seal.last_error = outcome.message
            await self.session.flush()
            return seal

        if outcome.already_sealed:
            # `sequence_out_of_order` means the head has moved past us - a previous
            # attempt landed after we stopped waiting for it. Reconcile, do not retry.
            book = await client.read_book(
                contract_id=seal.contract_id or "",
                namespace=setting.org_namespace,
            )
            if book is not None:
                return await self._resolve_against_chain(seal, setting, client, book)

        seal.last_error = f"{outcome.error_name or 'rejected'}: {(outcome.message or '')[:400]}"
        seal.status = SealStatus.PENDING
        await self.session.flush()

        if seal.attempts >= MAX_SEAL_ATTEMPTS:
            return await self._fail(
                seal,
                f"Gave up after {seal.attempts} attempts. Last error: {seal.last_error}",
                severity=AuditSeverity.CRITICAL,
            )
        return seal

    async def _resolve_against_chain(
        self,
        seal: Seal,
        setting: AttestationSetting,
        client: SorobanClient,
        book: BookView,
    ) -> Seal:
        """Decide what a seal's local status should be, given the chain's view.

        Reached whenever the chain's head is at or beyond this seal - which happens
        after an ambiguous timeout, after a duplicate rejection, and on every
        reconciliation pass. It reads the seal the contract actually holds at our
        sequence number and compares roots, because "the head has moved" is not the
        same as "our seal is what moved it".
        """
        on_chain = await client.read_seal(
            contract_id=seal.contract_id or setting.contract_id or "",
            namespace=setting.org_namespace,
            seq=seal.seq,
        )

        if on_chain is None:
            return await self._fail(
                seal,
                f"The chain reports a head of #{book.head} but holds no seal at "
                f"#{seal.seq}. Local state cannot be reconciled automatically.",
                severity=AuditSeverity.CRITICAL,
            )

        if on_chain.root != seal.merkle_root:
            # Our sequence number is taken by a *different* root. The books have
            # diverged from what was published, and no amount of retrying fixes it.
            return await self._fail(
                seal,
                f"Seal #{seal.seq} is already on chain with a different root "
                f"({on_chain.root[:16]}… on chain, {seal.merkle_root[:16]}… locally). "
                "The chain is the authority.",
                severity=AuditSeverity.CRITICAL,
            )

        # Our seal is on chain and we simply had not recorded it.
        was_unrecorded = seal.status is not SealStatus.CONFIRMED
        seal.status = SealStatus.CONFIRMED
        seal.sealed_at = on_chain.at
        seal.confirmed_at = dt.datetime.now(dt.UTC)
        # `ledger_sequence` is deliberately left as it was. It is only known from a
        # submission we watched; a seal discovered by reconciliation has no ledger
        # number to report, and inventing one would put a figure in the audit trail
        # that nothing on chain backs.
        await self.session.flush()

        if was_unrecorded:
            await self.audit.record(
                AuditAction.SEAL_RECONCILED,
                organization_id=seal.organization_id,
                resource_type="seal",
                resource_id=seal.id,
                summary=(
                    f"Seal #{seal.seq} was found on chain and recorded locally - "
                    "the submission had landed after we stopped waiting"
                ),
                context={"merkle_root": seal.merkle_root, "tx_hash": seal.tx_hash},
            )
        return seal

    async def _confirm(
        self,
        seal: Seal,
        setting: AttestationSetting,
        client: SorobanClient,
        outcome: SubmitOutcome,
    ) -> Seal:
        """Record a confirmed seal, taking the timestamp from the chain.

        The network's ``at`` is read back from the contract rather than taken from
        our own clock or from the transaction's ``created_at``. It is the field the
        verifier's whole claim rests on - "sealed at a time the business cannot
        back-date" - so it comes from the only party with no interest in it.
        """
        on_chain = await client.read_seal(
            contract_id=seal.contract_id or setting.contract_id or "",
            namespace=setting.org_namespace,
            seq=seal.seq,
        )

        seal.status = SealStatus.CONFIRMED
        seal.tx_hash = outcome.tx_hash
        seal.ledger_sequence = outcome.ledger
        seal.confirmed_at = dt.datetime.now(dt.UTC)
        seal.sealed_at = on_chain.at if on_chain else outcome.network_time
        seal.last_error = None
        await self.session.flush()

        await self.audit.record(
            AuditAction.SEAL_CONFIRMED,
            organization_id=seal.organization_id,
            resource_type="seal",
            resource_id=seal.id,
            summary=(
                f"Seal #{seal.seq} confirmed on {seal.network} - "
                f"{seal.entry_count} entries, root {seal.merkle_root[:16]}…"
            ),
            context={
                "seq": seal.seq,
                "tx_hash": seal.tx_hash,
                "ledger": seal.ledger_sequence,
                "sealed_at": seal.sealed_at.isoformat() if seal.sealed_at else None,
                "explorer": seal.explorer_url,
            },
        )
        log.info(
            "seal confirmed",
            extra={
                "organization_id": str(seal.organization_id),
                "seq": seal.seq,
                "tx_hash": seal.tx_hash,
                "entries": seal.entry_count,
            },
        )
        return seal

    async def _fail(
        self,
        seal: Seal,
        reason: str,
        *,
        severity: AuditSeverity = AuditSeverity.WARNING,
    ) -> Seal:
        """Park a seal as failed and hand its leaves back to the backlog.

        Releasing the leaves is what makes a failure recoverable: they return to
        the head of the queue with their original sequence numbers, so the
        replacement batch covers the same range and reuses the same on-chain
        sequence number - which is still the only one the contract will accept,
        because a failed submission never moved its head.

        The failed row itself is kept. "We tried to seal on the 3rd and it was
        refused" is exactly the sort of thing an auditor is entitled to see rather
        than have tidied away.
        """
        seal.status = SealStatus.FAILED
        seal.last_error = reason[:2000]
        await self.session.flush()
        await self.leaves.release_from_seal(seal.id)

        await self.audit.record(
            AuditAction.SEAL_FAILED,
            organization_id=seal.organization_id,
            resource_type="seal",
            resource_id=seal.id,
            summary=f"Seal #{seal.seq} failed: {reason[:200]}",
            severity=severity,
            context={"seq": seal.seq, "attempts": seal.attempts, "reason": reason[:1000]},
        )
        log.error(
            "seal failed",
            extra={
                "organization_id": str(seal.organization_id),
                "seq": seal.seq,
                "attempts": seal.attempts,
                "reason": reason[:300],
            },
        )
        return seal

    # -- worker entry points ----------------------------------------------
    async def drain(self, *, limit: int = 25) -> dict[str, int]:
        """Advance every seal that is still owed an outcome.

        The worker's inner call, and also reachable from an endpoint so an operator
        can force a pass without waiting for the timer. One code path, so what runs
        on a schedule is the same thing that runs when somebody presses the button.
        """
        pending = await self.seals.open_work(limit=limit)
        tally = {"processed": 0, "confirmed": 0, "failed": 0, "waiting": 0}

        for seal in pending:
            tally["processed"] += 1
            try:
                after = await self.submit(seal)
            except SorobanUnavailable as exc:
                log.warning(
                    "chain unavailable during drain",
                    extra={"seal_id": str(seal.id), "error": str(exc.message)},
                )
                tally["waiting"] += 1
                continue
            except Exception as exc:
                # One bad seal must not stop the queue. Logged, counted, moved past.
                log.error(
                    "unexpected error while submitting a seal",
                    extra={"seal_id": str(seal.id), "error": str(exc)},
                )
                tally["waiting"] += 1
                continue

            if after.status is SealStatus.CONFIRMED:
                tally["confirmed"] += 1
            elif after.status is SealStatus.FAILED:
                tally["failed"] += 1
            else:
                tally["waiting"] += 1

        return tally

    async def seal_now(
        self,
        organization_id: uuid.UUID,
        actor: User,
        ctx: RequestContext | None = None,
    ) -> Seal | None:
        """Prepare and immediately submit a seal, for the Trust screen's button.

        Submits inline rather than leaving it to the worker, because a person is
        watching. If the submission's outcome is unknown the row is still parked
        for the worker, so pressing the button never leaves a seal orphaned.
        """
        seal = await self.create_seal(
            organization_id,
            trigger=SealTrigger.MANUAL,
            actor=actor,
            ctx=ctx,
        )
        if seal is None:
            return None
        return await self.submit(seal)

    async def reconcile(self, organization_id: uuid.UUID) -> dict[str, Any]:
        """Correct local state from the chain.

        Run on worker startup and available on demand. It answers one question -
        what does the chain say? - and makes the database agree. Never the reverse.
        """
        setting = await self.settings_repo.for_organization(organization_id)
        if setting is None or not setting.contract_id or not setting.network:
            return {"reconciled": False, "reason": "not configured"}

        client = SorobanClient(setting.network)
        book = await client.read_book(
            contract_id=setting.contract_id, namespace=setting.org_namespace
        )
        if book is None:
            return {"reconciled": False, "reason": "no book on chain"}

        changed = 0
        for seal in await self.seals.open_work(limit=100):
            if seal.organization_id != organization_id:
                continue
            if book.head >= seal.seq:
                await self._resolve_against_chain(seal, setting, client, book)
                changed += 1

        local_head = await self.seals.latest_confirmed(organization_id)
        return {
            "reconciled": True,
            "chain_head": book.head,
            "chain_root": book.root,
            "local_head": local_head.seq if local_head else 0,
            "adjusted": changed,
            "agrees": bool(
                local_head and local_head.seq == book.head and local_head.merkle_root == book.root
            )
            or (local_head is None and book.head == 0),
        }


# =============================================================================
# Verification
# =============================================================================
class VerifyService:
    """Proof bundles - the only output read by somebody who distrusts us."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings_repo = AttestationSettingRepository(session)
        self.leaves = SealLeafRepository(session)
        self.seals = SealRepository(session)
        self.audit = AuditService(session)

    async def bundle_for_entry(
        self,
        organization_id: uuid.UUID,
        journal_entry_id: uuid.UUID,
        *,
        actor: User | None = None,
        ctx: RequestContext | None = None,
    ) -> ProofBundle:
        """Build a self-contained proof for one journal entry.

        The server checks its own work before handing it over: the leaf is
        recomputed from the payload that will actually be sent, and the path is
        walked back to the root. Shipping a bundle that does not verify would be
        worse than shipping none, because the business would find out from its bank
        rather than from us.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.modules.accounting.models import Account, JournalEntry

        setting = await self.settings_repo.for_organization(organization_id)
        if setting is None or not setting.contract_id:
            raise BusinessRuleError("The proof ledger is not configured for this organization.")

        leaf = await self.leaves.for_entry(journal_entry_id)
        if leaf is None or leaf.organization_id != organization_id:
            raise NotFoundError("Proof")
        if leaf.seal_id is None:
            raise BusinessRuleError(
                "This entry has not been sealed yet, so there is nothing to prove. "
                "Seal the books first."
            )

        seal = await self.seals.get(leaf.seal_id)
        if seal is None or seal.status is not SealStatus.CONFIRMED:
            raise BusinessRuleError(
                "The seal covering this entry has not been confirmed on chain yet."
            )

        entry = (
            await self.session.execute(
                select(JournalEntry)
                .where(
                    JournalEntry.id == journal_entry_id,
                    JournalEntry.organization_id == organization_id,
                )
                .options(selectinload(JournalEntry.lines))
            )
        ).scalar_one_or_none()
        if entry is None:  # pragma: no cover - the leaf's FK guarantees it
            raise NotFoundError("Journal entry")

        payload = payload_from_entry(entry)
        recomputed = leaf_hash_hex(payload)
        if recomputed != leaf.leaf_hash:
            # The entry no longer hashes to what was sealed. Posted entries are
            # immutable, so this is either a canonical-encoding change that escaped
            # the golden-vector test, or somebody has edited the database directly.
            # Both are exactly what this subsystem exists to detect, and neither is
            # something to paper over by shipping a bundle that cannot verify.
            log.error(
                "an entry no longer matches its sealed leaf",
                extra={
                    "journal_entry_id": str(journal_entry_id),
                    "sealed": leaf.leaf_hash,
                    "recomputed": recomputed,
                },
            )
            raise ConflictError(
                "This entry no longer matches the hash that was sealed. The books "
                "have been altered since sealing, or the canonical encoding has "
                "changed. A proof cannot be issued.",
                details={"sealed_hash": leaf.leaf_hash, "current_hash": recomputed},
            )

        siblings = await self.leaves.for_seal(seal.id)
        digests = [bytes.fromhex(item.leaf_hash) for item in siblings]
        index = leaf.leaf_index or 0
        path: list[ProofStep] = inclusion_proof(digests, index)

        if not verify_inclusion(bytes.fromhex(recomputed), path, bytes.fromhex(seal.merkle_root)):
            raise ConflictError(
                "The proof this server built does not verify against the sealed "
                "root, so it will not be issued. This is a bug and has been logged."
            )

        # Account codes and names travel as display metadata, clearly separated,
        # because they are *not* hashed - see the note in `canonical`.
        account_ids = [line.account_id for line in entry.lines]
        accounts = (
            (
                await self.session.execute(
                    select(Account.id, Account.code, Account.name).where(
                        Account.id.in_(account_ids)
                    )
                )
            )
            .tuples()
            .all()
        )
        account_labels = {str(row[0]): {"code": row[1], "name": row[2]} for row in accounts}

        bundle: dict[str, Any] = {
            "format": BUNDLE_FORMAT,
            "generated_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
            "chain": {
                "network": seal.network,
                "contract_id": seal.contract_id,
                "org_namespace": setting.org_namespace,
                "rpc_hint": settings.soroban_rpc_url or None,
                "explorer_tx": seal.explorer_url,
            },
            "seal": {
                "seq": seal.seq,
                "merkle_root": seal.merkle_root,
                "prev_root": seal.prev_root,
                "entry_count": seal.entry_count,
                "debit_minor": str(seal.debits),
                "covered_from": seal.covered_from.isoformat().replace("+00:00", "Z"),
                "covered_to": seal.covered_to.isoformat().replace("+00:00", "Z"),
                "sealed_at": (
                    seal.sealed_at.isoformat().replace("+00:00", "Z") if seal.sealed_at else None
                ),
                "tx_hash": seal.tx_hash,
                "ledger": seal.ledger_sequence,
                "tree_depth": tree_depth(seal.entry_count),
            },
            "leaf": {
                "index": index,
                "hash": recomputed,
                "canonical_version": leaf.canonical_version,
            },
            "path": path,
            # The hashed payload. Re-encoded and re-hashed by the verifier.
            "entry": payload_to_json(payload),
            # NOT hashed. Labelled so a verifier cannot mistake it for proven.
            "display": {
                "_note": (
                    "Not covered by the proof. Account codes and names are labels a "
                    "business may renumber or reword; the proof commits to account "
                    "ids, amounts, dates and the entry number."
                ),
                "accounts": account_labels,
            },
            "spec": CANONICAL_SPEC,
            "how_to_verify": [
                "1. Re-encode `entry` using `spec` and hash it: SHA-256(0x00 || canonical).",
                "2. Check the result equals `leaf.hash`.",
                "3. Fold `path` into it innermost-first: SHA-256(0x01 || left || right).",
                "4. Check the result equals `seal.merkle_root`.",
                "5. Ask the contract, from any Soroban RPC you choose: "
                "verify(org_namespace, seal.seq, seal.merkle_root) must return true.",
                "Step 5 is the one that matters. Steps 1-4 can be checked offline; "
                "step 5 is what makes the answer independent of this server.",
            ],
        }

        if actor is not None:
            await self.audit.record(
                AuditAction.PROOF_EXPORTED,
                actor=actor,
                organization_id=organization_id,
                resource_type="journal_entry",
                resource_id=journal_entry_id,
                summary=(
                    f"Exported a proof for entry {entry.entry_number} against seal #{seal.seq}"
                ),
                context={"seal_seq": seal.seq, "leaf_index": index},
                **_audit_ctx(ctx),
            )

        return ProofBundle(payload=bundle)

    async def verify_bundle(
        self, bundle: dict[str, Any], *, check_chain: bool = True
    ) -> VerifyResult:
        """Check a bundle the way the browser does, as a convenience.

        Explicitly **not** the authority. A verifier who trusts this endpoint has
        gained nothing: a compromised server would happily return ``valid=True``
        for anything. It exists so a business can sanity-check a bundle before
        sending it, and so the test suite can assert that the server and the
        browser agree on the same vectors.
        """
        if bundle.get("format") != BUNDLE_FORMAT:
            return VerifyResult(
                valid=False,
                reason=f"Unrecognised bundle format {bundle.get('format')!r}",
            )

        try:
            entry = bundle["entry"]
            seal = bundle["seal"]
            leaf_claim = bundle["leaf"]["hash"]
            path: list[ProofStep] = bundle["path"]
            expected_root = seal["merkle_root"]
        except (KeyError, TypeError) as exc:
            return VerifyResult(valid=False, reason=f"Bundle is missing {exc}")

        try:
            recomputed = leaf_hash_hex(entry)
        except Exception as exc:
            return VerifyResult(
                valid=False, reason=f"The entry could not be canonically encoded: {exc}"
            )

        if recomputed != leaf_claim:
            return VerifyResult(
                valid=False,
                reason=(
                    "The entry does not hash to the leaf the bundle claims. Its "
                    "contents have been altered."
                ),
                leaf_hash=recomputed,
            )

        acc = bytes.fromhex(recomputed)
        ok = verify_inclusion(acc, path, bytes.fromhex(expected_root))
        if not ok:
            return VerifyResult(
                valid=False,
                reason="The proof path does not lead to the sealed root.",
                leaf_hash=recomputed,
                expected_root=expected_root,
            )

        if not check_chain:
            return VerifyResult(
                valid=True,
                reason="The bundle is internally consistent. The chain was not checked.",
                leaf_hash=recomputed,
                computed_root=expected_root,
                expected_root=expected_root,
                seal_seq=seal.get("seq"),
            )

        chain = bundle.get("chain") or {}
        try:
            client = SorobanClient(chain.get("network"))
            on_chain = await client.read_seal(
                contract_id=chain.get("contract_id") or "",
                namespace=chain.get("org_namespace") or "",
                seq=int(seal["seq"]),
            )
        except Exception as exc:
            return VerifyResult(
                valid=False,
                reason=f"The chain could not be reached to confirm the root: {exc}",
                leaf_hash=recomputed,
                computed_root=expected_root,
                seal_seq=seal.get("seq"),
            )

        if on_chain is None:
            return VerifyResult(
                valid=False,
                reason="No seal exists on chain at that sequence number.",
                leaf_hash=recomputed,
                computed_root=expected_root,
                seal_seq=seal.get("seq"),
                chain_checked=True,
            )

        if on_chain.root != expected_root:
            return VerifyResult(
                valid=False,
                reason=(
                    "The root on chain does not match the bundle's root. The books "
                    "presented are not the books that were sealed."
                ),
                leaf_hash=recomputed,
                computed_root=expected_root,
                on_chain_root=on_chain.root,
                seal_seq=seal.get("seq"),
                chain_checked=True,
            )

        return VerifyResult(
            valid=True,
            reason=(
                "Verified. This entry was part of the books sealed at "
                f"{on_chain.at.isoformat()} and has not changed since."
            ),
            leaf_hash=recomputed,
            computed_root=expected_root,
            expected_root=expected_root,
            on_chain_root=on_chain.root,
            seal_seq=on_chain.seq,
            chain_checked=True,
        )

    async def public_chain(self, namespace: str, *, limit: int = 24) -> dict[str, Any]:
        """A namespace's chain, readable without authentication.

        The public verifier's context call. It returns seals and nothing else - no
        organization name, no entry, no amount beyond the control totals that are
        already on a public ledger. A verifier holding a namespace already got it
        from the business, so this discloses nothing the business has not chosen to.
        """
        setting = await self.settings_repo.by_namespace(namespace)
        if setting is None or not setting.contract_id or not setting.network:
            raise NotFoundError("Chain")

        client = SorobanClient(setting.network)
        book = await client.read_book(contract_id=setting.contract_id, namespace=namespace)
        if book is None:
            return {
                "namespace": namespace,
                "network": setting.network,
                "contract_id": setting.contract_id,
                "head": 0,
                "seals": [],
            }

        seals: list[dict[str, Any]] = []
        seq = book.head
        while seq > 0 and len(seals) < limit:
            item = await client.read_seal(
                contract_id=setting.contract_id, namespace=namespace, seq=seq
            )
            if item is None:
                break
            seals.append(
                {
                    "seq": item.seq,
                    "root": item.root,
                    "prev": item.prev,
                    "entry_count": item.count,
                    "debit_minor": str(item.debits),
                    "covered_from": dt.datetime.fromtimestamp(
                        item.period_from, tz=dt.UTC
                    ).isoformat(),
                    "covered_to": dt.datetime.fromtimestamp(item.period_to, tz=dt.UTC).isoformat(),
                    "sealed_at": item.at.isoformat(),
                }
            )
            seq -= 1

        # Continuity is asserted here rather than left to the reader: each seal's
        # `prev` must be its predecessor's `root`, and a break is the single most
        # important thing this endpoint can report.
        continuous = all(seals[i]["prev"] == seals[i + 1]["root"] for i in range(len(seals) - 1))
        if seals and seals[-1]["seq"] == 1:
            continuous = continuous and seals[-1]["prev"] == GENESIS_ROOT

        return {
            "namespace": namespace,
            "network": setting.network,
            "contract_id": setting.contract_id,
            "head": book.head,
            "root": book.root,
            "entries": book.entries,
            "sealed_at": book.sealed_at.isoformat() if book.sealed_at else None,
            "continuous": continuous,
            "seals": seals,
        }
