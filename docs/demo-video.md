<div align="center">

# Demo video

**A three-minute script: what to record, in what order, and what to say over it.**

<!-- nav:start -->
[Docs](README.md) · [Spec](spec.md) · [Architecture](architecture.md) · [Database](database.md) · [Accounting](accounting.md) · [Proof ledger](attestation.md) · [API](api.md) · [Security](security.md) · [Audit](security-audit.md) · [Commands](commands.md) · [Screenshots](screenshots.md) · **Demo video** · [Evidence](evidence.md) · [Development](development.md) · [Deployment](deployment.md)
<!-- nav:end -->

[Submission](../SUBMISSION.md)

</div>

---

## The one idea

Everything below serves a single moment: **a proof verifies, one digit is changed,
and it fails.** That is the product. A tour of the invoicing screens is a tour of
software that already exists in a hundred forms; the seal is the part that does not.

So the shot budget is spent accordingly - about 40 seconds establishing why private
books are worthless to a bank, 90 seconds on sealing and verifying, and 30 on the
tamper. Nothing else earns a place.

**Do not narrate the UI.** "Now I click Trust" tells a viewer what they can already
see. Say why the screen exists while they watch you use it.

---

## Before you record

| | |
| --- | --- |
| Stack up | `make up`, and confirm <http://localhost:5173> loads |
| Data | An organization with **at least three posted journal entries** and one GST invoice. Empty tables film badly - `make seed` populates twelve businesses if you need them |
| Sealing | **Off** at the start - switching it on is a shot |
| Theme | Light. It reproduces better after compression, and the seal badges read more clearly |
| Window | 1440 × 900, browser chrome minimised, no bookmarks bar, no extensions visible |
| Second window | A private window, signed out, already on `/verify` - so the "no account" claim is visibly true rather than asserted |
| Explorer tab | stellar.expert on the [contract page](https://stellar.expert/explorer/testnet/contract/CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR), pre-loaded |
| Notifications | Off. A Slack toast mid-take costs you the whole take |
| Bundle | Export one proof bundle **beforehand** and keep a copy - you need an untouched one and an edited one |

Record at 1080p or better, 30 fps. Screen only; a webcam inset competes with the
figures on screen, which are the point.

---

## Shot list

Total ≈ 3:00. Timings are targets, not a metronome.

### 1 · The problem - 0:00–0:35

**On screen.** The Accounting screen, trial balance visible. Scroll slowly through
the journal. Then open a terminal beside it and run `make psql`, and type - without
executing - an `UPDATE journal_entry SET ...`.

> These are real books. Double-entry, GST, period locks. Posted entries have no edit
> path - you correct by reversal - and the audit trail has no update column, because
> a log you can edit is not evidence.
>
> All of that protects the business from its own staff and its own bugs. It protects
> nobody from the business. Two minutes in psql and both ledgers agree, in flawless
> double-entry, on a history that never happened.
>
> So the books are useless to exactly the people who most need to read them. A bank
> underwriting a working-capital line asks for bank statements instead, and prices
> the difference as risk.

**Do not run the UPDATE.** Typing it is the point; executing it wastes the fixture
you are about to seal.

---

### 2 · Why not just publish them - 0:35–0:50

**On screen.** Stay on the invoice. Hover a customer name, a margin, a supplier
term.

> The obvious answer is to put the books on a blockchain. That exposes margins,
> customers, supplier terms and salaries - and in India it runs straight into the
> DPDP Act.
>
> The real problem is narrower: make a private ledger provably unaltered to a
> stranger who never sees anything inside it.

---

### 3 · Switching it on - 0:50–1:20

**On screen.** Trust screen. Press **Turn sealing on**, and let the toast land.
Then open the details block.

> Switching sealing on creates a Stellar account for this organization, funds it,
> and registers its book on a Soroban contract. Two transactions, and the business
> never sees a wallet.
>
> Each organization gets its own signer. That is also what removes sequence-number
> contention - there is no shared account for two writers to collide on.

Point at the namespace field.

> What identifies this business on chain is a salted hash of its internal id.
> Nothing up there names them until they hand somebody a proof.

---

### 4 · Sealing - 1:20–1:45

**On screen.** Press **Seal now**. When the seal appears, click through to the
transaction on stellar.expert. Let the explorer page sit for three seconds.

> That is the whole payload: a 32-byte root, an entry count, a control total, and
> the window it covers. No amount attached to a party, no customer, no GSTIN, no
> product. Zero bytes of business data.
>
> The contract refuses a skipped sequence, so a gap is not a missing record - it is
> evidence. And `seal` takes no timestamp argument; it reads the ledger's own. A
> caller-supplied time would make every claim here worthless.

Then, back on Trust, point at the backlog tile.

> The headline is the age of the unsealed backlog, not the number of seals. "412
> entries sealed" is reassuring and says nothing about now.

---

### 5 · The verdict - 1:45–2:20

**On screen.** Open an invoice, export the proof bundle, and open the JSON in an
editor for two seconds - just long enough to see it is small and contains one entry.
Then switch to the **private window**, on `/verify`, and drop the file in.

> This is a different browser profile. Not signed in, no account, no wallet, no
> extension.
>
> It re-encodes the entry, hashes it, folds the Merkle path, and asks the contract
> directly - over an RPC endpoint I can change, right here, on screen.

Change the RPC field to a public endpoint and re-run. Green again.

> Our API is not consulted for that answer at any point. That is the only reason
> the answer is worth anything.
>
> The rules are implemented twice - once in Python on the server, once in
> TypeScript in this page - and a test pins them to the same golden vector on every
> CI run. One shared implementation would be cheaper and would mean the verifier is
> running our code.

---

### 6 · The tamper - 2:20–2:45

**On screen.** Open the bundle, change **one digit** of the amount, save, re-drop it
in the verifier. Let the failure render. Zoom on the failed step.

> One digit.
>
> It does not fail with "the file looks wrong". The recomputed leaf hash no longer
> folds to the root the contract holds - a number that was published before that
> edit was made.

---

### 7 · What it does not prove - 2:45–3:00

**On screen.** Back on Trust, scroll to the **"What it does not"** card. Leave it on
screen while you say this. Do not cut away early.

> A seal proves the books shown today are byte-identical to the books that existed
> when it was written, at a time the network attests to and the business cannot
> back-date.
>
> It does not prove the entries were true when they were made. No cryptographic
> scheme can. What it removes is retroactive fabrication - which is how accounts are
> actually cooked.
>
> That is on the screen, not in the footnotes. And while the signing key sits on the
> server, the operator could still doctor the books before sealing - which is why
> the default is daily, and why the book can be moved onto a 2-of-3 multisig with
> the business's accountant.

**End on that card.** A trust product that oversells itself is not a trust product,
and a demo that ends on a green tick is overselling.

---

## Optional closing card - 3:00–3:10

Static, three lines:

```
Stellar ERP · the third ledger
Contract  CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR  (testnet)
github.com/Madhur-Prakash/Stellar-ERP
```

---

## Things that ruin a take

| | |
| --- | --- |
| **Sealing with an empty journal** | The contract rejects an empty seal - `EmptySeal`, error 5. Post entries first |
| **A stale frontend build** | `VITE_*` values are inlined at build time. If you redeployed the contract, `make build` before recording or the verifier reads the old one |
| **Recording the verifier in the same profile** | It looks identical and quietly destroys the claim. Use a private window and let the viewer see it has no session |
| **Editing the bundle before the good take** | Export two copies. You need the clean one first |
| **Cutting away from the limitation card** | It is the most credible fifteen seconds in the video |
| **A visible `.env`, terminal history, or a real customer name** | Use fixture data throughout - and if you seeded, remember the demo rows are marked |

---

## After recording

1. Upload unlisted, or public if you prefer.
2. Put the link in [SUBMISSION.md](../SUBMISSION.md) row 7 and in the README.
3. Pull three stills from the take for the [screenshots](../SUBMISSION.md#screenshots)
   requirement - the Trust screen, the verifier's verdict, and the failure. Capture
   the mobile shot separately at 390 × 844.

<!-- related:start -->

---

## Related reading

- [Proof ledger](attestation.md) - what a seal proves and does not, in full
- [Commands](commands.md#7-demonstrating-that-the-records-are-tamper-evident) - the same tamper walkthrough, as commands
- [Submission](../SUBMISSION.md) - the checklist this video is one row of

[All documentation](README.md)
<!-- related:end -->
