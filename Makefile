# =============================================================================
# Stellar ERP - task runner
#
#   make help          list every target
#   make setup         first-time setup
#   make up            start the development stack
#   make check         everything CI checks, locally
# =============================================================================

.DEFAULT_GOAL := help

# -----------------------------------------------------------------------------
# Shell
# -----------------------------------------------------------------------------
# Every recipe runs in one shell with strict flags, so a failing line aborts the
# target instead of the next line running against a broken state.
.SHELLFLAGS := -eu -o pipefail -c
SHELL := bash

# On Windows, `SHELL := bash` is silently ignored. Native GNU Make cannot find a
# bare `bash` (Git Bash is not on PATH - only `git` is), falls back to `cmd.exe`,
# and every recipe dies with "'grep' is not recognized as an internal or external
# command". So the shell is resolved explicitly here.
#
# **Not via `where bash`.** On a machine with WSL that returns
# `C:\Windows\System32\bash.exe`, and recipes would then run inside the WSL
# filesystem namespace - wrong working directory, and `uv`/`npm`/`docker` either
# missing or pointing at a different install. Silently running in the wrong place
# is far worse than failing loudly.
#
# Git Bash ships beside `git`, which *is* on PATH, so it is derived from there.
# `PROGRA~1` is the 8.3 short name for `Program Files`: make splits its SHELL
# value on spaces, so the long form resolves to `C:/Program` and fails.
ifeq ($(OS),Windows_NT)
  GIT_BASH := C:/PROGRA~1/Git/bin/bash.exe
  ifeq ($(wildcard $(GIT_BASH)),)
    $(error Git Bash not found at $(GIT_BASH). Install Git for Windows, \
      or override it: make SHELL=/path/to/bash <target>)
  endif
  SHELL := $(GIT_BASH)

  # Stop MSYS from rewriting environment values that look like Unix paths when it
  # spawns a native Windows process.
  #
  # Without this, `API_V1_PREFIX=/api/v1` reaches Python as
  # `C:/Program Files/Git/api/v1`, and the app dies at import with
  # "A path prefix must start with '/'". The translation is correct for real paths
  # and wrong for everything else - and every value this project puts in the
  # environment is a config string, not a path the MSYS layer should touch.
  #
  # `PATH` is special-cased by MSYS and still translated, so recipes continue to
  # find `uv`, `npm`, and `docker` normally. Verified, not assumed.
  export MSYS2_ENV_CONV_EXCL := *

  # The same translation applies to command-line *arguments*. That used to bite the desktop
  # client, whose config arrived as `--dart-define=API_V1_PREFIX=/api/v1` and reached the
  # compiler as `C:/Program Files/Git/api/v1`, baked into the binary. It now reads
  # `app_frontend/.env` instead, so no path-shaped argument crosses this boundary - but the
  # exclusion stays, because any argument in any future recipe would hit the same rewrite.
  export MSYS2_ARG_CONV_EXCL := *
endif

COMPOSE      := docker compose
COMPOSE_PROD := docker compose -f docker-compose.prod.yml
BACKEND      := cd backend &&
FRONTEND     := cd frontend &&
DESKTOP      := cd app_frontend &&
CONTRACT     := cd contracts &&

# The Stellar network the contract targets. `testnet` until the contract has run
# against real books; override on the command line for a mainnet deploy:
#
#     make contract-up ARGS="--network public --yes"
#
# Note the two vocabularies. The application calls mainnet `public`, which is
# Stellar's own name for it and what `STELLAR_NETWORK` in .env holds; the CLI
# calls it `mainnet`. `contract-up` translates. The lower-level targets below
# take the CLI's name, because they are thin wrappers around it.
#
STELLAR_NETWORK  ?= testnet

# The same network under the name the CLI knows it by.
CLI_NETWORK      := $(if $(filter public,$(STELLAR_NETWORK)),mainnet,$(STELLAR_NETWORK))
STELLAR_IDENTITY ?= stellar-erp-deployer

# Where the desktop client points: `app_frontend/.env`, read at start-up.
#
# It used to be `--dart-define` on every run and build, which meant a rebuild to change a
# host and - on Windows - a silently corrupted value: Git Bash's MSYS layer rewrote
# `--dart-define=API_V1_PREFIX=/api/v1` into `C:/Program Files/Git/api/v1` and baked that
# into the binary. A file has no argument parsing to survive, so there is nothing left for
# these recipes to pass.
#
# To point a build somewhere else, edit `app_frontend/.env` - see its `.env.sample`.

# The desktop target to run. Defaults to whichever this machine is.
ifeq ($(OS),Windows_NT)
  DESKTOP_DEVICE ?= windows
else
  UNAME_S := $(shell uname -s)
  ifeq ($(UNAME_S),Darwin)
    DESKTOP_DEVICE ?= macos
  else
    DESKTOP_DEVICE ?= linux
  endif
endif

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------
.PHONY: setup
setup: ## First-time setup: env files, dependencies, database
	@test -f .env || { cp .env.sample .env; echo "Created .env - review it before continuing."; }
	# The desktop client's `.env` is bundled as an asset, so `flutter build` fails on a
	# missing file rather than falling back to defaults. Created here so it always exists.
	@test -f app_frontend/.env || { cp app_frontend/.env.sample app_frontend/.env; \
		echo "Created app_frontend/.env - review it before continuing."; }
	$(MAKE) install
	$(COMPOSE) up -d postgres redis
	@echo "Waiting for PostgreSQL..."
	@until $(COMPOSE) exec -T postgres pg_isready -q; do sleep 1; done
	$(MAKE) migrate
	@echo ""
	@echo "Setup complete. Run 'make up' to start the stack."

# `setup` assumes the toolchain is already there and fails mid-way with a raw
# "command not found" when it is not. `deployment` checks first, then does the same
# work, then prints the two commands you actually need next - which is the part
# people were reading the Makefile source to find.
.PHONY: deployment
deployment: ## Full bootstrap: check tools, install deps, migrate, print run commands
	@echo "Checking the toolchain..."
	@command -v uv >/dev/null 2>&1     || { echo "  MISSING uv     - install from https://docs.astral.sh/uv/"; exit 1; }
	@command -v node >/dev/null 2>&1   || { echo "  MISSING node   - Node 24, from https://nodejs.org/"; exit 1; }
	@command -v docker >/dev/null 2>&1 || { echo "  MISSING docker - needed for PostgreSQL and Redis"; exit 1; }
	@echo "  uv     $$(uv --version)"
	@echo "  node   $$(node --version)"
	@echo "  docker $$(docker --version | cut -d, -f1)"
	@echo ""
	$(MAKE) setup
	@echo ""
	@echo "======================================================================"
	@echo " Ready. Two processes, two terminals:"
	@echo ""
	@echo "   BACKEND    make dev-api"
	@echo "              (raw: cd backend && uv run uvicorn app.main:app --reload"
	@echo "                    --host 0.0.0.0 --port 8000)"
	@echo ""
	@echo "   FRONTEND   make dev-web"
	@echo "              (raw: cd frontend && npm run dev)"
	@echo ""
	@echo " Or run everything in Docker instead of on the host:  make up"
	@echo ""
	@echo "   API        http://localhost:8000        docs at /docs"
	@echo "   Web        http://localhost:5173"
	@echo "   Verifier   http://localhost:5173/verify   (no account needed)"
	@echo ""
	@echo " Demo data, if you want a populated install:  make seed"
	@echo " Before serving real traffic, replace the placeholders in .env -"
	@echo " SECRET_KEY, ENCRYPTION_KEY and POSTGRES_PASSWORD. See docs/commands.md."
	@echo "======================================================================"

.PHONY: seed
seed: ## Seed demo organizations, entries and feedback: make seed [n=12]
	$(BACKEND) uv run python scripts/seed_demo.py $(if $(n),--organizations $(n),)

.PHONY: install
install: ## Install backend and frontend dependencies
	$(BACKEND) uv sync
	$(FRONTEND) npm ci

# -----------------------------------------------------------------------------
# Development
# -----------------------------------------------------------------------------
.PHONY: up
up: ## Start the full development stack in Docker
	$(COMPOSE) up -d
	@echo ""
	@echo "  Frontend   http://localhost:5173"
	@echo "  API        http://localhost:8000"
	@echo "  API docs   http://localhost:8000/docs"

.PHONY: up-objectstore
up-objectstore: ## Start the stack plus MinIO, for the object-storage backend
	$(COMPOSE) --profile objectstore up -d
	@echo ""
	@echo "  Frontend       http://localhost:5173"
	@echo "  API            http://localhost:8000"
	@echo "  MinIO console  http://localhost:9001"
	@echo ""
	@echo "  Documents still go to PostgreSQL until you set all three of"
	@echo "  MINIO_ENDPOINT, MINIO_ACCESS_KEY and MINIO_SECRET_KEY in .env."

.PHONY: down
down: ## Stop the development stack
	# `--profile objectstore` on the way down too, or a MinIO started by
	# `up-objectstore` is left running by a plain `make down` - compose only stops
	# services in the active profiles, so the default `down` cannot see it.
	$(COMPOSE) --profile objectstore down

.PHONY: clean
clean: ## Stop and DELETE all volumes (destroys local data)
	$(COMPOSE) --profile objectstore down -v

.PHONY: services
services: ## Start only PostgreSQL and Redis
	$(COMPOSE) up -d postgres redis

.PHONY: dev-api
dev-api: ## Run the API on the host with reload
	$(BACKEND) uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: dev-web
dev-web: ## Run the Vite dev server on the host
	$(FRONTEND) npm run dev

.PHONY: desktop
desktop: ## Run the Flutter desktop client on the host
	$(DESKTOP) flutter run -d $(DESKTOP_DEVICE)

.PHONY: logs
logs: ## Tail all container logs
	$(COMPOSE) logs -f

.PHONY: logs-api
logs-api: ## Tail the API logs
	$(COMPOSE) logs -f backend

.PHONY: shell
shell: ## Open a Python shell with the app importable
	$(BACKEND) uv run python

.PHONY: psql
psql: ## Open psql against the development database
	$(COMPOSE) exec postgres psql -U stellarerp -d stellarerp

.PHONY: redis-cli
redis-cli: ## Open redis-cli against the development Redis
	$(COMPOSE) exec redis redis-cli

# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------
.PHONY: migrate
migrate: ## Apply all migrations
	$(BACKEND) uv run alembic upgrade head

.PHONY: migration
migration: ## Generate a migration: make migration m="add invoice table"
	@test -n "$(m)" || { echo 'Usage: make migration m="description"'; exit 1; }
	$(BACKEND) uv run alembic revision --autogenerate -m "$(m)"

.PHONY: rollback
rollback: ## Roll back the most recent migration
	$(BACKEND) uv run alembic downgrade -1

.PHONY: db-check
db-check: ## Verify the models and migrations agree (no drift)
	$(BACKEND) uv run alembic check
# `alembic check` alone is not enough: autogenerate does not compare CHECK expressions, so
# adding a value to a StrEnum is invisible to it. It reported no pending operations while
# audit_log.action was missing 49 of 95 values - and because every write records an audit row
# inside its own transaction, uploads, invoices and stock adjustments all failed with a 409
# against a schema the tests could not exercise. They build their schema from the models.
	$(BACKEND) uv run python scripts/check_schema_drift.py

.PHONY: db-history
db-history: ## Show the migration history
	$(BACKEND) uv run alembic history --verbose

.PHONY: db-reset
db-reset: ## Drop and rebuild the database from migrations
	$(BACKEND) uv run alembic downgrade base && uv run alembic upgrade head

# -----------------------------------------------------------------------------
# Quality
# -----------------------------------------------------------------------------
.PHONY: check
check: lint typecheck test ## Run every check CI runs

.PHONY: lint
lint: ## Lint every surface
	$(BACKEND) uv run ruff check .
	$(FRONTEND) npm run lint
	$(DESKTOP) dart format --output=none --set-exit-if-changed lib test

.PHONY: format
format: ## Format every surface
	$(BACKEND) uv run ruff format . && uv run ruff check --fix .
	$(FRONTEND) npm run format
	$(DESKTOP) dart format lib test

.PHONY: typecheck
typecheck: ## Type check every surface
	$(BACKEND) uv run mypy app
	$(FRONTEND) npx tsc -b
	$(DESKTOP) flutter analyze

.PHONY: test
test: ## Run backend tests (needs PostgreSQL and Redis), web and desktop tests
	$(BACKEND) uv run pytest -q
	$(FRONTEND) npm test
	$(DESKTOP) flutter test

.PHONY: test-cov
test-cov: ## Run backend tests with a coverage report
	$(BACKEND) uv run pytest --cov --cov-report=term-missing --cov-report=html

# -----------------------------------------------------------------------------
# Ledger 3 - the proof ledger contract
# -----------------------------------------------------------------------------
# Its own targets rather than folded into `check`, deliberately: the Rust
# toolchain is a heavy dependency that nobody working on the accounting core
# needs, and `make check` has to stay runnable by somebody who has never
# installed cargo. CI runs `contract-test` as a separate job for the same reason.

.PHONY: contract-test
contract-test: ## Run the proof-ledger contract's tests (28, adversarial)
	$(CONTRACT) cargo test

.PHONY: contract-lint
contract-lint: ## Clippy and format check on the contract
	$(CONTRACT) cargo clippy --all-targets -- -D warnings
	$(CONTRACT) cargo fmt --check

.PHONY: contract-build
contract-build: ## Build the contract to wasm (~15 KB)
	$(CONTRACT) stellar contract build

.PHONY: contract-up
contract-up: ## Build, deploy and wire up the contract in one command
	bash contracts/deploy.sh $(ARGS)

.PHONY: contract-key
contract-key: ## Create and fund a testnet deploy key (contract-up does this for you)
	stellar keys generate $(STELLAR_IDENTITY) --network $(CLI_NETWORK) --fund
	@echo "Deployer: $$(stellar keys address $(STELLAR_IDENTITY))"

.PHONY: contract-deploy
contract-deploy: contract-build ## Deploy only; prefer contract-up, which also writes .env
	$(CONTRACT) stellar contract deploy \
		--wasm target/wasm32v1-none/release/proof_ledger.wasm \
		--source $(STELLAR_IDENTITY) \
		--network $(CLI_NETWORK) \
		--alias proof_ledger
	@echo ""
	@echo "Put that contract id in .env as SOROBAN_CONTRACT_ID, then restart the API."
	@echo "Or just run 'make contract-up', which does all of it."

.PHONY: seal-worker
seal-worker: ## Run the seal worker on its own (SEAL_WORKER_ENABLED=false in the API)
	$(BACKEND) uv run python -m app.modules.attestation.worker

# The script exits non-zero while the wallet-interaction count is under ten, so it
# cannot be wired into CI and quietly pass while the submission claims otherwise.
# That is a fact about the *deployment*, not a failure of this target - the file
# was written either way - so the note is surfaced and the build is not broken.
.PHONY: evidence
evidence: ## Generate submission evidence (wallet interactions, feedback, usage)
	@$(BACKEND) uv run python scripts/submission_evidence.py --out ../docs/evidence.md || echo "  (see the note above - the file was still written)"

.PHONY: verify-proof
verify-proof: ## Check an exported proof bundle: make verify-proof f=bundle.json
	@test -n "$(f)" || (echo "Usage: make verify-proof f=path/to/bundle.json" && exit 1)
	$(BACKEND) uv run python scripts/verify_proof.py "$(abspath $(f))"

.PHONY: build
build: ## Build the web frontend for production
	$(FRONTEND) npm run build

.PHONY: build-desktop
build-desktop: ## Build a release desktop binary for this platform
	$(DESKTOP) flutter build $(DESKTOP_DEVICE) --release

# Fetched rather than committed. It is 25 MB of Microsoft's binary that git would then
# carry forever - and every future version would add another 25 MB that cannot be
# removed without rewriting history. The aka.ms link always serves the current build,
# which a committed copy would not.
#
# `stellar-erp.iss` fails the compile when it is missing, so a fresh clone is told what
# to run rather than silently producing an installer without it.
.PHONY: installer-deps
installer-deps: ## Fetch the Visual C++ redistributable the Windows installer bundles
	@if [ -f installer/VC_redist.x64.exe ]; then \
		echo "installer/VC_redist.x64.exe is already here - nothing to do."; \
	else \
		echo "Fetching VC_redist.x64.exe (~25 MB) into installer/ ..."; \
		curl -fL --progress-bar -o installer/VC_redist.x64.exe \
			https://aka.ms/vs/17/release/VC_redist.x64.exe && \
		echo "Done. Now compile installer/stellar-erp.iss with Inno Setup."; \
	fi

# -----------------------------------------------------------------------------
# Production
# -----------------------------------------------------------------------------
.PHONY: prod-up
prod-up: ## Start the production stack
	$(COMPOSE_PROD) up -d --build

.PHONY: prod-down
prod-down: ## Stop the production stack
	$(COMPOSE_PROD) down

.PHONY: prod-logs
prod-logs: ## Tail production logs
	$(COMPOSE_PROD) logs -f --tail=100

.PHONY: prod-migrate
prod-migrate: ## Apply migrations in production
	$(COMPOSE_PROD) run --rm migrate

.PHONY: prod-config
prod-config: ## Validate the production compose file
	$(COMPOSE_PROD) config --quiet && echo "docker-compose.prod.yml is valid"

# Backup and restore run entirely through the postgres container, so there is no
# script tree to keep in sync with the compose file and nothing extra to install
# on the server. `./backups` is the host side of the container's /backups mount.
.PHONY: backup
backup: ## Back up the production database to ./backups
	@mkdir -p backups
	@name="stellarerp-$$(date -u +%Y%m%dT%H%M%SZ).dump"; \
	echo "dumping to backups/$$name"; \
	$(COMPOSE_PROD) exec -T -e DUMP="/backups/$$name" postgres sh -c \
	  'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" --format=custom --file="$$DUMP.partial" \
	   && pg_restore --list "$$DUMP.partial" > /dev/null \
	   && mv "$$DUMP.partial" "$$DUMP"'; \
	echo "verified backups/$$name"

.PHONY: restore
restore: ## Restore from a backup: make restore f=backups/stellarerp-....dump
	@test -n "$(f)" || { echo 'Usage: make restore f=backups/stellarerp-....dump'; exit 1; }
	@test -f "$(f)" || { echo "no such file: $(f)"; exit 1; }
	@echo "This REPLACES the production database with $(f)."
	@read -p 'Type the database name to confirm: ' answer; \
	expected=$$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-); \
	test "$$answer" = "$$expected" || { echo 'aborted'; exit 1; }
	$(COMPOSE_PROD) stop backend
	$(COMPOSE_PROD) exec -T postgres sh -c \
	  'pg_restore -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" --clean --if-exists \
	     --single-transaction --no-owner' < "$(f)"
	$(COMPOSE_PROD) run --rm migrate
	$(COMPOSE_PROD) start backend
