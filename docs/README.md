<div align="center">

# Documentation

**Twelve documents covering what this system is, how it is built, and how to run it.**

[Spec](spec.md) · [Architecture](architecture.md) · [Database](database.md) · [Accounting](accounting.md) · [Proof ledger](attestation.md) · [API](api.md) · [Security](security.md) · [Audit](security-audit.md) · [Commands](commands.md) · [Demo video](demo-video.md) · [Development](development.md) · [Deployment](deployment.md)

</div>

---

Every one of them explains **why** as well as what. Where a common alternative was
rejected, the reason is written down - a decision whose rationale nobody remembers is a
decision that gets undone in the next refactor.

Every page carries the nav bar above, so you are never more than one click from any
other, and ends with a few suggestions for where to go next.

---

## Start here

Pick the row that matches what you are trying to do.

| I want to… | Read, in order |
| --- | --- |
| **Record the demo** | [Demo video](demo-video.md) - shot list, timings and narration |
| **Just run the thing** | [Commands](commands.md#2-first-time-setup) - every task as a `make` target and as raw commands |
| **Deploy the contract** | [Commands](commands.md#5-the-proof-ledger-contract) → [Proof ledger](attestation.md) |
| **See the deployed contract** | [Commands](commands.md#6-seeing-the-deployed-contract) - six ways, two of which trust us not at all |
| **Show that the records cannot be altered** | [Commands](commands.md#7-demonstrating-that-the-records-are-tamper-evident) |
| **Understand the third ledger** | [Proof ledger](attestation.md) → [`contracts/README.md`](../contracts/README.md) |
| **Verify a proof somebody sent me** | [Proof ledger](attestation.md#what-a-seal-proves--stated-precisely) - then open `/verify`. No account, no wallet |
| **Run it on my machine** | [Development](development.md) → [Architecture](architecture.md) |
| **Put it on a server** | [Deployment](deployment.md) → [Security](security.md) |
| **Understand the money model** | [Accounting](accounting.md) → [Database](database.md) |
| **Call the API** | [API](api.md) → [Security](security.md#authentication) |
| **Review the security posture** | [Security](security.md) → [Security audit](security-audit.md) |
| **Add a feature** | [Architecture](architecture.md#extending-it) → [Development](development.md) → [Database](database.md#migrations) |
| **Know what this is meant to be** | [Specification](spec.md) |

---

## The documents

| Document | What is in it |
| --- | --- |
| [**Specification**](spec.md) | The requirements: product goals, module breakdown, delivery model, and the non-negotiables everything else is measured against. |
| [**Architecture**](architecture.md) | Layering and the inward-pointing dependency rule, the request lifecycle end to end, module structure, and how the frontend is organised. |
| [**Database**](database.md) | Schema and entity relationships, naming conventions, the indexes and why each exists, migrations, transactions, and backups. |
| [**Accounting**](accounting.md) | The double-entry core: the invariants, why money is never a float, why entries are reversed rather than edited, numbering, and the fiscal calendar. |
| [**Proof ledger**](attestation.md) | The third ledger, on Stellar: what a seal proves and what it cannot, the frozen canonical encoding, the Merkle tree and selective disclosure, the ambiguous-failure problem, why Stellar rather than another chain, and the operational runbook. |
| [**API**](api.md) | The HTTP contract: authentication flows, the error envelope, endpoints, pagination, and rate limits. |
| [**Security**](security.md) | The threat model and every control, each with its rationale - network edge, authentication, sessions, authorization, input handling, secrets, and rate limiting. |
| [**Security audit**](security-audit.md) | A full review of the exposure surface: sixteen findings, each verified against the code, with the fix applied and how to confirm it. Several carry supersession notes where the system has since changed, and the limits the third ledger adds are stated there rather than left implied. |
| [**Commands**](commands.md) | Every task twice: as a `make` target and as the raw commands it runs. Prerequisites, first-time setup, running it, the database, deploying the contract, six ways to inspect the deployed contract, demonstrating tamper-evidence, quality gates, release builds, production, and a troubleshooting table. |
| [**Demo video**](demo-video.md) | A three-minute script built around one moment: a proof verifies, one digit changes, and it fails. Shot list, narration, and the mistakes that ruin a take. |
| [**Development**](development.md) | Local setup, backend and frontend conventions, testing, the pre-PR checklist, debugging, and the gotchas hit while building this. |
| [**Deployment**](deployment.md) | Self-hosting on a VPS: configuration, the proxy you have to supply, backups, updates, and a pre-flight checklist. |

---

## How they relate

```mermaid
graph TD
    spec[Specification<br/>what to build]
    arch[Architecture<br/>how it fits together]
    db[Database<br/>the schema]
    acct[Accounting<br/>the ledger rules]
    proof[Proof ledger<br/>the third ledger]
    api[API<br/>the HTTP contract]
    sec[Security<br/>the controls]
    audit[Security audit<br/>the review]
    dev[Development<br/>working on it]
    cmds[Commands<br/>how to run anything]
    deploy[Deployment<br/>running it]

    spec --> arch
    arch --> db
    arch --> api
    db --> acct
    acct --> proof
    acct --> api
    proof --> api
    api --> sec
    sec --> audit
    arch --> dev
    dev --> cmds
    cmds --> deploy
    sec --> deploy
```

Read top to bottom for the whole picture; jump straight to a box if you already know
where you are going.

---

## Elsewhere in the repository

| | |
| --- | --- |
| [Root README](../README.md) | What the product does, quick start, everyday commands, and the design decisions worth knowing before reading any of the above. |
| [`contracts/README.md`](../contracts/README.md) | The Soroban contract behind [Proof ledger](attestation.md) - its interface, its errors, why every invariant is enforced on chain rather than trusted, and the deployed instance with its reproducible wasm hash. |
| [`backend/README.md`](../backend/README.md) | The FastAPI service - layout, commands, configuration, optional extras. |
| [`frontend/README.md`](../frontend/README.md) | The React web client - structure, conventions, design tokens. |
| [`app_frontend/README.md`](../app_frontend/README.md) | The Flutter desktop client, and the four places a native window honestly differs from a browser. |
| [`installer/README.md`](../installer/README.md) | Packaging the Windows build with Inno Setup. |
| `/docs` (running app) | Interactive OpenAPI reference, generated from the code. Development only - it is disabled in production and returns 404 there, so [API](api.md) is the written contract. |
