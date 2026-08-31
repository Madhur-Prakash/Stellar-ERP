<div align="center">

# Level 4 submission

**Stellar ERP - the third ledger.**

[Live demo](https://stellar-erp-sigma.vercel.app) · [Repository](https://github.com/Madhur-Prakash/Stellar-ERP) · [Readme](README.md) · [Proof ledger](docs/attestation.md) · [Screenshots](docs/screenshots.md) · [Commands](docs/commands.md) · [Demo script](docs/demo-video.md)

</div>

---

## Checklist

| # | Required | Status | Where |
| --- | --- | --- | --- |
| 1 | Public GitHub repository | Done | [Madhur-Prakash/Stellar-ERP](https://github.com/Madhur-Prakash/Stellar-ERP) |
| 2 | README with complete documentation | Done | [README.md](README.md) + [fourteen documents](docs/README.md) |
| 3 | Minimum 15+ meaningful commits | Done - **34** | `git log --oneline \| wc -l` |
| 4 | Live demo link | Done | **[stellar-erp-sigma.vercel.app](https://stellar-erp-sigma.vercel.app)** - the [verifier](https://stellar-erp-sigma.vercel.app/verify) runs there with no backend. See [Deploying the demo](#deploying-the-demo) |
| 5 | Contract deployment address | Done | [`CCB66KMN…S5YR`](https://stellar.expert/explorer/testnet/contract/CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR) |
| 6 | Screenshots: product UI, mobile responsive, analytics/monitoring | Done - **21** | All three categories covered, plus the verifier passing **and failing**. [docs/screenshots.md](docs/screenshots.md); four in the [README](README.md#screenshots) |
| 7 | Demo video link | Done | Uploaded and embedded at the top of the [README](README.md) - see [Demo video](#demo-video) |
| 8 | Proof of 10+ user wallet interactions | **In progress - 8 of 10** | 6 registrations + 2 confirmed seals, each resolving on a public explorer. `make evidence` - see [Wallet interactions](#wallet-interactions) |
| 9 | Basic user feedback summary | **Outstanding - seeded only** | 12 submissions exist, but `seed_demo.py` wrote all 12. The widget is live; real submissions are not. See [docs/evidence.md](docs/evidence.md) |

**Seven of nine are done.** The two that are not both need other people, and neither can
be closed by writing anything into this repository:

- **Item 8 needs two more on-chain interactions.** Eight exist and every one resolves on
  a public explorer. Two more organizations switching sealing on would do it - the
  generator refuses to report ten until there are ten, so this cannot be rounded up.
- **Item 9 needs real submissions.** All twelve rows in the feedback table were written
  by the seeder, and `docs/evidence.md` says so at the top of the page rather than
  letting the count speak for itself.

That distinction is the whole point of [docs/evidence.md](docs/evidence.md): it is
generated from the live database and the live ledger, it marks its own seeded rows, and
its generator **exits non-zero while the headline count is short** - so it cannot be
wired into CI and quietly pass while this file claims otherwise.

---

## What this is

An ERP that keeps **three** ledgers. The journal and the audit trail live in the
business's own PostgreSQL, as they always did. The third is a Soroban contract
holding cryptographic commitments to the first two.

The problem it solves is narrow and specific: a self-hosted ledger is trusted by
its owner and by nobody else, because anyone with the database password could
rewrite it and no bank, buyer or auditor could tell. Publishing the books would fix
that and destroy the business - margins, customers, supplier terms, salaries, and
in India the DPDP Act. So:

> Make a private ledger provably unaltered to a stranger who never sees anything
> inside it.

**Zero bytes of business data go on chain.** A 32-byte Merkle root, an entry count,
a control total, a window, and an opaque namespace. A counterparty is handed one
invoice plus about `log₂(n)` sibling hashes and checks it in their own browser,
against a public RPC, with no wallet and no account.

Full reasoning: [docs/attestation.md](docs/attestation.md).

---

## Contract

| | |
| --- | --- |
| Network | Stellar Testnet |
| Contract | [`CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR`](https://stellar.expert/explorer/testnet/contract/CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR) |
| Wasm hash | `2324b519f8a205a8cae31e1b8ebf3944be1bc5d1d6ec7028cdea3829f5e79246` |
| Source | [`contracts/proof_ledger`](contracts/README.md) - 15 KB, 8 functions, 28 adversarial tests |
| Record | [`contracts/deployments/testnet.json`](contracts/deployments/testnet.json) |

```bash
make contract-build    # must print that exact wasm hash, from this source
```

That is the claim worth checking first: the deployed bytes are the bytes in this
repository, and the toolchain is pinned so a reviewer can reproduce the hash rather
than take our word for it.

Deploying a fresh one is one command - `make contract-up` - which tests, builds,
funds a key, deploys, **reads the contract back off the network**, and writes every
setting into `.env`. See [Commands §5](docs/commands.md#5-the-proof-ledger-contract).

---

## Wallet interactions

Every organization that switches sealing on is given **its own funded Stellar
account** and registers its book on the contract. Every seal afterwards is a
transaction that account signs. So the proof is a list of `G…` addresses and
transaction hashes, each resolving on a public explorer.

```bash
make evidence            # writes docs/evidence.md from the live database + chain
```

The generator reads `adoption`, the feedback summary and the usage rollup, and
prints an explorer link beside every on-chain figure. It **exits non-zero while the
count is under ten**, so this cannot be wired into CI and quietly pass while the
submission claims otherwise.

A count we assert about ourselves is worth very little. A count that resolves to
transactions on a public ledger is worth something - which is the argument this
whole product makes about accounting, so it would be odd not to apply it here.

### Where it stands: 8 of 10

From the current [docs/evidence.md](docs/evidence.md):

| | |
| --- | --- |
| Organizations with a book on chain | **6** |
| Organizations that have actually sealed | **1** |
| Confirmed seals | **2** |
| **Signed on-chain interactions** | **8** |

Six `register` calls plus two confirmed `seal`s. The table deliberately separates
*has a book* from *has sealed*, because conflating them is exactly the flattering
arithmetic a submission should not contain.

### Getting the last two

Each organization contributes one `register` plus one transaction per seal, so **two
more seals by any organization closes it** - or one more organization switching sealing
on and sealing once. Every account is funded by Friendbot, so it costs nothing:

1. Register an organization, post two or three journal entries.
2. Open **Trust**, switch sealing on. That funds the account and registers the book -
   two on-chain transactions already.
3. Press **Seal now**. One more.
4. `make evidence` to regenerate the table.

---

## Screenshots

**Twenty-one, covering all three required categories.** Full gallery with what each shot
shows and why: **[docs/screenshots.md](docs/screenshots.md)**. Four are carried in the
[README](README.md#screenshots).

### The two that matter

| File | Shot | Why it is the evidence |
| --- | --- | --- |
| `verify.png` | `/verify`, signed **out**, a verified bundle | Everything else is a signed-in user looking at their own data, which proves nothing to a third party. Here a stranger checks the books and our servers are not in the path: *"Nothing is uploaded"*, a real 2-hash Merkle path folds, and the RPC endpoint is the reader's to change |
| `verify-tampered.png` | The same bundle, `total_debit` 100 → 1010 | Anything can render a green tick. It fails at **step 3, "Hash the document"** - `20bfba900ea9…` is not the `9a73ba8ca75c…` the bundle claims - and never reaches the network, because it does not need to |

### Checklist item 6, mapped

| Required | Covered by |
| --- | --- |
| **Product UI** | `product-ui.png` (Trust, two seals, unbroken chain), `dashboard.png`, `accounting.png`, `accounting-charts.png`, `audit-log.png`, `roles.png`, `feedback.png` |
| **Mobile responsive** | `mobile.png` (Trust, 360 × 740) plus six more viewports: dashboard, analytics, reconciliation, seal history, audit log, settings |
| **Analytics** | `analytics.png` - period against the same period last year, tiles reading *"no prior data to compare"* rather than a fabricated delta |
| **Monitoring** | `monitoring.png` - `GET /attestation/status` showing `days_unsealed` and `chain.agrees_with_local`, plus `/health/ready`. `monitoring-chain-check.png` is the same reconciliation from the UI. Sentry is wired but off by default, [deliberately](backend/app/core/monitoring.py) |

Three shots carry limitations that a more flattering capture would have cropped: the
amber signing-key banner on Trust, the *"It does not mean"* card on the verifier, and
the honest *"no prior data to compare"* on analytics. That is deliberate - see
[Rules](docs/screenshots.md#rules).

**One caveat, stated rather than hidden:** the mobile shots were taken in Chrome's device
toolbar with DevTools docked, so the panel takes up most of each frame and the phone
viewport is a strip on the left. The emulated dimensions are legible, which is the
evidence that matters, but they would look better cropped to the viewport. The layout
needs nothing; only the framing does.

---

## Deploying the demo

**Web client - live.** [stellar-erp-sigma.vercel.app](https://stellar-erp-sigma.vercel.app),
on Vercel, built from [`frontend/`](frontend/). SPA rewrites are in
[`frontend/vercel.json`](frontend/vercel.json); the `VITE_STELLAR_*` values are set as
Vercel build environment variables.

**API - self-hosted on Ubuntu, deliberately not public.** An ERP holds a business's
ledger, its customers and its supplier terms, and a permanently exposed instance full
of real double-entry data is not something this project will publish. That is the
product's own argument applied to its own demo: the books stay private, only the proof
is public.

**This costs the demo less than it sounds like**, because the screen that matters does
not need the API. `/verify` re-encodes the entry, folds the Merkle path, and queries a
public Soroban RPC endpoint from the reader's browser - no call to our backend at any
point. It is fully functional on the hosted client, against the real testnet contract.
Signed-in screens are the part that needs a local `make up`.

If you are standing up your own public instance, **read
[docs/deployment.md](docs/deployment.md) first** - in particular the production
guardrails: `ENVIRONMENT=production` refuses to boot on a placeholder `SECRET_KEY`,
`DEBUG=true`, `CORS_ORIGINS=*`, an empty `ALLOWED_HOSTS`, or a default Postgres
password. That is deliberate - it crashes at boot rather than serving traffic with a
placeholder signing key - but it does mean a half-filled environment looks like a
failed deploy.

Three things that will otherwise cost an evening:

- **`ALLOWED_HOSTS` must contain the hostname you are served at**, or every API call
  answers `400 Invalid host header` while the platform's health probes keep reporting
  the service up. `RENDER_EXTERNAL_HOSTNAME`, if set, is folded in automatically.
- **`CORS_ORIGINS` must contain the web client's origin** - a browser-hosted frontend
  on one domain calling an API on another is cross-origin, and the guardrail refuses
  to let you paper over it with `*`.
- **`VITE_*` values are inlined at build time.** Rebuild and redeploy the web client
  after any change to the contract id, or the browser verifies against the old contract
  while the API uses the new one.

---

## Demo video

**Two deliverables, and they are not interchangeable.**

### 1. The launch video - built, rendered

**Published, and embedded at the top of the [README](README.md):**
<https://github.com/user-attachments/assets/937cacee-1728-4db6-bf98-d811abc2ab1e>

88s, 1920x1080, narrated, with a music bed. A HyperFrames project: ten HTML compositions
on a seek-safe timeline, rendered to MP4. The source is in `videos/stellar-erp-launch/`
and every frame is re-renderable; `renders/video-v1.mp4` is the first cut, kept for
comparison (same visuals, rougher narration, no music).

GitHub hosts the upload, so the README plays it inline with nothing to click through
and no third-party player. A local copy sits in `docs/videos/`, which is gitignored -
the published asset above is the canonical link, not the file.

Arc: your books balance and are worthless to your bank → two ledgers, one password →
keep them private or publish them, both fail → **the thesis** → the third ledger →
zero bytes on chain → a counterparty verifies → **one digit changes and it fails** →
what a seal does *not* prove → the real contract id.

Everything in it is typography and designed diagram. Nothing is deployed publicly, so
there was nothing to screenshot, and a mocked-up UI would be an invented artifact in a
video whose whole argument is that its claims are checkable. The contract id and wasm
hash are typeset verbatim instead, because those *are* checkable.

Music is `happy-beats-business-moves-vol-11` from the `/brag` skill's bundled assets,
sitting at 0.10 gain under the narration. **Check its licence before publishing** - the
skill's own README says the terms still need verifying, and this video is going on a
public submission.

One gap remains: **no captions**, because the local TTS engine returns no word timings
to sync to. The keep-out band is reserved, so they can be added later without relayout.

### 2. The screen recording - scripted, not shot

**[docs/demo-video.md](docs/demo-video.md)** - about three minutes, shot by shot, with
narration. This is the one that shows the *real* running product: the actual Trust
screen, the actual verifier, the actual explorer. The launch video reconstructs those
surfaces; only this one proves they exist.

For the submission, the launch video is the safer link and the screen recording is the
more convincing one. Recording it needs a screen recorder, which is yours to run.

---

## Feedback

The in-app widget is live on every screen and **works signed out**, which is the
part that matters: somebody who cannot get past the sign-in screen is exactly the
person whose report is worth having, and a form behind the sign-in would never hear
from them. It lands in `POST /feedback`.

`make evidence` summarises what has been submitted - counts by kind and status, and
the mean rating with its sample size attached, because a mean over three ratings is
noise and quoting it alone would be misleading.

---

## Engineering notes worth a reviewer's time

Each is explained where it lives, in the code.

| | |
| --- | --- |
| **An entry's status is not hashed** | This ledger corrects by reversal, so `posted → reversed` is the normal path. Hashing it meant reversing an entry invalidated its own proof - the subsystem accused a business of tampering for doing the right thing |
| **The chain is the authority on what has been sealed** | A submission whose outcome is unknown may still have landed. The contract enforces idempotency by sequence number and a reconciler corrects local state from `latest()` |
| **The canonical encoding exists twice** | Python and TypeScript, with a shared golden vector asserted on both sides in CI. A verifier who called our server for a verdict has gained nothing; the duplication is the cost of the answer being independent |
| **`seal` takes no timestamp** | It reads `env.ledger().timestamp()`. A caller-supplied time would make every claim here worthless |
| **Accounting does not import attestation** | The posting engine announces that an entry was posted and has no idea the proof ledger is listening. `ATTESTATION_ENABLED=false` removes the subsystem entirely |

### Quality gates

| Backend | Frontend | Desktop | Contract |
| --- | --- | --- | --- |
| `ruff` + `mypy --strict`, 132 modules | `eslint --max-warnings 0` | `dart format` | `cargo clippy -D warnings` |
| `pytest`, ~1,220 tests against real PostgreSQL + Redis | `tsc -b`, 42 tests | `flutter analyze`, 113 tests | `cargo test`, 28 adversarial |
| `alembic check` + a CHECK-constraint drift script | `vite build` | - | reproducible wasm hash |

The contract's tests are written adversarially: its value is what it **refuses**, so
most of them assert a panic.

---

<div align="center">

Forked from [Personal-ERP](https://github.com/Madhur-Prakash/Personal-ERP) · Built on [Stellar](https://stellar.org)

</div>
