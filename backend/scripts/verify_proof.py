#!/usr/bin/env python
"""Check a proof bundle from the command line, against the live chain.

Why this exists when the browser already does it
------------------------------------------------
The **authoritative** verifier is the one in the browser at ``/verify``: it is
written in TypeScript, it re-implements the encoding independently, and it reads
the contract over an RPC endpoint the reader chooses. That independence is the
whole point - a verdict issued by the party being audited is not a verdict.

This script is for the other two audiences:

* **The business**, confirming a bundle is good *before* emailing it to a bank.
  Discovering a bad export from the bank is the expensive way to find out.
* **A counterparty who would rather not run a browser** - a CI job, a script in a
  credit team, an auditor with a terminal. They should be told plainly that this
  code came from us, which the output does.

It needs no database, no running API, and no account. Give it a bundle and it
talks to Soroban directly::

    uv run python scripts/verify_proof.py bundle.json
    uv run python scripts/verify_proof.py bundle.json --rpc https://my-own-rpc
    uv run python scripts/verify_proof.py bundle.json --offline

Exit codes: ``0`` verified, ``1`` not verified, ``2`` the bundle could not be
read at all. A non-zero exit is meant to be usable in a pipeline, so the failure
modes are distinguished rather than collapsed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Importable as a script from the backend root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules.attestation.canonical import (
    CANONICAL_VERSION,
    leaf_hash_hex,
)
from app.modules.attestation.merkle import verify_inclusion
from app.modules.attestation.service import BUNDLE_FORMAT
from app.modules.attestation.stellar import SorobanClient

TICK = "  [ok]  "
CROSS = "  [FAIL]"
INFO = "  [--]  "


class BundleError(Exception):
    """The file is not a bundle this script can read - distinct from a bundle
    that reads fine and does not verify. One is a mistake, the other is a
    finding, and conflating them would let a typo look like fraud."""


def _load(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BundleError(f"Could not read {path}: {exc}") from exc

    try:
        bundle = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BundleError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(bundle, dict):
        raise BundleError(f"{path} does not contain a JSON object")

    fmt = bundle.get("format")
    if fmt != BUNDLE_FORMAT:
        raise BundleError(
            f"Unrecognised bundle format {fmt!r}; this script reads {BUNDLE_FORMAT!r}"
        )

    for key in ("entry", "seal", "leaf", "path", "chain"):
        if key not in bundle:
            raise BundleError(f"The bundle has no {key!r} section")

    return bundle


def _print_header(bundle: dict[str, Any]) -> None:
    chain = bundle["chain"]
    seal = bundle["seal"]
    print("Proof bundle")
    print(f"{INFO}format          {bundle.get('format')}")
    print(f"{INFO}exported        {bundle.get('generated_at')}")
    print(f"{INFO}network         {chain.get('network')}")
    print(f"{INFO}contract        {chain.get('contract_id')}")
    print(f"{INFO}namespace       {chain.get('org_namespace')}")
    print(f"{INFO}seal            #{seal.get('seq')}  ({seal.get('entry_count')} entries)")
    print(f"{INFO}covering        {seal.get('covered_from')} .. {seal.get('covered_to')}")
    if seal.get("tx_hash"):
        print(f"{INFO}transaction     {seal['tx_hash']}")
    print()


async def _verify(bundle: dict[str, Any], *, rpc: str | None, offline: bool) -> bool:
    entry = bundle["entry"]
    seal = bundle["seal"]
    claimed_leaf = bundle["leaf"]["hash"]
    expected_root = seal["merkle_root"]
    path = bundle["path"]

    # ---- 1. the encoding version -------------------------------------------
    spec_version = (bundle.get("spec") or {}).get("version")
    if spec_version != CANONICAL_VERSION:
        print(
            f"{CROSS} step 1  the bundle was written with encoding version "
            f"{spec_version}, and this script speaks version {CANONICAL_VERSION}"
        )
        print(
            "\n         This is a version mismatch, not tampering. The encoding is "
            "frozen per\n         version precisely so an old bundle keeps verifying "
            "- use a build that\n         speaks its version."
        )
        return False
    print(f"{TICK} step 1  encoding version {spec_version}")

    # ---- 2. the entry hashes to the leaf it claims --------------------------
    try:
        recomputed = leaf_hash_hex(entry)
    except Exception as exc:
        print(f"{CROSS} step 2  the entry could not be canonically encoded: {exc}")
        return False

    if recomputed != claimed_leaf:
        print(f"{CROSS} step 2  the entry does not hash to the leaf the bundle claims")
        print(f"         computed {recomputed}")
        print(f"         claimed  {claimed_leaf}")
        print("\n         The entry's contents have been altered since it was sealed.")
        return False
    print(f"{TICK} step 2  entry hashes to {recomputed}")

    # ---- 3. the path folds to the sealed root ------------------------------
    if not verify_inclusion(bytes.fromhex(recomputed), path, bytes.fromhex(expected_root)):
        print(f"{CROSS} step 3  the proof path does not lead to the sealed root")
        print(f"         path of {len(path)} step(s), expected root {expected_root}")
        return False
    print(f"{TICK} step 3  path of {len(path)} step(s) folds to {expected_root}")

    if offline:
        print(f"{INFO}step 4  skipped (--offline)")
        print(
            "\nThe bundle is internally consistent. **This is the weaker of the two "
            "answers.**\nIt says the file has not been edited; it does not say the root "
            "was ever\npublished. Only the chain says that."
        )
        return True

    # ---- 4. the chain holds that root at that sequence ---------------------
    chain = bundle["chain"]
    if rpc:
        # Deliberately overridable, and printed. A verifier who cannot choose the
        # RPC is trusting whoever chose it for them.
        print(f"{INFO}step 4  using RPC {rpc}")

    try:
        client = SorobanClient(chain.get("network"))
        if rpc:
            client.rpc_url = rpc
        on_chain = await client.read_seal(
            contract_id=str(chain.get("contract_id") or ""),
            namespace=str(chain.get("org_namespace") or ""),
            seq=int(seal["seq"]),
        )
    except Exception as exc:
        print(f"{CROSS} step 4  the chain could not be reached: {exc}")
        print(
            "\n         Nothing is proven either way. This is a network result, not a "
            "verdict\n         about the books - try another RPC endpoint with --rpc."
        )
        return False

    if on_chain is None:
        print(f"{CROSS} step 4  no seal exists on chain at sequence #{seal['seq']}")
        return False

    if on_chain.root != expected_root:
        print(f"{CROSS} step 4  the root on chain does not match the bundle")
        print(f"         on chain {on_chain.root}")
        print(f"         bundle   {expected_root}")
        print("\n         The books presented are not the books that were sealed.")
        return False

    print(f"{TICK} step 4  the contract holds that root at #{on_chain.seq}")
    print(f"{TICK} step 5  sealed at {on_chain.at.isoformat()} by the network's clock")

    if on_chain.count != int(seal.get("entry_count", -1)):
        # Not fatal to the inclusion proof, but worth saying out loud: the counts
        # disagreeing means the bundle's description of the batch is wrong even
        # though the root is right.
        print(
            f"{INFO}note    the bundle says {seal.get('entry_count')} entries and the "
            f"chain says {on_chain.count}"
        )

    print(
        f"\nVERIFIED. This entry was part of the books sealed at "
        f"{on_chain.at.isoformat()}\nand has not changed since."
    )
    print(
        "\nWhat this does not say: that the entry was true when it was made. No "
        "proof can.\nWhat it rules out is the books being rewritten afterwards."
    )
    print(
        "\nThis script is ours. If you are the party being audited, that is fine. If "
        "you\nare not, check it again at /verify in a browser, or against your own RPC "
        "with\n--rpc - the contract is public and the encoding is in the bundle's "
        "`spec`."
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a Stellar ERP proof bundle against the live chain.",
    )
    parser.add_argument("bundle", type=Path, help="the exported proof bundle, as JSON")
    parser.add_argument(
        "--rpc",
        default=None,
        help="a Soroban RPC endpoint of your own choosing, instead of the default",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="check only that the bundle is internally consistent (weaker answer)",
    )
    args = parser.parse_args()

    try:
        bundle = _load(args.bundle)
    except BundleError as exc:
        print(f"{CROSS} {exc}", file=sys.stderr)
        return 2

    _print_header(bundle)
    ok = asyncio.run(_verify(bundle, rpc=args.rpc, offline=args.offline))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
