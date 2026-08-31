<div align="center">

# Master specification

**What this is meant to be: product goals, modules, delivery model, non-negotiables.**

<!-- nav:start -->
[Docs](README.md) · **Spec** · [Architecture](architecture.md) · [Database](database.md) · [Accounting](accounting.md) · [Proof ledger](attestation.md) · [API](api.md) · [Security](security.md) · [Audit](security-audit.md) · [Commands](commands.md) · [Screenshots](screenshots.md) · [Demo video](demo-video.md) · [Evidence](evidence.md) · [Development](development.md) · [Deployment](deployment.md)
<!-- nav:end -->

</div>

---

The requirements document for this project. It lived in a chat prompt for the first
two modules; it lives here now so it is versioned alongside the code it describes.

---

## Product

A **self-hosted ERP for small businesses**. Simple to run, yours to keep.

Small businesses get offered two bad options: cloud SaaS that rents you your own
books and raises the price once you depend on it, or legacy desktop software that
lives on one machine and dies with its hard drive. This is the third option - one
server, your own PostgreSQL, no vendor between you and your accounts.

**And sovereignty has a cost the brochure never mentions.** Books nobody but you can
see are books nobody else can rely on: a bank underwriting a credit line, a corporate
buyer running supplier diligence, an insurer, an investor. Self-hosting removes the
vendor and takes the third-party credibility with it. Closing that gap without
publishing the books is what [the third ledger](attestation.md) is for, and it is the
one requirement in this document that could not be met by writing more application
code.

The design constraint is **restraint**. Deliberately not an enterprise-scale
platform: no Kubernetes, no broker cluster, no service mesh. One
`docker compose up` that a single person can operate. Everything is modular, so a
component that a business genuinely outgrows can be scaled or swapped without
rewriting the rest.

---

## Delivery model

**Build modules in parallel.** A module ships when it is green, not when the
previous one has signed off. There is no stage gate.

What is *not* negotiable, because it is arithmetic rather than process:

- **The dependency graph.** Sales and purchasing post into the ledger, so the
  ledger's posting contract must exist before they can call it. It does now -
  `PostingService.post_simple()` - and it is stable.

  Document intelligence is the exception, and deliberately so: **it never posts.**
  Extraction produces a suggestion a human confirms, and confirming calls
  `BillService.create()` - the same entry point `POST /bills` uses - so every rule
  that protects a hand-entered bill protects a scanned one. A second posting path
  for machine-read figures would eventually diverge from the real one, and the
  divergence would be in the code that writes to the ledger.
- **The quality bar.** Every module: strict mypy, ruff clean, tests against real
  PostgreSQL and Redis, reversible migration, documented rationale. A module that
  does not meet this is not done, regardless of how much of it exists.

The rationale for the second point is empirical rather than theoretical. Two
defects in the ledger were found only by running it - a `RequestContext` type
mismatch that would have crashed every write endpoint, and an enum-storage bug
that had silently disabled a Stage 1 unique index since it was written. Neither
was visible by reading the code. Parallel build-out multiplies throughput; it does
not remove the need to actually execute what you wrote.

---

## Modules

Ordered by dependency, not by priority. **Built** means built and verified, not
merely started.

| Module | Depends on | Status |
| --- | --- | --- |
| Foundation - auth, orgs, RBAC, audit, design system, CI/CD | - | Built |
| Accounting - chart, journals, ledger, trial balance, P&L, balance sheet, cash flow | Foundation | Built |
| Billing - record money in and out with no customer or supplier; posts real double-entry | Accounting | Built |
| Sales - CRM, leads, quotations, sales orders, invoices, payments | Accounting | Built - PDF pending |
| Purchasing & inventory - suppliers, POs, goods receipt, warehouses, stock moves, barcodes | Accounting | Built |
| OCR & document intelligence - invoice extraction, per-field confidence, duplicate detection, review UI | Purchasing | Built |
| Analytics - dashboard figures, period comparison, trends, rankings, control-account reconciliation | Accounting | Built - report builder pending |
| AI assistant - conversational interface, RAG over business data, forecasting | Sales, Purchasing | Planned |
| Automation - workflow builder, triggers, scheduled jobs, approvals, messaging | Sales | Planned |
| Enterprise - API keys, webhooks, SSO, passkeys, compliance | Foundation | Planned |
| Production hardening - security review, monitoring, load testing, tuning | all | Monitoring and analytics built; load testing pending |
| **Proof ledger** - Soroban contract, canonical encoding, Merkle seals, seal worker, public verifier | Accounting | **Built** |
| Settlement - SEP-24/31 anchors, tokenised receivables, invoice financing | Proof ledger | **Gated** |

Every remaining module is unblocked **except settlement**, which is blocked twice
over: technically on the proof ledger, and administratively on Stellar Builder Team
approval. That second block is the reason it is marked *gated* rather than *planned* -
nothing in this document is contingent on it, and no claim made above needs it. They
are still built one at a time, because each edits the same shared files
(`db/registry.py`, `api/v1/router.py`, `audit/models.py`) and Alembic migrations form
a linear chain - two generated from the same parent produce two heads and
`alembic upgrade head` fails.

### Why the dependency column matters

`Sales → Accounting` is not a preference. An invoice is not a record that
*resembles* a ledger entry; issuing one **is** two ledger postings (debit
receivables, credit revenue) plus a tax line. Building invoices against an
unsettled ledger means the invoice module encodes assumptions about a contract
that is still moving, and every one of those assumptions becomes a migration later.

The same applies to inventory: a goods receipt debits inventory and credits
payables, and a sale recognises COGS. Get the ledger contract wrong and both
modules inherit it.

---

## Non-negotiables

**Money is never a float.** `NUMERIC(18, 4)` → `Decimal`, and a JSON **string** on
the wire. A JSON number is a double in every JavaScript client.

**Posted accounting records are immutable.** Correction is by reversal. No code
path edits a posted entry.

**Multi-tenancy is enforced by the token, not the URL.** The active organization
comes from the signed access token. There is no org id in a path for a client to
tamper with.

**No account enumeration.** Reset, magic link, and OTP respond identically whether
or not the account exists, and login burns an equivalent Argon2 cycle on a miss so
timing cannot distinguish them either.

**No OAuth.** Email/password plus passwordless (magic link, email OTP) and TOTP
2FA. Deliberate: a self-hosted deployment should not require registering an app
with Google to let its own staff sign in.

**All backend logging goes through [logifyx](https://pypi.org/project/logifyx/).**
Nothing calls `logging.getLogger` or `print`. Ruff's `T20` enforces it, because a
stray `print` bypasses credential masking.

---

## Stack

**Frontend** - React 19, TypeScript, Vite, Tailwind, TanStack Router/Query/Table,
React Hook Form, Zod, Recharts, Lucide, Sonner.

**Backend** - FastAPI, Python 3.13 (uv), SQLAlchemy 2, Alembic, PostgreSQL 17,
Redis 7, Pydantic v2, logifyx.

**Billing** - no new dependencies and no new tables either, and for the same reason.
Entries post through `PostingService.create_entry` and are read back from the ledger,
tagged `source_type="billing"`. A table holding "the user's simple view" alongside the
journal entry that view describes is a cache that can disagree with the books, and this
codebase has already been bitten by a figure stored twice. Reconstruction is exact
rather than heuristic: every entry has precisely two lines, one on a cash-equivalent
account and one on an income or expense account, so direction and amount follow from
the shape with no guessing.

**Analytics** - no new dependencies and no new tables. The dashboard composes the
existing `ReportingService` rather than aggregating the ledger a second way: a tile
that disagrees with the P&L it summarises destroys trust in both, and nobody can tell
which is right. A materialised metrics table was considered and rejected for the same
reason - a cache that can disagree with the ledger is a liability, and at
small-business scale two `SUM`s over a few thousand journal lines cost nothing.

**Document intelligence** - pypdf (text layer) and Tesseract via pytesseract,
both in an optional `ocr` extra. This deviates from the original plan of "PaddleOCR
with EasyOCR/Tesseract fallback + OpenCV", and the reason is the product goal above:
PaddlePaddle is ~500 MB of wheels and torch is ~2 GB, which is incompatible with
"one person can run this on a small VPS". The order was also inverted - the PDF
*text layer* is tried before any OCR, because most invoices arrive as digital PDFs
whose characters are already in the file, and recognising a picture of text you
already have can only be worse. OpenCV was dropped: without it, preprocessing is
limited to grayscale and upscaling, and aggressive binarisation helps some scans
while destroying others, so there was nothing safe to add.

**Planned per module** - Celery + Redis Streams (automation),
Ollama/OpenAI-compatible + Sentence Transformers + Qdrant + LangGraph (AI).

**Infrastructure** - Docker Compose and GitHub Actions, deployed on your own server
behind a TLS terminator you already run. **No edge ships in this repository**: the one that
used to live in the production stack could not start, because its configuration was
never committed, and it was removed rather than rebuilt - the edge is one of the few
things every host already has an opinion about. Prometheus, Grafana, Loki and Sentry
remain planned rather than present.

No object store. Uploaded documents are compressed into PostgreSQL, so a single
`pg_dump` captures the ledger and the scans that support it at one consistent moment
and there is no second service whose volume can be forgotten. S3-compatible storage
remains an option for an install whose blobs outgrow that - see
[security.md](security.md#document-storage) and
`backend/app/modules/ocr/storage.py`.

Deferred deliberately: Kafka, Temporal, and Kubernetes. All three are in the
original wish-list and all three contradict "one person can operate this". Revisit
only when a real deployment's load demands it.

---

## Quality gates

Every module passes all of these before it is called done:

```bash
uv run ruff check app tests     # lint
uv run ruff format .            # format
uv run mypy app                 # typecheck (strict)
uv run pytest                   # tests, real PostgreSQL + Redis
uv run alembic check            # no schema drift
```

Plus, per module: a reversible migration (`downgrade` then `upgrade` must both
run), and a documented rationale in `docs/` for any decision where a common
alternative was rejected.

**These are local gates, not CI gates.** CI covers the frontend and the compose
files only - see [Development](development.md#before-opening-a-pull-request). The bar
is unchanged; what enforces it is discipline rather than a red check.

<!-- related:start -->

---

## Related reading

- [Architecture](architecture.md) - how these requirements are actually laid out in code
- [Accounting](accounting.md) - the ledger the non-negotiables demand
- [Development](development.md) - the quality gates every module has to pass

[All documentation](README.md)
<!-- related:end -->
