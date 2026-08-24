<div align="center">

# Security Policy

**What is in scope, how to report something you have found, and what happens next.**

![Report](https://img.shields.io/badge/report-privately-D29922?style=flat-square)
![Supported](https://img.shields.io/badge/supported-main_only-2EA043?style=flat-square)
![Audit](https://img.shields.io/badge/audit-16_findings_documented-8957E5?style=flat-square)
![Disclosure](https://img.shields.io/badge/disclosure-coordinated-4C8BF5?style=flat-square)

[Controls and threat model](https://github.com/Madhur-Prakash/Personal-ERP/blob/main/docs/security.md) · [Security audit](https://github.com/Madhur-Prakash/Personal-ERP/blob/main/docs/security-audit.md) · [Deployment](https://github.com/Madhur-Prakash/Personal-ERP/blob/main/docs/deployment.md) · [Documentation](https://github.com/Madhur-Prakash/Personal-ERP/blob/main/docs/README.md)

</div>

<!--
  Every link in this file is absolute, and that is not a style preference.

  GitHub renders this file in two places: the normal file view, and the Security tab at
  /security/policy. Relative links work in the first and BREAK in the second - the tab
  resolves them without the branch segment, so `docs/deployment.md` becomes
  /blob/docs/deployment.md, GitHub reads "docs" as the ref, and the reader gets
  "404 - Ref is invalid". Absolute URLs behave identically in both. Do not tidy them
  back to relative paths: the breakage is invisible from the file view, which is where
  anyone would think to check.
-->

---
## This is the policy

It sets out what this project promises, what it asks of you, and how a report is handled.

It is **not** the control set. What the software actually does to defend itself lives in
[docs/security.md](https://github.com/Madhur-Prakash/Personal-ERP/blob/main/docs/security.md):

- **The threat model** - what is defended against, in order of likelihood
- **Authentication** - password storage and policy, two-factor, account-enumeration and
  brute-force resistance
- **Sessions** - the access/refresh token split, rotation with reuse detection, and how a
  stateless token is revoked
- **Authorization** - how permissions are enforced, and how tenants stay isolated
- **Input and storage** - validation boundaries, document handling, secret redaction in
  logs
- **The edges** - rate limiting, transport headers, and a mapping to the OWASP Top 10

Each with the reasoning behind it, and the alternatives that were rejected.

> [!NOTE]
> **Every commitment here is kept.** Personal ERP is maintained by one person, in their
> own time, so this document promises only what one person can deliver: there is no
> guaranteed response time and no bug bounty. What there is: a reply to every report, a
> fix when the finding is real, and disclosure dates you can plan around.

---

## What this software is

A self-hosted ERP and double-entry accounting system. Every install runs on its owner's
own server, against their own PostgreSQL, with their own credentials - **there is no
service to compromise**, and no central deployment holding anyone's books.

That shapes the whole policy. A flaw here is not one operator's incident: it is latent in
every install until each of them pulls a fix. Which is exactly why it should reach the
maintainer before it reaches the public.

---

## Scope

### In scope

| | |
| --- | --- |
| **The application code** | Everything in this repository - backend, web client, desktop client, migrations |
| **The shipped configuration** | `docker-compose.yml`, `docker-compose.prod.yml`, the Dockerfiles, the Makefile, the CI workflow |
| **Documented defaults** | A setting whose documented value is unsafe is a finding, even if the code is doing what it was told |
| **The installer** | `installer/personal-erp.iss` and what it packages |

### What counts as a vulnerability here

If you can do any of the following, it is worth reporting. The first two matter most:
this is an accounting system, and the data is somebody's books.

| Class | What it looks like |
| --- | --- |
| **Cross-tenant access** | Any request that reads, changes or deletes another organization's data - customers, invoices, documents, members, anything |
| **Ledger integrity** | A posted entry that can be altered, deleted or hidden; a reversal that does not balance; a locked period that can be written to anyway |
| **Authentication** | Signing in as someone else; a session that survives sign-out or a password change; a token that can be forged, replayed, or refreshed after revocation |
| **Authorization** | Acting without the permission it requires - a Viewer who can post an entry, a member who can edit roles, an owner-only check that can be side-stepped |
| **Injection** | SQL, command, template or header injection - anywhere input is executed rather than treated as data |
| **Secret exposure** | A password, token, TOTP secret, recovery code or bank detail appearing in a response, a log line, or the audit trail |
| **Mass assignment** | Setting a field through a request that should never accept one - `is_superuser`, `is_owner`, `organization_id` |

Not sure which it is, or whether it counts? **Report it anyway.** A report that turns out
to be intended behaviour costs one reply; an unreported flaw costs every install.

### Out of scope

| | |
| --- | --- |
| **Your deployment's own infrastructure** | TLS terminator, DNS, host, firewall, database hardening. The [pre-flight checklist](https://github.com/Madhur-Prakash/Personal-ERP/blob/main/docs/deployment.md#8-pre-flight-checklist) is where to check whether the stack expected you to configure something |
| **Accepted limits already documented** | [What this does not solve](https://github.com/Madhur-Prakash/Personal-ERP/blob/main/docs/security-audit.md#what-this-does-not-solve) states them plainly - volumetric DDoS, infrastructure-level insider threat, supply-chain attestation |
| **Findings that need an admin already** | An owner can delete their own organization. That is the feature |
| **Missing hardening with no exploit** | A header that could be stricter, a dependency with a CVE in a code path this project never calls. Still welcome as an issue - just not as a vulnerability report |
| **Automated scanner output** | Pasted without a working reproduction. It costs more to triage than to run |

---

## Reporting a vulnerability

> [!IMPORTANT]
> **Do not open a public issue.**
>
> A GitHub issue is visible to everyone the moment you post it - and stays visible, in
> search results and in the repository's history, even if it is deleted afterwards.
>
> A public report of a live flaw is not a warning. **It is a working set of instructions
> for attacking every install still running the vulnerable code** - and those installs
> belong to real businesses, holding real books, run by people who will not see your
> issue and have no fix to apply yet. Reporting privately is what gives them a patch to
> pull before anyone knows there is something to attack.

**Use GitHub's private vulnerability reporting** - the **Security** tab on this
repository, then **Report a vulnerability**. It opens a thread visible only to the
maintainer, so a fix can be prepared and shipped before anything is public.

If that is disabled, email the address on the maintainer's GitHub profile with
`SECURITY` in the subject line.

### What to include

A report that can be reproduced gets fixed; one that cannot, gets a conversation. In
rough order of usefulness:

1. **The commit SHA or version** you tested (`git rev-parse --short HEAD`).
2. **The smallest sequence that reproduces it** - the endpoint or screen, the inputs, the
   role or permissions the account held.
3. **What you expected, and what happened instead.** For a cross-tenant finding, say
   which organization's data appeared where it should not have.
4. **Impact as you see it.** What can an attacker actually do with it, and what do they
   need first - an account, a role, a valid link, physical access?
5. **A proof of concept** if you have one. Welcome, never required.
6. **Logs**, if relevant. Redaction is on by default, so pasted output should be free of
   tokens - check anyway before sending.

### Please do not

- Test against a deployment that is not yours. Every install belongs to somebody, and
  their ledger is not a test fixture. Run it locally: `make setup && make up`.
- Access, modify, or retain data that is not yours if you do find a live instance.
- Run denial-of-service or volumetric tests against anything.
- Report it publicly, or to a third party, before the coordinated window below.

Research done within those lines is welcome and will not be met with a complaint or a
legal threat. That is as close to a safe-harbour statement as one maintainer can honestly
offer - it binds this project, and it cannot bind anyone else whose install you touch.

---

## How a report is handled

| Stage | What happens | When |
| --- | --- | --- |
| **Acknowledgement** | You get a reply confirming it was received and read | Within a few days |
| **Triage** | Reproduced, severity assessed, scope confirmed. If it cannot be reproduced you will be asked for specifics rather than dismissed | Days, not weeks |
| **Fix** | Developed privately, with a regression test that fails without it. Money-path and cross-tenant findings take priority over everything else in progress | Depends entirely on severity and on one person's availability |
| **Release** | The fix lands on `main`. Self-hosted installs update by pulling it | With the fix |
| **Disclosure** | The finding is written up, credited unless you would rather not be named | After the fix is on `main` |

**No promised fix time, and no bounty.** This is a single-maintainer project, not a
staffed programme, and a repair deadline nobody can meet helps nobody. What *is* dated is
[disclosure](#disclosure) - what you may do, and when - because that is the part you need
in order to plan, and it does not depend on how busy one person is.

If a report goes unanswered for **14 days**, assume it was missed rather than ignored -
send it again, or email to the maintainer's address on their GitHub profile with `SECURITY` in the subject line.

---

## Disclosure

**Coordinated, with dates rather than goodwill.** "A reasonable window" means nothing to
someone who has to decide whether to publish on a Tuesday, so here are the actual numbers:

| Situation | The ask |
| --- | --- |
| **While a fix is being worked on** | Please hold. If you have heard nothing at all for **14 days**, chase once - then treat the clock below as running |
| **Once the fix is on `main`** | Please allow **14 days** before publishing, so self-hosted installs have a chance to pull it |
| **If no fix has appeared** | **90 days** from your first report is a fair ceiling. Publish after that. A flaw sat on indefinitely because one maintainer is busy protects nobody |
| **If it is already being exploited** | None of the above applies. Tell whoever needs to know, whenever they need to know it |

**Set a deadline if you want one.** State it in your first message and it will be
respected rather than argued with. If it cannot be met, you will be told that before it
expires - along with what is left to do - rather than at the deadline or after it.

Credit is given in the fix and in the audit write-up unless you ask to stay anonymous.

---

## Supported versions

| What you are running | Supported | What that means for you |
| --- | :---: | --- |
| **The current `main`** | Yes | Every security fix lands here, and only here. Whatever `origin/main` points at today *is* the supported version |
| **An older commit of `main`** | No | Not a different version - just `main` from an earlier date, missing every fix since. Nothing is backported to it; pulling is what makes you supported again |
| **A fork, or a modified copy** | No | Report it to whoever maintains that copy. If it also reproduces on a clean `main`, report it here as well |
| **A tagged release** | n/a | There are none. See below |

**There are no releases, no tags, and no maintained release branches.** `main` is the
only thing that exists. That is not an oversight to be fixed later - it is what one
maintainer can keep honest. Two supported branches would mean every security fix has to
be written twice, tested twice, and remembered twice, and the version that quietly stops
getting the second half is the one somebody is running.

**What "supported" means here:** a fix lands on `main`, and that is the whole release
process. Nothing is backported, because there is nothing to backport *to*.

**What it means for you as an operator:**

| | |
| --- | --- |
| **You are on whatever you last pulled** | There is no version number to check and no upgrade notice. `git log --oneline HEAD..origin/main` tells you what you are missing |
| **Updating is a `git pull` and a rebuild** | The exact sequence, including taking a backup first and running migrations as a separate step, is [Deploying updates](https://github.com/Madhur-Prakash/Personal-ERP/blob/main/docs/deployment.md#6-deploying-updates) |
| **A pull is not always enough** | Some fixes change the *deployment shape* - a new setting in `.env`, a changed compose service, something your TLS terminator has to do. Those need a read, not just a rebuild |
| **The audit says which is which** | The [audit report](https://github.com/Madhur-Prakash/Personal-ERP/blob/main/docs/security-audit.md) marks the findings that changed the deployment rather than only the code |

**So: pull regularly.** An install that has not been updated in months is running every
flaw fixed since, and nothing in this repository will tell it so - there is no telemetry,
no update check, and no way for a fix to reach a server that never asks for it. That is
the trade that comes with self-hosting: nobody can read your data, and nobody can patch
it for you either.

---

## If you run this yourself

Some of the security of an install is yours, not this repository's. Two things in
particular, both worth reading before you go live:

- **`.env` holds live credentials** (finding 16 in the audit). Treat that file as a
  secret store: `chmod 600`, never committed, never pasted into an issue.
- **The stack terminates no TLS.** Whatever you put in front of it is a control surface
  this repository cannot check for you - which is why production refuses to boot without
  https origins rather than quietly serving sessions in the clear.

The [pre-flight checklist](https://github.com/Madhur-Prakash/Personal-ERP/blob/main/docs/deployment.md#8-pre-flight-checklist)
is the short version of everything the stack expects you to have configured.

---

## Already known

Two places worth checking before you write a report:

- **[The security audit](https://github.com/Madhur-Prakash/Personal-ERP/blob/main/docs/security-audit.md)** -
  sixteen findings against running code, each with its fix and how to verify it
- **[What this does not solve](https://github.com/Madhur-Prakash/Personal-ERP/blob/main/docs/security-audit.md#what-this-does-not-solve)** -
  the accepted limits, named rather than left implied

Checking there first saves us both a round trip.

<div align="center">

---

**Thank you for reporting responsibly.** A private report is what makes a fix possible
before the flaw is public.

</div>
