<div align="center">

# Contributing

**How to report something, how to propose something, and what a mergeable change looks like.**

[Readme](README.md) · [Documentation](docs/README.md) · [Development](docs/development.md) · [Architecture](docs/architecture.md) · [Security](docs/security.md)

</div>

---

This is a single-maintainer project, so the honest version up front: **issues are read,
pull requests are reviewed, and neither is on a schedule.** Small, focused changes land
quickly. Large ones land only if the shape was agreed before the code was written.

---

## Before you open anything

| You want to | Do this |
| --- | --- |
| Report a bug | [Open a bug report](https://github.com/Madhur-Prakash/Personal-ERP/issues/new?labels=bug) |
| Propose a feature | [Open a feature request](https://github.com/Madhur-Prakash/Personal-ERP/issues/new?labels=enhancement) |
| Report a **security vulnerability** | **Not an issue.** Use GitHub's private vulnerability reporting - see [SECURITY.md](SECURITY.md) |
| Ask how something works | Check [docs/](docs/README.md) first - it is nine documents deep and indexed by task - then open a discussion or an issue |

**Search the existing issues first**, and check [Delivery status](README.md#delivery-status).
Stages 6, 7, 9 and 10 are *planned*, not missing - a request for the AI assistant or the
workflow builder is already on the roadmap rather than a gap nobody noticed.

---

## Reporting a bug

The report that gets fixed fastest contains:

- **What you ran** - the commit SHA (`git rev-parse --short HEAD`), and whether it was
  `make up`, host mode, or the desktop build
- **The smallest sequence that reproduces it** - the endpoint or screen, the inputs, and
  the result you expected instead
- **The `X-Request-ID` from the response**, if it was an API call. Every response carries
  one and the log is filterable by it:
  ```bash
  tail -f logs/personalerp.log | jq 'select(.request_id == "<id>")'
  ```
- **The relevant log lines.** Redaction is on by default, so pasted output should already
  be free of tokens - check anyway before posting

Money and ledger bugs get priority over everything else. If a figure is wrong, say which
figure, and what the documents behind it say it should be.

---

## Proposing a feature

The design constraint of this project is **restraint** - it is a self-hosted ERP that one
person can run, not an enterprise platform. A proposal is judged against that first:

- **Does it work with one `docker compose up`?** Anything requiring a broker cluster, a
  service mesh, or a second datastore starts from behind
- **Does it belong to a stage that exists?** New surface inside a *planned* stage is better
  raised as a design comment than built ahead of the stage
- **Does it hold the accounting invariants?** Entries are immutable and corrected only by
  reversal, periods lock, numbering is gap-free under concurrency. A feature that needs an
  exception to any of those needs a different design, not an exception

Say what problem it solves for a real business before saying what to build. The second part
is easier to get right once the first is written down.

---

## Setting up to work on it

Requires Docker, [uv](https://docs.astral.sh/uv/), Node 22, and Flutter for the desktop
client.

```bash
make setup     # .env, dependencies, services, migrations
make up        # everything in Docker
make help      # every task
```

[Development](docs/development.md) is the full guide - host mode with a working debugger,
how email works locally (codes are readable straight out of the log), and the conventions
below in detail.

---

## The conventions that are not style preferences

These are enforced by tests, lint, or hard experience. [Development](docs/development.md)
explains why each exists; the short list is what a review will actually catch:

**Backend**

- Dependencies point inward: `router` → `service` → `repository`. A router that touches the
  session, or a service that raises an `HTTPException`, is the one thing to catch in review
- **Never `commit()` in a service** - `get_db` owns the transaction boundary. Use `flush()`
- **Never read `os.environ`** - import `get_settings()`
- **Never `logging.getLogger` or `print`** - use `app.core.logging.get_logger(__name__)`,
  which is where redaction and request context come from
- **Register new models in `db/registry.py` in the same commit**, or autogenerate produces
  an empty migration and silently omits the table
- Separate request and response schemas. A response schema reused as a request schema is
  how `is_superuser` becomes mass-assignable
- New permission? Add it to the `Permission` enum, put it in a `PermissionGroup`, grant it
  in `SYSTEM_ROLE_PERMISSIONS`, then enforce it with `require_permission`. A test asserts
  every permission belongs to exactly one group

**Frontend**

- Server state in TanStack Query, client state in React state. There is no global store
- **Never store a token in `localStorage`** - the access token lives in memory, the refresh
  token is an HttpOnly cookie
- No hard-coded API paths in components; they go in the feature's `api.ts`
- Semantic colour tokens (`bg-surface`), never literals (`bg-zinc-900`) - dark mode is one
  set of variable overrides
- A navigation control is a `<Link>` with `buttonClasses()`, not a `<button>`
- Mutations never retry. A retried POST can duplicate an invoice

**Migrations**

Reversible and drift-free. `upgrade → downgrade → upgrade` has to run clean, and
`alembic check` has to report no drift.

---

## Tests

New behaviour comes with a test. The suite is weighted toward where a bug is expensive
rather than toward a coverage percentage:

- Token rotation and reuse detection
- Permission expansion
- Cross-tenant isolation
- Owner-lockout prevention
- Ledger balance under concurrency
- Secret redaction

```bash
make test              # backend, needs PostgreSQL + Redis
make test-cov          # with coverage
cd backend && uv run pytest -k "two_factor" -v
```

Tests run against **real PostgreSQL 17 and Redis 7**, not substitutes - partial unique
indexes, JSONB, cursor pagination and `SELECT … FOR UPDATE` are exactly what a substitute
engine implements differently. Use `example.com` for test emails; `.test` and `.local` are
rejected by `email-validator`.

For anything cross-tenant, **attempt the bad thing and assert it is refused.** A test that
only proves the happy path proves the least interesting half.

---

## Before opening a pull request

```bash
make check      # lint + typecheck + test, every surface
make db-check   # no migration drift
```

**Run both, because CI will not.** [`ci.yml`](.github/workflows/ci.yml) has two jobs -
Frontend (`tsc -b`, `eslint`, `prettier --check`, `vite build`) and Compose config. The
There is no backend job, so `ruff`, `mypy --strict`, `pytest` and `alembic check` block
nothing on their own. A red backend merges unless you catch it here.

---

## The pull request itself

- **Branch from `main`.** There are no release branches - `main` is the only supported
  version, and self-hosted installs update by pulling it
- **One concern per PR.** A bug fix plus a refactor plus a rename is three reviews wearing
  one hat, and the review that matters gets the least attention
- **Conventional-style subject** - `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`
- **Say what changed and why.** The *why* is the part that cannot be reconstructed from the
  diff a year later. Link the issue it closes
- **Update the docs in the same commit** if behaviour changed. A doc that describes last
  month's behaviour is worse than no doc
- **Screenshots for UI changes**, in both light and dark

A PR that changes the shape of something - a new module, a new dependency, a change to the
posting contract or the token model - is worth raising as an issue first. It is a much
cheaper conversation before the code exists.

---

## Licence

Contributions are made under the [MIT Licence](LICENSE), the same terms the project ships
under. By opening a pull request you agree your work is distributed on those terms.

---

<div align="center">

**Thanks for taking the time.** A good bug report is a contribution too.

</div>
