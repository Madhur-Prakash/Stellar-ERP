<div align="center">

# proof_ledger

**Ledger 3. A Soroban contract holding cryptographic commitments to a business's books — and nothing else.**

![Rust](https://img.shields.io/badge/Rust-1.96-DEA584?style=flat-square&logo=rust&logoColor=white)
![SDK](https://img.shields.io/badge/soroban--sdk-27.0.6-1C6B4C?style=flat-square)
![Wasm](https://img.shields.io/badge/wasm-15_KB-8E5B0C?style=flat-square)
![Tests](https://img.shields.io/badge/tests-28_adversarial-2EA043?style=flat-square)

[Design rationale](../docs/attestation.md) · [Source](proof_ledger/src/lib.rs) · [Tests](proof_ledger/src/test.rs)

</div>

---

## What it stores

A 32-byte Merkle root per batch of journal entries, the number of entries it
covers, a control total in minor units, the batch's window, and the ledger
timestamp at which the network accepted it.

**Nothing else.** No amount attached to a party, no customer name, no GSTIN, no
product, no salary, no account number, no document. Nothing personal is written,
so there is nothing here a data-erasure request could need to reach.

The organisation is identified by 32 opaque bytes — `SHA-256(organization_id ‖
install_salt)` — so the record is unlinkable to a named business until the business
itself discloses the namespace to a counterparty.

---

## Why it is a contract and not a memo

A chain that accepts any hash handed to it is a log. This is a referee: it
re-enforces, at the boundary, the same rules the journal enforces internally.

| Invariant | Enforced how |
| --- | --- |
| **Append-only** | No `update`, no `delete`, and no administrative override on a written seal |
| **Strict sequencing** | `seq` must be exactly `head + 1`, so a skipped period is not a missing record — it is evidence |
| **Chain continuity** | `prev` must equal the stored root, so rewriting one period means re-sealing every period after it |
| **The network timestamps it** | `seal` takes no `at` argument; it is read from `env.ledger().timestamp()` |

That last row is the single most important line in the contract. A caller-supplied
timestamp would make every claim the whole subsystem makes worthless.

### Why there is no admin override

The obvious convenience — an `admin_fix_seal` for when something goes wrong — would
reintroduce exactly the problem this ledger exists to remove. If the operator can
rewrite a seal, a seal proves nothing about the operator. A permanently wrong seal
is recoverable (the business explains it, and the chain shows both the error and
the correction); a rewritable one is not.

---

## Interface

```rust
register(org: BytesN<32>, admin: Address) -> Book
seal(org, seq, root, prev, count, debits, from, to) -> Seal
get(org: BytesN<32>, seq: u32) -> Seal
latest(org: BytesN<32>) -> Book
verify(org: BytesN<32>, seq: u32, root: BytesN<32>) -> bool
history(org: BytesN<32>, before_seq: u32, limit: u32) -> Vec<Seal>
is_registered(org: BytesN<32>) -> bool
rotate(org: BytesN<32>, new_admin: Address) -> Book
```

`verify` is the verifier's call: one invocation, one boolean, the cheapest form of
the only question that matters. It returns `false` for a missing seal rather than
panicking, so a hand-edited proof shows a red tick instead of a crash.

`rotate` is the upgrade path to 2-of-3 co-signing. It requires **both** the outgoing
and incoming accounts to authorise — the outgoing so a stolen key alone cannot hand
the book away, the incoming so a book cannot be parked on an account that never
agreed to hold it and can therefore never seal again.

### Errors

| # | Name | Means |
| --- | --- | --- |
| 1 | `AlreadyRegistered` | A book exists for that namespace |
| 2 | `NotRegistered` | No book for that namespace |
| 3 | `SequenceOutOfOrder` | `seq != head + 1`. **On a retry this is success in disguise** — a previous attempt landed |
| 4 | `ChainBroken` | `prev` does not match the stored root; the caller's history has diverged |
| 5 | `EmptySeal` | `count == 0`. Sealing nothing is not an attestation |
| 6 | `PeriodOutOfOrder` | `to < from`, or a window starting before the last one ended |
| 7 | `SealNotFound` | No seal at that sequence |
| 8 | `RootIsSentinel` | An all-zero root, which is the genesis sentinel |

Error 3 is the one that matters operationally: it is what makes submission
idempotent **by consensus** rather than by the caller's retry logic.

---

## Storage

Everything is `persistent`, with the TTL extended on every touch.

`temporary` would be wrong to the point of dangerous: an expired temporary entry is
gone, and a missing seal in an append-only chain is indistinguishable from evidence
of tampering. A persistent entry that outlives its TTL is *archived*, not deleted —
restoring it is a fee, not a loss — and a proof bundle carries the root anyway, so
a restore is only ever needed to re-read what the verifier already holds.

Seals are keyed `(namespace, seq)` rather than held in a `Vec` on the book. A
vector would be re-serialised in full on every append, so sealing would get more
expensive as the chain grew and a business in its tenth year would pay more than
one in its first.

---

## Building and deploying

```bash
make contract-test      # 28 adversarial tests, native
make contract-lint      # clippy -D warnings, and a format check
make contract-build     # wasm32v1-none, ~15 KB
make contract-key       # generate and fund a testnet deployer
make contract-deploy    # prints the contract id
```

Then put the printed id in `.env` as `SOROBAN_CONTRACT_ID` and restart the API.

For mainnet:

```bash
make contract-deploy STELLAR_NETWORK=public STELLAR_IDENTITY=my-mainnet-key
```

The toolchain is pinned in [`rust-toolchain.toml`](rust-toolchain.toml). A Soroban
deployment is addressed by the hash of its wasm, so "compiles with whatever rustc
is installed" would mean the deployed hash cannot be reproduced — and a reviewer
could not confirm that the code they read is the code that is running.

---

## Tests

28 of them, and they are written **adversarially**. The contract's whole value is
what it refuses, so most assert a panic: an out-of-order sequence, a broken chain,
a re-seal, an empty seal, a stranger signing, a back-dated window. A suite that
only proved sealing works would prove nothing worth knowing.

Two are worth reading first:

- `a_duplicate_submission_is_rejected_by_the_contract` — the idempotency guarantee
  the backend's whole ambiguous-failure design rests on.
- `the_network_sets_the_timestamp_not_the_caller` — proves the timestamp moves with
  the ledger and cannot be supplied.

`should_panic(expected = "Error(Contract, #3)")` matches on the error **code**
rather than a message, so renaming a variant cannot silently make a test pass for
the wrong reason.

---

## Deployed instance

The testnet deployment this repository is configured against:

| | |
| --- | --- |
| **Network** | Testnet |
| **Contract** | `CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR` |
| **Wasm hash** | `2324b519f8a205a8cae31e1b8ebf3944be1bc5d1d6ec7028cdea3829f5e79246` |
| **Explorer** | [stellar.expert](https://stellar.expert/explorer/testnet/contract/CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR) |

The wasm hash is published so anyone can rebuild from this source and confirm they
get the same bytes that are deployed.
