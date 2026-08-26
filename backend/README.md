<div align="center">

# Stellar ERP - backend

**FastAPI · PostgreSQL 17 · Redis 7 · Python 3.13, managed with [uv](https://docs.astral.sh/uv/).**

![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.118-009688?style=flat-square&logo=fastapi&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0_async-D71F00?style=flat-square&logo=sqlalchemy&logoColor=white)
![Alembic](https://img.shields.io/badge/Alembic-migrations-6E7681?style=flat-square)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?style=flat-square&logo=pydantic&logoColor=white)
![mypy](https://img.shields.io/badge/mypy-strict-1F5082?style=flat-square)
![ruff](https://img.shields.io/badge/ruff-lint_and_format-D7FF64?style=flat-square&logo=ruff&logoColor=black)

[Architecture](../docs/architecture.md) · [Database](../docs/database.md) · [Proof ledger](../docs/attestation.md) · [API](../docs/api.md) · [Commands](../docs/commands.md) · [Development](../docs/development.md) · [Security](../docs/security.md)

</div>

---

All logging goes through [logifyx](https://pypi.org/project/logifyx/) - see
[`app/core/logging.py`](app/core/logging.py). Nothing in this codebase calls
`logging.getLogger` or `print` directly, and ruff's `T20` rule is what keeps it that
way: a stray `print` bypasses credential masking.

---

## Layout

```
app/
  core/          Cross-cutting concerns - config, logging, security, errors, middleware
  db/            Declarative base, mixins, session/engine, model registry
  modules/       One vertical slice per bounded context
    <module>/
      models.py        SQLAlchemy tables
      schemas.py       Pydantic request/response contracts
      repository.py    Data access - the only layer that touches the session
      service.py       Business rules - transport-agnostic, raises domain errors
      router.py        HTTP surface - thin, delegates to the service
  api/v1/        Router aggregation and versioning
migrations/      Alembic
scripts/         One-off operational scripts, run with `uv run python`
tests/           pytest
```

The dependency rule points inward: `router → service → repository → models`. A
service never imports a router; a repository never raises HTTP errors. That is
what keeps modules independently testable and replaceable.

### The modules

| Tier | Modules |
| --- | --- |
| **Platform** | `auth`, `users`, `organizations`, `rbac`, `audit`, `notifications`, `health`, `feedback` |
| **Ledger** | `accounting` - every commercial module posts through it |
| **Commerce** | `billing`, `sales`, `purchasing`, `tax`, `ocr`, `analytics` |
| **Proof** | `attestation` - Ledger 3, on Stellar |

`accounting` is the one nothing may skip. An invoice is not a record resembling a
ledger entry; issuing one *is* two postings plus a tax line, and
`PostingService.post_simple` is the single contract they all call.

`attestation` is the one nothing *calls*. It observes: `PostingService` finishes by
announcing that an entry was posted through
[`accounting/hooks.py`](app/modules/accounting/hooks.py), and attestation subscribes
once, from `create_app`. Wiring it in directly would make the accounting core
undeployable without the blockchain subsystem, and `ATTESTATION_ENABLED=false` would
stop meaning anything. Its files:

| File | What it is |
| --- | --- |
| `canonical.py` | The **frozen** byte encoding of a journal entry. Changing it invalidates every proof already issued |
| `merkle.py` | RFC 6962 trees, inclusion proofs innermost-first |
| `stellar.py` | The contract client, and the three-way submit outcome: confirmed, rejected, **unknown** |
| `service.py` | Leaf recording, batching, submission, reconciliation, proof bundles |
| `worker.py` | The only thing that waits on consensus, and never in a request path |
| `hooks.py` | The subscriptions, installed from the composition root |

---

## Commands

```bash
uv sync                              # install (creates .venv)
uv run uvicorn app.main:app --reload # dev server → :8000
uv run alembic upgrade head          # apply migrations
uv run alembic revision --autogenerate -m "message"
uv run pytest                        # tests
uv run pytest --cov                  # with coverage
uv run ruff check . && uv run ruff format .
uv run mypy app
uv run python scripts/verify_proof.py bundle.json   # check a proof bundle
uv run python -m app.modules.attestation.worker     # the seal worker, standalone
```

Requires PostgreSQL and Redis. `docker compose up postgres redis` from the repo
root is the easiest way to get both; `make services` does the same thing.

> **On Windows, run these from PowerShell rather than Git Bash.** MSYS rewrites
> environment values that look like Unix paths, so `API_V1_PREFIX=/api/v1` reaches
> Python as `C:/Program Files/Git/api/v1` and the app dies at import. `make` targets
> are safe from either shell - the root Makefile switches that translation off.

### The tests need real infrastructure, on purpose

The suite runs against PostgreSQL and Redis rather than SQLite or mocks, because what
is worth testing here - partial unique indexes, JSONB behaviour, cursor pagination,
`SELECT … FOR UPDATE` under concurrency - is exactly what a substitute engine
implements differently. Each test runs in a transaction that is rolled back, so state
never leaks between them.

`ENVIRONMENT=test` refuses to start against a database whose name does not end in
`_test`, because the harness drops every table. That guard exists because it once did
not - see finding 1 in [the security audit](../docs/security-audit.md).

---

## Configuration

Every setting lives in [`app/core/config.py`](app/core/config.py) and is read
from the repo-root `.env` (copy `.env.sample`). Application code must not read
`os.environ` - import `get_settings()` instead, which is what makes configuration
testable and discoverable in one place.

`LOG_*` variables belong to logifyx and are documented in `.env.sample`.

Booting with `ENVIRONMENT=production` runs a set of guardrail assertions
(real `SECRET_KEY`, no wildcard CORS, `ENCRYPTION_KEY` present, non-default
database password) and **refuses to start** if any fail. Crashing at boot beats
serving traffic with a placeholder signing key.

---

## Optional extras

```bash
uv sync --extra ocr       # read scanned invoices
```

`pypdf` handles digital PDFs immediately. Images additionally need the Tesseract
binary, which pip cannot install - `GET /api/v1/documents/capabilities` reports what
the running server can actually read, so the UI can say so plainly instead of
offering an upload that fails. The heavyweight engines are deliberately absent;
[the root README](../README.md#optional-reading-scanned-invoices) explains why.
