<div align="center">

# Development

**Local setup, the conventions that are not style preferences, testing, and the gotchas.**

<!-- nav:start -->
[Docs](README.md) · [Spec](spec.md) · [Architecture](architecture.md) · [Database](database.md) · [Accounting](accounting.md) · [Proof ledger](attestation.md) · [API](api.md) · [Security](security.md) · [Audit](security-audit.md) · **Development** · [Deployment](deployment.md)
<!-- nav:end -->

</div>

---

## Setup

Requires Docker, [uv](https://docs.astral.sh/uv/), and Node 24 - the version CI and
the frontend image both use.

```bash
make setup     # .env, dependencies, services, migrations
make up        # everything in Docker
```

Or run the app on the host with only the data services in containers, which gives
faster reloads and a working debugger:

```bash
make services       # PostgreSQL, Redis
make dev-api        # terminal 1
make dev-web        # terminal 2
```

When running on the host, point `.env` at `localhost`:

```env
POSTGRES_HOST=localhost
REDIS_HOST=localhost
```

`make help` lists every task.

### Working on the contract

Only needed if you are changing Rust. The application runs against an already-deployed
contract, so a full `make setup` needs neither Rust nor the Stellar CLI.

Requires Rust (the version is pinned in
[`contracts/rust-toolchain.toml`](../contracts/rust-toolchain.toml)) and the
[Stellar CLI](https://developers.stellar.org/docs/tools/developer-tools).

```bash
make contract-test      # 28 adversarial tests, native, no network
make contract-lint      # clippy -D warnings, plus a format check
make contract-build     # wasm32v1-none, ~15 KB
make contract-key       # generate and fund a testnet deployer
make contract-deploy    # prints the id for SOROBAN_CONTRACT_ID
```

**The toolchain is pinned, and that is not tidiness.** A Soroban deployment is
addressed by the hash of its wasm. "Builds with whatever rustc is installed" means the
published hash cannot be reproduced, and a reviewer cannot confirm that the code they
read is the code that is running. `make contract-build` on this source must produce
`2324b519f8a205a8cae31e1b8ebf3944be1bc5d1d6ec7028cdea3829f5e79246`.

### Running the seal worker

In `make up` and `make dev-api` the worker runs inside the API process as a lifespan
task, governed by `SEAL_WORKER_ENABLED`. To watch it on its own:

```bash
make seal-worker
```

Useful when the interesting question is *why nothing sealed*, because the worker's
decisions are then the only thing in the log.

---

## Email in development

**There is one transport: the Gmail API.** No SMTP, and no local mail catcher -
a second path is a second way for the same email to render, and the one nobody
exercises is the one that breaks. See
`backend/app/modules/notifications/email.py`.

**With `GMAIL_CREDENTIALS_B64` unset (the default)** the mailer writes the message
body to the log instead of sending it, so a fresh checkout and the test suite work
with no credentials.

**Emailed codes are readable straight out of the log** - the sign-in code and the
password-reset code are 6 digits in the body, not URL parameters, so nothing masks
them. Request one, read it from the console, type it in.

**Signing in from the desktop app** does not need the link to be opened *in* the app.
The app sends it, shows a four-character code, and polls; opening the link in any
browser signs **only the app** in - the browser gets a "your app is signing in" page,
because the client that requested the link is the one that gets the session. To
exercise it locally, request the link in the app, read it out of the log (see below),
open it in a browser, and watch the app come to life on its own.

**Links are not.** Verification and magic-link mails carry a `token=` parameter, and
**logifyx's masking redacts it from the logged URL**:

```
http://localhost:5173/magic-link/verify?****
```

That redaction is correct - a one-time credential should not sit in a log file -
so to click a link locally, set `LOG_MASK=false` and read it out of the log.

**With a real token configured**, mail goes to the actual recipient's inbox. Point
the flows at an address you own; there is no local inbox to intercept them.

---

## Getting a real Gmail token

`GMAIL_CREDENTIALS_B64` is **base64 of a pickled `Credentials` object** carrying the
`gmail.send` scope. It is not an API key, and it is not the `credentials.json` you
download from Google - that file is only the input. Producing it is a one-time chore.

### 1. Get `credentials.json` from Google

1. [Google Cloud Console](https://console.cloud.google.com/) - a project, new or existing
2. **APIs & Services → Library → Gmail API → Enable**
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**, application
   type **Desktop app**
4. Download the JSON. That file is your `credentials.json`

**A service-account key will not work.** Sending as a user needs that user's consent, or
domain-wide delegation this transport does not implement.

**Publish the consent screen** - *Google Auth Platform → Audience → Publish app*. While it
sits in **Testing**, Google expires every refresh token it issues after **seven days**, and
sending starts failing with `invalid_grant` a week after it last worked. Publishing first
turns a weekly chore into a one-time one.

### 2. Mint the token

The script runs the consent flow and prints the finished `.env` line:

```bash
cd backend
uv sync --group dev     # google-auth-oauthlib is a dev dependency - only this script needs it
uv run python scripts/mint_gmail_token.py path/to/credentials.json
```

A browser opens. **Consent as the mailbox that should send the mail** - whatever account
signs in is the `From:` on every email the deployment sends, and it has to match
`GMAIL_SENDER` if that is set. What comes back is one line:

```
GMAIL_CREDENTIALS_B64=gASVvQMAAAAAAACMHmdvb2dsZS5vYXV0aDIuY3JlZGVudGlhbHOU...
```

### By hand, if you would rather see the moving parts

The script is exactly the OAuth flow plus `base64(pickle.dumps(creds))`. Written out, it is
these two snippets - run them from `backend/`, next to `credentials.json`:

**Mint `token.pickle`:**

```python
import pickle
import os
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

# Sending, and nothing else. A token scoped this narrowly cannot read the mailbox it
# sends from, so a leak costs spam rather than the contents of the inbox.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

creds = None
# Reuse an existing token if one is already lying around
if os.path.exists("token.pickle"):
    with open("token.pickle", "rb") as token:
        creds = pickle.load(token)
# If credentials are missing or invalid, get new ones
if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        # Both arguments matter - see the note below
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    # Save the credentials for future use
    with open("token.pickle", "wb") as token:
        pickle.dump(creds, token)
print("token.pickle created successfully")
```

**Turn it into the environment value:**

```python
import base64

data = open("token.pickle", "rb").read()
print(base64.b64encode(data).decode())
```

Paste that output as `GMAIL_CREDENTIALS_B64` - in `.env` locally, or as an environment
variable in whatever hosts the backend.

> **`access_type="offline"` and `prompt="consent"` are not optional.** Without both,
> re-consenting for a client Google has already authorised returns an access token with
> **no refresh token attached**. It sends mail for about an hour and then stops for good,
> and nothing in the response says so. `mint_gmail_token.py` passes them and then checks
> `credentials.refresh_token` before printing anything, which is the one thing this
> shorter version does not do for you.

### 3. Set it, and restart

```env
GMAIL_CREDENTIALS_B64=<the base64 line>
GMAIL_SENDER=you@yourdomain.com
EMAIL_FROM_NAME=Stellar ERP
```

**Restart the backend.** Settings are read once at import, so a running server keeps using
the old value - including "unset", which means it carries on logging emails instead of
sending them.

**Neither file belongs in a commit.** `credentials.json` and `token.pickle` are both in
[`.gitignore`](../.gitignore) and `backend/.dockerignore`; delete them once the base64 is
in your secret store, since the env var is the only copy anything reads.

**If sending fails with `invalid_grant`**, Google has rejected the refresh token itself -
nothing is misconfigured and there is no fix but a new token. Re-run the script, and
publish the consent screen if you have not.

---

## Backend conventions

### Module layout

```
modules/<name>/
  models.py        SQLAlchemy tables
  schemas.py       Pydantic contracts
  repository.py    Data access - the only layer touching the session
  service.py       Business rules - raises domain exceptions, never HTTP ones
  router.py        HTTP - thin; parse, delegate, shape
```

Dependencies point inward. Violating that is the one thing to catch in review.

### Rules that are not negotiable

**Never call `commit()` in a service.** The request-scoped transaction in
`get_db` owns that boundary. Use `flush()` to get a primary key or to surface a
constraint violation early.

**Never read `os.environ`.** Import `get_settings()`. One file holds every knob,
which is what makes configuration testable and discoverable.

**Never use `logging.getLogger` or `print`.** Use
`app.core.logging.get_logger(__name__)`. All logging goes through logifyx, which is
where redaction and request context come from.

**Register new models in `db/registry.py`, in the same commit.** Alembic
autogenerate only sees imported models; forgetting this produces an empty
migration and silently omits the table.

**Separate request and response schemas.** A response schema reused as a request
schema is how `is_superuser` becomes mass-assignable.

**Eager-load relationships you will serialise.** Async SQLAlchemy raises
`MissingGreenlet` on a lazy load outside the greenlet context. Use
`selectinload`, or assign the related object at construction:

```python
# Wrong: `.role` is not loaded, and reading it in the response schema raises.
Invitation(role_id=role.id, ...)

# Right: the relationship is populated with no extra query.
Invitation(role=role, ...)
```

This is a real bug we hit and fixed - see the comment in
`organizations/service.py`.

### Adding a permission

1. Add it to the `Permission` enum in `rbac/permissions.py`.
2. Add it to a `PermissionGroup`. A test asserts every permission belongs to
   exactly one group, so a forgotten entry fails the suite.
3. Grant it to the relevant `SYSTEM_ROLE_PERMISSIONS` entries.
4. Enforce it: `Depends(require_permission(Permission.YOUR_THING))`.

Because permissions live in code, one that is missing from the catalogue cannot be
granted through the UI at all - which is the point.

---

## Frontend conventions

### Structure

```
src/
  components/ui/       Primitives - no data fetching
  components/layout/   Shell, palette, theme toggle
  features/<name>/     api.ts + page components, colocated
  lib/                 HTTP client, env validation, formatting
  routes/              Router tree
  types/api.ts         Mirrors of the backend contracts
```

### Rules

**Server state goes in TanStack Query. Client state goes in React state.** There
is no global store; nothing in Stage 1 needs one.

**Never store a token in `localStorage`.** The HTTP client holds the access token
in memory; the refresh token is an HttpOnly cookie. Both are deliberate.

**Never hard-code an API path in a component.** Add it to the feature's `api.ts`,
so a route rename touches one file and a typo is a compile error.

**Use semantic colour tokens.** `bg-surface`, not `bg-zinc-900`. Dark mode is one
set of variable overrides, and literal colours break it.

**A navigation control must be a link, not a button.** Use `buttonClasses()` on a
`<Link>`. A `<Link>` inside a `<button>` is invalid HTML, and a `<button>` that
navigates loses middle-click and "open in new tab".

**Mutations never retry.** Configured globally. A retried POST can duplicate an
invoice.

### Type-aware linting is on

`no-floating-promises` and `no-misused-promises` catch the class of bug TypeScript
alone misses. `void promise` is the explicit opt-out:

```tsx
onClick={() => void save()}
```

---

## Testing

```bash
make test              # backend, needs PostgreSQL + Redis
make test-cov          # with coverage
cd backend && uv run pytest tests/test_auth_api.py -q
cd backend && uv run pytest -k "two_factor" -v
```

### How isolation works

Each test runs in a transaction that is always rolled back, with
`join_transaction_mode="create_savepoint"` so the application's own `commit()`
calls become savepoint releases. Production transaction boundaries run for real;
the outer rollback erases everything.

Redis gets database index 15, flushed around every test. Auth state lives there,
so leakage would make tests order-dependent.

Argon2 is dialled to its minimum in tests. At production parameters, hashing
dominates the runtime of an auth-heavy suite.

### The frontend and the contract have their own suites

```bash
cd frontend && npm test          # Vitest - 42 tests, all of them the canonical encoding
make contract-test               # Rust - 28 tests, all of them adversarial
```

**`frontend/src/features/trust/canonical.test.ts` is the one to know about.** It pins
the TypeScript encoding against a golden vector asserted in Python by
`backend/tests/test_attestation_canonical.py`. The two implementations exist
separately on purpose - a verifier who called our server for a verdict has gained
nothing - and this test is the only thing stopping them drifting. **If it fails, do
not update the golden vector.** A changed vector means every proof already handed to a
counterparty now reads as tampering. Change the encoding version instead.

### What to test

The suite is weighted toward places where a bug is expensive:

- token rotation and reuse detection
- permission expansion, including wildcards and unknown grants
- cross-tenant isolation - *attempt* the bad thing and assert it is refused
- owner-lockout prevention
- secret redaction in the audit trail
- account-enumeration resistance, including timing
- the proof ledger's ambiguous failure - a submission that lands *and* times out,
  which is why it is tested against a fake chain rather than testnet

Use `example.com` for test emails. `email-validator` rejects special-use TLDs like
`.test` and `.local` - correct behaviour for production, and it means those
domains cannot be used in fixtures.

---

## Before opening a pull request

```bash
make check      # lint + typecheck + test, every surface
make db-check   # no migration drift
```

**Run both, because CI will not.** [`ci.yml`](../.github/workflows/ci.yml) has two
jobs - Frontend (`tsc -b`, `eslint`, `prettier --check`, `vite build`) and Compose
config. There is no backend job, so ruff, mypy, pytest, and `alembic check` block
nothing on their own. A red backend merges unless you catch it here.

---

## Debugging

**A request, end to end.** Every response carries `X-Request-ID`. Filter the log
by it:

```bash
tail -f logs/stellarerp.log | jq 'select(.request_id == "<id>")'
```

Audit rows store the same id, so a business event pivots to its log lines.

**SQL.** Set `DB_ECHO=true`. Off by default because it is deafening.

**Inspect state.**

```bash
make psql
make redis-cli
docker exec stellarerp-redis redis-cli -n 0 KEYS 'stellarerp:*'
```

**A user seems stuck signed out.** Check their token epoch - anything that bumps
it invalidates outstanding tokens:

```bash
docker exec stellarerp-redis redis-cli GET "stellarerp:auth:epoch:<user_id>"
```

---

## Gotchas we hit building this

Recorded because each cost real time:

- **`model_validate(obj, update={...})` does not exist.** Pydantic has no `update`
  parameter. Use `with_computed()` in `core/schemas.py`, which validates then
  overlays.
- **`INET` columns return `IPv4Address`, not `str`.** Use the `IpAddress` type from
  `core/schemas.py` at the serialisation boundary.
- **A model method named like a schema field breaks validation.** `from_attributes`
  reads the bound method, not its result. `Invitation.is_expired` is a property for
  exactly this reason.
- **A `lazy="raise"` relationship colliding with a schema field name** trips the
  N+1 guard on every response. `AuditLogRead.from_row` builds explicitly instead.
- **pydantic-settings JSON-decodes list fields before validators run.** `NoDecode`
  is required for comma-separated env vars.
- **TanStack Router requires `search` on `<Link>`** unless `validateSearch` returns
  *optional* properties. `{ redirect?: string }`, not
  `{ redirect: string | undefined }`.
- **Type-aware ESLint rules must be scoped to `**/*.ts{,x}`.** Spreading them
  globally makes ESLint try to type-check its own config file and fail to load.
- **logifyx's formatters drop `extra={...}` silently.** Both the console and JSON
  formatters build output from a fixed set of record attributes, so structured
  fields vanished with no error. `StructuredLogger` in `core/logging.py` folds
  them into the message text so they actually appear - see that class for the
  trade-off.
- **Vite's object-form `manualChunks` matches exact specifiers only.** It will not
  capture `react/jsx-runtime`, producing an empty chunk while React stays in the
  main bundle. Use the function form.
- **`manualChunks` overrides dynamic-import splitting.** The Stellar SDK is
  `await import`-ed precisely so the billing screen never pays for it, and a
  `vendor` rule matching it pulled all ~940 kB back into the eager bundle -
  silently, because the build still succeeded. It needs its own chunk entry.
- **RFC 6962 inclusion proofs are innermost-first.** Emitting siblings
  outermost-first verifies correctly for n ≤ 2 and fails for every larger tree.
  Getting that order wrong is the single easiest way to make this subsystem accuse
  an honest business of fraud, which is why `merkle.py` builds top-down and
  reverses, and why the test checks against an independently written reference for
  n = 1..69 at every index.

<!-- related:start -->

---

## Related reading

- [Architecture](architecture.md) - the layering the conventions exist to protect
- [Database](database.md) - migrations, and how tests get an isolated schema
- [API](api.md) - the contract a new endpoint has to fit
- [Proof ledger](attestation.md) - the contract workflow, and the one test you must never "fix" by updating its expected value

[All documentation](README.md)
<!-- related:end -->
