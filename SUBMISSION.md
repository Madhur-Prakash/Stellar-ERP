<div align="center">

# Level 4 submission

**Stellar ERP — the third ledger.**

[Repository](https://github.com/Madhur-Prakash/Stellar-ERP) · [Readme](README.md) · [Proof ledger](docs/attestation.md) · [Commands](docs/commands.md) · [Demo script](docs/demo-video.md)

</div>

---

## Checklist

| # | Required | Status | Where |
| --- | --- | --- | --- |
| 1 | Public GitHub repository | Done | [Madhur-Prakash/Stellar-ERP](https://github.com/Madhur-Prakash/Stellar-ERP) |
| 2 | README with complete documentation | Done | [README.md](README.md) + [eleven documents](docs/README.md) |
| 3 | Minimum 15+ meaningful commits | Done — **24** | `git log --oneline \| wc -l` |
| 4 | Live demo link | **Outstanding** | see [Deploying the demo](#deploying-the-demo) |
| 5 | Contract deployment address | Done | [`CCB66KMN…S5YR`](https://stellar.expert/explorer/testnet/contract/CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR) |
| 6 | Screenshots: product UI, mobile responsive, analytics/monitoring | **Outstanding** | see [Screenshots](#screenshots) |
| 7 | Demo video link | **Rendered, needs uploading** | `videos/stellar-erp-launch/renders/video.mp4` — see [Demo video](#demo-video) |
| 8 | Proof of 10+ user wallet interactions | **In progress** | `make evidence` — see [Wallet interactions](#wallet-interactions) |
| 9 | Basic user feedback summary | **In progress** | `make evidence` — the widget is live, submissions are not |

Item 7 is rendered and sitting in the repo; it needs uploading and the link pasting
back here. Items 4 and 6 need a deployment and a browser. Items 8 and 9 need people
using it. **None of the remaining ones can be written into the repository honestly
without the underlying thing existing**, which is why they are listed as outstanding
rather than quietly filled in.

---

## What this is

An ERP that keeps **three** ledgers. The journal and the audit trail live in the
business's own PostgreSQL, as they always did. The third is a Soroban contract
holding cryptographic commitments to the first two.

The problem it solves is narrow and specific: a self-hosted ledger is trusted by
its owner and by nobody else, because anyone with the database password could
rewrite it and no bank, buyer or auditor could tell. Publishing the books would fix
that and destroy the business — margins, customers, supplier terms, salaries, and
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
| Source | [`contracts/proof_ledger`](contracts/README.md) — 15 KB, 8 functions, 28 adversarial tests |
| Record | [`contracts/deployments/testnet.json`](contracts/deployments/testnet.json) |

```bash
make contract-build    # must print that exact wasm hash, from this source
```

That is the claim worth checking first: the deployed bytes are the bytes in this
repository, and the toolchain is pinned so a reviewer can reproduce the hash rather
than take our word for it.

Deploying a fresh one is one command — `make contract-up` — which tests, builds,
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
transactions on a public ledger is worth something — which is the argument this
whole product makes about accounting, so it would be odd not to apply it here.

### Getting to ten

Each organization contributes one `register` plus one transaction per seal. Ten
interactions is therefore roughly **five organizations that each seal twice**, or
fewer organizations sealing more often. Every account is funded by Friendbot, so it
costs nothing:

1. Register an organization, post two or three journal entries.
2. Open **Trust**, switch sealing on. That funds the account and registers the book —
   two on-chain transactions already.
3. Press **Seal now**. One more.
4. Repeat with the next organization.

---

## Screenshots

Not yet captured. What each one has to show, and why that shot rather than another:

| File | Shot | Must show |
| --- | --- | --- |
| `docs/screenshots/product-ui.png` | Trust screen, signed in, sealing on | The backlog age tile, a confirmed seal with its explorer link, and the **"What it does not"** card. That card is the honest limitation, and a screenshot that crops it out is selling something the product does not do |
| `docs/screenshots/verify.png` | `/verify`, signed **out**, a verified bundle | The five-step verdict and the editable RPC field. The point is that the reader's own endpoint produced the answer |
| `docs/screenshots/mobile.png` | 390 × 844, the Trust screen | Nothing clipped, no horizontal scroll, the settings controls stacked rather than side by side |
| `docs/screenshots/analytics.png` | Analytics dashboard | Real figures with a like-for-like comparison |
| `docs/screenshots/monitoring.png` | `GET /attestation/status` or the Sentry project | `days_unsealed` and `chain.agrees_with_local` — the two figures that distinguish sealing working from sealing having silently stopped |

Capture on a **light** theme at **1440 × 900** for the desktop shots, then drop them
in `docs/screenshots/` and link them from the README.

---

## Deploying the demo

`RENDER_EXTERNAL_HOSTNAME` in `.env` points at `stellar-erp.onrender.com`, so a
Render deployment is the intended target.

**Read [docs/deployment.md](docs/deployment.md) first**, and in particular the
production guardrails: `ENVIRONMENT=production` refuses to boot on a placeholder
`SECRET_KEY`, `DEBUG=true`, `CORS_ORIGINS=*`, an empty `ALLOWED_HOSTS`, or a default
Postgres password. That is deliberate — it crashes at boot rather than serving
traffic with a placeholder signing key — but it does mean a half-filled environment
looks like a failed deploy.

Two things that will otherwise cost an evening:

- **`ALLOWED_HOSTS` must contain the hostname you are served at**, or every API call
  answers `400 Invalid host header` while the platform's health probes keep
  reporting the service up. `RENDER_EXTERNAL_HOSTNAME` is folded in automatically.
- **`VITE_*` values are inlined at build time.** Rebuild the web client after any
  change to the contract id, or the browser verifies against the old contract while
  the API uses the new one.

Once it is up, put the URL in the README and in the table at the top of this file.

---

## Demo video

**Two deliverables, and they are not interchangeable.**

### 1. The launch video — built, rendered

`videos/stellar-erp-launch/renders/video-v2.mp4` — 88s, 1920x1080, narrated, with a
music bed. (`video-v1.mp4` is the first cut, kept for comparison: same visuals, rougher
narration, no music.) A
HyperFrames project: ten HTML compositions on a seek-safe timeline, rendered to MP4.
The source is in `videos/stellar-erp-launch/` and every frame is re-renderable.

Arc: your books balance and are worthless to your bank → two ledgers, one password →
keep them private or publish them, both fail → **the thesis** → the third ledger →
zero bytes on chain → a counterparty verifies → **one digit changes and it fails** →
what a seal does *not* prove → the real contract id.

Everything in it is typography and designed diagram. Nothing is deployed publicly, so
there was nothing to screenshot, and a mocked-up UI would be an invented artifact in a
video whose whole argument is that its claims are checkable. The contract id and wasm
hash are typeset verbatim instead, because those *are* checkable.

Music is `happy-beats-business-moves-vol-11` from the `/brag` skill's bundled assets,
sitting at 0.10 gain under the narration. **Check its licence before publishing** — the
skill's own README says the terms still need verifying, and this video is going on a
public submission.

One gap remains: **no captions**, because the local TTS engine returns no word timings
to sync to. The keep-out band is reserved, so they can be added later without relayout.

### 2. The screen recording — scripted, not shot

**[docs/demo-video.md](docs/demo-video.md)** — about three minutes, shot by shot, with
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

`make evidence` summarises what has been submitted — counts by kind and status, and
the mean rating with its sample size attached, because a mean over three ratings is
noise and quoting it alone would be misleading.

---

## Engineering notes worth a reviewer's time

Each is explained where it lives, in the code.

| | |
| --- | --- |
| **An entry's status is not hashed** | This ledger corrects by reversal, so `posted → reversed` is the normal path. Hashing it meant reversing an entry invalidated its own proof — the subsystem accused a business of tampering for doing the right thing |
| **The chain is the authority on what has been sealed** | A submission whose outcome is unknown may still have landed. The contract enforces idempotency by sequence number and a reconciler corrects local state from `latest()` |
| **The canonical encoding exists twice** | Python and TypeScript, with a shared golden vector asserted on both sides in CI. A verifier who called our server for a verdict has gained nothing; the duplication is the cost of the answer being independent |
| **`seal` takes no timestamp** | It reads `env.ledger().timestamp()`. A caller-supplied time would make every claim here worthless |
| **Accounting does not import attestation** | The posting engine announces that an entry was posted and has no idea the proof ledger is listening. `ATTESTATION_ENABLED=false` removes the subsystem entirely |

### Quality gates

| Backend | Frontend | Desktop | Contract |
| --- | --- | --- | --- |
| `ruff` + `mypy --strict`, 132 modules | `eslint --max-warnings 0` | `dart format` | `cargo clippy -D warnings` |
| `pytest`, ~1,220 tests against real PostgreSQL + Redis | `tsc -b`, 42 tests | `flutter analyze`, 113 tests | `cargo test`, 28 adversarial |
| `alembic check` + a CHECK-constraint drift script | `vite build` | — | reproducible wasm hash |

The contract's tests are written adversarially: its value is what it **refuses**, so
most of them assert a panic.

---

<div align="center">

Forked from [Personal-ERP](https://github.com/Madhur-Prakash/Personal-ERP) · Built on [Stellar](https://stellar.org)

</div>
