<div align="center">

# Security

**The threat model and every control in the system, each with its rationale.**

![Auth](https://img.shields.io/badge/passwords-Argon2id-2EA043?style=flat-square)
![Sessions](https://img.shields.io/badge/refresh_tokens-rotated_reuse--detected-2EA043?style=flat-square)
![At rest](https://img.shields.io/badge/secrets_at_rest-Fernet-4C8BF5?style=flat-square)
![Audit](https://img.shields.io/badge/audit_trail-append--only-8957E5?style=flat-square)

<!-- nav:start -->
[Docs](README.md) · [Spec](spec.md) · [Architecture](architecture.md) · [Database](database.md) · [Accounting](accounting.md) · [Proof ledger](attestation.md) · [API](api.md) · **Security** · [Audit](security-audit.md) · [Commands](commands.md) · [Development](development.md) · [Deployment](deployment.md)
<!-- nav:end -->

</div>

---

**This page is the control set: what the software does, and why.** Every measure in the
system, from password hashing to tenant isolation, with the reasoning behind it. The
*policy* - scope, how to report, how a report is handled, disclosure - is
[SECURITY.md](../SECURITY.md) in the repository root.

Every control below exists for a stated reason. Where a common alternative was
rejected, the reason is given - a control whose rationale nobody remembers is a
control that gets removed in the next refactor.

---

## Found something? Report it privately

> [!IMPORTANT]
> **Do not open a public issue.** A GitHub issue is visible to everyone the moment it is
> posted. A public report of a live flaw is a working set of instructions for attacking
> every install still running the vulnerable code - businesses who will not see your
> issue, and have no fix to apply yet.

Use **GitHub's private vulnerability reporting** - the *Security* tab on this repository,
then *Report a vulnerability*.

**[SECURITY.md](../SECURITY.md) is the policy**, and the only copy of it: what is in and
out of scope, what to put in a report, what not to test against, how a report is handled
and on what timeline, disclosure, and what an operator owns rather than this repository.
It lives there because a second copy of a policy is a copy that goes stale, and the one
that goes stale is always the one somebody reads.

This page is the other half - **what the software actually does**, control by control,
and why each one is there.

---

## On this page

| Section | What it covers |
| --- | --- |
| [Threat model](#threat-model) | What this defends against, in order of likelihood |
| [Reaching the API at all](#reaching-the-api-at-all) | Why there is no edge gateway, origin enforcement, host/method/size limits |
| [Who is calling](#who-is-calling---client-address-resolution) | Client address resolution, and why the left-most `X-Forwarded-For` is a trap |
| [Authentication](#authentication) | Argon2id, password policy, device sign-in, enumeration, lockout, TOTP |
| [Session management](#session-management) | The token split, rotation with reuse detection, revoking a stateless token |
| [Authorization](#authorization) | Permissions in code, staleness bounds, tenant isolation, lockout prevention |
| [Input handling](#input-handling) | Validation boundaries and mass-assignment defence |
| [Document storage](#document-storage) | Attacker-controlled paths, tenant predicates, byte-exact round trips |
| [Secrets and logging](#secrets-and-logging) | Redaction, card numbers and PCI DSS scope, encrypted bank details, the sealing key |
| [The public verifier](#the-public-verifier) | The one unauthenticated router, and why it is safe to be one |
| [Error reporting](#error-reporting) | What may leave this machine when something crashes |
| [Rate limiting](#rate-limiting) | Both layers, and why both fail open |
| [Transport and headers](#transport-and-headers) | What this application sets, and what it cannot |
| [OWASP Top 10](#owasp-top-10-2021) | The mapping |
| [Deferred](#deferred-to-later-stages) | Stated plainly rather than left implied |

For the findings that produced the rate-limiting and header controls described here -
including one critical issue in the test harness and the reason a shipped client cannot
authenticate itself - see [security-audit.md](security-audit.md). **That report predates
two changes**: the edge-gateway check was removed, and the edge itself - proxy and
certificate tooling both - was removed from the production stack entirely. Findings 3 and 5
carry notes saying so.

---

## Threat model

What this system is actually defending against, in rough order of likelihood:

| Threat | Primary control |
| --- | --- |
| Direct access to the API, bypassing our own edge | **Not defended** - this service *is* the edge. See below |
| Credential stuffing from a breach elsewhere | Argon2id, per-account lockout, per-IP rate limiting, 2FA |
| Spoofed client address defeating IP-based limits | Forwarding hops counted from the right, `--no-proxy-headers` |
| API surface reconnaissance | No docs or OpenAPI schema in production, enforced in three places |
| Account enumeration to build a target list | Identical responses *and timing* for existing/absent accounts |
| XSS stealing a session | Access token in memory only; refresh token HttpOnly; strict CSP |
| Stolen refresh token used indefinitely | Rotation with reuse detection; lineage revocation |
| CSRF against state-changing endpoints | `SameSite=Strict` cookie; bearer token required |
| Cross-tenant data access | Organization identity from the signed token, never from a URL |
| Privilege escalation via mass assignment | Separate request/response schemas; `extra="forbid"` |
| Insider action denied later | Append-only audit trail with actor, IP, request id, and diff |
| Leaked database dump | Passwords hashed, tokens digested, 2FA secrets encrypted |
| A tenant locking itself out | Owner cannot be removed, suspended, or demoted |

Explicitly **out of scope**: volumetric DDoS, insider threat at the infrastructure
level, and supply-chain attestation. The first belongs to whatever sits in front of
this stack; the other two belong to Stage 10 (production hardening), and neither is
claimed to be handled today.

---

## Reaching the API at all

Two checks run before any handler. They constrain different callers, and neither is
sufficient alone.

### There is no edge gateway, deliberately

"Only our frontend may call the API" has an honest form and a wishful one, and this
deployment can satisfy neither — so it claims neither.

**A shipped client cannot authenticate itself.** The React bundle is JavaScript the
browser is handed on request. Any header, token or signature the *client* holds is
readable by whoever holds the client and replayable from `curl`. There is no client-side
version of this control, and `X-Gateway-Key` was never sent by the frontend — that was the
point, not an omission.

**Only an edge could,** and there isn't one of ours. This service runs behind whatever the
operator puts in front, not behind an edge we configure and ship, so there is nothing
positioned to inject a server-side value. A `GATEWAY_SECRET` check was removed rather than left half-wired.

What it *would* have bought, if a proxy is ever added: it closes the **side door** — the
backend reachable at its own address, where the edge's TLS, logging, IP rules and rate
limits are all skipped. That matters most when an origin IP behind a CDN leaks. It does
**not** make the API private; anyone may still walk through the front door, it only
requires that they use it.

Re-adding it needs three things, and the third is the one that bites:

1. A proxy config stamping the header on every forwarded request.
2. That config **overwriting** any client-supplied value, not passing one through —
   otherwise the check is satisfied by the very caller it exists to stop.
3. An exemption for CORS preflights. A preflight cannot carry a custom header by
   specification; gating it produced `No 'Access-Control-Allow-Origin' header is present`
   and took the whole frontend down while pointing at the CORS config. See
   `OriginGuardMiddleware` and `TestPreflightReachesCors`.

### Origin enforcement

Every state-changing method (`POST`/`PUT`/`PATCH`/`DELETE`) must carry an `Origin` - or,
failing that, a `Referer` - that reduces to one of `CORS_ORIGINS`. Browsers do not let
page script forge either header, so a cross-site write from an attacker's page is
identifiable and refused with 403.

A request with **neither** header passes. That is the desktop app, `curl`, a backup
script; refusing them would break every non-browser client while stopping no attacker,
because an attacker simply omits both. This closes the browser-driven CSRF path - which
`SameSite=Strict` already covers, making this the second layer - not scripted access. What
constrains a scripted caller is authentication and rate limiting; no header-based check can,
which is exactly why the removed gateway secret needed a proxy rather than a client.

Comparison is on `scheme://host:port`, lowercased, so a trailing slash or a capitalised
host does not decide the outcome. A check that fails on formatting rather than identity is
worse than no check, because it looks like it is working.

### Host, method and size

- `TrustedHostMiddleware` in production, against `ALLOWED_HOSTS`. A forged `Host` poisons
  the absolute URLs in password-reset mail.
- `TRACE`, `TRACK` and `CONNECT` are refused with 405 before routing. `TRACE` reflects the
  request verbatim, which is the Cross-Site Tracing technique for reading headers a page
  otherwise cannot.
- `BodySizeLimitMiddleware` rejects a body over `MAX_REQUEST_BYTES` (1 MiB) from
  `Content-Length` before reading it, and counts bytes while streaming when no length is
  declared - a chunked request has no `Content-Length`, so a header-only check is
  bypassable by omitting it. Multipart bodies get `MAX_UPLOAD_BYTES` plus 1 MiB of framing
  headroom, with the file itself still bounded by `read_within_limit`.

---

## Who is calling - client address resolution

Every IP-based control is only as good as the answer to "what is this caller's address?",
and behind a proxy that answer comes from a header the caller can write.

`X-Forwarded-For` is a list each proxy **appends** to. A client can send
`X-Forwarded-For: 1.2.3.4` and the router in front faithfully appends the real address
after it:

```
X-Forwarded-For: 1.2.3.4, 203.0.113.7
                 ^spoofed  ^appended by the proxy - the real client
```

So `app.core.net.client_ip` counts hops from the **right**, `TRUSTED_PROXY_HOPS` deep.
The left-most entry - the conventional choice - is precisely the value an attacker
controls.

This is why both Dockerfile targets pass **`--no-proxy-headers`**. uvicorn's own handling
takes the left-most entry when `--forwarded-allow-ips` includes the peer, so
`request.client.host` under `'*'` is attacker-chosen; disabling it leaves `scope["client"]`
as the real socket peer, one unforgeable fact underneath the resolution rule.

Set `TRUSTED_PROXY_HOPS` to match the topology. Too high and the client controls its own
apparent IP again: one terminator or one platform router is `1`, a CDN in front of that
is `2`. **`0` is not a legal value** - the field is `ge=1`, because production also refuses
to boot without https origins, and nothing in this stack terminates TLS. "Deployed directly
with nothing in front" is not a shape this application supports.

---

## Authentication

### Password storage - Argon2id

Parameters are configurable and recorded in the hash itself.

Argon2id is memory-hard, so GPU and ASIC cracking gains far less against it than
against bcrypt or PBKDF2. It won the Password Hashing Competition and is the
current OWASP recommendation.

Because the parameters live in the stored hash, raising the cost later re-hashes
users transparently on their next successful login (`password_needs_rehash`)
rather than locking anyone out.

### Password policy - composition rules, with a blocklist backstop

Enforced rules ([`auth/password_policy.py`](../backend/app/modules/auth/password_policy.py)):

| Rule | Value |
| --- | --- |
| Minimum length | 6 |
| Maximum length | 128 |
| Uppercase letter | required |
| Lowercase letter | required |
| Special character | required (`string.punctuation`) |
| Digit | **not** required (`REQUIRE_DIGIT` flips it on) |

Two rules apply beyond that composition set, and both are deliberate:

**A blocklist backstop.** Composition requirements are satisfied by precisely the
passwords cracking dictionaries enumerate first - `Password@1` clears every rule
above at ten characters. So the password is reduced to its letters-only root and
checked against a weak-root list. Three normalisations are compared, because none
subsumes the others:

| Input | Normalisation that catches it | Root |
| --- | --- | --- |
| `Password@1` | strip non-letters | `password` |
| `P@ssw0rd` | reverse leetspeak | `password` |
| `Passw0rd!` | trim edge padding, *then* reverse leetspeak | `password` |

Applying leetspeak unconditionally is not sufficient: it rewrites trailing padding
into letters, so `Password@1` would become `passwordai` and miss.

**No personal information.** The user's own name and email local part are
rejected, since targeted guessing starts there.

**Known limitation - caseless scripts.** Requiring both letter cases means a
password written wholly in Devanagari, Arabic, Chinese, Japanese, Hebrew, or Thai
cannot satisfy the policy, since `str.isupper()`/`str.islower()` are both false
for every character in those scripts. Affected users must mix in Latin
characters. This is an inherent consequence of mandating both cases, not a bug;
a test in `tests/test_password_policy.py` pins the behaviour so it stays visible
rather than being discovered by a locked-out user.

Whitespace is not accepted as the special character - a space the user cannot see
is not usable variety, and a pasted password with stray spaces fails at login in a
way nobody can diagnose.

The enforced policy is served from `GET /auth/password-policy`, so the client's
hints can never contradict what the server accepts. The frontend consumes it
through one shared module
([`features/auth/passwordPolicy.ts`](../frontend/src/features/auth/passwordPolicy.ts))
rather than restating the rules per form.

### Device sign-in (the desktop app)

The app can send a magic link but never receives one: the link opens in a browser.
So it opens a pending record, holds a 256-bit **handle**, and polls until the link is
opened - on that machine or any other. The browser click approves the handle; the
next poll establishes a session in the *app's* own request, so device history records
the app's IP and user agent rather than the browser's.

**One click, one session, on the client that asked.** The token records which flow
minted it - an app's link carries a device handle, a browser's does not - so opening
an app's link approves the app and signs the browser out of the story entirely. The
alternative left sessions on two machines when the user had asked for one, and the
extra one was on whatever device happened to open the mail.

What holds it together:

- **The handle is the credential, and only the app ever has it.** It is not emailed,
  not put in a URL, and only its digest is stored in Redis. The approving request
  carries that digest inside the magic-link token's payload, so nothing in the
  approval path can reconstruct something pollable.
- **The `user_code` closes the one hole this flow opens.** Anyone who knows an address
  can start a device sign-in, so without a check the attacker's app would be signed in
  by a link the *owner* clicks. The app shows a four-character code, the email repeats
  it and says not to open the link unless it matches.
- **2FA is still enforced, in the app.** Opening the link proves control of the
  mailbox; the poll then returns a challenge rather than tokens, and the app completes
  it through `/auth/login/2fa`. Mailbox access alone is not enough.
- **Single-use.** The record is destroyed the moment it is claimed, before the session
  is handed back, so a replayed handle gets a 401.
- **The poll is exempt from the tighter auth rate limit** (`AUTH_PATH_EXCEPTIONS`).
  It is called every two seconds by design and is not a guessing surface: 256 bits,
  bounded by its own TTL, destroyed on first success.

### What counts as proof of an email address

**Login refuses an unverified address** ([`auth/service.py`](../backend/app/modules/auth/service.py)),
so "verified" is a gate rather than a badge. Four things satisfy it, and only the
first is the dedicated flow:

| Proof | Why it counts |
| --- | --- |
| The verification link | The obvious one - a token delivered to the address, redeemed |
| A magic link, on first use | Same proof, arriving through a different door |
| An email OTP, on first use | The code was readable only in that mailbox |
| **Registering through an invitation** | The invite token is emailed to the invited address, is never returned by any API, and registration rejects a mismatched email - so holding it *is* control of the mailbox |

The last one surprises people, including its author, so it is worth being explicit:
someone who signs up through an invitation link is **already verified and never
receives a verification email**. Removing that would not add a check - it would send
a second email asking for proof the first one already provided, and until they opened
it they could not sign in at all, having just been added to an organization.

The exposure this accepts is a **forwarded invitation**: whoever holds the link can
register as the invited address. That is inherent to bearer invite links rather than
particular to this design, it needs the invitee to hand it over, the token is
single-use with a seven-day expiry, and the address owner still controls password
reset.

### Account enumeration

Password reset, magic link, OTP request, and resend-verification all return the
same message whether or not the account exists.

Login goes further: on a missing account it calls `dummy_password_verify()`,
burning an equivalent Argon2 cycle. Without that, "no such user" returns in
microseconds while a real user costs ~50 ms - a trivially measurable oracle. A
test asserts the two timings stay within the same order of magnitude.

Registration is the deliberate exception: it *does* return 409 on a duplicate
email. Pretending to succeed would leave the user waiting for a verification
email that never arrives, with no path to recovery. Rate limiting is the
appropriate control there instead.

### Brute-force protection

Two independent layers:

- **Per-account lockout** (Redis) - 5 failures locks the account for 15 minutes.
  Keyed on email, not IP: an attacker rotates IPs trivially, and IP-based locking
  punishes everyone behind one NAT.
- **Per-IP rate limiting** in the application - a tight tier on auth paths, a looser
  one everywhere else. Fixed-window, because one `INCR` plus one `EXPIRE` keeps it
  cheap enough to sit in front of everything. Anything the platform router or a
  reverse proxy sheds before that is a bonus, not a layer this system relies on.

2FA failures count toward the same account lockout budget. Without that, the
second factor is brute-forceable at leisure once the password is known.

The application limiter **fails open** if Redis is unavailable. Fail-closed would
turn a cache outage into a total outage - a worse trade for a protective layer.

### Two-factor authentication - TOTP

Standard parameters (6 digits, 30-second step, SHA-1) because that is what every
authenticator app actually implements. Deviating is cryptographically defensible
and practically useless: most apps ignore the algorithm parameter in the
provisioning URI and compute SHA-1 regardless, producing codes that never
validate.

Two distinct replay defences:

- **A one-step window** tolerates ±30 s of clock skew. Wider windows multiply the
  guessing surface.
- **Single-use enforcement.** Because a code stays valid for up to 90 seconds, an
  attacker who observes one can replay it. Every accepted code is burned in Redis
  via `SET NX` - atomic, so two concurrent requests cannot both win.

Enrolment requires proving a valid code before 2FA takes effect. A secret is
written during setup but `totp_enabled_at` stays null until confirmed, so a
mis-scanned QR cannot lock anyone out.

Secrets are **Fernet-encrypted at rest** (AES-128-CBC + HMAC). A leaked database
must not hand over working second factors. Recovery codes are stored as Argon2
hashes and shown exactly once.

---

## Session management

### Token split

|  | Access token | Refresh token |
| --- | --- | --- |
| Format | JWT (HS256) | 256-bit opaque random |
| Lifetime | 15 minutes | 7 days, or 30 with "remember me" |
| Storage (client) | Memory only | HttpOnly, Secure, SameSite=Strict cookie |
| Storage (server) | Stateless | SHA-256 digest in PostgreSQL |
| Revocation | Redis epoch / session marker | Row update |

**Why the access token is not in `localStorage`:** any XSS on the page can read
it, and a stolen token is valid until it expires. A module-scoped variable dies
with the tab.

**How a page reload stays signed in:** the HttpOnly refresh cookie, which
JavaScript cannot read at all. On boot the app calls `/auth/refresh` once and gets
a fresh access token. The long-lived credential is never reachable from JS; the
short-lived one never outlives the tab.

**Why refresh digests, not Argon2:** these are 256-bit random values, not human
passwords. There is no dictionary to attack, so a slow KDF buys nothing and would
add latency to every refresh.

### Rotation with reuse detection

Every refresh mints a new token and revokes the old one, recording
`rotated_to_id`.

The stolen-token problem is that an attacker who copies a refresh token can
refresh forever, and the server cannot tell them from the real user. Rotation does
not prevent that - it makes it **detectable**. The first party to refresh
invalidates the other's copy, so a second use of an already-rotated token is
reliable evidence that two parties hold it.

Response: revoke the entire session lineage, bump the user's token epoch, and
audit it as `critical`. Both parties must re-authenticate.

### Revoking a stateless token

Two mechanisms, because they answer different questions:

- **Epoch counter** (`stellarerp:auth:epoch:<user_id>`) - every token carries the
  user's epoch; incrementing it invalidates all of them at once. Used for password
  change, sign-out-everywhere, role change, suspension, and removal.
- **Per-session marker** (`stellarerp:auth:revoked-sid:<session_id>`) - revokes one
  device without signing the user out everywhere.

Both are checked in a single pipelined Redis round trip. Entries only need to
outlive the longest access token, after which the JWT expires on its own.

Rotation deliberately does **not** set a revoked-session marker. Rotation is not a
security event, and marking it would 401 any request already in flight with the
previous token.

---

## Authorization

### Permissions in code, roles in data

A permission is a capability the software implements, so it lives in an enum. If
`invoice:approve` existed as a row but no endpoint checked it, the row would be a
lie; if an endpoint checked a permission absent from the table, authorization
would silently fail. The enum cannot drift from the code, it is greppable, and it
type-checks.

Roles are per-organization rows holding a JSONB array of grant slugs. Wildcards
(`invoice:*`, `*:*`) are expanded eagerly at token-issue time, so the hot path is
a set-membership test with no pattern matching.

Unknown grants are **dropped** during expansion, not preserved. A permission
removed from the catalogue in a later release must stop granting access.

### Staleness

Permissions in the token means authorization costs no database query, bounded by
the 15-minute TTL. Anything that must apply immediately bumps the epoch:

- role changed → epoch bump → next request re-mints with new permissions
- member suspended or removed → epoch bump → access ends within milliseconds
- a role's permissions edited → epoch bump for every holder

`GET /auth/permissions` resolves live from the database, for a client that needs
current truth rather than what was true at issue time.

### Tenant isolation

The active organization comes from the signed token. No API path contains an
organization id, so there is nothing for a client to tamper with - cross-tenant
access is structurally impossible rather than merely checked.

Defence in depth on top of that: `RoleRepository.get_scoped` puts the tenant
filter *in the query* rather than checking after the fetch, so a cross-tenant id
returns no row instead of relying on a caller's `if`.

### The four proof-ledger permissions

`seal:read`, `seal:write`, `seal:configure` and `proof:export` are separate because
they are four different powers, and one of the splits is the interesting one:

| Permission | Power | Seeded roles that hold it |
| --- | --- | --- |
| `seal:read` | See the sealing status and history | owner, admin, accountant, sales, viewer |
| `seal:write` | Trigger a seal, drain, reconcile | owner, admin, accountant |
| `proof:export` | Hand a proof bundle to an outsider | owner, admin, accountant |
| `seal:configure` | Enable, disable, change cadence, rotate the signer | owner, admin |

**The accountant deliberately cannot `seal:configure`.** Somebody who keeps the books
should be able to prove them and unable to stop proving them - the ability to switch
attestation off is the ability to work unobserved, so it sits with the people who
answer for the organization rather than the people who write in it.

`proof:export` is separate from `seal:read` for the same reason a report and a
signature are different acts: reading that a batch was sealed is internal, and handing
a counterparty a document that discloses one journal entry in full is disclosure.

Note what none of them can do: **there is no permission that unseals anything.** No
role, including owner, and no superuser. `disable` stops future seals; written seals
stay written, because the contract has no update path and no administrative override.

### Lockout prevention

An organization must not be able to destroy its own administrability:

- the owner cannot be removed, suspended, demoted, or leave;
- exactly one owner per organization, enforced by a **partial unique index**, not
  only application code;
- a role still held by members cannot be deleted (`RESTRICT` foreign key plus a
  check that returns an actionable message);
- built-in roles cannot be deleted or renamed, though their permissions are
  editable;
- the Owner role cannot have `*:*` removed.

---

## Input handling

- **Separate request and response schemas.** A response schema reused as a request
  schema is how `is_superuser` becomes mass-assignable.
- **`extra="forbid"`** on every request schema. An unknown field is a 422, not a
  silent ignore - a client's typo should be reported, not swallowed.
- **`exclude_unset`** on partial updates, so "field omitted" is distinguishable
  from "field set to null". Without it, a client sending only `theme` would blank
  the user's phone number.
- **Allow-listed sort fields.** `sort_by` arrives from a query string;
  interpolating it into `ORDER BY` is an injection vector, so it is resolved
  against columns the repository opts into.
- **Parameterised queries throughout** via SQLAlchemy. No string-built SQL.
- **Open-redirect guard.** `redirect_path` on a magic link must be relative and
  must not begin with `//`.

---

## Document storage

Uploaded documents are compressed and stored in PostgreSQL, in the `document_blob` table, as
`BYTEA`. Object storage is available but optional, and needs an extra dependency; see
[`storage.py`](../backend/app/modules/ocr/storage.py) for the trade-offs. Four properties here
are security ones rather than plumbing.

### The path is never attacker-controlled

A blob's key is `{organization_id}/{sha256[:2]}/{sha256}.{ext}` - derived from a digest and
nothing else. A filename is attacker-supplied text, and joining one onto a path is how
`../../../etc/authorized_keys` becomes a write target; no amount of sanitising is as safe as
never using it. `original_filename` is kept for display and read by nothing that resolves a
location.

Retiring the filesystem backend removed the class entirely: there is no longer a path to
traverse, because the key is a value in a `WHERE` clause.

### Tenant isolation is a predicate, not a prefix

The key *contains* the organization id, which means a store that matched on key alone would
look correct. Every read instead filters `WHERE organization_id = ...` from the store's own
scope, which is set when it is constructed and never taken from the request path. There is a
test that asks a store scoped to one tenant for another tenant's key by exact string and
asserts it is not found.

### Bytes and rows commit together

A blob is written in the request's transaction. Under the retired filesystem backend - and
under a bucket today - the write is *outside* it, so a request that failed after storing bytes
leaked them permanently, and a restored backup could pair a row with a blob from a different
moment. Now a rolled-back upload leaves nothing, and one `pg_dump` captures a posted bill and
the scan that supports it at one consistent point in time.

That consistency is the security argument for the default backend: the integrity of accounting
evidence is not separable from the integrity of the entries citing it.

### The round trip is byte-exact, and checked

`document.sha256` is the digest of the **original** upload and does three jobs: the duplicate
key, the storage address, and the integrity check on download. Compression is therefore
lossless and never rewrites content - notably, PDFs are **not** re-encoded or re-rendered,
even though that would compress better. A file that renders identically but hashes differently
is not the file the supplier sent, and an auditor asking "is this the document the entry cites"
deserves an answer that is not "morally, yes".

The controls around that round trip:

- **Downloads verify the decompressed bytes** against the recorded digest, and fail with
  `blob_corrupted` rather than serving something else
- **Uploads are bounded while streaming** - `MAX_UPLOAD_BYTES`, 15 MB - so an oversized
  file is refused mid-flight rather than after it has been read
- **The declared content type is ignored**, in favour of the type sniffed from the bytes
- **The download response carries `Content-Disposition: attachment`, `nosniff`, and a
  `sandbox` CSP** - see [Transport and headers](#transport-and-headers)

---

## Secrets and logging

**logifyx redacts by default.** Passwords, tokens, and secrets are masked in every
log line, which is what makes it safe to log request metadata at all.

The audit trail has an independent redaction backstop (`audit/service.py::redact`),
applied recursively, covering:

- `password`
- `token`
- `totp_secret`
- `recovery_codes`
- `api_key`
- and more

A test drives a real password change end to end and asserts neither the old nor the new
password appears anywhere in the trail.

Production configuration is validated at boot. The app **refuses to start** if
`SECRET_KEY` is a placeholder, `DEBUG` is true, CORS is `*`, `ENCRYPTION_KEY` is
missing, or the database password is still a default. Crashing at boot is strictly
better than silently serving traffic with a placeholder signing key.

### Card numbers - staying out of PCI DSS scope

**No Primary Account Number is stored anywhere.** Cards are on file so a payment can say
which one it went on, and what is kept is the scheme and the last four digits - the two
things a card receipt and a bank statement already print.

This is deliberate scope avoidance, not a shortcut. Persisting a PAN would pull this
entire database, its backups, its replicas, and every host that touches them into PCI DSS
scope, in exchange for a convenience the product does not need: nothing here charges a
card, so the full number has no use after the moment it is typed.

How that is held in place:

- `billing/cards.py` is the only module that ever handles a full number. It imports
  nothing from the rest of the app, every function takes a number and returns something
  that is not one, and `inspect_card_number` returns exactly the two facts that get
  persisted. The PAN exists as a local variable for the length of one call.
- The `payment_card` table has **no column** it could go in. A test asserts that against
  `information_schema.columns` rather than trusting the model, so adding one fails the
  build. `CardRead` has no field to return one in either.
- **A rejected number is never echoed back.** The request schema rejects letters by
  pattern rather than by quoting the value, and the 422 handler forwards messages, never
  inputs. A test posts a malformed number and asserts the digits do not appear in the
  response.
- Both clients clear the field as soon as the request succeeds, and neither sets an
  autofill hint on it - the one "helpful" platform default that would undo the whole
  arrangement by inviting the browser or OS to store the number instead.

The Luhn check is duplicated client-side, which is safe because Luhn is a fixed algorithm
that cannot drift. The issuer-range table that identifies the scheme is **not** duplicated
- it lives only on the server.

### Bank account numbers - encrypted, not discarded

A **bank account number is stored in full**, Fernet-encrypted with the same key material as
`app_user.totp_secret`. That is the opposite decision to the one above, and the difference is
the point rather than an oversight:

| | Card number | Bank account number |
| --- | --- | --- |
| Still needed after entry? | No - the last four digits identify the card | Yes - you quote it to be paid, print it on invoices, match it to statements |
| Brings the DB into PCI DSS scope? | Yes | No |
| Stored | Never, in any form | In full, encrypted |

Discarding an account number would stop the software doing its job; keeping a PAN would take
on a compliance regime for no benefit. Both answers follow from the same reasoning.

The protections that apply to the stored number:

- **Encrypted at rest**, so it is not legible in a stolen dump or a database screenshot. A
  test reads the column directly and asserts the plaintext does not appear in it.
- **`account_number_last4` is kept separately in the clear**, so lists render without
  decrypting a row per line - the same trick as a card's last four digits.
- **One route returns it**, `GET /billing/money-accounts/{id}/details`, behind
  `account:read`. It is deliberately not a field on the account list that every load of the
  billing screen fetches, so decrypting is an explicit act rather than ambient.
- **It is never logged.** The account-creation log line carries the bank name and the last
  four digits only.
- Both clients set `autoComplete="off"` on the field, for the same reason the card field does.

### The sealing key - encrypted, and the honest limitation

Each organization gets its own Stellar account, and its secret is Fernet-encrypted
with the same key material as `app_user.totp_secret`. Per-organization rather than one
shared signer, for two reasons: one compromised key exposes one book, and each
organization's account supplies its own transaction sequence numbers, so two
organizations sealing at the same instant cannot collide.

**What that key can do is worth stating plainly, because the whole subsystem's value
depends on it.** It cannot rewrite a written seal - nothing can. What a holder of the
key *can* do is seal a doctored batch as though it were the original, which is
tampering *before* the seal rather than after it. Three things bound that:

- **Cadence.** Daily sealing leaves a one-day window, not a one-year one. Costing
  fractions of a cent per seal is what makes daily affordable, and affordability is
  what makes the window small.
- **The chain.** Seals form a hash chain the network timestamps, so a rewrite of any
  period requires re-sealing every period after it, publicly, at a time the network
  records.
- **`POST /attestation/signer/rotate`.** Moves the book onto a 2-of-3 multisig held
  with the business's accountant, after which no single machine - this one included -
  can seal alone. That is the honest end state, and it is a Stellar protocol
  primitive rather than a contract we would have to write and then audit forever.

The Trust screen says this rather than implying more. A trust product that overstates
what it proves is worse than one that proves nothing.

---

## The public verifier

`/api/v1/verify/*` is the **only unauthenticated router in the application**, and that
is a deliberate hole in an otherwise closed wall. It exists because the reader of a
proof is a bank's credit officer or an auditor, and requiring them to hold an account
here would defeat the design.

What makes it safe is not a check - it is that there is nothing behind it:

| | |
| --- | --- |
| **Issues a query?** | No. Neither handler touches the session it is handed; every response is computed from the caller's own bundle or read from the public Stellar ledger. A test counts the SQL statements and asserts zero |
| **Accepts an identifier that resolves to a tenant?** | No. A namespace is `SHA-256(organization_id ‖ install_salt)` - 32 opaque bytes, unlinkable to a named business until the business discloses it |
| **Can it confirm a guess?** | No. Guessing a namespace requires guessing the install salt, which is 32 random bytes and never leaves the server |
| **Rate limited?** | Separately, at `RATE_LIMIT_PUBLIC_VERIFY` (default 60/min per IP), because it is unauthenticated and Merkle folding is CPU work |
| **Body size** | Bounded by the global request-size limit; a bundle is a few kilobytes |

The salt is why the same organization sealing on two installs produces two unrelated
namespaces, and why an observer watching the contract sees traffic they cannot
attribute.

**And the endpoint is a convenience, not the verifier.** The real check runs in the
reader's browser against an RPC endpoint they can change on screen - a verdict issued
by the party being audited is not a verdict. See
[Proof ledger](attestation.md#the-encoding-exists-twice-on-purpose).

---

## Error reporting

Error tracking is **off unless `SENTRY_DSN` is set**, and when it is on, what may leave
this machine is filtered rather than trusted:

- **Request bodies are dropped entirely.** A `ValidationError` on an invoice carries
  the invoice - the customer, the amounts, the GSTIN. There is no subset of that safe
  to send to a third party, so none of it is sent.
- **SQL parameters are dropped.** A query's *shape* is useful for debugging; its
  bound values are the row.
- **Remaining values are scrubbed recursively** against a key blocklist, with a depth
  limit, and the scrubber **fails closed** - a structure it cannot walk is replaced
  rather than passed through.
- **Nothing is sent when the DSN is absent**, and the boot log says so explicitly, so
  a self-hosted deployment can confirm it rather than assume it.

Usage analytics stay in your own PostgreSQL and have **no free-text payload column**:
actions come from a closed vocabulary and context keys from an allow-list. An events
table with an open payload is how analytics ends up holding customer names and inside
the compliance boundary.

---

## Rate limiting

Two limiters, both on Redis, both governed by `RATE_LIMIT_ENABLED`. They are not
redundant.

### The blanket layer - tiered token buckets

`RateLimitMiddleware` classifies every request into one of seven tiers by method and path
(`app.core.ratelimit.classify`) and enforces a **token bucket** per tier:

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

Tiers rather than one number, because a single budget has to be set for the loosest
endpoint and therefore protects none of the others: a dashboard needs 300 reads a minute,
and that is a preposterous budget for a login form.

A **token bucket, not a fixed window.** A window keyed on `floor(now / 60)` lets a client
spend its whole budget in the last instant of one window and the whole of the next in the
first instant of the following one - twice the limit, back to back, straddling the
boundary. On the auth tier that is 20 password guesses against a budget of 10. Tokens
accrue continuously, so there is no boundary to straddle. It is a Lua script, so the read,
the refill and the decrement are one atomic round trip.

**Buckets key on the authenticated user** when a request carries a valid token, and on the
resolved client IP otherwise. Pure IP keying puts an entire NAT'd office in one bucket,
which is a denial of service users inflict on each other. The token's signature is
verified before its `sub` is used - an unverified `sub` is attacker-chosen, so a flood
could mint a fresh identity per request and never touch a limit. A revoked-but-unexpired
token is fine to key on: it is rejected a layer later, and until then it is a stable,
attributable identity.

The per-IP ceiling exists so that authenticating is not a way out of source-based limits,
and so an unauthenticated flood cannot walk across cheap endpoints staying under every
individual tier.

`OPTIONS` is classified as a read, so the preflight a browser sends before every
cross-origin write does not consume the write budget.

### The declarative layer - slowapi

`app/core/limiter.py` wires slowapi to the same Redis with a `moving-window` strategy, and
the auth handlers carry explicit budgets where a reader of the endpoint will see them:

```python
@router.post("/login", ...)
@limiter.limit(LOGIN_LIMIT)          # 5/minute
async def login(request: Request, ...): ...
```

**Why both.** The middleware is exhaustive - a route added tomorrow is limited without
anyone remembering to limit it - at the cost of stating the budget in a pattern table in
another module. The decorator is local and fires independently, so a pattern that stops
matching after a path is renamed does not silently unprotect the endpoints that matter
most.

**Decorator order is load-bearing.** `@limiter.limit` must sit *below* `@router.post`, so
that `limit()` wraps the handler and `post()` registers the wrapper. Reversed, the budget
is still registered but the route mounts the bare function - the endpoint is unlimited,
with nothing in the code or the logs to say so. Two tests assert on the mounted endpoint
for exactly this reason.

**slowapi is deliberately not the blanket layer.** Version 0.1.10 drives the *synchronous*
`limits` storage, so each check is a blocking Redis call on the event loop. On login,
already dominated by ~50 ms of Argon2, that is invisible; in front of every request it
would serialise the worker. That is why the middleware limiter is hand-written against
`redis.asyncio`.

Its `RateLimitExceeded` is mapped into the application's error envelope. slowapi's own body
is `{"error": "Rate limit exceeded: 5 per 1 minute"}`, and the frontend branches on
`error.code` - the un-normalised shape would arrive as a blank failure on the login form.

### Both fail open

If Redis is unreachable the request proceeds, with an error in the log. Rate limiting is a
protective layer, and turning a cache outage into a total outage is a strictly worse trade.
The consequence is that **Redis availability is a security property**;
`SWALLOW_STORAGE_ERRORS` in `app/core/limiter.py` is the switch if you want the credential
endpoints to fail closed instead.

**Volumetric shedding is not this system's job, and it is not claimed to be.** Whatever
sits in front - a platform router, a CDN, a reverse proxy you run - is what stops a flood
before it reaches Python at all. Everything above runs *after* the request has arrived, and
buys per-caller fairness, where the caller's identity is actually known.

---

## Transport and headers

**TLS terminates in front of this application, not in it.** There is no edge in this
repository and none in `docker-compose.prod.yml`: the stack is expected to sit behind a
terminator the operator already runs. So cipher suites, protocol versions, OCSP
stapling and session-ticket policy are configured **there** - the checklist in
[Deployment](deployment.md) says what to verify rather than what to paste.

What this application controls is the headers on its own responses, and those it sets
unconditionally.

HSTS carries a two-year `max-age` and `includeSubDomains`. `preload` is **opt-in**
(`HSTS_PRELOAD`): submitting a domain to the preload list is effectively permanent and
commits every subdomain to HTTPS forever, so it is not something to acquire by default.

### The response header set

`SecurityHeadersMiddleware` is registered **last**, which makes it the *outermost* layer
and the headers unconditional - present on a 429 from the limiter, a 404 from a guard, and
a 500 from a handler alike. Registered first (the obvious reading of "applied last on the
way out") it is innermost, and only decorates responses that reached the router: every
rejection goes out bare, which is the majority of what an attacker sees.

| Header | Closes |
| --- | --- |
| `X-Content-Type-Options: nosniff` | a browser reinterpreting JSON as HTML and executing it |
| `X-Frame-Options: DENY` | clickjacking, for browsers predating CSP `frame-ancestors` |
| `Referrer-Policy: strict-origin-when-cross-origin` | tokens in URLs leaking via `Referer` |
| `Cross-Origin-Opener-Policy: same-origin` | an opener navigating or inspecting this response |
| `Cross-Origin-Resource-Policy: same-origin` | another site embedding an API response as a subresource |
| `Cross-Origin-Embedder-Policy: require-corp` | the response being a cross-origin leak vector |
| `X-Permitted-Cross-Domain-Policies: none` | the `crossdomain.xml` mechanism, still honoured by PDF readers |
| `X-DNS-Prefetch-Control: off` | speculative resolution of hostnames in a response body |
| `Permissions-Policy` | 21 device capabilities, named explicitly rather than defaulted |
| `Cache-Control: no-store, private` | a shared proxy retaining one user's invoices for the next |

CSP on API responses loads nothing, frames nothing, submits nowhere, and adds `sandbox`:

```
default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none';
object-src 'none'; script-src 'none'; style-src 'none'; img-src 'none';
connect-src 'none'; font-src 'none'; media-src 'none'; worker-src 'none'; sandbox
```

The API returns only JSON, so every one of those capabilities is unused and denying them
all is both correct and free.

`Server` and `X-Powered-By` are stripped from application responses - naming the stack and
its version is a free CVE shortlist - and uvicorn also runs with `--no-server-header`.

One honest limit: **whatever terminates TLS announces itself**, and this application cannot
strip a header it never sees. A platform router advertises the platform, and a self-hosted
terminator generally advertises its own product name - most of them will drop the *version*
on request, which is the part that hands over a CVE shortlist, but not the name itself. So a
scanner learns the edge's identity and nothing more specific about what runs behind it.

**A route that sets its own value keeps it.** The document-download endpoint returns bytes
a stranger uploaded and sets a stricter `sandbox` CSP plus a deliberately private,
cacheable `Cache-Control`; overwriting either would silently remove a hardening measure or
turn every download into a fresh transfer. That is the regression a middleware assigning
unconditionally causes, and it is invisible in testing - so there is a test for it.

`Referrer-Policy` specifically protects magic-link, verification and invitation
tokens, which appear in URLs and would otherwise leak to third parties through the
`Referer` header. The password-reset code is deliberately *not* one of them: it is
typed into the page that requested it and never enters a URL, because a prefetched
reset link hands over the account permanently, where a prefetched sign-in link
grants only a session that 2FA still gates and the user can revoke.

HSTS is production-only. Sending it over plain HTTP in development would pin
`localhost` to HTTPS in the developer's browser and break every other local
project on that port.

---

## OWASP Top 10 (2021)

| Risk | Controls |
| --- | --- |
| A01 Broken access control | Token-derived tenancy, permission enum, scoped queries, owner protections, deny-over-grant overrides |
| A02 Cryptographic failures | Argon2id, Fernet at rest, TLS 1.2+, HSTS, hashed tokens |
| A03 Injection | Parameterised ORM queries, allow-listed sorts, Pydantic validation, Jinja autoescape |
| A04 Insecure design | Staged delivery, threat model, lockout prevention, reversible migrations |
| A05 Misconfiguration | Boot-time production validation (https origins, strong secrets, rate limiting and origin enforcement all required), no docs in production, non-root read-only containers with all capabilities dropped, internal-only data network |
| A06 Vulnerable components | Pinned lockfiles both sides; `npm ci` in CI installs the lockfile exactly and fails if it disagrees with `package.json`. `uv sync --frozen` is a local gate only - see the note below |
| A07 Auth failures | 2FA, lockout, rotation with reuse detection, no enumeration, session revocation |
| A08 Integrity failures | Append-only audit, pinned base images, `alembic check` for migration drift - run locally, not in CI |
| A09 Logging failures | logifyx everywhere with redaction, audit trail, request-id correlation |
| A10 SSRF | No user-supplied URL is fetched server-side. Documents arrive as uploaded bytes, never as a URL the server retrieves |

> **Two rows above depend on a person, not a pipeline.** CI runs the frontend checks and
> validates the compose files; there is no backend job. So `uv sync --frozen`,
> `alembic check`, ruff, mypy and pytest block nothing on their own - `make check` before
> pushing is what enforces them. Adding that job is the single highest-value hardening
> change available to this repository.

---

## Deferred to later stages

Stated plainly rather than left implied:

- **Passkeys / WebAuthn** - Stage 9.
- **SSO / SAML** - Stage 9.
- **Have I Been Pwned range check** - Stage 10. The local blocklist covers the
  worst offenders with no network dependency; the k-anonymity API is the proper
  version.
- **Database-level audit immutability** - Stage 9. A trigger denying
  `UPDATE`/`DELETE` to the application role. Today it is enforced by the absence
  of any code that mutates the table.
- **Secrets manager** - Stage 10. Currently environment variables.
- **API keys and webhook signing** - Stage 9.
- **Automated dependency scanning in CI** - Stage 10.

<!-- related:start -->

---

## Related reading

- [Audit](security-audit.md) - the review that produced several of these controls
- [API](api.md) - the surface they protect
- [Deployment](deployment.md) - the ones that only exist once it is on a server
- [Proof ledger](attestation.md) - what the sealing key can and cannot do, stated plainly

[All documentation](README.md)
<!-- related:end -->
