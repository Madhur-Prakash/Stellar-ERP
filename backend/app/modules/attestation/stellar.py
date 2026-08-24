"""The Soroban boundary - the only module in the backend that talks to Stellar.

Everything network-facing lives here so that :mod:`app.modules.attestation.service`
stays a pure statement of the business rules and can be tested without a chain.
That separation is not decorative: the service's hardest logic is what to do when
a submission's outcome is *unknown*, and testing that requires being able to make
a submission time out on demand.

Three things in this file are load-bearing.

**Reads are simulations, not storage pokes.** ``latest``, ``get`` and ``verify``
are invoked through ``simulate_transaction``, which costs nothing and submits
nothing. It would be marginally cheaper to compute the storage key and call
``get_ledger_entries`` directly, but then the backend would be reading the
contract's *storage layout* rather than its *interface* - and the verifier in the
browser reads the interface. Two readers taking different routes to the same fact
is how they come to disagree.

**A timeout is not a failure.** :meth:`SorobanClient.submit_seal` distinguishes
three outcomes, and the third is the important one: confirmed, rejected, or
*unknown*. A transaction that has left this process may still land after we stop
waiting. Treating that as failure and resubmitting is how a double seal happens;
treating it as success is how a gap happens. So it is reported as unknown, and
the service parks the row in ``SUBMITTED`` for the reconciler to resolve against
the chain.

**Sequence-number contention is solved by giving every organization its own
account.** One shared submitter would serialise every business's month-end behind
a single sequence number, which is the classic reason a Stellar service that works
in a demo falls over in production. The usual fix is a pool of channel accounts;
here the per-organization signer *is* the channel account, which also happens to
be the right answer for custody - the business holds the key that seals its own
books. One decision, two problems.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import re
from typing import Any, Final

import httpx

from app.core.config import settings
from app.core.exceptions import ServiceUnavailableError
from app.core.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Optional import
# ---------------------------------------------------------------------------
# `stellar-sdk` is a declared dependency, so this import normally succeeds. It is
# still guarded, for the same reason the OCR engines and the object-store client
# are: the rest of the ERP must remain fully usable on an install where this one
# subsystem cannot start. A missing SDK degrades to "sealing unavailable" with a
# clear reason, rather than a failed import that takes the whole API down and
# tells the operator nothing about which feature caused it.
try:  # pragma: no cover - exercised by the absence of the package
    from stellar_sdk import (
        Address,
        Keypair,
        SorobanServerAsync,
        TransactionBuilder,
        scval,
    )
    from stellar_sdk import xdr as stellar_xdr
    from stellar_sdk.exceptions import PrepareTransactionException
    from stellar_sdk.soroban_rpc import GetTransactionStatus

    SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    SDK_AVAILABLE = False


# ---------------------------------------------------------------------------
# Networks
# ---------------------------------------------------------------------------
#: RPC endpoint and passphrase per network name.
#:
#: Testnet's public RPC is the default because that is where Level 4 lives. The
#: mainnet entry is present and correct so that the cutover is a configuration
#: change and not a code change - and so the *verifier* can be pointed at either
#: without a rebuild.
NETWORKS: Final[dict[str, dict[str, str]]] = {
    "testnet": {
        "rpc": "https://soroban-testnet.stellar.org",
        "passphrase": "Test SDF Network ; September 2015",
        "explorer": "https://stellar.expert/explorer/testnet",
        "friendbot": "https://friendbot.stellar.org",
    },
    "public": {
        "rpc": "https://mainnet.sorobanrpc.com",
        "passphrase": "Public Global Stellar Network ; September 2015",
        "explorer": "https://stellar.expert/explorer/public",
        "friendbot": "",
    },
}

#: 64 zeroes - the ``prev`` of a first seal, matching the contract's sentinel.
GENESIS_ROOT: Final = "0" * 64

#: Contract error codes, mirrored from ``contracts/proof_ledger/src/lib.rs``.
#:
#: Duplicated deliberately rather than generated. The contract is deployed and
#: immutable; a generator would imply these can drift, and they cannot - if the
#: contract ever changes them it is a new deployment with a new address, and this
#: table is versioned alongside the code that talks to it.
#:
#: The distinction that matters operationally is in the third column of the
#: reasoning below: ``SequenceOutOfOrder`` on a retry is *success in disguise* -
#: it means a previous attempt landed - while every other code is a real problem.
CONTRACT_ERRORS: Final[dict[int, str]] = {
    1: "already_registered",
    2: "not_registered",
    3: "sequence_out_of_order",
    4: "chain_broken",
    5: "empty_seal",
    6: "period_out_of_order",
    7: "seal_not_found",
    8: "root_is_sentinel",
}

_CONTRACT_ERROR_RE: Final = re.compile(r"Error\(Contract,\s*#(\d+)\)")


def contract_error_of(message: str) -> tuple[int, str] | None:
    """Extract ``(code, name)`` from a host error string, if there is one.

    The RPC reports a contract panic as ``Error(Contract, #3)`` inside a longer
    diagnostic blob. Parsing it out is what lets the worker tell "this seal
    already landed" from "the chain has diverged from our database" - two
    conditions with opposite correct responses that are otherwise the same
    failure.
    """
    match = _CONTRACT_ERROR_RE.search(message or "")
    if not match:
        return None
    code = int(match.group(1))
    return code, CONTRACT_ERRORS.get(code, f"contract_error_{code}")


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True, slots=True)
class BookView:
    """The contract's view of one organization's chain head."""

    admin: str
    head: int
    root: str
    sealed_at: dt.datetime | None
    covered_to: int
    entries: int

    @property
    def is_empty(self) -> bool:
        return self.head == 0


@dataclasses.dataclass(frozen=True, slots=True)
class SealView:
    """One seal, as the contract holds it."""

    seq: int
    root: str
    prev: str
    count: int
    debits: int
    period_from: int
    period_to: int
    at: dt.datetime


@dataclasses.dataclass(frozen=True, slots=True)
class SubmitOutcome:
    """The result of trying to put something on chain.

    ``status`` is one of ``confirmed`` / ``rejected`` / ``unknown``, and the third
    is not an error state - it is the honest answer when a transaction has left
    the process and no verdict came back in time. See the module docstring.
    """

    status: str
    tx_hash: str | None = None
    ledger: int | None = None
    network_time: dt.datetime | None = None
    error_code: int | None = None
    error_name: str | None = None
    message: str | None = None

    @property
    def is_confirmed(self) -> bool:
        return self.status == "confirmed"

    @property
    def is_rejected(self) -> bool:
        return self.status == "rejected"

    @property
    def is_unknown(self) -> bool:
        return self.status == "unknown"

    @property
    def already_sealed(self) -> bool:
        """Whether a rejection means the work was already done.

        ``sequence_out_of_order`` on a submission we believed was needed means the
        contract's head has already moved past it - so a previous attempt landed
        after we stopped waiting for it. The correct response is to reconcile, not
        to retry and not to fail.
        """
        return self.error_name == "sequence_out_of_order"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class SorobanUnavailable(ServiceUnavailableError):
    """Raised when the chain cannot be reached or the SDK is absent.

    A subclass of the platform's 503 rather than a bespoke exception, so an
    endpoint that touches the chain degrades the way every other unavailable
    dependency does instead of surfacing as a 500.
    """

    code = "soroban_unavailable"


class SorobanClient:
    """Talks to one Soroban network.

    Constructed per call rather than held as a process-wide singleton. An
    ``httpx`` client held open across a redeploy of the RPC endpoint is a source
    of stale connections, and the cost of a fresh one is irrelevant next to a
    five-second consensus round.
    """

    def __init__(self, network: str | None = None) -> None:
        self.network = (network or settings.stellar_network).lower()
        if self.network not in NETWORKS:
            raise SorobanUnavailable(f"Unknown Stellar network {self.network!r}")

        config = NETWORKS[self.network]
        # An operator override wins, so a self-hosted install can point at its own
        # RPC - which is the whole reason the verifier lets its endpoint be changed
        # too. Trusting one hosted RPC would reintroduce a single party whose word
        # everybody has to take.
        self.rpc_url = settings.soroban_rpc_url or config["rpc"]
        self.passphrase = config["passphrase"]
        self.explorer = config["explorer"]
        self.friendbot = config["friendbot"]

    # -- plumbing ----------------------------------------------------------
    def _require_sdk(self) -> None:
        if not SDK_AVAILABLE:
            raise SorobanUnavailable(
                "The Stellar SDK is not installed, so the proof ledger cannot be "
                "reached. Install it with `uv sync` in `backend/`."
            )

    def _server(self) -> Any:
        """A Soroban RPC server bound to an httpx transport.

        The SDK's default async transport is aiohttp, which it ships as an
        optional extra. Backed by httpx instead - see
        :mod:`app.modules.attestation.rpc_client` for why a second async HTTP
        stack was not worth the dependency.
        """
        self._require_sdk()
        from app.modules.attestation.rpc_client import HttpxSorobanClient

        return SorobanServerAsync(
            self.rpc_url,
            client=HttpxSorobanClient(timeout=float(settings.soroban_timeout_seconds)),
        )

    @staticmethod
    def _bytes32(hex_value: str) -> Any:
        """A 32-byte ``BytesN`` SCVal from a hex string.

        Length is checked rather than trusted. A 31-byte value would be accepted
        by the encoder and rejected by the contract as a type error, which surfaces
        as an opaque simulation failure a long way from the truncated hash that
        caused it.
        """
        raw = bytes.fromhex(hex_value)
        if len(raw) != 32:
            raise SorobanUnavailable(
                f"Expected a 32-byte value, got {len(raw)} bytes from {hex_value!r}"
            )
        return scval.to_bytes(raw)

    @staticmethod
    def _instant(seconds: int | None) -> dt.datetime | None:
        if not seconds:
            return None
        return dt.datetime.fromtimestamp(int(seconds), tz=dt.UTC)

    # -- keys --------------------------------------------------------------
    def generate_keypair(self) -> tuple[str, str]:
        """A fresh ``(public, secret)`` pair for an organization's signer."""
        self._require_sdk()
        pair = Keypair.random()
        return pair.public_key, pair.secret

    def public_key_of(self, secret: str) -> str:
        """The account a secret seed controls, for validating pasted keys."""
        self._require_sdk()
        return Keypair.from_secret(secret).public_key

    async def fund_testnet_account(self, public_key: str) -> bool:
        """Ask Friendbot for testnet XLM.

        Testnet only, and the guard is not paranoia: on mainnet there is no
        friendbot, and an endpoint that silently did nothing would leave an
        operator waiting for funds that were never coming.
        """
        if self.network != "testnet" or not self.friendbot:
            raise SorobanUnavailable("Friendbot funding is only available on testnet")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(self.friendbot, params={"addr": public_key})
        except httpx.HTTPError as exc:
            raise SorobanUnavailable(f"Friendbot is unreachable: {exc}") from exc

        # 400 with "op_already_exists" means the account is already funded, which
        # is the desired end state - so it is success, not failure. Re-running
        # onboarding must be safe.
        if response.status_code == 200:
            return True
        if "op_already_exists" in response.text or "already funded" in response.text.lower():
            return False
        raise SorobanUnavailable(
            f"Friendbot refused to fund the account (HTTP {response.status_code})"
        )

    async def account_exists(self, public_key: str) -> bool:
        """Whether the signer account is funded and usable.

        Checked before enabling sealing, because an unfunded account produces a
        ``tx_BAD_SEQ``-shaped failure on every submission - technically accurate
        and completely unhelpful next to "this account has no XLM yet".
        """
        server = self._server()
        try:
            await server.load_account(public_key)
            return True
        except Exception:
            return False
        finally:
            await server.close()

    # -- writes ------------------------------------------------------------
    async def _invoke(
        self,
        *,
        contract_id: str,
        function: str,
        args: list[Any],
        secret: str,
        timeout_seconds: int,
    ) -> SubmitOutcome:
        """Build, prepare, sign, submit, and wait for one contract call."""
        self._require_sdk()
        server = self._server()

        try:
            signer = Keypair.from_secret(secret)
            try:
                source = await server.load_account(signer.public_key)
            except Exception as exc:
                raise SorobanUnavailable(
                    f"The signing account {signer.public_key[:6]}… could not be loaded. "
                    "It may not be funded yet."
                ) from exc

            transaction = (
                TransactionBuilder(
                    source_account=source,
                    network_passphrase=self.passphrase,
                    # The *inclusion* fee only; the resource fee is added by
                    # `prepare_transaction` from the simulation. Configurable
                    # because this is the knob that stops a seal being stranded
                    # when the network is busy - a fee-bump after the fact would
                    # need the original transaction retained, and paying a few
                    # hundred stroops more up front is cheaper than the machinery.
                    base_fee=settings.stellar_base_fee,
                )
                .append_invoke_contract_function_op(
                    contract_id=contract_id,
                    function_name=function,
                    parameters=args,
                )
                .set_timeout(timeout_seconds)
                .build()
            )

            try:
                # Simulates and stamps on the resource footprint and fee. Also the
                # cheapest possible place to discover a contract rejection: a
                # sequence that has already been used fails here, before any fee is
                # spent and before anything is submitted.
                transaction = await server.prepare_transaction(transaction)
            except PrepareTransactionException as exc:
                detail = str(getattr(exc, "simulate_transaction_response", exc))
                parsed = contract_error_of(detail)
                if parsed:
                    code, name = parsed
                    return SubmitOutcome(
                        status="rejected",
                        error_code=code,
                        error_name=name,
                        message=detail[:1000],
                    )
                return SubmitOutcome(status="rejected", message=detail[:1000])

            transaction.sign(signer)
            sent = await server.send_transaction(transaction)
            tx_hash = sent.hash

            if str(getattr(sent, "status", "")).upper() == "ERROR":
                detail = str(getattr(sent, "error_result_xdr", "") or sent)
                return SubmitOutcome(status="rejected", tx_hash=tx_hash, message=detail[:1000])

            # From here the transaction is out of our hands, and the only three
            # answers are confirmed, rejected, or not yet known.
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(settings.soroban_poll_seconds)
                result = await server.get_transaction(tx_hash)
                status = result.status

                if status == GetTransactionStatus.SUCCESS:
                    return SubmitOutcome(
                        status="confirmed",
                        tx_hash=tx_hash,
                        ledger=getattr(result, "ledger", None),
                        network_time=self._instant(getattr(result, "created_at", None)),
                    )
                if status == GetTransactionStatus.FAILED:
                    detail = str(getattr(result, "result_xdr", "") or "transaction failed")
                    parsed = contract_error_of(detail)
                    if parsed:
                        code, name = parsed
                        return SubmitOutcome(
                            status="rejected",
                            tx_hash=tx_hash,
                            error_code=code,
                            error_name=name,
                            message=detail[:1000],
                        )
                    return SubmitOutcome(status="rejected", tx_hash=tx_hash, message=detail[:1000])
                # NOT_FOUND: not yet in a closed ledger. Keep waiting.

            log.warning(
                "soroban submission outcome unknown - parking for the reconciler",
                extra={"tx_hash": tx_hash, "function": function, "network": self.network},
            )
            return SubmitOutcome(
                status="unknown",
                tx_hash=tx_hash,
                message=(
                    "The transaction was submitted but no result arrived in time. "
                    "The chain is the authority; this will be reconciled."
                ),
            )
        finally:
            await server.close()

    async def register_book(
        self, *, contract_id: str, namespace: str, secret: str
    ) -> SubmitOutcome:
        """Open this organization's book on chain."""
        self._require_sdk()
        public_key = Keypair.from_secret(secret).public_key
        return await self._invoke(
            contract_id=contract_id,
            function="register",
            args=[
                self._bytes32(namespace),
                scval.to_address(Address(public_key)),
            ],
            secret=secret,
            timeout_seconds=settings.soroban_timeout_seconds,
        )

    async def rotate_admin(
        self, *, contract_id: str, namespace: str, secret: str, new_admin: str
    ) -> SubmitOutcome:
        """Hand the book to a different signer - the 2-of-3 upgrade path.

        Both signatures are required by the contract, so this only succeeds when
        the caller can also authorise as ``new_admin``. In practice that means the
        multisig account has been created with this server's key as one of its
        signers, which is the intended shape: the business's accountant becomes a
        co-signer without the server ever holding their key.
        """
        return await self._invoke(
            contract_id=contract_id,
            function="rotate",
            args=[
                self._bytes32(namespace),
                scval.to_address(Address(new_admin)),
            ],
            secret=secret,
            timeout_seconds=settings.soroban_timeout_seconds,
        )

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
        """Append one seal to the chain.

        Argument order matches the contract exactly. Keyword-only, because a
        positional call site that transposed ``root`` and ``prev`` would build a
        perfectly well-typed transaction that breaks the chain.
        """
        return await self._invoke(
            contract_id=contract_id,
            function="seal",
            args=[
                self._bytes32(namespace),
                scval.to_uint32(seq),
                self._bytes32(root),
                self._bytes32(prev),
                scval.to_uint32(count),
                scval.to_int128(int(debit_minor)),
                scval.to_uint64(int(covered_from)),
                scval.to_uint64(int(covered_to)),
            ],
            secret=secret,
            timeout_seconds=settings.soroban_timeout_seconds,
        )

    # -- reads -------------------------------------------------------------
    async def _simulate(self, *, contract_id: str, function: str, args: list[Any]) -> Any:
        """Read a contract function without submitting anything.

        Needs a source account to build a transaction against, and uses the
        network's own reserve-less "null" account for it: reads cost nothing and
        submit nothing, so requiring the caller to hold a funded key just to
        *look* at a seal would put a wallet between a verifier and a verdict.
        """
        self._require_sdk()
        server = self._server()
        try:
            # `Account` with sequence 0 - never submitted, so the sequence is
            # irrelevant and the account need not exist.
            from stellar_sdk import Account

            source = Account("GAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWHF", 0)
            transaction = (
                TransactionBuilder(
                    source_account=source,
                    network_passphrase=self.passphrase,
                    base_fee=settings.stellar_base_fee,
                )
                .append_invoke_contract_function_op(
                    contract_id=contract_id,
                    function_name=function,
                    parameters=args,
                )
                .set_timeout(30)
                .build()
            )
            simulated = await server.simulate_transaction(transaction)

            if getattr(simulated, "error", None):
                parsed = contract_error_of(str(simulated.error))
                if parsed and parsed[1] in ("not_registered", "seal_not_found"):
                    return None
                raise SorobanUnavailable(f"Reading {function} failed: {str(simulated.error)[:300]}")

            results = getattr(simulated, "results", None) or []
            if not results or not results[0].xdr:
                return None
            return scval.to_native(stellar_xdr.SCVal.from_xdr(results[0].xdr))
        finally:
            await server.close()

    async def read_book(self, *, contract_id: str, namespace: str) -> BookView | None:
        """The chain's head for one organization, or ``None`` if unregistered."""
        raw = await self._simulate(
            contract_id=contract_id,
            function="latest",
            args=[self._bytes32(namespace)],
        )
        if raw is None:
            return None
        return BookView(
            admin=_address(_field(raw, "admin")),
            head=int(_field(raw, "head") or 0),
            root=_hex(_field(raw, "root")),
            sealed_at=self._instant(_field(raw, "sealed_at")),
            covered_to=int(_field(raw, "covered_to") or 0),
            entries=int(_field(raw, "entries") or 0),
        )

    async def read_seal(self, *, contract_id: str, namespace: str, seq: int) -> SealView | None:
        raw = await self._simulate(
            contract_id=contract_id,
            function="get",
            args=[self._bytes32(namespace), scval.to_uint32(seq)],
        )
        if raw is None:
            return None
        at = self._instant(_field(raw, "at"))
        return SealView(
            seq=int(_field(raw, "seq")),
            root=_hex(_field(raw, "root")),
            prev=_hex(_field(raw, "prev")),
            count=int(_field(raw, "count")),
            debits=int(_field(raw, "debits")),
            period_from=int(_field(raw, "from")),
            period_to=int(_field(raw, "to")),
            at=at or dt.datetime.fromtimestamp(0, tz=dt.UTC),
        )

    async def is_registered(self, *, contract_id: str, namespace: str) -> bool:
        raw = await self._simulate(
            contract_id=contract_id,
            function="is_registered",
            args=[self._bytes32(namespace)],
        )
        return bool(raw)

    async def health(self) -> dict[str, Any]:
        """Whether the configured RPC is reachable, for ``/health/ready``."""
        server = self._server()
        try:
            latest = await server.get_latest_ledger()
            return {
                "reachable": True,
                "network": self.network,
                "rpc": self.rpc_url,
                "ledger": getattr(latest, "sequence", None),
            }
        except Exception as exc:
            return {
                "reachable": False,
                "network": self.network,
                "rpc": self.rpc_url,
                "error": str(exc)[:200],
            }
        finally:
            await server.close()


# ---------------------------------------------------------------------------
# Native-value helpers
# ---------------------------------------------------------------------------
def _field(raw: Any, name: str) -> Any:
    """Read one field from a decoded contract struct.

    ``scval.to_native`` renders a Soroban struct as a mapping, but the key type
    has changed between SDK majors - ``str`` in some versions, ``Symbol`` in
    others - and a dataclass in yet others. Normalising here means an SDK upgrade
    cannot turn every read into ``None`` and every verification into a silent
    failure.
    """
    if isinstance(raw, dict):
        if name in raw:
            return raw[name]
        for key, value in raw.items():
            if str(key) == name:
                return value
        return None
    return getattr(raw, name, None)


def _hex(value: Any) -> str:
    """A 32-byte contract value as lowercase hex."""
    if isinstance(value, bytes | bytearray):
        return bytes(value).hex()
    text = str(value or "")
    return text.lower()


def _address(value: Any) -> str:
    """A decoded contract ``Address`` as a bare ``G...`` / ``C...`` string.

    ``scval.to_native`` hands back the SDK's ``Address`` object, whose ``str()``
    is a debug repr - ``<Address [type=ACCOUNT, address=GDKL...]>``. Storing or
    displaying that would put a Python repr in front of a user and, worse, make an
    equality check against a real public key silently fail.
    """
    for attribute in ("address", "account_id"):
        resolved = getattr(value, attribute, None)
        if isinstance(resolved, str) and resolved:
            return resolved
    return str(value or "")
