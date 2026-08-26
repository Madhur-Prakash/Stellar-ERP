<div align="center">

# The proof ledger

**Ledger 3: cryptographic commitments to the books, on Stellar. What it proves, what it does not, and every decision behind it.**

![Chain](https://img.shields.io/badge/contract-Soroban-1C6B4C?style=flat-square)
![Data](https://img.shields.io/badge/business_data_on_chain-0_bytes-DA3633?style=flat-square)
![Encoding](https://img.shields.io/badge/canonical_encoding-v1_frozen-8E5B0C?style=flat-square)

<!-- nav:start -->
[Docs](README.md) · [Spec](spec.md) · [Architecture](architecture.md) · [Database](database.md) · [Accounting](accounting.md) · **Proof ledger** · [API](api.md) · [Security](security.md) · [Audit](security-audit.md) · [Commands](commands.md) · [Development](development.md) · [Deployment](deployment.md)
<!-- nav:end -->

</div>

---

## The problem this solves

This ERP keeps two ledgers, and both are built carefully:

- **Ledger 1 — the journal.** Every entry balances, enforced as a database `CHECK`.
  Posted entries have no edit path; correction is by reversal, leaving both the
  mistake and its cancellation on the record.
- **Ledger 2 — the audit trail.** Append-only by construction: no `updated_at`,
  no soft delete, no update path in the repository.

Both are **self-attested**. They are immutable because no code path mutates them —
which protects the business against its own staff, its own bugs and its own
accidents, and protects nobody at all against the business. In a self-hosted
install the person holding the database password is the owner. Two minutes in
`psql` and both ledgers agree, in flawless double-entry, on a history that never
happened.

So the books are useless to exactly the people who most need to read them: a bank
underwriting a working-capital line, a buyer running supplier diligence, a
marketplace onboarding a seller. The only instrument that converts private books
into credible ones is a statutory audit, which costs more than the software and
arrives eleven months late.

**Ledger 3 fixes that, and only that.**

---

## What each ledger is for

| | Ledger 1 · Journal | Ledger 2 · Audit | **Ledger 3 · Proof** |
| --- | --- | --- | --- |
| **Answers** | What happened to the money | Who did it, and what changed | **Has any of it been altered since** |
| **Lives in** | PostgreSQL, on your server | PostgreSQL, same server | **Stellar, in a Soroban contract** |
| **Holds** | Balanced debit/credit lines | Actions, actors, field diffs | **Merkle roots and control totals** |
| **Written by** | `PostingService.post_entry` | `AuditService.record` | `SealService.seal_period` |
| **Immutable because** | No update code path exists | No `updated_at`, no update path | **Consensus** |
| **Trusted by** | The business | The business | **Anyone** |

---

## What a seal proves — stated precisely

> A seal proves that the books presented today are **byte-identical to the books
> that existed when the seal was written**, and that the seal was written at a time
> the network attests to and the business cannot back-date.
>
> It does **not** prove the entries were true when they were made. No cryptographic
> scheme can. What it eliminates is **retroactive** fabrication — which is the
> overwhelming majority of real books-cooking, because accounts are usually cooked
> by editing history to fit a story told later.

Overclaiming here is how this kind of project loses credibility, so the limitation
is on the Trust screen as well as in this document — including the sharper version
of it:

**While the signing key is held on the server, the operator could doctor the books
*before* sealing.** Three things narrow that, in increasing strength:

1. **Seal often.** The default cadence is daily, which makes the tampering window a
   day rather than a year. This is affordable only because of Stellar's fees — see
   [Why Stellar](#why-stellar).
2. **The hash chain plus network timestamps.** Each seal references its
   predecessor's root, so rewriting one period requires re-sealing every period
   after it — and the network attests to when each seal actually happened, so
   back-dating is not merely detectable, it is loud and permanent.
3. **2-of-3 co-signing.** `POST /attestation/signer/rotate` moves the book onto a
   Stellar multisig account whose signers are the business, its chartered
   accountant, and a neutral third party. Tampering then requires a licensed
   professional carrying statutory liability to co-sign a fraud.

---

## Architecture

```
posting                     batching                    submission
───────                     ────────                    ──────────
PostingService              SealService                 seal worker
  .post_entry()               .create_seal()              .drain()
      │                           │                           │
      │ notify_entry_posted       │ writes ONE row             │ Soroban RPC
      ▼   (same transaction)      ▼   (same transaction)       ▼
  seal_leaf                    seal (status=pending)      proof_ledger
  32-byte hash                 merkle root + totals       contract
      └───────────────────────────┴───────────────────────────┘
                                  │
                          verification (elsewhere)
                                  │
                      the counterparty's browser
                      recomputes and asks the chain
```

**Nothing in the request path touches the network.** Posting an entry writes one
extra row. Closing a period writes one extra row. Both transactions commit in
milliseconds, so a chain outage can never block a month-end close. The worker
reaches the chain afterwards.

### Why accounting does not import attestation

Dependencies in this codebase point inward, and accounting is the contract sales,
purchasing and billing are built against. It must not acquire a dependency on a
module that sits above it — an `import` from accounting into attestation would mean
the ledger could no longer be tested, reasoned about, or deployed without the
blockchain subsystem.

So accounting **announces** and interested modules **subscribe**
([`accounting/hooks.py`](../backend/app/modules/accounting/hooks.py)). Attestation
registers itself once, from the composition root in `main.py`. Two properties that
seam guarantees:

- **A hook runs inside the caller's transaction**, so a leaf commits or rolls back
  with the posting it describes.
- **A hook can never fail its caller.** Every exception is caught and logged. The
  posting is the statutory act; a commitment to it is commentary. A bug in the
  proof ledger must not be able to stop a business invoicing.

Setting `ATTESTATION_ENABLED=false` removes the subscriber and the accounting core
never notices.

---

## The canonical encoding

[`canonical.py`](../backend/app/modules/attestation/canonical.py) is **a one-way
door**, and it is the reason the whole subsystem can be trusted.

A Merkle root is only meaningful if the same journal entry hashes to the same 32
bytes forever — across a Python upgrade, a schema migration, a refactor, a
different machine, and a re-implementation in TypeScript written by somebody who
has never read the file. Once a root is on chain, every proof issued against it
depends on the encoding being reproducible. Change one byte of it and every
historical proof silently stops verifying, with no error anywhere to say so.

So the rules are deliberately paranoid:

| Rule | Why |
| --- | --- |
| **Field order is hand-written, never derived from the ORM** | Built from `__table__.columns`, a later migration adding a column would silently enter the hash — and a migration is exactly the kind of change nobody reviews for cryptographic consequences |
| **Every value is length-prefixed, and absent ≠ empty** | `narration=""` and `narration=None` must not share a leaf; the difference between them is what a dispute turns on |
| **Money is a fixed-width `i128` of minor units** | `Decimal("100.00")` and `Decimal("100.0000")` are the same number and different strings, and a round-trip through the database can change which one you hold |
| **Leaf and node hashes are domain-separated** (`0x00` / `0x01`) | Without it, an interior node's 64-byte preimage could be presented as leaf data and a second preimage constructed for free |
| **A version byte leads the encoding** | A v2 gets a new byte and a new field order; v1 leaves keep verifying against v1 roots forever. Additive, never in place |
| **A golden-vector test pins the output** | Any change that alters a hash fails the build rather than quietly invalidating every proof ever issued |

### What is deliberately not hashed

**An entry's status.** This was a real bug, caught by the reversal test. A leaf
commits to what was *recorded*, not to what later happened to it — and this ledger
corrects by reversal, so `posted` becoming `reversed` is the normal path for any
entry. With the status in the hash, a business would have sealed its March books,
issued a credit note in May, and found that its March invoice no longer verified.
The subsystem would have accused it of tampering for doing the right thing.

Nothing is lost: a reversal is itself a journal entry, gets its own leaf, and is
sealed in its turn.

**Account codes and names.** Account **ids** are hashed; codes and names are not. A
code is a label a business may renumber and a name is one it may reword, and
hashing either would mean that renaming `1100 Accounts Receivable` next year
invalidated every proof for every entry that ever touched it. Codes travel in the
proof bundle as display metadata, explicitly labelled as not covered.

---

## The Merkle tree

RFC 6962, **not** the Bitcoin construction. The obvious tree duplicates the last
node when a level has an odd count, and two different leaf lists can then produce
the same root — so a proof for one can be presented as a proof for the other. RFC
6962 splits at the largest power of two below `n`, which is unambiguous for every
`n`, and it is specified precisely enough that two independent implementations can
be checked against each other rather than against each other's bugs.

The point of a tree rather than one hash over the period is **selective
disclosure**: a business can prove one invoice without revealing the other four
hundred. It sends the entry plus about `log₂(n)` sibling hashes, and the siblings
are opaque — a verifier learns that other entries exist and nothing about what they
say.

### The encoding exists twice, on purpose

`canonical.py` and
[`canonical.ts`](../frontend/src/features/trust/canonical.ts) implement the same
specification independently. If the verifier called the server for a verdict, a
verifier would have gained nothing — a compromised backend would answer
`valid: true` for anything.

The cost is that the two can drift, and the failure mode is catastrophic and
invisible. So
[`canonical.test.ts`](../frontend/src/features/trust/canonical.test.ts) asserts
that the TypeScript reaches the same 32 bytes as the pinned Python golden vector,
in CI, on every run.

---

## Batching: why not by accounting period

The intuitive unit is the month. It is the wrong one, for a reason that only shows
up in real books: **a journal entry can be posted into a period after that period
has already been sealed.** A daily seal covers an open month; the next day brings
three more entries dated inside it. And a bill for March genuinely arrives on 3
April.

If the sealing unit were the month, either the March root would have to change —
which the contract forbids, correctly — or those entries would never be sealed.

So the unit is a **batch**: leaves `(last_sealed, cutoff]` in per-organization
posting order. Batches are consecutive and non-overlapping by construction, which
is also what makes the contract's "windows tile forwards" check hold with no
special casing.

The consequence is stated on the Trust screen: a seal attests *"these entries were
in the books at this moment"*, not *"these are all the entries for March"*. The
second claim is the one a naive design accidentally makes and cannot keep.

---

## The contract

[`contracts/proof_ledger`](../contracts/proof_ledger) — Rust, ~15 KB of wasm, eight
exported functions, 28 adversarial tests.

```rust
pub struct Seal {
    pub seq:    u32,        // per-org, strictly previous + 1
    pub root:   BytesN<32>, // merkle root of the batch
    pub prev:   BytesN<32>, // the previous seal's root - the chain
    pub count:  u32,        // entries covered
    pub debits: i128,       // control total, minor units
    pub from:   u64,        // window start
    pub to:     u64,        // window end
    pub at:     u64,        // ledger timestamp - set by the NETWORK
}
```

| Function | Refuses |
| --- | --- |
| `register(org, admin)` | A second book for a namespace that already has one |
| `seal(org, seq, root, prev, count, debits, from, to)` | `seq != head + 1`, `prev != stored root`, `count == 0`, `to < from`, `from < covered_to`, an all-zero root |
| `get(org, seq)` / `latest(org)` / `verify(org, seq, root)` / `history(...)` | — reads |
| `rotate(org, new_admin)` | A rotation not authorised by **both** accounts |

There is no `update`, no `delete`, and **no administrative override on a written
seal** — an admin who could rewrite a seal would reintroduce the exact problem this
ledger exists to remove.

`seal` takes no `at` parameter. The timestamp is read from
`env.ledger().timestamp()`. This is the single most important line in the contract:
a caller-supplied timestamp would make every claim worthless.

### What is on chain, and what is never

**On chain:** 32-byte roots, entry counts, control totals in minor units,
timestamps.

**Never on chain:** an amount attached to a party, a customer name, a GSTIN, a
product, a salary, a bank account number, a document. Nothing personal is written,
so there is nothing a DPDP Act or GDPR erasure request could need to reach.

The organisation is identified by `org_ns = SHA-256(organization_id ‖ salt)`, so
the on-chain record is unlinkable to a named business until the business itself
discloses the namespace — which is exactly what handing a counterparty a proof
bundle does, deliberately, one counterparty at a time.

---

## The ambiguous failure

PostgreSQL commits in milliseconds; Soroban finalises in about five seconds. You
cannot hold a database transaction open across a call to a blockchain, and a chain
outage must not block a month-end close.

The transactional outbox handles the easy half. The hard half is this: **the submit
times out and we do not know whether it landed.** Resubmitting risks a double seal;
not resubmitting risks a gap — and a gap in this design is indistinguishable from
evidence of tampering, so it is not a benign failure mode.

The resolution is one sentence: **the chain, not the database, is the authority on
what has been sealed.**

- Idempotency is enforced by the **contract**, not by retry logic: a duplicate
  submission carries a `seq` that is no longer `head + 1` and is refused by
  consensus.
- A submission whose outcome is unknown parks the row as `submitted` and the
  reconciler resolves it against `latest()` on the next pass and on every startup.
- A seal that fails permanently releases its leaves back to the head of the
  backlog, keeps its own row for the record, and lets the replacement **reuse the
  same sequence number** — because the contract's `head` never moved, so that
  number is still the only one it will accept. Two partial unique indexes
  (`WHERE status <> 'failed'`) exist for exactly this.

---

## Why Stellar

Notarising a hash is possible on any chain. These are reasons this product is
materially worse, or infeasible, elsewhere.

**Cost is the feasibility argument, not a nice-to-have.** An attestation is only
worth something if it happens often — a proof written once a year leaves a
twelve-month window in which history can be rewritten freely, which is exactly the
window fraud lives in. Stellar charges under one US cent per hundred thousand
operations, so a daily seal costs less than the electricity the server draws
computing it. On a gas-priced network the same product must either seal rarely and
destroy the guarantee, or charge more than the software costs.

**Soroban enforces rather than stores.** A chain that accepts any hash handed to it
is a log; this is a referee. The contract re-enforces the ledger's own invariants
at the boundary, so a skipped period is not a missing record — it is evidence.

**Native multisig is a protocol primitive.** 2-of-3 co-signing is a `set_options`
call, not a contract to write and audit forever. Getting the same property
elsewhere means shipping a multisig contract with a larger security surface than
the rest of the product.

**The road from proof to money is on the same network.** Once a receivable is
attested, SEP-24/SEP-6 anchors and SEP-31 give it a settlement rail, and SEP-41 /
the Stellar Asset Contract are where it becomes a transferable instrument. That
work is **gated** and not built — see the roadmap in the submission — but it is on
the same ledger the proof already lives on.

---

## Tables

| Table | Rows | Notes |
| --- | --- | --- |
| `attestation_setting` | one per organization | Whether sealing is on, the namespace, the contract, the signer. The signing seed is **Fernet-encrypted** with the same key material as a TOTP secret |
| `seal_leaf` | one per posted entry | The canonical hash. No `updated_at` — a leaf cannot change. Partial index on the unsealed backlog, which is the only part ever scanned |
| `seal` | one per batch | **This row is also the outbox.** A separate outbox table would be a second record of the same fact |

`Numeric(38, 0)` for `debit_minor`, not `BIGINT`: a lifetime turnover in paise
exhausts a signed 64-bit integer well inside the range this product targets, and a
control total that silently wraps is worse than none.

---

## API

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `GET /attestation/status` | `seal:read` | Everything the Trust screen shows, including the chain's own view |
| `GET /attestation/seals` | `seal:read` | Seal history, with continuity computed server-side |
| `POST /attestation/enable` | `seal:configure` | Configure a signer, fund it on testnet, open the book. Idempotent |
| `POST /attestation/seals` | `seal:write` | Seal now |
| `POST /attestation/reconcile` | `seal:write` | Correct local state from the chain |
| `GET /attestation/proof/{id}` | `proof:export` | A self-contained proof bundle for one entry |
| `GET /attestation/adoption` | **superuser** | Every organization with a book, install-wide — and the transaction hashes to check it with |
| `POST /verify/bundle` | **none** | Check a bundle — a convenience, never the authority |
| `GET /verify/chain/{namespace}` | **none** | A namespace's seals, read from the chain |
| `GET /verify/network` | **none** | Chain coordinates, so the browser can read the contract without us |
| `GET /verify/spec` | **none** | The canonical encoding, so anybody can reimplement it |

The full surface — fourteen authenticated routes and these four — is in
[API](api.md#proof-ledger---attestation).

`seal:write` is deliberately **not** implied by `journal:post`, for the same reason
`period:close` is not: a bookkeeper posts daily and should not be able to publish a
commitment to a public network on the organization's behalf.

`GET /attestation/adoption` is the report that answers "is anybody actually using
this?" - install-wide, superuser, most active first. It counts organizations that
have written a **confirmed** seal, which is deliberately not the same as
organizations that switched sealing on, and every row carries the signer address and
the head transaction hash so the answer can be checked on a public explorer by
somebody who does not have to believe us.

`/verify/*` is the only unauthenticated router in the application. It exists
because the verifier — a bank's credit officer, an auditor, a buyer — has been
handed a proof bundle and needs a verdict, and requiring an account would defeat
the whole design. What it returns is either computed from a bundle the caller
already sent, or already public on the Stellar ledger; none of the four handlers
issues a single SQL statement, and a test counts them to keep it that way. All four
are rate-limited separately from the global budget.

The last two exist so the *browser* needs nothing from us but the page — they are
what turns "trust our verdict" into "here are the coordinates, check it yourself".

---

## Operating it

```bash
# Build, test, deploy the contract
make contract-test          # 28 adversarial tests, native
make contract-build         # wasm, ~15 KB
make contract-up            # test, build, deploy, verify, and write .env

# Then set SOROBAN_CONTRACT_ID in .env and restart.
```

The seal worker runs **in-process by default**, which is what keeps the whole
product one `docker compose up`. It is safe in every replica: it holds no lock and
needs none, because two replicas racing to seal collide on the contract's sequence
number and the loser is refused by consensus.

To run it separately, set `SEAL_WORKER_ENABLED=false` in the API and run:

```bash
python -m app.modules.attestation.worker
```

Same code, same function — the worker is a loop around something the API can also
call, not a parallel implementation.

### Checking a bundle before you send it

```bash
make verify-proof f=bundle.json            # against the live chain
uv run python scripts/verify_proof.py bundle.json --offline   # structure only
uv run python scripts/verify_proof.py bundle.json --rpc https://my-own-rpc
```

Four steps, printed one line each, exit 0 or 1. Two design points worth naming:

- **`--offline` is labelled as the weaker answer.** It proves the file has not been
  edited and says nothing about whether the root was ever published. Only the chain
  says that, and a tool that blurred the two would be actively misleading.
- **A bundle that cannot be read exits 2, not 1.** A typo in a filename must not
  come back looking like a failed verification. One is a mistake; the other is a
  finding about somebody's books.

The script tells the reader that it came from us. A counterparty should use the
browser verifier or their own RPC - this exists so a business can catch a bad export
*before* a bank does.

### What to watch

**The age of the backlog, not the seal count.** "412 entries sealed" is reassuring
and says nothing about now. `days_unsealed` on `GET /attestation/status` is the
only figure that distinguishes sealing working from sealing having silently
stopped — the two are otherwise identical from the outside. The Trust screen leads
with it, and `chain.agrees_with_local == false` outranks everything else.

<!-- related:start -->

---

## Related reading

- [Accounting](accounting.md) — the ledger this commits to, and the reversal rule the encoding had to respect
- [Architecture](architecture.md) — the inward-pointing dependency rule the hook seam exists to preserve
- [Security](security.md) — how the signing key is stored, and what an error report is allowed to carry

[All documentation](README.md)
<!-- related:end -->
