<div align="center">

# Security audit

**Sixteen findings against running code - each with its fix, and how to confirm it.**

![Critical](https://img.shields.io/badge/critical-1-DA3633?style=flat-square)
![High](https://img.shields.io/badge/high-2-F85149?style=flat-square)
![Medium](https://img.shields.io/badge/medium-5-D29922?style=flat-square)
![Low](https://img.shields.io/badge/low-7-4C8BF5?style=flat-square)
![Info](https://img.shields.io/badge/info-1_action_required-8957E5?style=flat-square)

<!-- nav:start -->
[Docs](README.md) · [Spec](spec.md) · [Architecture](architecture.md) · [Database](database.md) · [Accounting](accounting.md) · [Proof ledger](attestation.md) · [API](api.md) · [Security](security.md) · **Audit** · [Development](development.md) · [Deployment](deployment.md)
<!-- nav:end -->

</div>

---

Full review of the backend's exposure surface, and the hardening applied as a result.
Every finding below was verified against the code, not inferred from a pattern.

- **Scope**: `backend/app/**`, `backend/Dockerfile`, `docker-compose.prod.yml`,
  `frontend/nginx.conf`, the edge proxy configuration as it stood at the time,
  `.env` / `.env.sample`, `tests/conftest.py`.
- **Read this alongside its supersession notes.** Findings 3 and 5 have since changed -
  the gateway check was removed, and the nginx/certbot edge was removed from the production
  stack entirely - and two items under
  [what this does not solve](#what-this-does-not-solve) have since been closed. Each says
  so where it sits, and [security.md](security.md) is the current statement of the control
  set.
- **This report predates the third ledger.** Its scope was the ERP as it stood; the
  attestation module, the public verifier and the sealing key came later, so nothing below
  covers them. What they add to the exposure surface - and what they honestly do not
  solve - is stated in [what this does not solve](#what-this-does-not-solve) and in
  [Proof ledger](attestation.md).
- **Method**: route-table introspection for authorization coverage, source review of
  every `app/core` module and the auth module, dependency-source review where behaviour
  mattered (uvicorn's proxy-header handling, slowapi's storage backend), 145 new tests,
  a production-shaped boot probed from outside, and the edge proxy run against a stub
  backend that echoes what it receives.
- **Route coverage measured**: 196 API routes. 177 require authentication. The 19 that
  do not are listed in [Authorization coverage](#authorization-coverage) and every one is
  a deliberate pre-auth endpoint. The count has since grown, and so has the pre-auth set:
  `POST /feedback` is open on purpose, and so are the four `/verify/*` routes. Both are
  argued for where they live - see
  [the public verifier](security.md#the-public-verifier).

---

## Summary

| # | Severity | Finding | Status |
| --- | --- | --- | --- |
| 1 | **Critical** | `make test` destroys the database `DATABASE_URL` points at | Fixed |
| 2 | **High** | Client IP was attacker-controlled, defeating all IP-based limits | Fixed |
| 3 | **High** | No control restricted the API to traffic from our own edge | Fixed |
| 4 | **Medium** | One global rate-limit budget, with a boundary-burst weakness | Fixed |
| 5 | **Medium** | `infra/nginx/**` did not exist, so the production stack could not start | Fixed - by removing the edge |
| 6 | **Medium** | No request-body size limit | Fixed |
| 7 | **Medium** | Security headers absent from every response the router did not produce | Fixed |
| 8 | **Medium** | Uploaded documents had no durable storage in production | Fixed |
| 9 | **Low** | `X-Request-ID` echoed unsanitised into a header and the logs | Fixed |
| 10 | **Low** | `TRACE`/`TRACK` reached the router | Fixed |
| 11 | **Low** | API responses were cacheable by shared proxies | Fixed |
| 12 | **Low** | Containers ran with all default Linux capabilities | Fixed |
| 13 | **Low** | `/auth/refresh`, `/auth/verify-email`, invitation preview took the loose budget | Fixed |
| 14 | **Low** | Blank `DATABASE_URL`/`REDIS_URL` crashed the app instead of falling back | Fixed |
| 15 | **Low** | `RATE_LIMIT_IP` below a tier silently makes that tier unreachable | Surfaced |
| 16 | **Info** | Live Gmail refresh token and production DB password sit in `.env` | **Action required** |

Two findings need you rather than code: **#16**, and reading
[What this does not solve](#what-this-does-not-solve).

---

## 1. Critical - the test suite drops the production database

**`tests/conftest.py`, `app/core/config.py`**

`conftest.py` isolated itself by setting `POSTGRES_DB=stellarerp_test` and
`REDIS_DB=15`. Both were silently ignored, because `sqlalchemy_dsn` and `redis_dsn`
prefer a full URL over the composed parts:

```python
def sqlalchemy_dsn(self) -> str:
    if self.database_url is not None:   # <- DATABASE_URL wins
        ...
    return f"postgresql+asyncpg://{self.postgres_user}:...{self.postgres_db}"
```

The repository's own `.env` sets both:

```
DATABASE_URL=postgresql://stellarerp:<password>@dpg-d9piqie1egvs73ffrgj0-a/stellarerp
REDIS_URL=redis://red-d8bu4ke7r5hc738u2fq0:6379
```

Those are remote managed-database hostnames, not local ones. So `POSTGRES_DB=stellarerp_test`
had no effect, and the session fixture ran:

```python
engine = create_async_engine(settings.sqlalchemy_dsn)   # <- the deployment's database
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.drop_all)         # <- every table
```

while an autouse fixture ran `await redis.flushdb()` around every test.

`make test` from a machine that can resolve those hostnames drops every table in the
deployment's database and flushes its Redis. Nothing in the output would say so - the
suite would pass. The only thing that prevented it here is that those private hostnames do
not resolve from outside the network they belong to, which is why the Redis tests failed
with `getaddrinfo failed` rather than succeeding against a live database.

**Fixed** in three independent places, because one is not enough for this:

1. `conftest.py` sets `DATABASE_URL=""` and `REDIS_URL=""`, falling back to the composed
   local parts. A `.env` value cannot be removed by the environment, only overridden, so
   the override is the empty string.
2. `Settings._enforce_test_safety` refuses to construct when `ENVIRONMENT=test` and the
   resolved database name does not end in `_test`, or `REDIS_URL` is set at all.
3. `_create_test_database` asserts the same thing at the line that does the dropping.

```
ValueError: Refusing to run tests against database 'stellarerp': the test suite
drops every table, so the name must end in '_test'.
```

---

## 2. High - the client IP was whatever the caller said it was

**`backend/Dockerfile`, `app/core/context.py`, `app/core/middleware.py`**

Both Dockerfile targets ran:

```dockerfile
CMD ["uvicorn", ..., "--proxy-headers", "--forwarded-allow-ips", "*"]
```

`*` sets `always_trust`, and uvicorn's `_TrustedHosts.get_trusted_client_address` then
returns the **left-most** `X-Forwarded-For` entry (verified in the installed
`uvicorn/middleware/proxy_headers.py`):

```python
if self.always_trust:
    return _parse_host_port(x_forwarded_for_hosts[0])   # <- caller-controlled
```

Every proxy *appends* the peer it saw, so the left-most entry is the one the client
wrote. `curl -H 'X-Forwarded-For: 1.2.3.4'` made `request.client.host` equal `1.2.3.4`,
and that value was the sole input to:

- the rate limiter's bucket key - so **rate limiting was bypassable** by sending a fresh
  random address per request;
- `RequestContext.ip_address`, which is the address written to **every audit row** and to
  device history. An audit trail recording an attacker's chosen address is worse than
  one recording nothing, because it is trusted.

Account lockout was unaffected: `LoginThrottle` keys on the email, deliberately, and
says so.

**Fixed.** `app/core/net.client_ip` resolves the address by counting hops from the
**right** - the end our own proxy appends to - governed by `TRUSTED_PROXY_HOPS`. Both
Dockerfile targets now pass `--no-proxy-headers`, leaving `scope["client"]` as the real
socket peer so one unforgeable fact stays available underneath the resolution rule.

```
X-Forwarded-For: 1.2.3.4, 203.0.113.7
                 ^spoofed  ^appended by our nginx - what we read
```

Ten tests cover it, including IPv6 (a naive `split(":")` would collapse every IPv6
client into one bucket) and the case where there are fewer entries than configured hops.

---

## 3. High - nothing restricted the API to our own frontend

**`app/core/config.py`, `app/core/middleware.py`, `infra/nginx/**`**

This was the request, and it needs the honest version stated first.

**A shipped client cannot authenticate itself.** The React bundle is JavaScript the
browser hands over on request; `app_frontend/.env.sample` says of the desktop client's
config, in its own words, "anything in it ships to whoever has the binary". Any header,
signature, or token *the client* holds is readable by anyone holding the client, and
replayable from `curl`. There is no version of "only the frontend may call the API" that
is enforced at the client.

**The edge can.** nginx runs on your machine. A value it injects server-side never
reaches a user, a page, or a decompiler. So the property enforced is *"this request
arrived through our edge"* - which is checkable, and is what the requirement actually
means in a deployment where the frontend is served from that same edge.

> **SUPERSEDED — the gateway check has since been removed.** Every `GATEWAY_SECRET` /
> `X-Gateway-Key` / `GatewayGuardMiddleware` reference in the rest of this report is
> historical. The stack turned out to have no edge of its own — it sits behind whatever the
> operator runs in front, which is the `ALLOW_DIRECT_BACKEND_ACCESS=true` topology this
> section already described as the escape hatch. The `infra/nginx/` configuration the
> control depended on was never in the repository, so the check had no counterpart to stamp
> the header and refused every browser request.
>
> It was removed rather than left configured-but-unsatisfiable, along with its setting, its
> `secrets_match` helper, and the production boot check that required it. The reasoning is
> preserved in `app/core/config.py` where the setting was declared, including the two things
> re-adding it would require. `OriginGuardMiddleware` keeps the origin half.
>
> Its removal also fixed a live outage it was causing: it rejected the browser's CORS
> preflight — which by specification cannot carry a custom header — from *outside*
> `CORSMiddleware`, so the response had no `Access-Control-Allow-Origin` and every call from
> the frontend failed with an error pointing at the CORS configuration. See
> [security.md](security.md#there-is-no-edge-gateway-deliberately).

Implemented as:

- `GATEWAY_SECRET`, stamped by `infra/nginx/proxy-params.conf` as `X-Gateway-Key` on
  every proxied request, and compared by `GatewayGuardMiddleware` with
  `hmac.compare_digest`. A request without it gets **404** - not 403, which would confirm
  there is an API at this address.
- Production **refuses to boot** without it, unless `ALLOW_DIRECT_BACKEND_ACCESS=true` is
  set explicitly (the documented escape hatch for a single-service PaaS deployment with
  no proxy of your own).
- `Origin`/`Referer` enforcement on every state-changing method, against `CORS_ORIGINS`.
  Browsers will not let page script forge either header, so a cross-site `POST` from
  `evil.example` is refused. Requests with **neither** header pass - that is the desktop
  app, `curl`, a backup script, and refusing them would break every non-browser client
  while stopping no attacker.
- `TrustedHostMiddleware` in production (pre-existing) plus the network topology in
  `docker-compose.prod.yml`, where the API published no host port.

Reaching the API therefore required network access to the internal bridge *and* the
secret. The three layers were independent: the secret covered scripted callers, the origin
check covers browser-driven CSRF, the host check covers Host-header injection.

> **What is true today.** With the gateway check gone and the edge proxy removed from
> `docker-compose.prod.yml` (see finding 5), the API *does* publish a host port - bound to
> `127.0.0.1` by default, so it is still not reachable from the internet without a proxy
> deliberately put in front of it. What remains as enforced controls is the origin check,
> the host check, authentication, and the application's own rate limiter. Nothing claims to
> restrict the API to one client any more, and [security.md](security.md#there-is-no-edge-gateway-deliberately)
> explains why no such claim can be honestly made from a shipped client.

---

## 4. Medium - one global budget, and a boundary-burst weakness

**`app/core/middleware.py`, `app/core/ratelimit.py`**

The previous limiter had two problems.

**One budget for everything.** `200/minute` default, `10/minute` for paths matching a
substring list. A single number has to be set for the loosest endpoint, so it protects
none of the others: a dashboard needs 200/minute of reads, which means a document upload
running inline OCR also got 200/minute.

**Fixed window.** The key was `floor(now / window)`, whose documented weakness -
acknowledged in the old docstring - is that a client can spend the full budget in the
last instant of one window and the full budget again in the first instant of the next.
For the auth tier that is 20 password guesses back to back against a budget of 10.

**Fixed.** Seven tiers (`ratelimit.classify`), each with its own budget, enforced by a
**token bucket** evaluated in a Redis Lua script - one atomic round trip, continuous
refill, no boundary to straddle:

| Tier | Default | Covers |
| --- | --- | --- |
| `auth-strict` | 3/min | forgot/reset password, magic link, OTP request, resend verification |
| `auth` | 10/min | login, 2FA, register, refresh, verify-email, invitation preview |
| `upload` | 5/min | `POST /documents` - OCR runs inline, seconds of CPU each |
| `export` | 5/min | report exports and document downloads |
| `write` | 15/min | POST/PATCH/PUT/DELETE |
| `read` | 25/min | GET/HEAD/OPTIONS |
| `default` | 15/min | anything unmatched |
| *(per-IP)* | 20/min | applied **on top of** the above, whoever is calling |

Two further changes:

- **Buckets key on the authenticated user** where a valid token is present, IP otherwise.
  Pure IP keying puts a whole NAT'd office in one bucket, which is a denial of service
  users inflict on each other. The token's signature is verified before its `sub` is used
  as a key - an unverified `sub` is attacker-chosen, so a flood could mint a fresh
  identity per request and never touch a limit.
- **`OPTIONS` is a read**, so the preflight a browser sends before every cross-origin
  write does not consume the write budget.

**Second limiter (slowapi).** Alongside the middleware, `app/core/limiter.py` wires
slowapi to the same Redis with a `moving-window` strategy, and the auth handlers carry
explicit `@limiter.limit(...)` decorators - `5/minute` on login and 2FA, `3/minute` on
register and the four mail-sending endpoints, `20/minute` on refresh.

The two are not redundant. The middleware is exhaustive (a route added tomorrow is
limited without anyone remembering), at the cost of stating the budget in a regex table
in another module. The decorator states the endpoint's budget where a reader of the
endpoint sees it, and fires independently - so a pattern that stops matching after a path
is renamed does not silently unprotect the endpoints that matter most.

slowapi is deliberately *not* the blanket layer: version 0.1.10 drives the synchronous
`limits` storage, so each check is a blocking Redis call on the event loop. On login,
already dominated by ~50 ms of Argon2, that is invisible; in front of every request it
would serialise the worker. That is why the middleware limiter is hand-written against
`redis.asyncio`.

Its `RateLimitExceeded` is mapped into the application's error envelope - slowapi's own
body is `{"error": "Rate limit exceeded: 5 per 1 minute"}`, and the frontend branches on
`error.code`, so the un-normalised shape would have arrived as a blank failure on the
login form.

Two tests guard the decorator's *order*, which fails silently when reversed:
`@limiter.limit` must sit **below** `@router.post`, or the route mounts the bare handler
and the budget is never enforced.

---

## 5. Medium - the production stack could not start

`docker-compose.prod.yml` mounted `./infra/nginx/nginx.conf` and `./infra/nginx/conf.d`.
Neither existed. `docker compose -f docker-compose.prod.yml up` would have created a
directory where nginx expected a file and failed to start - meaning the documented
production deployment path had never been run.

**Resolved by removing the edge, not by writing the configs.** The configuration was
authored during the audit and did not survive into the repository; rather than restore a
proxy nothing deploys, `nginx` and `certbot` were **deleted from
`docker-compose.prod.yml`** along with every `./infra/**` mount. The production stack is
now postgres, redis, migrate, backend, frontend - all of which exist, all of which start.

That matches how the system is actually served: an install puts its own TLS terminator in
front, which is a thing the operator already has rather than a thing this repository should
ship a half-tested opinion about.

What moved out of the stack, and where it went:

| Was the edge's job | Now |
| --- | --- |
| TLS termination, certificate renewal | Whatever the operator runs in front. `certbot` is gone |
| `limit_req` / `limit_conn` volumetric shedding | The proxy in front. The application's own per-IP limiter is untouched and still runs |
| Load-balancing two API replicas | Nothing - both services run **one** replica, because a published host port admits one container. Scaling out means putting a proxy back and switching to a port range |
| Binding 80/443 | Nothing binds them. `backend` and `frontend` publish plain HTTP on `127.0.0.1` by default (`PUBLISH_ADDR`), so a fresh `up -d` exposes nothing publicly |

Two consequences worth stating rather than discovering: a redeploy now has a brief gap
where `order: start-first` used to cover it, and `docs/deployment.md` no longer describes
issuing a certificate because this stack no longer can.

The three configuration mistakes the audit caught by running `nginx -t` are kept here
because they are the reason the config is not simply pasted back in by the next person:
the frontend upstream pointed at port 80 while that image drops root and listens on 8080;
`proxy-params.conf` needed its own mount, since nginx parses everything in `conf.d/*.conf`
as top-level configuration and a bare list of `proxy_set_header` directives there fails the
config check; and `proxy_read_timeout` appearing twice in one location is **fatal** to
nginx rather than an override.

---

## 6. Medium - no request-body limit

Only the document upload path bounded its input (`read_within_limit`, 15 MB, while
streaming). Every other endpoint accepted a body of any size; FastAPI buffers a JSON body
fully before validating it, so a single request could exhaust the container's 1 GB.

**Fixed.** `BodySizeLimitMiddleware` rejects on `Content-Length` before the body is read,
and - because a chunked request has no `Content-Length`, making a header-only check
trivially bypassable - wraps `receive` and counts bytes as they arrive. Multipart bodies
get `max_upload_bytes + 1 MiB`; everything else gets `MAX_REQUEST_BYTES` (1 MiB).
`client_max_body_size 16m` at the edge is the outer bound.

---

## 7. Medium - security headers were missing from most rejections

`SecurityHeadersMiddleware` was registered **first**, which in Starlette makes it
*innermost*. It only decorated responses that reached the router. Everything generated by
an outer layer went out bare: the rate limiter's 429, the trusted-host rejection, and
every 404 - the response an attacker sees most while mapping the surface.

**Fixed.** It is now registered **last**, so it is the outermost layer and the headers
are unconditional. The set was also extended:

| Header | Why |
| --- | --- |
| `Cross-Origin-Resource-Policy: same-origin` | blocks another site embedding an API response as a subresource |
| `Cross-Origin-Embedder-Policy: require-corp` | removes the response from the class of documents that can be a leak vector |
| `X-Permitted-Cross-Domain-Policies: none` | refuses the `crossdomain.xml` mechanism, still honoured by PDF readers |
| `X-DNS-Prefetch-Control: off` | no speculative resolution of anything in a response body |
| `Permissions-Policy` | 21 capabilities explicitly denied, not 3 |
| `Cache-Control: no-store, private` | see finding 11 |
| CSP | extended from 4 directives to 13, including `sandbox` and `script-src 'none'` |

Per-route headers still win. The document-download endpoint's stricter `sandbox` CSP and
its deliberately cacheable `Cache-Control` both survive - there is a test for it, and the
existing test asserting the app-wide CSP was rewritten to compare against the constant
rather than a copy of its text.

---

## 8. Medium - uploaded documents were not durable in production

`docker-compose.prod.yml` mounted `./logs` and nothing else. With object storage
unconfigured, `document_storage` fell back to local disk and wrote to `backend/var/uploads`
**inside the container** - a layer a redeploy discards. Those files are the supporting
evidence for posted ledger entries, and they were the only thing in the stack with no
durability at all.

**Fixed twice.** The first fix was a named volume, which made the bytes survive a redeploy
and became load-bearing once the backend got a read-only root filesystem (finding 12).

The second fix removed the problem rather than mitigating it: **documents now live in
PostgreSQL**, compressed into `document_blob` as `BYTEA`, written in the same transaction as
the row describing them. The filesystem backend is gone, the volume is gone, and the backend
container writes nothing to disk at all. Three consequences worth naming:

- One `pg_dump` captures a posted bill and the scan supporting it at one consistent moment.
  A split store could restore a row pointing at a blob from a different point in time, and
  that failure surfaces months later as an invoice that cannot be produced.
- A rolled-back upload leaves nothing behind. A filesystem or bucket write is outside the
  transaction, so every failed upload used to leak bytes nobody would look for again.
- There is no second service whose volume can be forgotten - which is exactly what the
  development compose file did, silently making uploads ephemeral.

Object storage remains available for an install whose blobs outgrow the database, selected by
setting all three `MINIO_*` variables. The MinIO container that serves it in development now
sits behind the `objectstore` compose profile, so a plain `docker compose up` does not start
it - a service that only makes sense under one backend should not be running under the other.
See [security.md](security.md#document-storage) and `backend/app/modules/ocr/storage.py`.

---

## 9. Low - `X-Request-ID` was echoed unsanitised

The inbound header was honoured verbatim, then written into a response header, the error
envelope, and every log line for the request. Attacker-controlled text in a header value
is a response-splitting primitive, and in a log line it is a way to forge convincing
entries.

**Fixed.** Alphanumerics, `-` and `_` only, truncated to 64 characters; anything else is
replaced with a generated id rather than rejected, since the id is a debugging aid and
refusing the request over a malformed one would be the worse outcome.

---

## 10. Low - `TRACE` and `TRACK` reached the router

`TRACE` reflects the request back verbatim - historically the Cross-Site Tracing
technique for reading headers a page could not otherwise see. No client of this API uses
either.

**Fixed.** Refused with 405 in `GatewayGuardMiddleware`, and again at the edge so a
scanner's probe never reaches a Python worker.

---

## 11. Low - API responses were cacheable

No `Cache-Control` on API responses. A shared corporate proxy, or the browser's own disk
cache, may retain one user's invoice list and serve it to the next person on the machine.

**Fixed.** `no-store, no-cache, must-revalidate, private` by default, with per-route
values preserved.

---

## 12. Low - containers ran with default capabilities

No `cap_drop`, no `no-new-privileges`, no read-only root filesystems. Docker's default
grant includes `CAP_NET_RAW`, which permits ARP spoofing against every other container on
the bridge - including Postgres.

**Fixed.** An `x-hardening` anchor applies `cap_drop: [ALL]` and
`no-new-privileges:true` to every service, with the minimum given back where genuinely
needed (`NET_BIND_SERVICE` for nginx's privileged ports; the `SETUID`/`SETGID` pair where
a master process drops to an unprivileged user). `backend`, `migrate`, `frontend` and
`nginx` run `read_only: true` with `tmpfs` for their scratch paths, so code execution in
a container cannot drop a payload on disk to survive a restart or overwrite the
application's own modules.

---

## 13. Low - three token-bearing endpoints took the loose budget

`AUTH_PATH_MARKERS` did not include `/auth/refresh`, `/auth/verify-email`, or
`GET /invitations/{token}`, so all three took the 200/minute default. None is guessable -
each takes a 256-bit opaque token - but refresh rotates a long-lived credential and
writes a session row, so hammering it churns session state cheaply.

**Fixed.** All three are in the `auth` tier, with a test that asserts **no**
unauthenticated route falls through to the default tier.

---

## 14. Low - a blank URL override crashed the app

`database_url: PostgresDsn | None` rejected `""`, so `DATABASE_URL=` in `.env` - the
natural way to write "use the composed parts" - was a boot-time validation error rather
than the documented fallback.

**Fixed.** A `_blank_to_none` validator, with the scheme still checked explicitly at boot
so a typo remains a startup message rather than a first-query connection error. This is
also what makes finding 1's fix possible.

---

## 15. Low - a per-IP ceiling below a tier silently eats it

**`app/core/config.py`, `app/main.py`**

The two rate-limit buckets are consulted together and a request needs room in **both**, so
setting `RATE_LIMIT_IP` below a tier makes that tier unreachable and the per-IP number the
only limit that binds. Nothing about the configuration says so.

At the defaults currently in `config.py` this is live: `RATE_LIMIT_IP=20/minute` sits below
`RATE_LIMIT_READ=25/minute`, so reads are effectively capped at 20/minute. That is a
defensible choice for a single-operator install - it is also **shared by everyone behind
one NAT**, while each user has their own tier bucket, so the symptom in a shared office is
intermittent 429s that track how many colleagues are online rather than anything the user
did.

**Surfaced, not "fixed"** - it is a tuning decision, and refusing to boot over one would be
obnoxious. `settings.rate_limit_tiers_eclipsed_by_ip` computes the set, and `app.main`
logs it at startup:

```
WARNING RATE_LIMIT_IP is below one or more tiers, so those tiers can never bind - the
per-IP ceiling is the effective limit, and it is shared across a NAT
        rate_limit_ip=20/minute
        eclipsed_tiers={'RATE_LIMIT_DEFAULT': '200/minute', 'RATE_LIMIT_READ': '25/minute'}
```

Comparison is on rates, not counts, so `600/hour` and `10/minute` are correctly equal. If
this is deployed for more than one or two people, raise `RATE_LIMIT_IP` to roughly ten
times the largest tier.

---

## 16. Info - live credentials in `.env` - action required

`.env` is correctly git-ignored, and `git ls-files` confirms it was never committed.
`credentials.json` and `token.pickle` are ignored too. Nothing has leaked through version
control.

What is on disk, in plaintext, is worth naming:

- `GMAIL_CREDENTIALS_B64` - a working refresh token for `synfin.no.reply@gmail.com`
  scoped to `gmail.send`, **plus the OAuth client secret** (`GOCSPX-…`), both recoverable
  by base64-decoding the value. A refresh token does not expire on its own.
- `POSTGRES_PASSWORD` and the same password inside `DATABASE_URL` - a live managed
  Postgres.
- `backend/credentials.json` and `backend/token.pickle` - the same Gmail credential in
  two more forms.

**Recommendations, in order:**

1. **Rotate the Gmail OAuth client secret and revoke that refresh token.** It is the one
   credential here that grants the ability to send mail as your domain, which is a
   phishing capability, and rewriting history would not un-leak it if it ever escaped -
   only rotation does.
2. Do not keep production `DATABASE_URL`/`REDIS_URL` in a development `.env`. Finding 1
   shows what that combination costs; the guardrails now stop the specific catastrophe,
   but the credential is still on a laptop.
3. Delete `backend/credentials.json` and `backend/token.pickle` once the value is in
   `.env`, or move them outside the repository tree. They are ignored, but "ignored" and
   "absent" differ the moment someone runs `git add -f` or zips the directory.
4. Rotate `SECRET_KEY` and `ENCRYPTION_KEY` for the real deployment if this `.env` has
   ever been shared. `SECRET_KEY` signs access tokens; `ENCRYPTION_KEY` decrypts stored
   2FA secrets.

---

## What was already right

Worth recording, both because it is most of the system and because a reviewer who reads
only the findings above will get the wrong impression of it.

- **Argon2id** with configurable cost and transparent rehash-on-login; parameters stored
  in the hash.
- **Refresh-token rotation with reuse detection**, tokens stored as SHA-256 digests, and
  lineage revocation on replay.
- **Access tokens in memory only** on the client; the refresh token is
  `HttpOnly; Secure; SameSite=Strict` and path-scoped to `/auth`.
- **Revocation inside milliseconds** via a Redis token epoch and a revoked-session
  marker, both checked in one pipelined round trip per request.
- **Timing-equalised login** (`dummy_password_verify`) and identical responses for
  existing and absent accounts, so there is no enumeration oracle.
- **Email-keyed lockout**, not IP-keyed - with the reasoning written down, and correct:
  an attacker rotates IPs trivially, and IP locking punishes a whole NAT.
- **Tenant isolation from the signed token**, never from a URL or body. `organization_id`
  is written server-side after a membership check.
- **No SQL injection surface.** Every query is SQLAlchemy Core/ORM; the handful of
  `text()` uses are static DDL or bound parameters. `sort_by` is allow-listed against
  `sortable_fields` rather than interpolated.
- **Errors never leak internals** - SQL, connection strings and tracebacks are logged and
  replaced with an opaque envelope carrying a request id.
- **2FA secrets encrypted at rest** with Fernet; TOTP codes marked spent to close the
  replay window; recovery codes single-use.
- **The document-download endpoint** is genuinely careful: `Content-Disposition:
  attachment`, `nosniff`, a `sandbox` CSP, the *sniffed* content type rather than the
  declared one, and a filename stripped of header-injection characters.
- **`extra="forbid"` request schemas** separate from response schemas, so mass assignment
  has nothing to bind to.
- **Append-only audit trail** with actor, IP, request id and diff.

### Authorization coverage

Measured by walking the mounted route table and checking each route's full dependency
closure for an authenticating dependency:

```
196 API routes    177 authenticated    19 public
```

All 19 public routes are pre-auth by necessity:

| Group | Routes |
| --- | --- |
| **Health** | `/health`, `/health/live`, `/health/ready` |
| **Sign-in** | `/auth/login`, `/auth/login/2fa`, `/auth/register`, `/auth/refresh` |
| **Email flows** | `/auth/verify-email`, `/auth/resend-verification`, `/auth/forgot-password`, `/auth/reset-password` |
| **Passwordless** | `/auth/magic-link`, `/auth/magic-link/verify`, `/auth/magic-link/device`, `/auth/magic-link/device/poll`, `/auth/otp`, `/auth/otp/verify` |
| **Other** | `/auth/password-policy`, `GET /invitations/{token}` |

**No endpoint was found missing an authorization check.** Every one of the 19 is now in
the `auth` or `auth-strict` rate-limit tier, and a test asserts that none falls through
to the default.

---

## What this does not solve

Stated plainly, because a security document that implies completeness is worse than one
that does not.

- **A public client still cannot be authenticated.** The gateway secret proves a request
  came through your edge. It does not prove a *browser running your bundle* sent it - and
  nothing can, because the bundle is public code. An attacker who obtains valid
  credentials can drive the API from a script through your edge, exactly as the browser
  does. Authentication, authorization, rate limiting and the audit trail are what bound
  that, not the gateway check.
- **Rate limiting fails open.** Both limiters allow the request through when Redis is
  unreachable. That is the right trade for a protective layer - a cache outage must not
  be a total outage - but it means Redis availability is a security property.
  `SWALLOW_STORAGE_ERRORS` in `app/core/limiter.py` is the switch if you want the
  credential endpoints to fail closed instead.
- **`TRUSTED_PROXY_HOPS` must match your topology.** Set too high, it hands the client
  control of its own apparent IP again - the exact bug finding 2 fixed. One terminator or
  one platform router is `1`; a CDN in front of that is `2`. `0` is rejected outright
  (`ge=1`), since production will not boot without https origins either.
- **`RATE_LIMIT_IP` is currently the binding limit for reads** (finding 15). Fine for one
  or two people; raise it before more than that share an office network.
- **Volumetric DDoS is out of scope, and now entirely somebody else's layer.** With the
  edge removed (finding 5) there is no connection- or request-rate shedding in front of the
  application at all - its own limiter runs only after the request has already reached
  Python. Absorbing a real flood needs the platform router, a CDN, or whatever you put in
  front.
- ~~**Frontend source maps are now served.**~~ **Fixed since this report.**
  `vite.config.ts` set `sourcemap: true` with the comment "not served publicly", which was
  true only because the frontend's own edge configuration returned 404 for `*.map`. That
  configuration was deleted with the edge and nothing replaced the rule, so the maps beside
  `dist/assets` became fetchable.

  It is now closed in two places, deliberately, because the comment was load-bearing once
  and turned out not to be: the build uses `sourcemap: 'hidden'`, which emits the maps
  without the `//# sourceMappingURL` comment so no browser asks for them and an error
  tracker can still be given them at release time; and the production image runs
  `find … -name '*.map' -delete` after copying `dist/`, so nothing is there to fetch. A
  fix that depends on one configuration file staying deleted is the fix that produced this
  finding.
- ~~**Twenty pre-existing test failures are unrelated to this work.**~~ **Cleared since
  this report.** At the time, 19 OCR tests failed because the optional `ocr` extra
  (`pypdf`) was not installed in that virtualenv, and
  `test_password_policy.py::test_rejects_password_containing_name` failed on `main` too -
  its password shared no substring with the name it was meant to be rejected for
  containing, so the assertion could never have held. Both are fixed; the suite runs
  clean.

### Added by the third ledger

The proof ledger arrived after this report, so its limits are stated here rather than
implied by silence. All three are also on the Trust screen, in the product, because a
trust feature that overstates itself is worse than none.

- **A server-held signing key means the operator can doctor the books *before* sealing.**
  What a seal proves is that nothing changed *after* it - which is the claim that matters,
  because retroactive editing is how accounts are actually cooked, but it is not the same
  as "these figures are true". Three things bound it: daily cadence (a one-day window,
  affordable only because Stellar charges fractions of a cent), the hash chain the network
  timestamps (rewriting one period means publicly re-sealing every period after it), and
  `POST /attestation/signer/rotate`, which moves the book onto a 2-of-3 multisig with the
  business's accountant so no single machine can seal alone.
- **A seal proves inclusion, not completeness.** It says the entries in the batch are
  unchanged. It does not say the batch was everything - an entry never written to the
  journal was never sealed and leaves no gap. Control totals and the sealed entry count
  make wholesale omission visible over time; they do not make one missing invoice visible.
- **Losing `ATTESTATION_NAMESPACE_SALT` orphans every book.** An organization's on-chain
  identity is `SHA-256(organization_id ‖ salt)`, and the mapping is deliberately
  unrecoverable from the chain. The seals survive under a namespace nothing can compute,
  and every proof already handed to a counterparty stops resolving. It belongs with
  `ENCRYPTION_KEY` in whatever holds the deployment's secrets - see the
  [pre-flight checklist](deployment.md#8-pre-flight-checklist).

The one thing it deliberately does *not* add to the exposure surface is business data:
nothing personal, no amount attached to a party, no name, and no document is written on
chain, so there is nothing there a data-erasure request would need to reach.

---

## Verification

### What was run

```bash
cd backend
uv run ruff check app tests                      # All checks passed
uv run mypy app                                  # clean except pre-existing pypdf stubs
uv run pytest tests/test_security_hardening.py   # 145 passed
uv run pytest                                    # 946 passed, 9 skipped, 20 failed

docker compose -f docker-compose.prod.yml config --quiet   # exit 0
```

The 20 failures are the pre-existing set named above - 19 OCR tests needing the
uninstalled `ocr` extra, and one password-policy assertion that also fails on a clean
checkout. `uv sync --extra ocr` clears the 19.

Beyond the suite, two things were verified by running them rather than by reading them.

**A production-shaped boot**, with `ENVIRONMENT=production` and a real gateway secret,
probed from the outside - 12/12:

```
docs blocked 404 | redoc blocked 404 | openapi blocked 404
no gateway key 404 | wrong gateway key 404 | with gateway key 200
bad Host 400 | foreign-origin write 403 | allowed-origin write 422
TRACE 405 | oversized body 413 | health needs no key 200
```

**The edge**, as a real nginx container in front of a stub that echoes what it receives:

```
nginx -t  ->  configuration file /etc/nginx/nginx.conf test is successful

# what the backend receives through the proxy
gateway=s3cr3t-from-nginx-… | xff=172.21.0.1 | reqid=065bd2c5… | host=app.example.com

# the same, when the client tries to forge both headers
#   the forged gateway key is REPLACED; the forged address lands to the LEFT of the
#   real peer, which is exactly what hop-counting-from-the-right reads past
gateway=s3cr3t-from-nginx-… | xff=1.2.3.4, 172.21.0.1

/docs 404  /redoc 404  /openapi.json 404  /.env 404  /.git/config 404  /wp-login.php 404
TRACE 405
```

That second line is the whole design in one string: the client's `X-Gateway-Key` never
survives the proxy, and its `X-Forwarded-For` can only ever be *prefixed* to the truth.

Manual checks worth running against a deployment:

```bash
# Docs and schema are gone in production
curl -sSi https://your.host/openapi.json | head -1     # HTTP/2 404

# The gateway secret is required
curl -sSi https://backend-host:8000/api/v1/auth/password-policy | head -1   # 404

# IP spoofing no longer moves the bucket
for i in $(seq 1 40); do
  curl -s -o /dev/null -w '%{http_code}\n' \
    -H "X-Forwarded-For: $RANDOM.$RANDOM.1.1" \
    -X POST https://your.host/api/v1/auth/login \
    -d '{"email":"a@b.com","password":"x"}' -H 'Content-Type: application/json'
done | sort | uniq -c        # expect 429s

# Headers
curl -sSI https://your.host/api/v1/auth/password-policy
```

<!-- related:start -->

---

## Related reading

- [Security](security.md) - the full control set, with rationale
- [Deployment](deployment.md) - applying the hardening to a live server
- [Development](development.md) - the test-isolation guarantees finding 1 turned into

[All documentation](README.md)
<!-- related:end -->
