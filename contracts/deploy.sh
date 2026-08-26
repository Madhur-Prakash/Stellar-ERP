#!/usr/bin/env bash
#
# Build, deploy and wire up the proof-ledger contract in one command.
#
#     make contract-up                                  # testnet, everything automatic
#     make contract-up ARGS="--network public --yes"    # mainnet
#     make contract-up ARGS="--dry-run"
#
# In order: runs the contract's tests, builds the wasm and reports its hash,
# creates and funds the deploy key if it does not exist, deploys, reads the
# contract's interface back off the live network to prove it is really there, and
# writes every setting the application needs into .env - backend and frontend
# both, because the browser reads the contract itself and cannot be handed those
# values at runtime.
#
# Two things it deliberately will not do without being told twice.
#
# It will not replace an existing contract id. A fresh contract is a fresh, empty
# book: organizations that have already sealed keep their seals on the OLD
# contract, which this install would stop looking at, and every proof already
# handed to a bank references an address nothing here uses any more. That is not
# a redeploy, it is an abandonment, so it needs --force.
#
# It will never overwrite ATTESTATION_NAMESPACE_SALT. An organization's on-chain
# identity is SHA-256(organization_id || salt) and the mapping is unrecoverable
# from the chain by design, so rotating the salt orphans every book already
# registered. Generated when absent, left alone forever after.
#
# Depends on bash, the stellar CLI, cargo, and the usual coreutils. Nothing from
# the application, so it runs before .env exists and without the backend
# installed.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(dirname -- "$SCRIPT_DIR")
CONTRACTS="$SCRIPT_DIR"

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
# Every function the contract is supposed to export. Checked against what the
# live network reports after deploying, so a stale wasm or a half-finished upload
# is caught here rather than by a seal failing at month end.
EXPECTED_FUNCTIONS=(register seal get latest verify history is_registered rotate)

# A Strkey contract id: 'C' plus 55 base32 characters.
ID_PATTERN='C[A-Z2-7]{55}'

WASM_REL="target/wasm32v1-none/release/proof_ledger.wasm"

# -----------------------------------------------------------------------------
# Output
# -----------------------------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
  GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
else
  BOLD=''; DIM=''; RED=''; GREEN=''; YELLOW=''; RESET=''
fi

STEP_N=0
STEP_TOTAL=7

step()  { STEP_N=$((STEP_N + 1)); printf '\n%s[%d/%d] %s%s\n' "$BOLD" "$STEP_N" "$STEP_TOTAL" "$1" "$RESET"; }
info()  { printf '       %s\n' "$1"; }
ok()    { printf '  %sok%s   %s\n' "$GREEN" "$RESET" "$1"; }
warn()  { printf '  %snote%s %s\n' "$YELLOW" "$RESET" "$1"; }

# One formatted exit path for everything an operator has to decide about. A deploy
# script that dies halfway with a bare error leaves somebody guessing which half
# already happened.
die() {
  printf '\n  %sfailed%s %s\n\n' "$RED" "$RESET" "$1" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Build, deploy and configure the proof-ledger contract.

usage: deploy.sh [options]

  --network testnet|public   the application's own vocabulary (default: testnet)
  --identity NAME            the stellar CLI key that signs (default: stellar-erp-deployer)
  --env PATH                 the env file to update (default: <repo>/.env)
  --set KEY=VALUE            any other env var to write; repeatable
  --force                    replace an existing contract id - abandons every existing book
  --yes                      do not prompt; required for --network public
  --skip-tests               do not run the contract's tests first
  --no-verify                do not read the contract back off the network
  --dry-run                  say what would happen and write nothing
  -h, --help                 this

examples:
  make contract-up
  make contract-up ARGS="--force"
  make contract-up ARGS="--network public --yes"
  make contract-up ARGS="--set SEAL_DAILY_HOUR=3 --set SEAL_MAX_BATCH=2000"
USAGE
}

# -----------------------------------------------------------------------------
# Arguments
# -----------------------------------------------------------------------------
NETWORK="${STELLAR_NETWORK:-testnet}"
IDENTITY="${STELLAR_IDENTITY:-stellar-erp-deployer}"
ENV_FILE="$REPO_ROOT/.env"
FORCE=false
ASSUME_YES=false
SKIP_TESTS=false
NO_VERIFY=false
DRY_RUN=false
EXTRA_KEYS=()
EXTRA_VALUES=()

while [ $# -gt 0 ]; do
  case "$1" in
    --network)    NETWORK="${2:-}"; shift 2 ;;
    --network=*)  NETWORK="${1#*=}"; shift ;;
    --identity)   IDENTITY="${2:-}"; shift 2 ;;
    --identity=*) IDENTITY="${1#*=}"; shift ;;
    --env)        ENV_FILE="${2:-}"; shift 2 ;;
    --env=*)      ENV_FILE="${1#*=}"; shift ;;
    --set)        pair="${2:-}"; shift 2
                  case "$pair" in *=*) ;; *) die "--set expects KEY=VALUE, got '$pair'" ;; esac
                  EXTRA_KEYS+=("${pair%%=*}"); EXTRA_VALUES+=("${pair#*=}") ;;
    --set=*)      pair="${1#*=}"; shift
                  case "$pair" in *=*) ;; *) die "--set expects KEY=VALUE, got '$pair'" ;; esac
                  EXTRA_KEYS+=("${pair%%=*}"); EXTRA_VALUES+=("${pair#*=}") ;;
    --force)      FORCE=true; shift ;;
    --yes|-y)     ASSUME_YES=true; shift ;;
    --skip-tests) SKIP_TESTS=true; shift ;;
    --no-verify)  NO_VERIFY=true; shift ;;
    --dry-run)    DRY_RUN=true; shift ;;
    -h|--help)    usage; exit 0 ;;
    *)            usage >&2; die "unknown option '$1'" ;;
  esac
done

# Take the CLI out of the ambient environment's hands.
#
# The CLI reads its own connection settings from the environment, and .env holds
# variables with the same names - including `SOROBAN_RPC_URL=`, which is
# deliberately blank so the backend falls back to the network default. Anyone who
# has sourced .env into their shell (`set -a; . .env`, direnv, a shell inside the
# API container) hands the CLI a *present but empty* rpc-url, and it stops with
#
#     error: rpc-url is used but network passphrase is missing
#
# which says nothing about where the rpc-url came from. The defaults above have
# already been read, and every call below passes --network explicitly, so ambient
# values can only do harm from here on.
unset SOROBAN_RPC_URL SOROBAN_NETWORK SOROBAN_NETWORK_PASSPHRASE       SOROBAN_ACCOUNT SOROBAN_SECRET_KEY       STELLAR_RPC_URL STELLAR_NETWORK STELLAR_NETWORK_PASSPHRASE       STELLAR_ACCOUNT STELLAR_SECRET_KEY 2>/dev/null || true

# The application calls mainnet 'public' - Stellar's own name for it, and what
# STELLAR_NETWORK in .env holds. The CLI calls it 'mainnet' and fails with
# "Failed to find config network for public", a long way from anything that
# explains why. So the two vocabularies are translated rather than passed through.
case "$NETWORK" in
  testnet) CLI_NETWORK=testnet; RPC_DEFAULT=https://soroban-testnet.stellar.org; EXPLORER=testnet; FUNDABLE=true ;;
  public)  CLI_NETWORK=mainnet; RPC_DEFAULT=https://mainnet.sorobanrpc.com;      EXPLORER=public;  FUNDABLE=false ;;
  *)       die "--network must be 'testnet' or 'public' (the application's names); got '$NETWORK'" ;;
esac

if $SKIP_TESTS; then STEP_TOTAL=6; fi

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
need() {
  command -v "$1" >/dev/null 2>&1 || die "\`$1\` is not on PATH.
       $2"
}

confirm() {
  if $ASSUME_YES; then return 0; fi
  [ -t 0 ] || die "$1
       Not a terminal, so nothing was assumed. Re-run with --yes."
  local reply
  read -r -p "       $1 [y/N] " reply
  case "$reply" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# Read one value out of the env file. Not a dotenv implementation - it answers a
# single question: does this key already hold a real value somebody chose? So it
# strips quotes and a trailing comment and stops there. The comment has to be
# preceded by whitespace, or a '#' inside a password would truncate it.
get_env() {
  local key=$1 line
  [ -f "$ENV_FILE" ] || return 0
  line=$(grep -E "^[[:space:]]*${key}[[:space:]]*=" -- "$ENV_FILE" 2>/dev/null | head -n 1 || true)
  [ -n "$line" ] || return 0
  local value=${line#*=}
  value=$(printf '%s' "$value" | sed -e 's/[[:space:]]\{1,\}#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  case "$value" in
    \"*\") value=${value#\"}; value=${value%\"} ;;
    \'*\') value=${value#\'}; value=${value%\'} ;;
  esac
  printf '%s' "$value"
}

# Values that mean "nobody has set this yet". .env.sample ships hints rather than
# blanks in a couple of places, and a hint left in place is indistinguishable
# from a decision unless it is named.
is_unset() {
  local value
  value=$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')
  case "$value" in
    ''|changeme*|change-me*|openssl*|'<'*|todo*|xxx*) return 0 ;;
    *) return 1 ;;
  esac
}

# Set a key in place, preserving the file's comments and its order.
#
# In place, rather than rewriting from a template, because .env is a file a human
# curates: it holds their database password, their Gmail token, and their own
# notes about why something is set the way it is. A deploy script that
# regenerated it would be one that quietly destroys the parts it does not know
# about. awk with the value in a variable, not sed, so a value containing '/' or
# '&' is written literally.
APPENDED_HEADER=false
set_env() {
  local key=$1 value=$2 tmp
  # Quote anything a dotenv reader would otherwise truncate: it stops an unquoted
  # value at the first space, and treats ` #` as the start of a comment.
  case "$value" in
    *[[:space:]]*|*'#'*) value="\"$value\"" ;;
  esac
  if $DRY_RUN; then return 0; fi
  tmp=$(mktemp) || die "could not create a temporary file"

  if grep -qE "^[[:space:]]*${key}[[:space:]]*=" -- "$ENV_FILE" 2>/dev/null; then
    awk -v k="$key" -v v="$value" '
      BEGIN { done = 0 }
      {
        if (!done && $0 ~ "^[[:space:]]*"k"[[:space:]]*=") { print k "=" v; done = 1 }
        else print
      }
    ' "$ENV_FILE" >"$tmp"   # no `--` here: awk has no end-of-options marker and
                            # would try to read a file called `--`
    mv -- "$tmp" "$ENV_FILE"
  else
    rm -f -- "$tmp"
    if ! $APPENDED_HEADER; then
      {
        printf '\n# %s\n' "==========================================================================="
        printf '# Written by contracts/deploy.sh\n'
        printf '# %s\n' "==========================================================================="
      } >>"$ENV_FILE"
      APPENDED_HEADER=true
    fi
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

# Report and apply one setting, saying whether it actually changed. "unchanged"
# matters here: it is the difference between a script that configured something
# and a script that confirmed it was already right.
apply() {
  local key=$1 value=$2 shown=${3:-$2} before
  before=$(get_env "$key")
  set_env "$key" "$value"
  if [ "$before" = "$value" ]; then
    printf '       %-9s %s=%s\n' "unchanged" "$key" "$shown"
  else
    printf '       %-9s %s=%s\n' "set" "$key" "$shown"
  fi
}

random_hex_32() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  elif [ -r /dev/urandom ]; then
    od -An -tx1 -N32 /dev/urandom | tr -d ' \n'
  else
    die "no way to generate a random salt - install openssl, or set ATTESTATION_NAMESPACE_SALT by hand"
  fi
}

# -----------------------------------------------------------------------------
# 1. Preflight
# -----------------------------------------------------------------------------
printf '%sproof_ledger -> %s%s\n' "$BOLD" "$NETWORK" "$RESET"
if $DRY_RUN; then warn "dry run: nothing will be deployed and nothing will be written"; fi

step "Checking the toolchain and the current configuration"

need stellar "Install it: cargo install --locked stellar-cli
       (or see developers.stellar.org/docs/tools/developer-tools)"
need cargo "Install Rust from https://rustup.rs - the version is pinned in rust-toolchain.toml"
need awk "It is part of coreutils; on Windows use the Git Bash that the Makefile resolves."
ok "$(stellar --version 2>&1 | head -n 1)"

if [ ! -f "$ENV_FILE" ]; then
  [ -f "$REPO_ROOT/.env.sample" ] || die "$ENV_FILE does not exist and neither does .env.sample"
  warn "$(basename -- "$ENV_FILE") does not exist; copying .env.sample"
  $DRY_RUN || cp -- "$REPO_ROOT/.env.sample" "$ENV_FILE"
  warn "that gives placeholder secrets - run \`make setup\` before serving traffic"
fi

EXISTING_ID=$(get_env SOROBAN_CONTRACT_ID)
if printf '%s' "$EXISTING_ID" | grep -qE "^${ID_PATTERN}\$"; then
  if ! $FORCE; then
    die "$(basename -- "$ENV_FILE") already points at $EXISTING_ID.

       A new contract is a new, empty book. Organizations that have already
       sealed keep their seals on the OLD contract, which this install would
       stop looking at, and every proof already sent to a counterparty would
       reference an address nothing here uses.

       If that is genuinely what you want, re-run with --force."
  fi
  warn "replacing $EXISTING_ID because --force was given"
elif [ -n "$EXISTING_ID" ]; then
  warn "the current SOROBAN_CONTRACT_ID is not a valid contract id ('$EXISTING_ID'); replacing it"
fi

if [ "$NETWORK" = public ]; then
  confirm "Deploying to mainnet spends real XLM and is where these books live permanently. Continue?" \
    || die "stopped at the mainnet confirmation"
fi

# -----------------------------------------------------------------------------
# 2. Tests
# -----------------------------------------------------------------------------
if ! $SKIP_TESTS; then
  step "Running the contract's tests"
  if $DRY_RUN; then
    info "would run: cargo test"
  else
    ( cd -- "$CONTRACTS" && cargo test )
    ok "the contract's tests pass"
  fi
fi

# -----------------------------------------------------------------------------
# 3. Build
# -----------------------------------------------------------------------------
step "Building the wasm"
WASM="$CONTRACTS/$WASM_REL"
if $DRY_RUN; then
  info "would run: stellar contract build"
  WASM_HASH="(dry run)"
else
  BUILD_OUT=$( cd -- "$CONTRACTS" && stellar contract build 2>&1 ) || die "the build failed:
$(printf '%s' "$BUILD_OUT" | sed 's/^/       | /')"

  # The CLI reports the path it wrote and the hash of what it wrote. The path is
  # parsed but not trusted: falling back to the conventional location means a
  # reworded log line cannot break the deploy.
  WASM_HASH=$(printf '%s' "$BUILD_OUT" | sed -n 's/^[[:space:]]*Wasm Hash:[[:space:]]*//p' | head -n 1)
  REPORTED=$(printf '%s' "$BUILD_OUT" | sed -n 's/^[[:space:]]*Wasm File:[[:space:]]*//p' | head -n 1)
  REPORTED=${REPORTED%% (*}
  REPORTED=${REPORTED//\\//}
  if [ -n "$REPORTED" ] && [ -f "$CONTRACTS/$REPORTED" ]; then
    WASM="$CONTRACTS/$REPORTED"
  fi
  [ -f "$WASM" ] || die "the build reported success but $WASM is not there"
  [ -n "$WASM_HASH" ] || die "the build did not report a wasm hash:
$(printf '%s' "$BUILD_OUT" | sed 's/^/       | /')"

  ok "$(basename -- "$WASM"), $(wc -c <"$WASM" | tr -d ' ') bytes"
  ok "wasm hash $WASM_HASH"
  info "${DIM}anyone can rebuild from this source and must get the same hash${RESET}"
fi

# -----------------------------------------------------------------------------
# 4. Deploy key
# -----------------------------------------------------------------------------
step "Preparing the deploy key"
if $DRY_RUN; then
  info "would ensure the key '$IDENTITY' exists on $CLI_NETWORK"
  DEPLOYER="(dry run)"
else
  if stellar keys ls 2>/dev/null | grep -qxF -- "$IDENTITY"; then
    ok "using existing key $IDENTITY"
  else
    info "no key named '$IDENTITY'; generating one"
    if $FUNDABLE; then
      stellar keys generate "$IDENTITY" --network "$CLI_NETWORK" --fund
    else
      stellar keys generate "$IDENTITY" --network "$CLI_NETWORK"
    fi
    ok "created $IDENTITY"
  fi

  DEPLOYER=$(stellar keys address "$IDENTITY" 2>&1 | tr -d '\r' | tail -n 1 | tr -d '[:space:]')
  case "$DEPLOYER" in
    G*) ;;
    *) die "could not read an address for '$IDENTITY'; got '$DEPLOYER'" ;;
  esac

  # Friendbot refuses an account it has already funded, and that refusal is not a
  # problem - the account exists, which is all this needs.
  if $FUNDABLE; then
    stellar keys fund "$IDENTITY" --network "$CLI_NETWORK" >/dev/null 2>&1 || true
  fi
  ok "deploying as $DEPLOYER"
fi

# -----------------------------------------------------------------------------
# 5. Deploy
# -----------------------------------------------------------------------------
step "Deploying to $NETWORK"
if $DRY_RUN; then
  info "would run: stellar contract deploy ..."
  CONTRACT_ID="C000000000000000000000000000000000000000000000000000000"
else
  DEPLOY_OUT=$(
    cd -- "$CONTRACTS" && stellar contract deploy \
      --wasm "$WASM" \
      --source "$IDENTITY" \
      --network "$CLI_NETWORK" \
      --alias proof_ledger 2>&1
  ) || die "the deploy failed:
$(printf '%s' "$DEPLOY_OUT" | sed 's/^/       | /')"

  # The last match: the CLI logs the transaction and the alias around it, and the
  # id is the final thing it says.
  CONTRACT_ID=$(printf '%s' "$DEPLOY_OUT" | grep -oE "$ID_PATTERN" | tail -n 1 || true)
  [ -n "$CONTRACT_ID" ] || die "the deploy produced no contract id:
$(printf '%s' "$DEPLOY_OUT" | sed 's/^/       | /')"
  ok "contract $CONTRACT_ID"
fi

# -----------------------------------------------------------------------------
# 6. Verify
# -----------------------------------------------------------------------------
step "Reading the contract back off the network"
if $DRY_RUN || $NO_VERIFY; then
  info "skipped"
else
  # A deploy that returns an id has not proved anything yet - the id is derived
  # locally. This asks the network what is actually at that address, which is the
  # cheapest possible answer to "did the thing I built end up where I think it did".
  IFACE=$(stellar contract info interface --network "$CLI_NETWORK" --id "$CONTRACT_ID" 2>&1) \
    || die "the contract could not be read back at $CONTRACT_ID:
$(printf '%s' "$IFACE" | sed 's/^/       | /')"

  MISSING=()
  for fn in "${EXPECTED_FUNCTIONS[@]}"; do
    printf '%s' "$IFACE" | grep -qF "fn ${fn}(" || MISSING+=("$fn")
  done
  if [ ${#MISSING[@]} -gt 0 ]; then
    die "the contract at that address is not the one just built - missing ${MISSING[*]}"
  fi
  ok "the network reports all ${#EXPECTED_FUNCTIONS[@]} functions at $CONTRACT_ID"
fi

# -----------------------------------------------------------------------------
# 7. Configure
# -----------------------------------------------------------------------------
step "Writing $(basename -- "$ENV_FILE")"

# One backup, overwritten each run. The one value that could not be reconstructed
# - the namespace salt - is never written over in the first place, so a single
# generation of backup is enough.
if ! $DRY_RUN && [ -f "$ENV_FILE" ]; then
  cp -- "$ENV_FILE" "$ENV_FILE.bak"
fi

SALT=$(get_env ATTESTATION_NAMESPACE_SALT)
if is_unset "$SALT"; then
  SALT=$(random_hex_32)
  warn "generated ATTESTATION_NAMESPACE_SALT - back it up with ENCRYPTION_KEY"
  warn "losing or changing it orphans every book on chain, permanently"
  GENERATED_SALT=true
else
  ok "ATTESTATION_NAMESPACE_SALT already set; left untouched"
  GENERATED_SALT=false
fi

apply ATTESTATION_ENABLED true
apply STELLAR_NETWORK "$NETWORK"
apply SOROBAN_CONTRACT_ID "$CONTRACT_ID"

# The browser reads the contract itself - that is the entire point of the
# verifier - so it needs its own copies. These are inlined at build time, which is
# why the summary below says to rebuild the web client.
apply VITE_STELLAR_NETWORK "$NETWORK"
apply VITE_SOROBAN_CONTRACT_ID "$CONTRACT_ID"

CURRENT_RPC=$(get_env VITE_SOROBAN_RPC_URL)
if is_unset "$CURRENT_RPC" \
  || [ "$CURRENT_RPC" = https://soroban-testnet.stellar.org ] \
  || [ "$CURRENT_RPC" = https://mainnet.sorobanrpc.com ]; then
  # A default belonging to the *other* network is a leftover, not a choice.
  apply VITE_SOROBAN_RPC_URL "$RPC_DEFAULT"
else
  info "leaving VITE_SOROBAN_RPC_URL as $CURRENT_RPC"
fi

if $GENERATED_SALT; then
  apply ATTESTATION_NAMESPACE_SALT "$SALT" "$(printf '%s' "$SALT" | cut -c1-8)... (64 hex chars)"
fi

i=0
while [ "$i" -lt "${#EXTRA_KEYS[@]}" ]; do
  apply "${EXTRA_KEYS[$i]}" "${EXTRA_VALUES[$i]}"
  i=$((i + 1))
done

# A small, committable record of what was deployed. Committed on purpose: the
# contract id and the wasm hash are the two things somebody needs in order to
# confirm that the code in this repository is the code that is running, and a
# claim like that is worth nothing if it lives only in a terminal that has since
# been closed.
RECORD="$CONTRACTS/deployments/$NETWORK.json"
if ! $DRY_RUN; then
  mkdir -p -- "$(dirname -- "$RECORD")"
  cat >"$RECORD" <<JSON
{
  "network": "$NETWORK",
  "contract_id": "$CONTRACT_ID",
  "wasm_hash": "$WASM_HASH",
  "deployer": "$DEPLOYER",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "reproduce": "make contract-build  # must print this wasm_hash"
}
JSON
fi

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
if $DRY_RUN; then
  printf '\n%sDry run complete.%s\n' "$BOLD" "$RESET"
else
  printf '\n%s%sDone.%s\n' "$BOLD" "$GREEN" "$RESET"
fi
printf '       contract   %s\n' "$CONTRACT_ID"
printf '       wasm hash  %s\n' "$WASM_HASH"
printf '       deployer   %s\n' "$DEPLOYER"
printf '       explorer   https://stellar.expert/explorer/%s/contract/%s\n' "$EXPLORER" "$CONTRACT_ID"
printf '       recorded   contracts/deployments/%s.json\n' "$NETWORK"
cat <<NEXT

${BOLD}Next${RESET}
       1. Restart the API so it reads the new contract id:  make up
       2. Rebuild the web client - VITE_* values are inlined at build
          time, so a restart alone will not pick them up:   make build
       3. Sign in, open Trust, and switch sealing on. That funds the
          organization's own signer and registers its book.
NEXT
if $DRY_RUN; then
  printf '       %sNothing above was actually deployed or written.%s\n\n' "$DIM" "$RESET"
fi
exit 0
