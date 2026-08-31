<div align="center">

# Commands

**Every task in this repository, as a `make` target and as the raw commands it runs.**

<!-- nav:start -->
[Docs](README.md) · [Spec](spec.md) · [Architecture](architecture.md) · [Database](database.md) · [Accounting](accounting.md) · [Proof ledger](attestation.md) · [API](api.md) · [Security](security.md) · [Audit](security-audit.md) · **Commands** · [Screenshots](screenshots.md) · [Demo video](demo-video.md) · [Evidence](evidence.md) · [Development](development.md) · [Deployment](deployment.md)
<!-- nav:end -->

</div>

---

## How to read this page

Every task is given twice: the `make` target, and the raw commands underneath it.
Neither is more official than the other - the Makefile is a thin wrapper, and every
target's recipe is exactly what is listed here as the raw form. Use whichever suits
you:

- **`make`** if you want one word, the right working directory chosen for you, and
  a recipe that aborts on the first failing line rather than running the rest
  against broken state.
- **raw commands** if you are in a container, in CI, on a machine without `make`,
  or debugging one step of a target that is failing.

**All `make` commands run from the repository root.** The Makefile `cd`s into
`backend/`, `frontend/`, `app_frontend/` and `contracts/` itself, so you never
need to. The raw forms name their own directory, because they do not.

### The variables the raw forms stand in for

The Makefile defines four prefixes. Where a raw command below says
`cd backend && ...`, that is the Makefile's `$(BACKEND)`:

| Makefile | Expands to |
| --- | --- |
| `$(BACKEND)` | `cd backend &&` |
| `$(FRONTEND)` | `cd frontend &&` |
| `$(DESKTOP)` | `cd app_frontend &&` |
| `$(CONTRACT)` | `cd contracts &&` |
| `$(COMPOSE)` | `docker compose` |
| `$(COMPOSE_PROD)` | `docker compose -f docker-compose.prod.yml` |
| `$(DESKTOP_DEVICE)` | `windows`, `macos` or `linux`, detected from the host |

### On Windows, prefer `make`

Raw commands are fine from **PowerShell**. They are *not* reliably fine from Git
Bash: the MSYS layer rewrites environment values that look like Unix paths when it
spawns a native Windows process, so `API_V1_PREFIX=/api/v1` reaches Python as
`C:/Program Files/Git/api/v1` and the application dies at import with "A path
prefix must start with '/'".

`make` targets are safe from **either** shell. The [Makefile](../Makefile) resolves
Git Bash explicitly and sets `MSYS2_ENV_CONV_EXCL=*` and `MSYS2_ARG_CONV_EXCL=*`
to switch that rewriting off. This is the one real reason to prefer the wrapper on
Windows.

### Discovering targets

```bash
make            # same as `make help`
make help       # every target with its one-line description
```

`make` with no argument prints the help rather than building anything, which is
deliberate: a task runner whose default action is unknown is a task runner nobody
reads twice.

---

## 1. Prerequisites

| Tool | Needed for | Verify |
| --- | --- | --- |
| [Docker](https://docs.docker.com/get-docker/) | PostgreSQL, Redis, the API | `docker --version` |
| [uv](https://docs.astral.sh/uv/) | Python 3.13 and the backend | `uv --version` |
| [Node](https://nodejs.org/) 24 | the web client | `node --version` |
| [Rust](https://rustup.rs/) + [Stellar CLI](https://developers.stellar.org/docs/tools/developer-tools) | **only** to build or deploy the contract | `cargo --version`, `stellar --version` |
| [Flutter](https://docs.flutter.dev/get-started/install) 3.44 | **only** for the desktop client | `flutter --version` |
| `make` | the wrappers on this page | `make --version` |

The last three are genuinely optional. The Rust toolchain is a heavy dependency
that nobody working on the accounting core needs, which is why the contract has its
own targets and `make check` stays runnable by somebody who has never installed
cargo.

Installing the Stellar CLI:

```bash
cargo install --locked stellar-cli
```

---

## 2. First-time setup

### make

```bash
make deployment      # checks the toolchain first, then prints how to run it
make setup           # the same work, without the checks or the summary
```

`deployment` is the one to reach for on a machine you have not built on before. It
verifies `uv`, `node` and `docker` are present and prints what is missing rather than
failing part-way through with a bare `command not found`, then runs `setup`, then
prints the two commands you actually need next - which was the part people were
reading the Makefile source to find.

### raw

```bash
# 1. Environment files. The desktop client's .env is bundled into the binary as an
#    asset, so `flutter build` fails on a missing file rather than falling back to
#    defaults - which is why it is created even if you never build the desktop app.
cp .env.sample .env
cp app_frontend/.env.sample app_frontend/.env

# 2. Dependencies
cd backend && uv sync && cd ..
cd frontend && npm ci && cd ..

# 3. Database
docker compose up -d postgres redis
docker compose exec -T postgres pg_isready          # repeat until it succeeds
cd backend && uv run alembic upgrade head && cd ..
```

`make setup` will not overwrite an existing `.env`. It tests for the file first, so
running it twice is safe.

### Demo data

```bash
make seed            # 12 organizations, 3 entries each, 12 feedback rows
make seed n=16       # more (16 business names are defined)
```

Raw, with the full flag set:

```bash
cd backend
uv run python scripts/seed_demo.py --dry-run
uv run python scripts/seed_demo.py --organizations 12 --entries 5
uv run python scripts/seed_demo.py --wipe     # removes the feedback/usage rows it wrote
```

Every row is written through the **real services** - registration, organization
creation, `post_simple`, the feedback service - not by direct INSERT, so the seeded
data satisfies every invariant the application enforces. A seeder writing its own SQL
would happily produce an unbalanced journal entry, and the trial balance is the first
thing anyone opens on a populated install.

Re-running is safe: existing accounts are skipped. Sign in as any of them with
`Sealed#Books-2026`.

> **Seeded rows are for screenshots and demos, not for evidence.** They are marked in
> three places - the email domain, the organization name suffix, the feedback contact -
> and [`make evidence`](#5-the-proof-ledger-contract) detects the marker and prints a
> warning banner, because it reads the same tables. The checklist's *user feedback
> summary* and *10+ wallet interactions* both mean real people. `--wipe` before quoting
> any of it.

### Then edit `.env`

`.env.sample` ships placeholders, not defaults. Five values have to be replaced
before the stack will work:

| Key | Set to | Generate with |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | - |
| `DEBUG` | `true` | - |
| `POSTGRES_PASSWORD` | anything except `postgres` or `stellarerp` | `openssl rand -hex 16` |
| `SECRET_KEY` | 32+ characters | `openssl rand -base64 48` |
| `ENCRYPTION_KEY` | a Fernet key | `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` |

In development `ENCRYPTION_KEY` may be left blank - one is derived from
`SECRET_KEY` so a fresh checkout runs. `ENVIRONMENT=production` rejects that
outright, along with `DEBUG=true`, `CORS_ORIGINS=*`, an empty `ALLOWED_HOSTS`, and
a default Postgres password. See `_enforce_production_safety` in
[`config.py`](../backend/app/core/config.py) - the whole point is that it crashes at
boot rather than serving traffic with a placeholder signing key.

**Nothing chain-related needs editing by hand.** `make contract-up`
([section 5](#5-the-proof-ledger-contract)) writes all of it.

---

## 3. Running it

### The whole stack in Docker

| make | raw |
| --- | --- |
| `make up` | `docker compose up -d` |
| `make down` | `docker compose --profile objectstore down` |
| `make clean` | `docker compose --profile objectstore down -v` |
| `make services` | `docker compose up -d postgres redis` |

| Then | |
| --- | --- |
| Web client | <http://localhost:5173> |
| Register | <http://localhost:5173/register> |
| **Public verifier** (no account) | <http://localhost:5173/verify> |
| API | <http://localhost:8000> |
| API reference | <http://localhost:8000/docs> |

Note `--profile objectstore` on the way **down**. Compose only stops services in
the active profiles, so a plain `docker compose down` cannot see a MinIO that
`up-objectstore` started, and leaves it running. `make down` always passes the
profile for that reason.

`make clean` deletes the volumes. That destroys your local database. There is no
confirmation prompt, because the target name is the warning.

### With MinIO, for object-storage documents

| make | raw |
| --- | --- |
| `make up-objectstore` | `docker compose --profile objectstore up -d` |

MinIO console at <http://localhost:9001>. Documents still go to PostgreSQL until
all three of `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` are set in
`.env` - partial configuration is treated as no configuration rather than as a
half-working storage backend.

### On the host, with reload

Useful when you want a debugger attached, or an editor's language server to see the
same interpreter the app runs on.

| make | raw |
| --- | --- |
| `make services` | `docker compose up -d postgres redis` |
| `make dev-api` | `cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` |
| `make dev-web` | `cd frontend && npm run dev` |

Run `make services` first: host mode still expects PostgreSQL and Redis to exist.

### The desktop client

| make | raw |
| --- | --- |
| `make desktop` | `cd app_frontend && flutter run -d windows` (or `macos`, `linux`) |

`make` picks the device for your platform. The client reads
`app_frontend/.env` at **start-up**, so changing the API host is a restart, not a
rebuild.

### Logs and shells

| make | raw |
| --- | --- |
| `make logs` | `docker compose logs -f` |
| `make logs-api` | `docker compose logs -f backend` |
| `make shell` | `cd backend && uv run python` |
| `make psql` | `docker compose exec postgres psql -U stellarerp -d stellarerp` |
| `make redis-cli` | `docker compose exec redis redis-cli` |

---

## 4. Database

| make | raw |
| --- | --- |
| `make migrate` | `cd backend && uv run alembic upgrade head` |
| `make migration m="add invoice table"` | `cd backend && uv run alembic revision --autogenerate -m "add invoice table"` |
| `make rollback` | `cd backend && uv run alembic downgrade -1` |
| `make db-history` | `cd backend && uv run alembic history --verbose` |
| `make db-reset` | `cd backend && uv run alembic downgrade base && uv run alembic upgrade head` |

### Drift checking is two commands, not one

```bash
make db-check
```

```bash
cd backend
uv run alembic check
uv run python scripts/check_schema_drift.py
```

**Both, always.** `alembic check` does not compare CHECK constraint expressions, so
adding a value to a `StrEnum` is invisible to it. It once reported no pending
operations while `audit_log.action` was missing 49 of 95 values - and because every
write records an audit row inside its own transaction, uploads, invoices and stock
adjustments all failed with a 409 against a schema the test suite could not
exercise, because the tests build their schema from the models rather than from the
migrations. The second script exists to catch exactly that class of miss.

---

## 5. The proof ledger contract

This is the third ledger. See [Proof ledger](attestation.md) for what it is and why,
and [`contracts/README.md`](../contracts/README.md) for the contract's own
interface.

### One command does everything

```bash
make contract-up
```

Which runs [`contracts/deploy.sh`](../contracts/deploy.sh):

```bash
bash contracts/deploy.sh
```

Seven steps, in order:

```
[1/7] Checking the toolchain and the current configuration
[2/7] Running the contract's tests               28, adversarial
[3/7] Building the wasm                          prints the hash
[4/7] Preparing the deploy key                   creates and funds if absent
[5/7] Deploying to testnet
[6/7] Reading the contract back off the network   proves it is really there
[7/7] Writing .env                                backend and frontend both
```

Step 6 is not ceremony. A deploy that returns a contract id has proved nothing yet -
the id is derived locally, before the network has said anything. The script asks the
network what is actually at that address and checks that all eight expected
functions are there, so a stale wasm or a half-finished upload is caught now rather
than by a seal failing at month end.

#### Options

```bash
make contract-up ARGS="--dry-run"                    # print the plan, write nothing
make contract-up ARGS="--force"                      # replace a contract already in use
make contract-up ARGS="--network public --yes"       # mainnet
make contract-up ARGS="--skip-tests"                 # skip step 2
make contract-up ARGS="--no-verify"                  # skip step 6
make contract-up ARGS="--identity my-key"            # sign with a different CLI key
make contract-up ARGS="--env /path/to/.env"          # write a different env file
make contract-up ARGS="--set SEAL_DAILY_HOUR=3"      # write any other variable too, repeatable
bash contracts/deploy.sh --help                      # all of the above
```

`--set` is how the script covers "edit any env var I need": it writes through the
same in-place editor as the chain settings, so a key that already exists is
replaced where it sits rather than appended, and a value containing spaces is
quoted.

#### What it writes, and what it will not touch

Written automatically:

| Key | Value |
| --- | --- |
| `ATTESTATION_ENABLED` | `true` |
| `STELLAR_NETWORK` | `testnet` or `public` |
| `SOROBAN_CONTRACT_ID` | the id just deployed |
| `VITE_STELLAR_NETWORK` | the same network |
| `VITE_SOROBAN_CONTRACT_ID` | the same id |
| `VITE_SOROBAN_RPC_URL` | the network's default, **unless** you set a custom one - that is left alone |
| `ATTESTATION_NAMESPACE_SALT` | generated **only if blank** |

There are `VITE_` duplicates because the verifier reads the contract **in the
browser**, and those values are inlined at build time - the page cannot be handed
them at runtime without a server in the loop, which is the one thing this design
refuses.

Never touched: `SECRET_KEY`, `ENCRYPTION_KEY`, `POSTGRES_PASSWORD`, `ENVIRONMENT`,
`DEBUG`, and the backend's own `SOROBAN_RPC_URL` - blank there means "use the
network default", which is a real choice rather than an omission. Nothing in
`app_frontend/.env` either: the desktop client has no chain configuration at all,
because it reads Trust data through the API rather than the chain.

The file is edited in place, preserving your comments and line order, and a
`.env.bak` is written first.

#### The two refusals

**Replacing an existing contract id requires `--force`.** Storage belongs to a
contract instance, so a fresh deployment starts empty. Without the guard you would
get: every registered book still on the old contract with the application no longer
looking there, a Trust screen back at "nothing sealed", and every proof bundle
already sent to a counterparty naming an address this install does not use. There
is no migration path, because the entire point is that a seal cannot be moved or
rewritten. The guard is about intent, so `--dry-run` trips it too.

**`ATTESTATION_NAMESPACE_SALT` is never overwritten.** An organization's on-chain
identity is `SHA-256(organization_id || salt)`, and the mapping is unrecoverable
from the chain by design. Rotating the salt orphans every book already registered,
permanently. The script generates one when absent and leaves it alone forever after.

#### After deploying

```bash
make up      # restart the API so it reads the new contract id
make build   # rebuild the web client
```

The second is not optional and is the step people skip. `VITE_*` values are inlined
at **build** time, so a restart alone leaves the browser verifying against the old
contract while the API uses the new one. The Trust screen will tell you:
`chain.agrees_with_local` goes false.

### Individual steps

| make | raw |
| --- | --- |
| `make contract-test` | `cd contracts && cargo test` |
| `make contract-lint` | `cd contracts && cargo clippy --all-targets -- -D warnings`<br>`cd contracts && cargo fmt --check` |
| `make contract-build` | `cd contracts && stellar contract build` |
| `make contract-key` | `stellar keys generate stellar-erp-deployer --network testnet --fund` |
| `make contract-deploy` | builds first, then the command below. Deploys only - does not touch `.env` |

```bash
cd contracts
stellar contract deploy \
  --wasm target/wasm32v1-none/release/proof_ledger.wasm \
  --source stellar-erp-deployer \
  --network testnet \
  --alias proof_ledger
```

### Two network vocabularies

| The application says | The CLI says |
| --- | --- |
| `testnet` | `testnet` |
| `public` | `mainnet` |

`public` is Stellar's own name for mainnet and is what `STELLAR_NETWORK` holds in
`.env`. The CLI does not know it, and answers `--network public` with `error: Failed
to find config network for public` - a message a long way from anything that
explains the cause. `contract-up` translates between the two. The lower-level
targets take the CLI's name, because they are thin wrappers around it; the
Makefile's `CLI_NETWORK` variable does the mapping.

### If the CLI complains about a missing network passphrase

```
error: rpc-url is used but network passphrase is missing
```

This is what you get when `.env` has been sourced into your shell. The CLI reads
its own connection settings from the environment, and `.env` sets
`SOROBAN_RPC_URL=` deliberately blank so the backend falls back to the network
default - a *present but empty* value, which the CLI takes as "an rpc-url was
supplied". `contract-up` unsets the CLI's connection variables before it runs
anything, so this only bites raw `stellar` commands. Fix it with:

```bash
unset SOROBAN_RPC_URL SOROBAN_NETWORK_PASSPHRASE STELLAR_RPC_URL STELLAR_NETWORK_PASSPHRASE
```

### The seal worker

Runs in-process by default, which is what keeps this a single `docker compose up`.
To move it to its own process, set `SEAL_WORKER_ENABLED=false` in `.env` and run:

| make | raw |
| --- | --- |
| `make seal-worker` | `cd backend && uv run python -m app.modules.attestation.worker` |

### Auto-seal

Two separate settings, and they are easy to confuse.

**The cadence is per organization** and lives in the database, not in `.env`. Set it
on the Trust screen, or:

```bash
curl -X PATCH localhost:8000/api/v1/attestation/cadence \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  --data '{"cadence": "daily"}'
```

| Cadence | Seals when |
| --- | --- |
| `daily` | Once a day, **and** on every period close. The default and the recommendation |
| `on_period_close` | Only when an accounting period is closed |
| `manual` | Only when somebody presses **Seal now** |

**The timing knobs are install-wide** and live in `.env`:

```ini
SEAL_WORKER_INTERVAL_SECONDS=60   # how often the worker LOOKS for work
SEAL_DAILY_HOUR=1                 # earliest UTC hour a daily seal may fire
SEAL_MAX_BATCH=5000               # most entries one seal may cover
```

Or through the deploy script, which writes them the same way it writes the chain
settings:

```bash
make contract-up ARGS="--force --set SEAL_DAILY_HOUR=3 --set SEAL_MAX_BATCH=2000"
```

Restart the API afterwards - these are read at start-up.

> **`SEAL_WORKER_INTERVAL_SECONDS` is not the sealing frequency.** Setting it to
> `10` does not seal six times more often; it wakes six times more often and finds
> nothing five of those times. What it actually shortens is the delay before an
> already-prepared seal is submitted, and before an unknown outcome is reconciled.

Three things you can trigger by hand, whatever the cadence:

| | |
| --- | --- |
| `POST /attestation/seals` | Seal now - what the Trust screen's button calls |
| `POST /attestation/drain` | Submit every seal still owed an outcome |
| `POST /attestation/reconcile` | Ask the chain what it holds and correct local state |

The last two are what the worker does on its own schedule; the endpoints exist so
you never have to wait for a tick to find out whether something is stuck.

What auto-seal does and does not do -
[Proof ledger, Auto-seal](attestation.md#auto-seal-the-cadence). The short version:
it never seals on posting (posting must never wait on consensus), it never re-seals
history, and it never tells you when it has stopped - which is why
`days_unsealed` is the number to watch.

### Checking a proof bundle from the terminal

| make | raw |
| --- | --- |
| `make verify-proof f=bundle.json` | `cd backend && uv run python scripts/verify_proof.py /abs/path/to/bundle.json` |

`make` resolves the path for you, which matters because the script runs from
`backend/` and a relative path would be resolved from there.

### Evidence for the submission

Four targets that read the live database and the live ledger rather than asserting
anything about them.

| make | raw |
| --- | --- |
| `make evidence` | `cd backend && uv run python scripts/submission_evidence.py --out ../docs/evidence.md` |
| `make interactions n=2` | `cd backend && uv run python scripts/demo_interactions.py --rounds 2` |
| `make interactions-list` | `cd backend && uv run python scripts/demo_interactions.py --list` |
| `make feedback-summary` | `docker compose exec -T postgres psql -U stellarerp -d stellarerp -v ON_ERROR_STOP=1 < backend/scripts/feedback_summary.sql` |

**`evidence`** regenerates [docs/evidence.md](evidence.md) with wallet interactions, the
feedback summary and the usage rollup, printing a public explorer link beside every
on-chain figure. It **exits non-zero while the signed-interaction count is under ten**,
so it cannot be wired into CI and quietly pass while the submission claims otherwise.

**`interactions`** posts one journal entry and seals it, `n` times. Seals are the
repeatable half of the interaction count - registrations are one per organization - and
`Seal now` is deliberately idempotent, so a second press with nothing outstanding writes
no transaction. An entry has to be posted *between* seals, which is the loop this
automates. Everything goes through `PostingService` and `SealService.seal_now`, so the
transactions are real and the invariants are the same as pressing the button.

> Run against a seeded organization these are seeded-organization transactions. Real
> on-chain, real signatures, but a strict reading of "**user** wallet interactions" would
> discount them - register a real organization and pass `--org` if that matters.

**`feedback-summary`** reports the `feedback` table with seeded rows counted separately
from real ones, using the same marker `submission_evidence.py` uses so the two cannot
drift. It never prints a combined total, because a total is a claim about users that the
number does not support - see [`feedback_summary.sql`](../backend/scripts/feedback_summary.sql).

---

## 6. Seeing the deployed contract

Six ways, in increasing order of how much they trust us. The last two trust us not
at all, which is the point.

### 6.1 The record in this repository

```bash
cat contracts/deployments/testnet.json
```

```json
{
  "network": "testnet",
  "contract_id": "CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR",
  "wasm_hash": "2324b519f8a205a8cae31e1b8ebf3944be1bc5d1d6ec7028cdea3829f5e79246",
  "deployer": "GDKLGSB2MF7ZXDCFR2VSAPZ5WYI25EKRMRQRVXKQ6FB6FB4EOZN2HSGZ",
  "deployed_at": "2026-08-24T00:00:00Z",
  "reproduce": "make contract-build  # must print this wasm_hash"
}
```

Written by `contract-up` on every deploy and committed on purpose: the contract id
and the wasm hash are the two things somebody needs in order to confirm that the
code in this repository is the code that is running, and a claim like that is worth
nothing if it lives only in a terminal that has since been closed.

### 6.2 A block explorer

<https://stellar.expert/explorer/testnet/contract/CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR>

Every invocation, every seal, every fee paid, and the wasm the contract was created
from. Nothing about a business, because there is nothing about a business in there -
what you will see is a 32-byte root, an entry count, a control total, a window, and
an opaque 32-byte namespace.

The pattern is `https://stellar.expert/explorer/<testnet|public>/contract/<id>`. The
application builds the same links itself: `Seal.explorer_url` in
[`attestation/models.py`](../backend/app/modules/attestation/models.py), and
`explorerTxUrl` / `explorerContractUrl` in
[`trust/chain.ts`](../frontend/src/features/trust/chain.ts).

### 6.3 Ask the network what is there

```bash
stellar contract info interface \
  --network testnet \
  --id CCB66KMNINKNGBCVWCYKEF26OIXNZQIIJ4EUKCUOUD4OCDFA6ID4S5YR
```

Downloads the contract's spec from the live network and prints its eight functions -
`register`, `seal`, `get`, `latest`, `verify`, `history`, `is_registered`, `rotate`.
This is exactly what step 6 of `contract-up` does.

### 6.4 Confirm the deployed bytes are this source

```bash
make contract-build
```

The last lines print:

```
Wasm Hash: 2324b519f8a205a8cae31e1b8ebf3944be1bc5d1d6ec7028cdea3829f5e79246
Wasm Size: 15244 bytes optimized
Exported Functions: 8 found
```

That hash must match `wasm_hash` in the deployment record and the hash the explorer
shows for the contract. If it does, the code you just read is the code that is
running. The toolchain is pinned in
[`rust-toolchain.toml`](../contracts/rust-toolchain.toml) for precisely this reason -
"compiles with whatever rustc is installed" would mean the deployed hash cannot be
reproduced, and a reviewer could not make this check at all.

### 6.5 Through the application

Signed in, on the Trust screen, or over the API:

```bash
curl -s localhost:8000/api/v1/attestation/status       -H "Authorization: Bearer $TOKEN"
curl -s localhost:8000/api/v1/attestation/network      -H "Authorization: Bearer $TOKEN"
curl -s localhost:8000/api/v1/attestation/chain/health -H "Authorization: Bearer $TOKEN"
curl -s localhost:8000/api/v1/attestation/seals        -H "Authorization: Bearer $TOKEN"
```

The figure to watch on `/status` is **`days_unsealed`**, not the seal count. "412
entries sealed" is reassuring and says nothing about now; the age of the unsealed
backlog is the only number that distinguishes sealing working from sealing having
silently stopped, because the two are otherwise identical from the outside.
`chain.agrees_with_local == false` outranks everything else on the screen.

### 6.6 Without an account at all

Unauthenticated, from anywhere:

```bash
curl -s localhost:8000/api/v1/verify/network
curl -s localhost:8000/api/v1/verify/spec
curl -s localhost:8000/api/v1/verify/chain/<namespace>
curl -s localhost:8000/api/v1/verify/bundle -X POST \
  -H 'content-type: application/json' --data @bundle.json
```

These are the only unauthenticated routes in the application. What they return is
either computed from a bundle the caller already sent, or already public on the
Stellar ledger.

And the one that matters most - **<http://localhost:5173/verify>**, in a private
window, signed out. Drop a proof bundle in. The page re-encodes the entry, hashes
it, folds the Merkle path and asks the contract directly, over an RPC endpoint the
reader can change on screen. Our API is not consulted for the verdict at any point,
which is the only reason the verdict is worth anything.

---

## 7. Demonstrating that the records are tamper-evident

This is the claim the third ledger exists to support, so it is worth being able to
show rather than assert. The sequence below takes a few minutes and needs nothing
but a running stack.

**1. Produce something to prove.** Sign in, post a few journal entries, or raise an
invoice under Sales.

**2. Seal.** Open `/trust` and switch sealing on. This creates a Stellar account
*for your organization*, funds it, and registers its book on the contract - each
organization gets its own signer, which is also how sequence-number contention is
avoided. Press **Seal now**. The batch's Merkle root goes on chain.

**3. Export a proof bundle** from one invoice. A small JSON file: that one entry,
its Merkle path, the seal reference, and the encoding spec. Note what is *not* in
it - the other entries in the batch. A Merkle path proves one leaf without
disclosing its siblings, which is why a business can hand a bank a single invoice
rather than its book.

**4. Verify it as a stranger.** `/verify`, private window, signed out. Green.

**5. Now tamper with it.** Open the bundle in an editor and change one digit of the
amount. Verify again.

It fails, and it fails at a nameable step: the recomputed leaf hash no longer folds
to the root the contract holds. Not "the file looks wrong" - the arithmetic does not
reach a number that was written to a public ledger before the edit was made.

**6. Check the timestamp claim.** In the explorer, the seal's ledger close time is
the network's, not ours. The contract's `seal` function takes **no timestamp
argument**; it reads `env.ledger().timestamp()`. That single decision is what stops
a back-dated seal, and without it every other guarantee here would be worthless.

**7. Try to skip a period.** The contract requires `seq == head + 1` and `prev ==`
the stored root. A gap is therefore not a missing record - it is evidence. Rewriting
one period means re-sealing every period after it, in public, with the network
timestamping each attempt.

### What this demonstrates, stated honestly

A seal proves the books presented today are **byte-identical** to the books that
existed when the seal was written, at a time the network attests to and the business
cannot back-date.

It does **not** prove the entries were true when they were made. No cryptographic
scheme can. What it eliminates is *retroactive* fabrication - which is how accounts
are actually cooked: by editing history to fit a story told later. And while the
signing key sits on the server, an operator could still doctor the books *before*
sealing, which is why the default cadence is daily, why the seals form a hash chain
the network timestamps, and why the book can be moved onto a 2-of-3 multisig with
the business's accountant.

The same limitation is printed on the Trust screen. A trust product that oversells
itself is not a trust product.

---

## 8. Quality gates

### Everything CI runs

| make | raw |
| --- | --- |
| `make check` | `make lint typecheck test`, in that order |

### Lint

| make | raw |
| --- | --- |
| `make lint` | `cd backend && uv run ruff check .`<br>`cd frontend && npm run lint` (`eslint . --max-warnings 0`)<br>`cd app_frontend && dart format --output=none --set-exit-if-changed lib test` |
| `make format` | `cd backend && uv run ruff format . && uv run ruff check --fix .`<br>`cd frontend && npm run format`<br>`cd app_frontend && dart format lib test` |

### Type check

| make | raw |
| --- | --- |
| `make typecheck` | `cd backend && uv run mypy app`<br>`cd frontend && npx tsc -b`<br>`cd app_frontend && flutter analyze` |

`mypy` runs in strict mode; the configuration is in `backend/pyproject.toml` rather
than on the command line, so the raw form and the target behave identically.

### Test

| make | raw |
| --- | --- |
| `make test` | `cd backend && uv run pytest -q`<br>`cd frontend && npm test` (`vitest run`)<br>`cd app_frontend && flutter test` |
| `make test-cov` | `cd backend && uv run pytest --cov --cov-report=term-missing --cov-report=html` |
| `make contract-test` | `cd contracts && cargo test` |

**The backend tests need real PostgreSQL and Redis.** Run `make services` first.
They are not run against SQLite, and that is deliberate: the things worth testing
here - partial unique indexes, `SELECT ... FOR UPDATE` under concurrency, JSONB
behaviour - are exactly what a substitute engine implements differently, so passing
against SQLite would prove nothing.

Current state: 1,189 backend tests, 42 frontend, 105 desktop, 28 contract.

---

## 9. Building for release

| make | raw |
| --- | --- |
| `make build` | `cd frontend && npm run build` (`tsc -b && vite build`) |
| `make build-desktop` | `cd app_frontend && flutter build windows --release` (or `macos`, `linux`) |
| `make installer-deps` | `curl -fL -o installer/VC_redist.x64.exe https://aka.ms/vs/17/release/VC_redist.x64.exe` |

`make build` reads `VITE_*` from `.env` and inlines them. Rebuild after any change
to a `VITE_` value, including a new contract id.

The Visual C++ redistributable is fetched rather than committed - it is 25 MB of
Microsoft's binary that git would carry forever, and every future version would add
another 25 MB that cannot be removed without rewriting history. `installer-deps` is
idempotent: it says so and does nothing if the file is already there. Then compile
[`installer/stellar-erp.iss`](../installer/README.md) with Inno Setup.

---

## 10. Production

Full walkthrough in [Deployment](deployment.md). The commands:

| make | raw |
| --- | --- |
| `make prod-up` | `docker compose -f docker-compose.prod.yml up -d --build` |
| `make prod-down` | `docker compose -f docker-compose.prod.yml down` |
| `make prod-logs` | `docker compose -f docker-compose.prod.yml logs -f --tail=100` |
| `make prod-migrate` | `docker compose -f docker-compose.prod.yml run --rm migrate` |
| `make prod-config` | `docker compose -f docker-compose.prod.yml config --quiet` |

Migrations are a **separate step** in production, never part of start-up. A
container that migrates on boot migrates once per replica and gives you no moment at
which to take a backup first.

### Backup

| make | raw |
| --- | --- |
| `make backup` | see below |

```bash
mkdir -p backups
docker compose -f docker-compose.prod.yml exec -T \
  -e DUMP="/backups/stellarerp-$(date -u +%Y%m%dT%H%M%SZ).dump" postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file="$DUMP.partial" \
   && pg_restore --list "$DUMP.partial" > /dev/null \
   && mv "$DUMP.partial" "$DUMP"'
```

Note the `.partial` suffix and the `pg_restore --list` before the rename. A dump is
only given its real name once it has been read back successfully, so an interrupted
or corrupt dump can never be mistaken for a good one - and "the backup existed" is
not the same claim as "the backup restores".

Everything runs through the postgres container, so there is no script tree to keep
in sync with the compose file and nothing extra to install on the server.

### Restore

```bash
make restore f=backups/stellarerp-20260824T101500Z.dump
```

This one is worth using the wrapper for. It stops the backend, restores in a single
transaction, re-runs migrations, and starts the backend again - and before any of
that it makes you **type the database name** to confirm. The raw form would let you
replace a production database with one mistyped filename.

---

## 11. Complete target index

| Target | Section |
| --- | --- |
| `help` | [How to read this page](#discovering-targets) |
| `deployment`, `setup`, `install`, `seed` | [First-time setup](#2-first-time-setup) |
| `up`, `up-objectstore`, `down`, `clean`, `services` | [Running it](#3-running-it) |
| `dev-api`, `dev-web`, `desktop` | [On the host](#on-the-host-with-reload) |
| `logs`, `logs-api`, `shell`, `psql`, `redis-cli` | [Logs and shells](#logs-and-shells) |
| `migrate`, `migration`, `rollback`, `db-check`, `db-history`, `db-reset` | [Database](#4-database) |
| `contract-up`, `contract-test`, `contract-lint`, `contract-build`, `contract-key`, `contract-deploy` | [The proof ledger contract](#5-the-proof-ledger-contract) |
| `seal-worker`, `verify-proof` | [The seal worker](#the-seal-worker) |
| `evidence`, `interactions`, `interactions-list`, `feedback-summary` | [Evidence for the submission](#evidence-for-the-submission) |
| `check`, `lint`, `format`, `typecheck`, `test`, `test-cov` | [Quality gates](#8-quality-gates) |
| `build`, `build-desktop`, `installer-deps` | [Building for release](#9-building-for-release) |
| `prod-up`, `prod-down`, `prod-logs`, `prod-migrate`, `prod-config`, `backup`, `restore` | [Production](#10-production) |

---

## 12. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `'grep' is not recognized as an internal or external command` | `make` fell back to `cmd.exe`. Install Git for Windows, or `make SHELL=/path/to/bash <target>`. |
| `A path prefix must start with '/'` at import | MSYS rewrote an environment value. Use PowerShell for raw commands, or use `make`, which switches the rewriting off. |
| `error: rpc-url is used but network passphrase is missing` | `.env` is sourced into your shell and `SOROBAN_RPC_URL` is present-but-empty. See [above](#if-the-cli-complains-about-a-missing-network-passphrase). |
| `error: Failed to find config network for public` | The CLI calls mainnet `mainnet`. Use `make contract-up ARGS="--network public"`, which translates. |
| `.env already points at C...` from `contract-up` | Not a failure - the redeploy guard. See [the two refusals](#the-two-refusals). |
| Backend tests fail on connection | They need real PostgreSQL and Redis. `make services`. |
| Verifier uses the old contract after a redeploy | `VITE_*` are inlined at build time. `make build`. |
| `[FAIL] Could not read ... bundle.json` | There is no bundle yet. Export one from an invoice's proof panel, or `GET /attestation/proof/{journal_entry_id}`, and save the response. A bundle that cannot be *read* exits **2**; a bundle that reads and does not verify exits **1** - a typo must never come back looking like a finding about somebody's books. |
| Seal worker logs an error every 60 seconds and seals nothing | It could not resolve a mapper - a model missing from `app/db/registry.py`. Every ORM class must be imported there, and the standalone worker imports the registry for exactly this reason. |
| No verification email | Mail goes through the Gmail API only. Without `GMAIL_CREDENTIALS_B64` the link is logged - but logifyx masks `token=...`, so set `LOG_MASK=false`, then `make logs-api`. |
| `alembic check` clean but writes fail with 409 | A CHECK constraint drifted, which `alembic check` cannot see. Run `make db-check`, which also runs `scripts/check_schema_drift.py`. |

<!-- related:start -->

---

## Related reading

- [Development](development.md) - conventions, testing, and how to add a module
- [Deployment](deployment.md) - putting it on a server, backups, and the proxy you supply
- [Proof ledger](attestation.md) - what the contract is for, and every decision behind it

[All documentation](README.md)
<!-- related:end -->
