"""HTTP surface for the proof ledger.

Two routers, and the split is the most important thing in this file.

``router`` is the ordinary authenticated, organization-scoped API: status,
history, seal now, configure, export a proof. Every route is behind a permission,
and the active organization comes from the signed token as it does everywhere else
in this application.

``public_router`` is **unauthenticated**, and deliberately so. It exists for the
verifier - a bank's credit officer, an auditor, a buyer - who has been handed a
proof bundle and needs a verdict. Requiring them to have an account would defeat
the purpose: the whole design goal is that a verifier needs no wallet, no account,
and no relationship with this server.

What that costs, and how it is paid
-----------------------------------
An unauthenticated endpoint on a system holding a business's books needs a reason
to exist for every byte it returns. These two return:

* ``/verify`` - a boolean and a sentence, computed from a bundle the caller
  already holds. It reveals nothing the caller did not send.
* ``/chain/{namespace}`` - seals for one namespace: roots, counts, control totals,
  timestamps. Every one of those is *already* on a public ledger, readable by
  anyone with the namespace. The namespace is the capability, and the business
  hands it out one counterparty at a time.

Neither returns an organization's name, an entry, a party, or an amount that is
not already on chain. And both are rate-limited, because "unauthenticated" and
"free to hammer" are different things.
"""

from __future__ import annotations

import itertools
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status

from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import get_logger
from app.core.pagination import CursorParams
from app.modules.attestation.canonical import CANONICAL_SPEC, CANONICAL_VERSION
from app.modules.attestation.merkle import tree_depth
from app.modules.attestation.models import Seal
from app.modules.attestation.schemas import (
    AdoptionRead,
    AdoptionRowRead,
    AttestationStatusRead,
    CanonicalSpecRead,
    ChainRead,
    DrainResultRead,
    EnableAttestationRequest,
    NetworkInfoRead,
    ProofBundleRead,
    PublicChainRead,
    ReconcileResultRead,
    RotateSignerRequest,
    SealNowResponse,
    SealPage,
    SealRead,
    SetCadenceRequest,
    VerifyBundleRequest,
    VerifyResultRead,
)
from app.modules.attestation.service import (
    AttestationService,
    AttestationStatus,
    SealService,
    VerifyService,
)
from app.modules.attestation.stellar import NETWORKS, SorobanClient
from app.modules.auth.dependencies import (
    ActiveOrganizationId,
    CurrentUser,
    DbSession,
    RequestCtx,
    require_permission,
    require_superuser,
)
from app.modules.rbac.permissions import Permission

log = get_logger(__name__)

#: Budget for the unauthenticated endpoints, named at module scope the way the auth
#: router names its own - so a reader of the decorator sees a policy rather than a
#: settings lookup.
PUBLIC_VERIFY_LIMIT = settings.rate_limit_public_verify

router = APIRouter(prefix="/attestation", tags=["Proof ledger"])

#: Unauthenticated verification. Mounted under the same version prefix so a proof
#: bundle's URL is stable, and tagged separately so the generated docs make the
#: trust boundary obvious to anybody reading them.
public_router = APIRouter(prefix="/verify", tags=["Public verification"])


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
def get_attestation(session: DbSession) -> AttestationService:
    return AttestationService(session)


def get_sealer(session: DbSession) -> SealService:
    return SealService(session)


def get_verifier(session: DbSession) -> VerifyService:
    return VerifyService(session)


AttestationDep = Annotated[AttestationService, Depends(get_attestation)]
SealerDep = Annotated[SealService, Depends(get_sealer)]
VerifierDep = Annotated[VerifyService, Depends(get_verifier)]


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------
def _seal(seal: Seal) -> SealRead:
    """One mapper, so the shape cannot drift between the routes that return one."""
    return SealRead(
        id=seal.id,
        seq=seal.seq,
        status=seal.status,
        trigger=seal.trigger,
        merkle_root=seal.merkle_root,
        prev_root=seal.prev_root,
        entry_count=seal.entry_count,
        debit_minor=str(seal.debits),
        covered_from=seal.covered_from,
        covered_to=seal.covered_to,
        entry_date_from=seal.entry_date_from,
        entry_date_to=seal.entry_date_to,
        network=seal.network,
        contract_id=seal.contract_id,
        tx_hash=seal.tx_hash,
        ledger_sequence=str(seal.ledger_sequence) if seal.ledger_sequence else None,
        sealed_at=seal.sealed_at,
        submitted_at=seal.submitted_at,
        confirmed_at=seal.confirmed_at,
        attempts=seal.attempts,
        last_error=seal.last_error,
        explorer_url=seal.explorer_url,
        tree_depth=tree_depth(seal.entry_count),
    )


def _status(view: AttestationStatus) -> AttestationStatusRead:
    return AttestationStatusRead(
        enabled=view.enabled,
        configured=view.configured,
        ready=view.ready,
        network=view.network,
        contract_id=view.contract_id,
        contract_url=view.contract_url,
        org_namespace=view.org_namespace,
        cadence=view.cadence,
        seal_time=view.seal_time,
        effective_seal_time=view.effective_seal_time,
        timezone=view.timezone,
        signer_public_key=view.signer_public_key,
        external_signer=view.external_signer,
        registered_at=view.registered_at,
        seals_confirmed=view.seals_confirmed,
        entries_sealed=view.entries_sealed,
        unsealed_entries=view.unsealed_entries,
        oldest_unsealed_at=view.oldest_unsealed_at,
        days_unsealed=view.days_unsealed,
        last_seal=_seal(view.last_seal) if view.last_seal else None,
        open_seal=_seal(view.open_seal) if view.open_seal else None,
        chain=ChainRead(
            reachable=view.chain.reachable,
            head=view.chain.head,
            root=view.chain.root,
            entries=view.chain.entries,
            sealed_at=view.chain.sealed_at,
            admin=view.chain.admin,
            agrees_with_local=view.chain.agrees_with_local,
            error=view.chain.error,
        ),
        warnings=list(view.warnings),
    )


# ---------------------------------------------------------------------------
# Configuration and status
# ---------------------------------------------------------------------------
@router.get(
    "/network",
    response_model=NetworkInfoRead,
    summary="Chain coordinates for a client",
)
async def network_info() -> NetworkInfoRead:
    """What a client needs to read the chain for itself.

    Deliberately not behind a permission: it is server configuration, not business
    data, and both clients need it before they can render anything - including the
    verifier, which uses it to talk to the chain without going through us.
    """
    config = NETWORKS.get(settings.stellar_network, {})
    return NetworkInfoRead(
        enabled=settings.attestation_enabled,
        network=settings.stellar_network if settings.attestation_enabled else None,
        contract_id=settings.soroban_contract_id,
        rpc_url=settings.soroban_rpc_url or config.get("rpc", ""),
        explorer_base=settings.stellar_explorer_base,
        spec_version=CANONICAL_VERSION,
    )


@router.get(
    "/spec",
    response_model=CanonicalSpecRead,
    summary="The canonical encoding",
)
async def canonical_spec() -> CanonicalSpecRead:
    """Publish the encoding the leaves are hashed with.

    The browser implements this independently - it must, or a verifier would be
    running our code and trusting our answer. Publishing the spec lets the client
    assert at runtime that it agrees with the server, so a half-deployed release
    says "the verifier is out of date" instead of failing every proof for no
    visible reason.
    """
    return CanonicalSpecRead(spec=CANONICAL_SPEC)


@router.get(
    "/status",
    response_model=AttestationStatusRead,
    summary="Sealing status for this organization",
)
async def read_status(
    service: AttestationDep,
    organization_id: ActiveOrganizationId,
    _: Annotated[None, Depends(require_permission(Permission.SEAL_READ))],
) -> AttestationStatusRead:
    """Everything the Trust screen shows, including the chain's own view.

    Reads the chain best-effort: an unreachable RPC yields a status that says so
    rather than an error that blanks the screen. A business whose network is down
    still needs to see how far its backlog has grown.
    """
    return _status(await service.status(organization_id))


@router.post(
    "/enable",
    response_model=AttestationStatusRead,
    status_code=status.HTTP_200_OK,
    summary="Switch sealing on",
)
async def enable(
    payload: EnableAttestationRequest,
    service: AttestationDep,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.SEAL_CONFIGURE))],
) -> AttestationStatusRead:
    """Configure a signer, fund it on testnet, and open the book on chain.

    Idempotent: every step checks whether it has already been done, so a retry
    after a closed browser tab converges rather than failing.
    """
    await service.enable(
        organization_id,
        user,
        cadence=payload.cadence,
        secret_key=payload.secret_key,
        fund_on_testnet=payload.fund_on_testnet,
        ctx=ctx,
    )
    return _status(await service.status(organization_id))


@router.post(
    "/disable",
    response_model=AttestationStatusRead,
    summary="Stop sealing new entries",
)
async def disable(
    service: AttestationDep,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.SEAL_CONFIGURE))],
) -> AttestationStatusRead:
    """Stop sealing. Nothing already on chain is touched or becomes unverifiable."""
    await service.disable(organization_id, user, ctx)
    return _status(await service.status(organization_id))


@router.patch(
    "/cadence",
    response_model=AttestationStatusRead,
    summary="How often to seal",
)
async def set_cadence(
    payload: SetCadenceRequest,
    service: AttestationDep,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.SEAL_CONFIGURE))],
) -> AttestationStatusRead:
    await service.set_cadence(
        organization_id, payload.cadence, user, ctx, seal_minute=payload.seal_minute
    )
    return _status(await service.status(organization_id))


@router.post(
    "/signer/rotate",
    response_model=AttestationStatusRead,
    summary="Hand the book to another account",
)
async def rotate_signer(
    payload: RotateSignerRequest,
    service: AttestationDep,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.SEAL_CONFIGURE))],
) -> AttestationStatusRead:
    """Move the book onto a different Stellar account - the 2-of-3 upgrade path.

    The contract requires the destination to authorise too, so this only succeeds
    when this server's key is one of its signers. That is the intended shape: the
    accountant becomes a co-signer without this server ever holding their key.
    """
    await service.rotate_signer(organization_id, payload.new_admin, user, ctx)
    return _status(await service.status(organization_id))


# ---------------------------------------------------------------------------
# Seals
# ---------------------------------------------------------------------------
@router.get("/seals", response_model=SealPage, summary="Seal history")
async def list_seals(
    session: DbSession,
    organization_id: ActiveOrganizationId,
    params: Annotated[CursorParams, Depends()],
    _: Annotated[None, Depends(require_permission(Permission.SEAL_READ))],
) -> SealPage:
    """This organization's chain, newest first.

    Continuity is computed here rather than in the client. A break in the chain is
    the most important thing this list can report, and three clients deriving it
    three ways would eventually disagree about it.
    """
    from app.modules.attestation.repository import SealRepository

    rows = list(await SealRepository(session).page(organization_id, params))
    has_more = len(rows) > params.limit
    items = rows[: params.limit]

    continuous = True
    for newer, older in itertools.pairwise(items):
        # Only confirmed seals are on chain, so only they participate in the chain.
        if newer.status.value == "confirmed" and older.status.value == "confirmed":
            continuous = continuous and newer.prev_root == older.merkle_root

    return SealPage(
        items=[_seal(seal) for seal in items],
        next_cursor=str(items[-1].seq) if has_more and items else None,
        has_more=has_more,
        continuous=continuous,
    )


@router.post(
    "/seals",
    response_model=SealNowResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Seal now",
)
async def seal_now(
    sealer: SealerDep,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.SEAL_WRITE))],
) -> SealNowResponse:
    """Prepare and submit a seal over everything not yet sealed.

    Submits inline, because somebody is watching. If the outcome comes back
    unknown the row is still parked for the worker, so pressing the button can
    never leave a seal orphaned.
    """
    seal = await sealer.seal_now(organization_id, user, ctx)
    if seal is None:
        return SealNowResponse(
            seal=None,
            message="Everything is already sealed - there was nothing new to commit.",
        )

    if seal.status.value == "confirmed":
        message = (
            f"Seal #{seal.seq} confirmed on {seal.network}: {seal.entry_count} entries committed."
        )
    elif seal.status.value == "failed":
        message = f"Seal #{seal.seq} could not be submitted. {seal.last_error or ''}".strip()
    else:
        message = (
            f"Seal #{seal.seq} was submitted and is awaiting confirmation. "
            "It will be reconciled against the chain automatically."
        )
    return SealNowResponse(seal=_seal(seal), message=message)


@router.post(
    "/reconcile",
    response_model=ReconcileResultRead,
    summary="Correct local state from the chain",
)
async def reconcile(
    sealer: SealerDep,
    organization_id: ActiveOrganizationId,
    _: Annotated[None, Depends(require_permission(Permission.SEAL_WRITE))],
) -> ReconcileResultRead:
    """Ask the chain what it holds and make the database agree.

    Never the reverse. The chain is the authority on what has been sealed; this
    endpoint exists because that authority is sometimes ahead of us.
    """
    result = await sealer.reconcile(organization_id)
    return ReconcileResultRead(**result)


@router.post(
    "/drain",
    response_model=DrainResultRead,
    summary="Run a worker pass now",
)
async def drain(
    sealer: SealerDep,
    _: Annotated[None, Depends(require_permission(Permission.SEAL_WRITE))],
) -> DrainResultRead:
    """Advance every seal still owed an outcome.

    The same function the background worker calls, exposed so an operator can
    force a pass without waiting for the timer - and so a deployment with the
    in-process worker switched off has a way to drive it from cron or a platform
    scheduler.
    """
    return DrainResultRead(**await sealer.drain())


# ---------------------------------------------------------------------------
# Proofs
# ---------------------------------------------------------------------------
@router.get(
    "/proof/{journal_entry_id}",
    response_model=ProofBundleRead,
    summary="Export a proof for one entry",
)
async def export_proof(
    journal_entry_id: Annotated[uuid.UUID, Path()],
    verifier: VerifierDep,
    user: CurrentUser,
    organization_id: ActiveOrganizationId,
    ctx: RequestCtx,
    _: Annotated[None, Depends(require_permission(Permission.PROOF_EXPORT))],
) -> ProofBundleRead:
    """A self-contained proof bundle for one journal entry.

    The server checks its own work before returning: it recomputes the leaf from
    the payload it is about to send and walks the path back to the sealed root.
    Handing out a bundle that does not verify would be worse than handing out
    none, because the business would learn it was broken from its bank.
    """
    bundle = await verifier.bundle_for_entry(organization_id, journal_entry_id, actor=user, ctx=ctx)
    return ProofBundleRead(bundle=bundle.to_json())


# ---------------------------------------------------------------------------
# Public verification - no authentication
# ---------------------------------------------------------------------------
@public_router.get(
    "/network",
    response_model=NetworkInfoRead,
    summary="Chain coordinates (public)",
)
async def public_network_info() -> NetworkInfoRead:
    """The chain the verifier should read, and the RPC it may replace.

    Duplicated on the public router rather than shared, because the verifier page
    is unauthenticated and must not depend on any route that could later acquire a
    permission. One line of duplication buys a trust boundary that cannot drift.
    """
    config = NETWORKS.get(settings.stellar_network, {})
    return NetworkInfoRead(
        enabled=settings.attestation_enabled,
        network=settings.stellar_network if settings.attestation_enabled else None,
        contract_id=settings.soroban_contract_id,
        rpc_url=settings.soroban_rpc_url or config.get("rpc", ""),
        explorer_base=settings.stellar_explorer_base,
        spec_version=CANONICAL_VERSION,
    )


@public_router.get(
    "/spec",
    response_model=CanonicalSpecRead,
    summary="The canonical encoding (public)",
)
async def public_spec() -> CanonicalSpecRead:
    return CanonicalSpecRead(spec=CANONICAL_SPEC)


@public_router.post(
    "/bundle",
    response_model=VerifyResultRead,
    summary="Check a proof bundle",
)
@limiter.limit(PUBLIC_VERIFY_LIMIT)
async def verify_bundle(
    request: Request,
    payload: VerifyBundleRequest,
    verifier: VerifierDep,
) -> VerifyResultRead:
    """Check a bundle, and say plainly what was checked.

    **This is a convenience, not the authority**, and the response says so through
    ``chain_checked``. A verifier who trusts this endpoint has gained nothing - a
    compromised server would happily return ``valid: true`` for anything. The real
    check is the one the browser does against a public RPC.

    It exists because a business wants to confirm a bundle is good *before*
    emailing it to a bank, and because a verifier without JavaScript needs some
    route to an answer.
    """
    result = await verifier.verify_bundle(payload.bundle, check_chain=payload.check_chain)
    return VerifyResultRead(
        valid=result.valid,
        reason=result.reason,
        leaf_hash=result.leaf_hash,
        computed_root=result.computed_root,
        expected_root=result.expected_root,
        on_chain_root=result.on_chain_root,
        seal_seq=result.seal_seq,
        chain_checked=result.chain_checked,
    )


@public_router.get(
    "/chain/{namespace}",
    response_model=PublicChainRead,
    summary="A namespace's seal chain",
)
@limiter.limit(PUBLIC_VERIFY_LIMIT)
async def public_chain(
    request: Request,
    namespace: Annotated[str, Path(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")],
    verifier: VerifierDep,
    limit: Annotated[int, Query(ge=1, le=24)] = 12,
) -> PublicChainRead:
    """The seals for one namespace, newest first, read from the chain.

    Returns only what is already on a public ledger: roots, counts, control
    totals, timestamps. No organization name, no entry, no party.

    The namespace *is* the capability. A verifier holding one was given it by the
    business, deliberately and one counterparty at a time - which is why this
    needs no authentication and still discloses nothing the business did not
    choose to disclose.
    """
    data = await verifier.public_chain(namespace.lower(), limit=limit)
    return PublicChainRead(**data)


# ---------------------------------------------------------------------------
# Install-wide
# ---------------------------------------------------------------------------
@router.get(
    "/adoption",
    response_model=AdoptionRead,
    summary="Who on this install is actually sealing",
)
async def adoption(
    service: AttestationDep,
    _: Annotated[None, Depends(require_superuser())],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> AdoptionRead:
    """Every organization with a book, and what it has put on chain.

    **Superuser rather than an organization permission**, because the question is
    about the deployment and not about one business - and because no member of one
    organization has any business reading another's row.

    Every figure is either configuration or already public on the Stellar ledger,
    and each row carries the signer address and the head transaction hash so a
    reader can leave this response and confirm it on an explorer. That is the
    point: a count of "organizations sealing" that only we can see is worth very
    little, and one that resolves to public transactions is worth something.

    `sealing` is deliberately not `len(organizations)`. Switching sealing on is not
    the same as having sealed, and conflating the two is exactly the sort of
    flattering arithmetic this endpoint exists to avoid.
    """
    rows = await service.adoption(limit=limit)
    return AdoptionRead(
        organizations=[AdoptionRowRead(**row) for row in rows],
        sealing=sum(1 for row in rows if row["seals"] > 0),
        total_seals=sum(int(row["seals"]) for row in rows),
        total_entries_sealed=sum(int(row["entries_sealed"]) for row in rows),
        network=settings.stellar_network if settings.attestation_enabled else None,
        contract_id=settings.soroban_contract_id,
        explorer_base=settings.stellar_explorer_base,
    )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
@router.get("/chain/health", summary="Is the configured RPC reachable?")
async def chain_health(
    _: Annotated[None, Depends(require_permission(Permission.SEAL_READ))],
) -> dict[str, object]:
    """A direct probe of the RPC, separate from ``/status``.

    Its own endpoint because "the chain is unreachable" and "this organization has
    not configured sealing" are different problems with the same symptom on the
    Trust screen, and an operator diagnosing one should not have to infer which.
    """
    if not settings.attestation_enabled:
        return {"enabled": False, "reachable": False, "reason": "ATTESTATION_ENABLED is false"}
    return {"enabled": True, **await SorobanClient().health()}
