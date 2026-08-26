<div align="center">

# Deployment

**Self-hosting on a VPS: configuration, the proxy you supply, backups, updates.**

![Stack](https://img.shields.io/badge/services-postgres_redis_migrate_backend_frontend-2496ED?style=flat-square&logo=docker&logoColor=white)
![TLS](https://img.shields.io/badge/TLS-terminated_in_front-D29922?style=flat-square)
![Self-hosted](https://img.shields.io/badge/self--hosted-your_server-6E7681?style=flat-square)

<!-- nav:start -->
[Docs](README.md) · [Spec](spec.md) · [Architecture](architecture.md) · [Database](database.md) · [Accounting](accounting.md) · [Proof ledger](attestation.md) · [API](api.md) · [Security](security.md) · [Audit](security-audit.md) · [Commands](commands.md) · [Development](development.md) · **Deployment**
<!-- nav:end -->

</div>

---

Self-hosted on a single VPS with Docker Compose. Everything below assumes Ubuntu
22.04+ or Debian 12+.

**Minimum viable server:** 2 vCPU, 4 GB RAM, 40 GB SSD. The compose file's
resource limits assume roughly that; PostgreSQL's `shared_buffers` should be
raised to about 25% of RAM on anything larger.

> **There is no proxy in this stack - you bring one.**
>
> `docker-compose.prod.yml` runs **postgres, redis, migrate, backend, frontend** and
> nothing else. It terminates no TLS, holds no certificates, and binds neither 80 nor
> 443. `backend` and `frontend` publish plain HTTP on `127.0.0.1` (`PUBLISH_ADDR`,
> `BACKEND_PORT`, `FRONTEND_PORT`), so a fresh `up -d` is reachable only from the host
> itself until you put something in front of it.
>
> TLS, certificates and any edge rate limiting belong to a terminator you operate -
> Caddy, Traefik, or a tunnel - forwarding to the two ports below.
>
> **Something has to sit in front, and the application enforces it.** Production boot
> requires every `CORS_ORIGINS` entry and `FRONTEND_URL` to be `https://`, and nothing
> in this stack terminates TLS - so a deployment with nothing in front does not merely
> run insecurely, it refuses to start. That is deliberate: a credentialled session over
> plain HTTP is a session anyone on the path can read.
>
> Set **`TRUSTED_PROXY_HOPS`** to the number of hops in front of the API - one router or
> one terminator is `1`, a CDN in front of that is `2`. **`0` is not accepted**: the
> setting is `ge=1`, because there is no supported production shape with nothing in
> front. Get it wrong and every IP-based control reads an address the caller chose;
> [Security](security.md#who-is-calling---client-address-resolution) explains why.

---

## 1. Prepare the server

```bash
# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER" && newgrp docker

# Firewall - only SSH and HTTP(S) reach the host.
# PostgreSQL and Redis are never published; they live on the internal Docker
# network, which is what stops a misconfigured rule exposing the database.
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

# Unattended security updates
sudo apt install -y unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades
```

---

## 2. Configure

```bash
sudo mkdir -p /srv/stellarerp && sudo chown "$USER" /srv/stellarerp
git clone <repo> /srv/stellarerp && cd /srv/stellarerp
cp .env.sample .env
```

Generate real secrets - do not hand-write them:

```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(64))"
python3 -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
python3 -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(32))"
python3 -c "import secrets; print('REDIS_PASSWORD=' + secrets.token_urlsafe(32))"
```

Required production values:

```env
ENVIRONMENT=production
DEBUG=false
SECRET_KEY=<64-byte random>
ENCRYPTION_KEY=<Fernet key>
POSTGRES_PASSWORD=<strong>
REDIS_PASSWORD=<strong>
CORS_ORIGINS=https://app.yourdomain.com
ALLOWED_HOSTS=app.yourdomain.com
FRONTEND_URL=https://app.yourdomain.com
PUBLIC_API_URL=https://app.yourdomain.com
LOG_JSON=true

# How many proxies sit in front of the API. Wrong here means every IP-based
# control reads an address the caller supplied. See docs/security.md.
TRUSTED_PROXY_HOPS=1

# Where the two HTTP services bind on the host. Loopback unless your proxy runs
# on another machine and a firewall is doing the work instead.
PUBLISH_ADDR=127.0.0.1
BACKEND_PORT=8000
FRONTEND_PORT=8080

# Email. Base64 of a pickled Credentials with the gmail.send scope. Produce it with
# `uv run python scripts/mint_gmail_token.py`, and keep it in a secret store.
GMAIL_CREDENTIALS_B64=<output of scripts/mint_gmail_token.py>
GMAIL_SENDER=no-reply@yourdomain.com
EMAIL_FROM_NAME=Stellar ERP
```

### The proof ledger

Optional, and **off by default** in the sense that `ATTESTATION_ENABLED=false`
removes the subsystem entirely - no worker, no chain calls, no Trust screen. Nothing
else in the deployment depends on it.

```env
ATTESTATION_ENABLED=true
STELLAR_NETWORK=testnet          # or: public
SOROBAN_CONTRACT_ID=<your deployment, or the published testnet one>
SOROBAN_RPC_URL=                 # blank uses the network's default public RPC

# The namespace salt. 32 random bytes, generated once, then NEVER CHANGED.
ATTESTATION_NAMESPACE_SALT=<openssl rand -hex 32>

SEAL_WORKER_ENABLED=true
SEAL_WORKER_INTERVAL_SECONDS=60  # how often it looks for work
SEAL_DAILY_HOUR=1                # local hour the daily cadence fires
SEAL_MAX_BATCH=5000              # entries per seal

RATE_LIMIT_PUBLIC_VERIFY=60/minute

# Baked into the frontend bundle at build time, so the browser can read the
# contract itself. They must match the three values above.
VITE_STELLAR_NETWORK=testnet
VITE_SOROBAN_CONTRACT_ID=<same id>
VITE_SOROBAN_RPC_URL=https://soroban-testnet.stellar.org
```

> **`ATTESTATION_NAMESPACE_SALT` is a one-way door.** An organization's on-chain
> identity is `SHA-256(organization_id ‖ salt)`. Change the salt and every existing
> book becomes unreachable - the contract still holds the seals, under a namespace
> nothing can now compute, and every proof already handed to a bank stops resolving.
> There is no migration, because the point of the salt is that the mapping cannot be
> recovered from the chain. **Generate it once, back it up with `ENCRYPTION_KEY`, and
> treat losing it as losing the seals.**

> **Leave it on testnet until you have watched it seal.** On mainnet, `POST
> /attestation/enable` funds a real account with real XLM, and the address it creates
> is where that organization's book lives permanently.

Deploying your own contract is **one** command, and reproducible:

```bash
make contract-up                                  # testnet
make contract-up ARGS="--network public --yes"    # mainnet
```

It tests, builds (printing the wasm hash, which is deterministic), creates and funds
a key if needed, deploys, reads the contract back off the network to confirm what is
at that address, and writes all six chain settings into `.env` - including
`ATTESTATION_NAMESPACE_SALT` if it is still blank. It will not replace a contract id
that is already in use without `ARGS="--force"`, and it never overwrites a salt that
is already set.

On mainnet it will not create the funding for you: generate and fund the key
yourself first, then pass `ARGS="--network public --yes --identity my-mainnet-key"`.

### Monitoring

```env
SENTRY_DSN=                      # blank = nothing leaves this machine
SENTRY_TRACES_SAMPLE_RATE=0.1
SENTRY_PROFILES_SAMPLE_RATE=0.0
VITE_SENTRY_DSN=
USAGE_ANALYTICS_ENABLED=true     # first-party, stays in your PostgreSQL
```

Error reports carry **no request bodies and no SQL parameters** - see
[Error reporting](security.md#error-reporting) for what is stripped and why. With the
DSN blank, the boot log says so explicitly, so a self-hosted operator can confirm
nothing is being sent rather than assume it.

```bash
chmod 600 .env
```

> **Where `GMAIL_CREDENTIALS_B64` comes from.** It is base64 of a pickled OAuth
> `Credentials` with the `gmail.send` scope - minted once from a **Desktop app** OAuth
> client's `credentials.json`, not an API key:
>
> ```bash
> cd backend
> uv sync --group dev
> uv run python scripts/mint_gmail_token.py path/to/credentials.json
> ```
>
> Consent as the mailbox that should send, and paste the printed line. The full
> walkthrough - the Google Cloud setup, the by-hand version of the two scripts, and why the
> consent screen must be **published** or the token dies after seven days - is
> [Getting a real Gmail token](development.md#getting-a-real-gmail-token).

The app **validates this at boot and refuses to start** if `SECRET_KEY` is a
placeholder, `DEBUG` is true, CORS is `*`, `ENCRYPTION_KEY` is missing, or the
database password is still a default. Crashing at boot beats silently serving
traffic with a placeholder signing key.

> `PUBLIC_API_URL` is baked into the frontend bundle at **build** time, because
> Vite inlines `VITE_*` values. Changing it later requires a rebuild, not a
> restart.

---

## 3. TLS - in front of the stack

Nothing in this repository terminates TLS. Point an A record at the server and let
your proxy handle the certificate; the two upstreams it needs are the ports from
step 2.

| Public path | Forward to | Notes |
| --- | --- | --- |
| `/api/`, `/health/` | `127.0.0.1:8000` | The API. Long-lived request budget on document upload - the OCR path is slow by nature |
| everything else | `127.0.0.1:8080` | The built SPA, served as static files by an unprivileged user |

Whatever proxies must, at minimum:

- **Forward the real client address** and append rather than replace `X-Forwarded-For`,
  then set `TRUSTED_PROXY_HOPS` to match. The application counts hops from the right
  precisely because the left-most entry is the one a caller can write.
- **Preserve the `Origin` header.** Every state-changing method is checked against
  `CORS_ORIGINS`, and stripping it turns a working write into a 403.
- **Not buffer responses indefinitely**, or report exports and file downloads stall.

A minimal Caddy file does all of that with no tuning, which is why it is the easiest
thing to reach for on a single host:

```caddyfile
app.yourdomain.com {
    handle /api/*   { reverse_proxy 127.0.0.1:8000 }
    handle /health/* { reverse_proxy 127.0.0.1:8000 }
    handle          { reverse_proxy 127.0.0.1:8080 }
}
```

> **The certificate is yours to keep alive.** Nothing in this repository issues or renews
> one, so whatever you put in front owns that job - Caddy does it automatically, and a
> tunnel that terminates TLS for you removes the question entirely.

---

## 4. Launch

> [!IMPORTANT]
> **`docker compose up -d` does not start production.** With no `-f`, Docker Compose
> reads `docker-compose.yml` - the *development* stack, with hot reload, the Vite dev
> server, and ports published for local work. Production needs the file named every
> time:
>
> ```bash
> docker compose -f docker-compose.prod.yml up -d --build
> ```

### Which file is which

| Command | File | What it starts |
| --- | --- | --- |
| `docker compose up -d`<br>`make up` | `docker-compose.yml` | **Development.** Source bind-mounted, `uvicorn --reload`, the Vite dev server, Postgres and Redis published to the host |
| `docker compose -f docker-compose.prod.yml up -d`<br>`make prod-up` | `docker-compose.prod.yml` | **Production.** Built images, no reload, resource limits, read-only containers, Postgres and Redis on an internal network only |

`make` is the shorter spelling of both - `make up` and `make prod-up` wrap exactly the
commands above, so there is no third behaviour to learn.

**The two stacks cannot collide.** Each compose file pins its own project name -
`stellarerp` and `stellarerp-prod` - so containers, networks and volumes are namespaced
separately. Two consequences worth knowing before they surprise you:

- **They do not share a database.** `stellarerp_postgres-data` and
  `stellarerp-prod_postgres-data` are different volumes. Bringing up the production
  stack on a machine where you have been developing gives you an **empty** database, not
  your development data.
- **Both can run at once**, and will fight over host ports if you let them. Development
  publishes 8000 and 5173; production publishes `BACKEND_PORT` and `FRONTEND_PORT` on
  `PUBLISH_ADDR`. Stop one, or move the ports.

### Starting it

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
curl -fsS http://127.0.0.1:8000/health/ready      # direct, before the proxy
curl -fsS https://app.yourdomain.com/health/ready # through it
```

Every later command in this guide carries the same `-f docker-compose.prod.yml`. If you
find yourself typing them often, `export COMPOSE_FILE=docker-compose.prod.yml` in that
shell makes the flag implicit - but do it deliberately, because from then on a bare
`docker compose down` in that terminal stops **production**.

Migrations run as a **one-shot `migrate` service** that the API waits on via
`service_completed_successfully`. That ordering is what stops a scaled-out API racing
to apply the same migration, and it is why a failed migration leaves the previous
version serving traffic untouched.

Register the first account at `https://app.yourdomain.com/register`. The first user
to create an organization becomes its owner.

---

## 5. Backups

`make backup` runs `pg_dump` inside the postgres container and writes to `./backups`,
which is that container's `/backups` mount. There is no script tree to keep in step
with the compose file and nothing extra to install on the server.

```bash
make backup      # -> backups/stellarerp-20260807T020000Z.dump

# Nightly at 02:00
(crontab -l 2>/dev/null; echo "0 2 * * * cd /srv/stellarerp && make backup >> logs/backup.log 2>&1") | crontab -
```

Two details that matter more than they look:

- The dump is written as `.partial` and renamed only on success, so an interrupted
  run never leaves a truncated file that looks like a valid backup.
- It is **verified immediately** with `pg_restore --list` before that rename. A
  backup that has never been read back is a guess, not a backup.

Custom format, not plain SQL, because `pg_restore` can then restore selectively - a
plain dump is all or nothing.

Restoring:

```bash
make restore f=backups/stellarerp-20260807T020000Z.dump
```

It requires typing the database name to confirm, stops the API first, restores
inside a single transaction, re-applies any migrations newer than the backup, and
starts the API again.

**Copy backups off the machine.** A backup on the same disk as the database does
not survive the failure it exists for:

```bash
0 3 * * * rclone sync /srv/stellarerp/backups remote:stellarerp-backups
```

Uploaded documents are compressed **into PostgreSQL**, so one dump captures the
ledger and the scans supporting it at a single consistent moment. There is no second
volume to remember.

**What a dump does not contain, if the proof ledger is on.** Two values live only in
`.env`, and losing either is unrecoverable in a way a database restore cannot fix:

| | Losing it means |
| --- | --- |
| `ENCRYPTION_KEY` | Every 2FA secret, bank account number, and **signer secret** in the dump is ciphertext nobody can read. The organization can no longer seal |
| `ATTESTATION_NAMESPACE_SALT` | Every book on chain sits under a namespace nothing can recompute. The seals are still there and permanently unreachable, and every proof already given to a bank stops resolving |

Back both up with the dumps and separately from the server. The seals themselves need
no backup - they are on a public ledger - which is the one part of this system a disk
failure cannot touch.

---

## 6. Deploying updates

**There is no deploy workflow in this repository.** `.github/workflows/ci.yml` runs
checks and builds nothing that gets shipped - the stack builds from source on your own
server. An image built in CI would be a second artefact nobody deploys, misleading the
moment it diverged.

### Self-hosted

```bash
cd /srv/stellarerp
make backup                                                   # always first
git pull --ff-only
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml run --rm migrate     # separate step, on purpose
docker compose -f docker-compose.prod.yml up -d --no-deps backend frontend
curl -fsS http://127.0.0.1:8000/health/ready
```

**Migrations are a separate step on purpose.** If one fails, the running version
keeps serving traffic untouched. Rolling out first and migrating after would leave
new code pointed at an old schema - the failure mode that takes an application down
rather than just stopping a deploy.

### The gap, and why it is honest to name it

The old stack ran two API replicas behind an edge that could route to either, so
`order: start-first` gave a genuinely zero-downtime rollout. With the edge gone, each
service publishes a host port and therefore runs **one** container - two cannot hold
the same port, so the replacement starts only after the old one has stopped. A
redeploy is a gap of a few seconds.

To get zero-downtime back, put a proxy in front, change the port mappings to a range
(`'8000-8003:8000'`), raise `replicas`, and restore `order: start-first`.

Either way, one constraint on migrations survives and is worth keeping even when
nothing overlaps: a migration should be **backward-compatible with the previous
release**, so a rollback does not need a schema change to go with it. Adding a
nullable column is safe; dropping a column the old code still reads is not. Renames
become expand → migrate → contract across two deploys.

### Rolling back

```bash
git checkout <previous-tag>
docker compose -f docker-compose.prod.yml up -d --build backend frontend
```

If the schema also changed: `alembic downgrade -1`. Verify reversibility locally with
`make db-check` before you need it - **CI does not check this**, because there is no
backend job.

---

## 7. Operating it

### Logs

logifyx writes JSON in production (`LOG_JSON=true`), to both stdout and rotating
files in `./logs`.

```bash
docker compose -f docker-compose.prod.yml logs -f --tail=100 backend
tail -f logs/stellarerp.log | jq 'select(.levelname == "ERROR")'
tail -f logs/stellarerp.log | jq 'select(.request_id == "01930f4c-...")'   # one request
```

Every line carries `request_id`, and authenticated lines carry `user_id` and
`org_id`. Audit rows store the same `request_id`, so a business event pivots
directly to its operational log lines.

Container logs are capped at 10 MB × 3 files per service. Without that, logs grow
without bound and eventually fill the disk - the most common way a small VPS dies.

### Health

| Endpoint | Use |
| --- | --- |
| `/health/live` | Liveness. No dependency checks, deliberately |
| `/health/ready` | Readiness. 503 when PostgreSQL or Redis is down |
| `/health` | Human-readable summary |

Point external monitoring at `/health/ready`.

### Common problems

**Backend will not start** - almost always failed config validation. The error
names every problem:

```bash
docker compose -f docker-compose.prod.yml logs backend | head -30
```

**502 or 504 from your proxy** - the backend is not healthy yet, or migrations
failed. Check the container before you touch the proxy config:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs migrate
curl -fsS http://127.0.0.1:8000/health/ready       # bypasses the proxy entirely
```

If that `curl` succeeds and the public URL does not, the fault is in the proxy or
the firewall, not in this stack.

**403 on every write, reads fine** - the proxy is stripping `Origin`. Every
state-changing method is checked against `CORS_ORIGINS`; a proxy that drops the
header makes each one look cross-site.

**Rate limits trigger for everyone at once** - `TRUSTED_PROXY_HOPS` does not match
the topology, so every request resolves to the proxy's own address and shares one
budget.

**No emails** - check `GMAIL_CREDENTIALS_B64` is set (unset means log-only, which
is the development default and a common production oversight). A failure logs
Google's own wording, including `invalid_grant` for a revoked refresh token and
an insufficient-scope message for a token without `gmail.send`:

```bash
docker compose -f docker-compose.prod.yml logs backend | grep -i "email"
```

**`invalid_grant`** - Google has rejected the refresh token itself, so the value is
dead rather than misconfigured and no restart or retry recovers it. Mint a new one
with `uv run python scripts/mint_gmail_token.py` and redeploy the secret. Then fix
the cause, because a replacement token dies the same way: most often the OAuth
consent screen is still in **Testing**, where Google expires every refresh token
after 7 days - publish the app to stop it. Otherwise the token was revoked from the
account's third-party access, the account password changed, the OAuth client was
deleted or recreated, or the host clock has drifted far enough for Google to reject
the assertion (`timedatectl status`).

**"Session is no longer valid" immediately after signing in** - the token epoch
was bumped, or the client and server clocks disagree. Check `timedatectl`.

**Sealing has silently stopped** - the figure to watch is `days_unsealed` on
`GET /attestation/status`, not the seal count. A count that has stopped rising looks
identical to sealing that is simply quiet, and the Trust screen leads with the age of
the backlog for exactly that reason. When it climbs:

```bash
curl -fsS http://127.0.0.1:8000/api/v1/attestation/chain/health   # is the RPC reachable?
docker compose -f docker-compose.prod.yml logs backend | grep -i seal
```

`chain/health` is deliberately **not** part of `/health/ready` - an unreachable RPC
must never make this deployment look unhealthy and get its containers killed.

**Seals stuck in `submitted`** - a submission left the process and no verdict came
back, which is the one genuinely ambiguous failure here. Do **not** resubmit by hand.
`POST /attestation/reconcile` reads `latest()` from the contract and corrects local
state from it; the chain is the authority, and the contract refuses a duplicate
sequence anyway. See
[the ambiguous failure](attestation.md#the-ambiguous-failure).

**`SequenceOutOfOrder` in the logs** - on a retry this is **success in disguise**: a
previous attempt landed. Reconcile rather than investigate.

### Scaling

The API process is stateless and holds no session state, so replicas need no
coordination - what is missing is something to balance across them. Scaling out is
therefore three coupled changes, not a flag:

1. Put a reverse proxy in front, if there is not one already.
2. Change the published port to a range in `docker-compose.prod.yml`:
   `'${PUBLISH_ADDR:-127.0.0.1}:8000-8003:8000'`.
3. Raise `deploy.replicas`, and restore `update_config: order: start-first` so a
   rollout overlaps again.

Beyond one host, the ordered next steps are: move PostgreSQL to managed hosting with
a read replica, put PgBouncer in front of it, add Redis Sentinel, and serve static
assets from a CDN.

---

## 8. Pre-flight checklist

**The stack**

- [ ] `.env` has real secrets; `chmod 600`
- [ ] `ENVIRONMENT=production`, `DEBUG=false`
- [ ] `CORS_ORIGINS` and `ALLOWED_HOSTS` name the real domain, no wildcards
- [ ] `ENCRYPTION_KEY` set (2FA secrets and bank account numbers are encrypted at rest)
- [ ] `GMAIL_CREDENTIALS_B64` and `GMAIL_SENDER` set, and a test email received
- [ ] `/docs` returns 404 in production
- [ ] First owner account created

**The proof ledger, if `ATTESTATION_ENABLED=true`**

- [ ] `ATTESTATION_NAMESPACE_SALT` generated **once**, and backed up with
      `ENCRYPTION_KEY` - changing it orphans every seal already written
- [ ] `SOROBAN_CONTRACT_ID` matches `VITE_SOROBAN_CONTRACT_ID`, and
      `STELLAR_NETWORK` matches `VITE_STELLAR_NETWORK`
- [ ] `GET /attestation/chain/health` reports the RPC reachable
- [ ] One seal written end to end, and its transaction found on the explorer
- [ ] A proof bundle exported and verified at `/verify` **from a different browser,
      signed out** - that is the only path that proves the verifier needs nothing
      from you
- [ ] `days_unsealed` monitored, not the seal count

**The edge you supplied**

- [ ] TLS certificate issued and renewing; HTTP redirects to HTTPS
- [ ] `TRUSTED_PROXY_HOPS` matches the number of proxies in front of the API
- [ ] The proxy forwards `Origin` unmodified, and appends to `X-Forwarded-For`
- [ ] `PUBLISH_ADDR` is `127.0.0.1` unless the proxy is on another machine
- [ ] `ufw` allows only 22, 80, 443 - not 8000 or 8080
- [ ] PostgreSQL and Redis not published to the host (`docker compose ps` shows no
      host ports for them)

**The part that saves you**

- [ ] Nightly `make backup` scheduled **and a restore rehearsed**
- [ ] Backups replicated off the machine
- [ ] External monitoring on `/health/ready`
- [ ] SSH key-only authentication; password login disabled

The restore rehearsal is the one people skip, and it is the one that matters.

<!-- related:start -->

---

## Related reading

- [Security](security.md) - what to verify is switched on before opening a port
- [Database](database.md) - backup and restore mechanics in detail
- [Development](development.md) - the local stack this mirrors
- [Proof ledger](attestation.md) - what sealing needs from a deployment, and what to watch once it is on

[All documentation](README.md)
<!-- related:end -->
