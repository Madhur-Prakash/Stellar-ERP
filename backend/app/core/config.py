"""Application configuration.

Single source of truth for every tunable in the backend. Values resolve in this
order (highest priority first):

    1. Real process environment variables
    2. The ``.env`` file at the repository root
    3. The defaults declared below

Nothing else in the codebase may read ``os.environ`` directly - import
:func:`get_settings` instead. That keeps configuration testable (override the
cache) and makes every knob discoverable in one file.
"""

from __future__ import annotations

import secrets
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BeforeValidator,
    Field,
    SecretStr,
    computed_field,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> <root>
BACKEND_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = BACKEND_DIR.parent


class Environment(StrEnum):
    """Deployment environment. Drives safety checks and defaults."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"

    @property
    def is_production(self) -> bool:
        return self is Environment.PRODUCTION

    @property
    def is_local(self) -> bool:
        return self in (Environment.DEVELOPMENT, Environment.TEST)


def _split_csv(value: object) -> object:
    """Accept ``a,b,c`` as well as a real JSON list for list-typed settings.

    Docker Compose and shell exports can only supply strings, so every list
    setting has to tolerate the comma-separated form.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):  # already JSON - parse it as such
            import json

            parsed = json.loads(stripped)
            return [str(item).strip() for item in parsed]
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return value


#: A list setting that accepts ``a,b,c`` from the environment.
#:
#: ``NoDecode`` is essential: without it pydantic-settings tries ``json.loads``
#: on the raw value *inside the env/dotenv source*, before any validator runs,
#: and a bare ``a,b,c`` raises SettingsError. NoDecode hands the string through
#: untouched so ``_split_csv`` can do the work.
CsvList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]


def _blank_to_none(value: object) -> object:
    """Treat an empty or whitespace-only value as "not set".

    Needed because a ``.env`` entry cannot be *removed* by the process environment, only
    overridden - and there is no string that means "ignore the file's value". Without
    this, ``DATABASE_URL=`` is a validation error rather than a way to fall back to the
    composed ``POSTGRES_*`` parts, which is exactly what the test suite needs in order to
    guarantee it is not pointed at the developer's real database.

    It also fixes the plain case: an operator who comments out the value but leaves the
    key gets the documented fallback instead of a crash at boot.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


#: A URL-shaped override where blank means "fall back to the composed parts".
OptionalDsn = Annotated[str | None, BeforeValidator(_blank_to_none)]


def _site_of(host: str) -> str:
    """A rough registrable domain: the last two labels of a hostname.

    Deliberately not a public-suffix lookup, which would mean shipping and refreshing
    the PSL to answer one question at boot. Two labels distinguishes the cases that
    actually occur - ``app.example.com`` and ``api.example.com`` are one site,
    ``…vercel.app`` and ``…onrender.com`` are not - and
    :attr:`Settings.cookie_samesite` is the escape hatch for the case it gets wrong.
    """
    cleaned = host.strip().lower().split(":")[0].rstrip(".")
    labels = [label for label in cleaned.split(".") if label]
    return ".".join(labels[-2:]) if len(labels) > 2 else cleaned


#: Period names accepted in a rate-limit spec, in seconds.
_RATE_PERIODS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def _rate_per_second(spec: str) -> float | None:
    """Requests per second described by ``"<count>/<period>"``, or ``None`` if malformed.

    A deliberate four-line duplicate of :func:`app.core.ratelimit.parse_budget`, which is
    the real parser and the one the limiter uses. Importing it here is a cycle - this
    module is what every other module imports for configuration, including the logging
    setup that ``ratelimit`` acquires a logger from - and the alternative, deferring the
    whole check to startup, would put it somewhere nobody reads.

    Returns a rate rather than a count so that ``600/hour`` and ``10/minute`` compare
    equal, which is the comparison the caller actually wants.
    """
    try:
        count, period = spec.split("/", 1)
        return int(count) / _RATE_PERIODS[period.strip().lower().rstrip("s")]
    except (ValueError, KeyError, ZeroDivisionError):
        return None


class Settings(BaseSettings):
    """Typed, validated application settings."""

    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # the shared .env also holds VITE_*/LOG_* keys
    )

    # ---- Runtime ------------------------------------------------------------
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = True
    app_name: str = "Stellar ERP"
    app_version: str = "0.1.0"

    # ---- HTTP ---------------------------------------------------------------
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    api_v1_prefix: str = "/api/v1"
    cors_origins: CsvList = Field(default_factory=lambda: ["http://localhost:5173"])
    allowed_hosts: CsvList = Field(default_factory=lambda: ["localhost", "127.0.0.1"])

    #: The hostname Render serves this service at, injected by the platform itself.
    #:
    #: Read only to fold into :attr:`allowed_hosts` - see :meth:`_allow_platform_hostname`.
    #: Nothing else should use it: a deployment on any other platform leaves it unset, and
    #: code that assumes a value here would work in exactly one place.
    render_external_hostname: str | None = None

    # ---- No edge-gateway secret ---------------------------------------------
    # There was a `GATEWAY_SECRET` here: a value an nginx in front of this service would
    # stamp on every forwarded request, which the backend then required. It is gone,
    # because this service *is* the public edge - it runs behind a platform router
    # (Render, Fly, a PaaS) rather than behind a proxy we configure.
    #
    # The distinction it drew is real, and worth recording so nobody re-adds it by halves.
    # It proved "this request came through my proxy", which closes the side door: the
    # backend reachable at its own address, bypassing whatever the edge does. It could
    # never be a *client* credential - a header a browser bundle holds is readable by
    # anyone who opens devtools - so only a server-side proxy could satisfy it, and there
    # is no such proxy here.
    #
    # It also never made the API private. Anyone can walk through the front door; it only
    # required that they use it. Everything that actually authorises a caller is still
    # here: JWT auth, RBAC, the origin check below, per-tier rate limits, host validation.
    #
    # Re-adding it is only worthwhile alongside a proxy configuration that stamps the
    # header, and that must *overwrite* any client-supplied value rather than pass one
    # through - otherwise the check is satisfied by the attacker it exists to stop.

    #: Enforce ``Origin``/``Referer`` on state-changing requests.
    #: Blocks browser-based CSRF by verifying headers browsers cannot forge.
    #: Non-browser clients are unaffected.
    enforce_origin: bool = True

    #: Force the refresh cookie's ``SameSite``, overriding what is derived below.
    #:
    #: Only needed when the derivation is wrong for your hosting - two apps on different
    #: subdomains of one *public suffix* (``a.vercel.app`` and ``b.vercel.app``) look
    #: same-site to the comparison in :attr:`refresh_cookie_samesite` but are cross-site
    #: to the browser. Setting ``none`` there is the fix.
    cookie_samesite: Literal["strict", "lax", "none"] | None = None

    #: Trust ``X-Forwarded-*`` headers when resolving the client.
    #: Safe behind trusted proxies because client IP is resolved from the
    #: proxy-facing end, not client-controlled values.
    trust_proxy_headers: bool = True

    #: Number of trusted proxies in front of this service.
    #: Each proxy appends to ``X-Forwarded-For``; the client IP is the Nth
    #: address from the right. An incorrect value can expose client-controlled IPs.
    trusted_proxy_hops: int = Field(default=1, ge=1, le=8)

    #: Maximum size for non-upload request bodies.
    #: Enforced from ``Content-Length`` before reading the body. File uploads
    #: are limited separately by :attr:`max_upload_bytes`.
    max_request_bytes: int = Field(default=1024 * 1024, ge=16 * 1024)

    #: HSTS ``max-age``, in seconds. Two years, the preload-list minimum.
    hsts_max_age: int = Field(default=63_072_000, ge=0)

    #: Add ``preload`` to the HSTS header.
    #: Enable only after all domains and subdomains support HTTPS.
    hsts_preload: bool = False

    # ---- PostgreSQL ---------------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "stellarerp"
    postgres_password: str = "stellarerp"
    postgres_db: str = "stellarerp"

    #: Full DSN override. Ignores all ``POSTGRES_*`` settings when set.
    #:
    #: Blank values are treated as unset. The DSN is validated at startup so
    #: configuration errors fail fast.
    database_url: OptionalDsn = None
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=20, ge=0)
    db_pool_recycle: int = Field(default=1800, ge=60)
    db_echo: bool = False

    #: Run ``alembic upgrade head`` before serving requests.
    #:
    #: Useful for single-instance deployments to automatically initialize or
    #: update the database. Disabled by default for staged or multi-instance
    #: deployments.
    run_migrations_on_startup: bool = False

    # ---- Redis --------------------------------------------------------------
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None

    #: Full Redis URL override. When set, every ``REDIS_*`` part above is ignored -
    #: including ``REDIS_DB``, which is how the test suite isolates itself. See
    #: :meth:`_enforce_test_safety`.
    redis_url: OptionalDsn = None

    # ---- Security -----------------------------------------------------------
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(64))
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=1440)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=365)
    argon2_time_cost: int = Field(default=3, ge=1)
    argon2_memory_cost: int = Field(default=65536, ge=8192)
    argon2_parallelism: int = Field(default=4, ge=1)
    encryption_key: str | None = None

    # ---- Auth policy --------------------------------------------------------
    email_verification_ttl_hours: int = Field(default=24, ge=1)
    password_reset_ttl_minutes: int = Field(default=30, ge=1)
    magic_link_ttl_minutes: int = Field(default=15, ge=1)
    otp_ttl_minutes: int = Field(default=10, ge=1)
    otp_length: int = Field(default=6, ge=4, le=10)
    max_login_attempts: int = Field(default=5, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)
    invite_ttl_days: int = Field(default=7, ge=1)

    # ---- Rate limiting ------------------------------------------------------
    #: Budgets use the ``"<count>/<period>"`` format (e.g. ``10/minute``).
    #:
    #: Each limit uses a token bucket, allowing bursts up to ``count`` while
    #: maintaining the configured average rate.
    rate_limit_enabled: bool = True

    #: Anything not matched by a more specific tier.
    rate_limit_default: str

    #: Credential and enumeration surfaces: login, register, 2FA, token exchange.
    rate_limit_auth: str

    #: Rate limit for auth endpoints that send email or issue one-time secrets.
    #:
    #: Stricter to prevent inbox spam and abuse.
    rate_limit_auth_strict: str

    #: Rate limit for read operations (list, get, search).
    #:
    #: Tuned for dashboard traffic, allowing page-load bursts while limiting
    #: sustained request rates.
    rate_limit_read: str

    #: Writes: POST/PATCH/PUT/DELETE outside auth. Each one costs a transaction and
    #: usually an audit row, so the budget is an order of magnitude below reads.
    rate_limit_write: str

    #: Document uploads. Every one runs OCR inline, which is seconds of CPU - this is
    #: the most expensive thing an authenticated user can ask for.
    rate_limit_upload: str

    #: Report exports (xlsx/pdf/csv). Each renders a full statement in memory.
    rate_limit_export: str

    #: The unauthenticated verification endpoints.
    #:
    #: Given a default, unlike the tiers above, and deliberately: those are required
    #: so an operator has to think about them, but this one guards a route that
    #: exists whether or not anybody configured it. A missing value must not be the
    #: reason the public verifier is unprotected.
    #:
    #: Generous, because the intended caller is a bank's credit officer checking a
    #: handful of invoices and each check costs one contract simulation - but
    #: bounded, because "unauthenticated" and "free to hammer" are different things.
    rate_limit_public_verify: str = "60/minute"

    #: Global per-IP limit applied alongside user-based rate limits.
    #:
    #: Prevents abuse from a single source, even with multiple users or stolen
    #: tokens. Should normally be higher than per-user limits.
    rate_limit_ip: str

    # ---- Per-endpoint budgets ------------------------------------------------
    #: ``/login`` and the 2FA + recovery-code endpoints. Five a minute is above any human's
    #: typing speed and far below anything useful for guessing. Bounds one *source*; the
    #: per-account side is :attr:`max_login_attempts`.
    rate_limit_login: str

    #: ``/register``. Account-creation spam costs a row and an outbound email each.
    rate_limit_register: str

    #: Every endpoint that **sends mail**: ``/forgot-password``, ``/magic-link``, ``/otp``,
    #: ``/resend-verification``. Tightest here on purpose - abuse spends someone else's inbox
    #: and this deployment's sending reputation, and a burned sending domain does not recover
    #: quickly. Raise this one if reset codes are being refused too eagerly.
    rate_limit_mail_sending: str

    #: ``/refresh``. A client needs one call per access-token lifetime (15 minutes by
    #: default), so this is three orders of magnitude of headroom and still bounds a
    #: token-churning loop.
    rate_limit_token_exchange: str

    #: ``/health``, ``/health/live`` and ``/health/ready``.
    #:
    #: Required, like every other budget here, so no deployment can silently run these
    #: unmetered - and required means required *everywhere the app boots*, including a
    #: platform dashboard, where there is no ``.env`` to fall back on. Omit it there and
    #: the process refuses to start.
    #:
    #: Choose the number with the probe interval in hand. ``5/minute`` allows one probe
    #: every 12 seconds per caller; anything faster collects a 429, which the platform
    #: reads as unhealthy, restarts the instance over, and throttles its replacement just
    #: as quickly. The endpoint worth protecting is ``/ready``, which pings PostgreSQL and
    #: Redis on every call.
    rate_limit_health: str

    # ---- Email (Gmail API) --------------------------------------------------
    gmail_credentials_b64: str | None = None
    gmail_sender: str | None = None
    email_from_name: str = "Stellar ERP"

    # ---- Frontend -----------------------------------------------------------
    frontend_url: str = "http://localhost:5173"

    # ---- Documents & OCR ----------------------------------------------------
    #: Hard ceiling on one upload. A 600 dpi colour scan of an A4 invoice is
    #: ~8 MB, so 15 MB accepts real documents and refuses everything else - the
    #: limit is enforced while streaming, so an oversized body is never buffered.
    max_upload_bytes: int = Field(default=15 * 1024 * 1024, ge=64 * 1024)

    # ---- Object storage: optional, off unless configured ---------------------
    #: S3-compatible storage for document blobs. **Leave these blank.**
    #:
    #: Blank is the default and the supported configuration: documents are compressed into
    #: PostgreSQL (``document_blob``), in the same transaction as the row describing them and
    #: covered by the same ``pg_dump``. See :mod:`app.modules.ocr.storage`.
    #:
    #: Setting all three of endpoint / access key / secret switches to a bucket, for an
    #: install whose blobs have outgrown the database. Two of three reads as "not
    #: configured" - see :attr:`document_storage`.
    minio_endpoint: str = ""
    minio_access_key: str = ""
    #: ``SecretStr("")``, not ``""``. A bare string here is not merely a type error: pydantic
    #: does not validate defaults, so the attribute would be a plain ``str`` at runtime and
    #: :attr:`document_storage` would raise ``AttributeError`` on ``.get_secret_value()`` the
    #: moment an endpoint and access key were configured without a secret.
    minio_secret_key: SecretStr = SecretStr("")
    minio_bucket: str = "stellarerp-documents"

    #: TLS to the object store. False only for a store on the loopback interface; true for
    #: anything reachable over a network - the credentials and the documents both cross it.
    minio_secure: bool = False

    # ---- Logifyx (backend logging) ---------------------------------------------
    # See https://pypi.org/project/logifyx/ - the LOG_* keys are read by logifyx itself.
    #: Single shared log file for the whole process, read by
    #: :func:`app.core.logging._resolve_log_file`.
    #:
    #: **It needs a default.** Declared bare it becomes a *required* setting, so a deployment
    #: that never set ``LOG_FILE`` - which is every deployment that was happy with logifyx's
    #: own default - stops booting, with a validation error about logging while the operator
    #: is looking for what they changed about the database.
    log_file: str = "stellarerp.log"

    #: Whether ``/health*`` requests get the same "request completed" line as everything
    #: else (:class:`~app.core.middleware.RequestContextMiddleware`).
    #:
    #: Tri-state on purpose. Unset resolves to :attr:`log_health_probes_enabled` - on
    #: everywhere but production - because the two situations are genuinely different: in
    #: development the probe is something you just typed and want to see answered, while in
    #: production it is an orchestrator firing every few seconds, and those lines bury the
    #: real traffic they sit between. Set it explicitly to force either behaviour, which is
    #: what you want when a probe is failing on a live deployment and the silence is the
    #: thing making it hard to diagnose.
    log_health_probes: bool | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def log_health_probes_enabled(self) -> bool:
        """Resolved value of :attr:`log_health_probes`; quiet in production by default."""
        if self.log_health_probes is not None:
            return self.log_health_probes
        return not self.environment.is_production

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cookie_is_secure(self) -> bool:
        """``Secure`` on the refresh cookie: everywhere but local development.

        Relaxed locally because there is no TLS there and the browser would refuse to
        store the cookie at all, which would break sign-in on a developer's machine.
        """
        return not self.environment.is_local

    @property
    def refresh_cookie_samesite(self) -> Literal["strict", "lax", "none"]:
        """``SameSite`` for the refresh cookie, derived from where the frontend lives.

        **`strict` is correct only while the app and the API are one site.** When they
        are not - an SPA on ``…vercel.app`` calling an API on ``…onrender.com`` - the
        browser withholds a `strict` cookie from every request to the API, including
        ``POST /auth/refresh``. The failure is quiet and very confusing: signing in works
        (that response *sets* the cookie), the tab keeps working off the in-memory access
        token, and then a page reload or a second tab finds no session at all, because
        the one request that could restore it is the one the cookie never reaches.

        So a cross-site deployment gets ``none``, which is the only value a browser will
        send cross-site - and only with ``Secure``, so without TLS this falls back to
        ``lax`` rather than emitting a combination browsers reject outright.

        **What is given up, and what covers it.** `strict` was this codebase's CSRF
        defence for the refresh endpoint. `none` gives that up, and
        :class:`~app.core.middleware.OriginGuardMiddleware` takes over: it rejects any
        state-changing request whose ``Origin`` is not in ``CORS_ORIGINS``, and
        ``/auth/refresh`` is a POST, so it is covered. Refresh tokens also rotate on
        every use and reuse revokes the lineage, so a replayed cookie is detected rather
        than merely being harder to obtain. Set :attr:`cookie_samesite` to force a value
        if that trade is not one you want to make.
        """
        if self.cookie_samesite is not None:
            return self.cookie_samesite

        frontend_host = urlsplit(self.frontend_url).hostname
        if not frontend_host:
            return "strict"

        if _site_of(frontend_host) in {_site_of(host) for host in self.allowed_hosts}:
            return "strict"

        return "none" if self.cookie_is_secure else "lax"

    @property
    def document_storage(self) -> Literal["object", "database"]:
        """Which backend holds document blobs. ``"database"`` unless a bucket is configured.

        Derived from whether object-storage credentials are present rather than set by a
        separate variable. A separate switch is a way for the credentials and the backend to
        disagree - configured but unused, or selected but unusable - and neither failure
        announces itself until someone uploads a file.

        All three of endpoint, access key and secret are required to switch. Two out of three
        is a half-finished configuration, and the safe reading of it is "not configured": the
        alternative is a deployment that boots happily and fails on its first upload.
        """
        configured = (
            self.minio_endpoint
            and self.minio_access_key
            and self.minio_secret_key.get_secret_value()
        )
        return "object" if configured else "database"

    ocr_enabled: bool = True

    #: Absolute path to the Tesseract binary. Blank means "find it on PATH".
    #: Needed because the Windows installer does not add itself to PATH, so the
    #: engine is unreachable on machines where it is plainly installed.
    tesseract_cmd: str = ""

    #: Tesseract language packs, ``+``-separated (e.g. ``eng+hin``). Each one must
    #: be installed alongside the binary; naming a missing pack makes recognition
    #: fail outright rather than degrade.
    ocr_languages: str = "eng"

    #: Wall-clock ceiling on one recognition pass. Tesseract on a large noisy
    #: image can run for minutes, and a request that never returns is worse than
    #: one that fails with an explanation.
    ocr_timeout_seconds: int = Field(default=30, ge=1, le=300)

    # ---- Ledger 3: the proof ledger on Soroban ------------------------------
    #: Master switch for the third ledger.
    #:
    #: The ERP is complete and fully usable with this off - Ledger 1 and Ledger 2
    #: do not depend on it in either direction. That independence is deliberate and
    #: it is what the layering buys: the attestation module imports from accounting,
    #: never the reverse, so an install that wants nothing to do with a blockchain
    #: turns this off and loses no accounting capability whatsoever.
    attestation_enabled: bool = True

    #: ``testnet`` or ``public``. Testnet is the default and the honest one: a
    #: default of ``public`` would have a fresh install writing commitments to
    #: mainnet before anybody had decided to.
    stellar_network: Literal["testnet", "public"] = "testnet"

    #: The deployed ``proof_ledger`` contract - a ``C...`` address.
    #:
    #: Unset means sealing cannot start, and the Trust screen says exactly that
    #: rather than failing on the first attempt. Each organization *copies* this
    #: value onto its own settings row when sealing is switched on, so changing it
    #: here re-points new organizations without stranding the proofs already issued
    #: by existing ones - see :attr:`AttestationSetting.contract_id`.
    soroban_contract_id: str | None = None

    #: Override the RPC endpoint. Blank uses the public one for the chosen network.
    #:
    #: Worth overriding for a real deployment, and the reason the verifier lets its
    #: endpoint be changed too: a scheme whose whole claim is "you do not have to
    #: trust us" should not quietly require everybody to trust one hosted RPC.
    soroban_rpc_url: str | None = None

    #: Inclusion fee in stroops. The resource fee is computed by simulation and
    #: added on top, so this is only the priority bid.
    #:
    #: 1,000 stroops is 0.0001 XLM - ten times the network minimum, and still a
    #: rounding error against the value of a seal. Deliberately generous: the
    #: failure mode of bidding the minimum is a seal stranded in a busy ledger, and
    #: the whole security argument depends on seals landing on schedule rather than
    #: eventually.
    stellar_base_fee: int = Field(default=1_000, ge=100, le=10_000_000)

    #: How long to wait for a submitted transaction before giving up on *knowing*
    #: the outcome. Soroban closes ledgers about every five seconds, so 45 covers
    #: several rounds.
    #:
    #: Giving up here does not fail the seal. It parks it as ``submitted`` for the
    #: reconciler, because a transaction that has left the process may still land -
    #: see :mod:`app.modules.attestation.stellar`.
    soroban_timeout_seconds: int = Field(default=45, ge=10, le=300)

    #: Gap between polls while waiting for confirmation.
    soroban_poll_seconds: float = Field(default=2.0, ge=0.5, le=15.0)

    #: Salt folded into an organization's on-chain namespace:
    #: ``SHA-256(organization_id || salt)``.
    #:
    #: This is what makes the chain unlinkable to a named business. Without it,
    #: anyone who guessed an organization id could confirm the guess by hashing it
    #: and looking for the namespace on chain.
    #:
    #: **Rotating it is safe**, which is not obvious and is worth stating: the
    #: computed namespace is stored on the organization's settings row the first
    #: time sealing is switched on, and never recomputed. A new salt therefore
    #: affects only organizations that have not started sealing yet. It is *not*
    #: safe to rotate the salt and then delete a settings row.
    #:
    #: Defaults to a value derived from :attr:`secret_key` so a fresh install works
    #: without another mandatory secret; production validation requires it to be
    #: set explicitly, because deriving it from the signing key ties two unrelated
    #: rotations together.
    attestation_namespace_salt: str | None = None

    #: Whether this process runs the seal worker in-process.
    #:
    #: On by default because the target deployment is one ``docker compose up`` on
    #: one small VPS, and requiring a second process for the feature to work at all
    #: would contradict that. A larger install turns it off here and runs
    #: ``python -m app.modules.attestation.worker`` as its own container, which is
    #: the same code path - the worker is a loop around a function the API can also
    #: call, not a parallel implementation.
    seal_worker_enabled: bool = True

    #: Seconds between worker passes. A pass with nothing to do is one indexed
    #: query, so this can be short without costing anything.
    seal_worker_interval_seconds: int = Field(default=60, ge=10, le=3600)

    #: Hour (0-23, organization-local) at which the daily cadence seals.
    #:
    #: 1 a.m. rather than midnight: an entry typed at 23:58 should land in the seal
    #: for the day it was typed, and a boundary exactly at midnight makes that a
    #: race against the clock.
    seal_daily_hour: int = Field(default=1, ge=0, le=23)

    #: Largest number of entries one seal may cover.
    #:
    #: A cap exists because the Merkle tree and its proof paths are built in memory,
    #: and because a first seal after a long backfill would otherwise try to cover
    #: every entry a business has ever posted. Exceeding it is not an error - the
    #: batch is split and the next pass takes the rest, so a large backlog drains
    #: over several seals instead of failing as one.
    seal_max_batch: int = Field(default=5_000, ge=1, le=100_000)

    # ---- Error tracking and product analytics -------------------------------
    #: Sentry DSN. Blank disables error tracking entirely, and nothing is sent.
    #:
    #: Optional on purpose: this is a self-hosted product whose pitch is that the
    #: books stay on the operator's own server, and a hard dependency on a
    #: third-party error tracker would undercut that. Configured, it reports
    #: exceptions; unconfigured, the logifyx log is still the record.
    sentry_dsn: str | None = None

    #: Fraction of requests traced for performance. Sampling rather than
    #: all-or-nothing: full tracing on a small VPS costs more than the insight is
    #: worth.
    sentry_traces_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)

    #: Fraction of requests profiled. Off by default - profiling is the most
    #: expensive thing the SDK does, and it answers a question nobody has yet on a
    #: fresh deployment.
    sentry_profiles_sample_rate: float = Field(default=0.0, ge=0.0, le=1.0)

    #: Record product-usage events (which screens, which actions) in PostgreSQL.
    #:
    #: First-party and local by design. The alternative - a hosted analytics script
    #: in the browser - would mean shipping every user's navigation to a third party
    #: from a product sold on data sovereignty. Events carry an organization and an
    #: action, never a customer name and never an amount.
    usage_analytics_enabled: bool = True

    # -------------------------------------------------------------------------
    # Derived values
    # -------------------------------------------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_dsn(self) -> str:
        """asyncpg DSN. Explicit ``DATABASE_URL`` wins over the composed parts."""
        if self.database_url is not None:
            dsn = str(self.database_url)
            # Normalise whatever scheme was supplied to the async driver.
            for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
                if dsn.startswith(prefix):
                    return "postgresql+asyncpg://" + dsn.removeprefix(prefix)
            return dsn
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_name(self) -> str:
        """The database the app will actually connect to.

        Parsed from whichever source won, because "which database is this?" is otherwise
        two different questions depending on how the DSN was configured - and the answer
        is what :meth:`_enforce_test_safety` checks before anything is allowed to drop a
        table.
        """
        path = urlsplit(self.sqlalchemy_dsn).path.lstrip("/")
        return path.split("?", 1)[0] or self.postgres_db

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_dsn(self) -> str:
        if self.redis_url is not None:
            return str(self.redis_url)
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def emails_enabled(self) -> bool:
        """When false, mail is logged instead of sent (the dev default)."""
        return bool(self.gmail_credentials_b64)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def docs_enabled(self) -> bool:
        """Whether the interactive docs and the OpenAPI schema are served at all.

        False in production, with no setting to override it. The schema is a complete
        map of every route, parameter, and error shape in the system - the single most
        useful document an attacker can be handed, and one nobody needs at runtime on a
        live deployment. Generate it in CI (``python -m app.openapi``-style scripts, or
        the staging deployment) where it costs nothing.

        :mod:`app.core.middleware` enforces this a second time at the HTTP layer, so a
        route re-added by hand cannot quietly reopen it.
        """
        return not self.environment.is_production

    @computed_field  # type: ignore[prop-decorator]
    @property
    def docs_url(self) -> str | None:
        """OpenAPI docs are never exposed in production."""
        return "/docs" if self.docs_enabled else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redoc_url(self) -> str | None:
        return "/redoc" if self.docs_enabled else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def openapi_url(self) -> str | None:
        return "/openapi.json" if self.docs_enabled else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def namespace_salt(self) -> str:
        """The resolved salt for on-chain organization namespaces.

        Falls back to a value derived from :attr:`secret_key` so a fresh checkout
        can switch sealing on without configuring a second secret. Derived through
        a domain-separated hash rather than used directly, so the signing key is
        never itself written into a namespace that ends up on a public ledger.

        Production validation requires the explicit setting, because the fallback
        ties the salt's lifetime to the JWT signing key's - and those rotate for
        completely unrelated reasons.
        """
        import hashlib

        if self.attestation_namespace_salt:
            return self.attestation_namespace_salt
        return hashlib.sha256(f"stellar-erp:namespace-salt:{self.secret_key}".encode()).hexdigest()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def attestation_ready(self) -> bool:
        """Whether the proof ledger could seal anything at all.

        Read by the Trust screen so the reason sealing is unavailable is stated up
        front - "no contract configured" - rather than discovered as a failed seal
        an hour later.
        """
        return bool(self.attestation_enabled and self.soroban_contract_id)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def stellar_explorer_base(self) -> str:
        """Explorer root for the configured network, for building links once."""
        return (
            "https://stellar.expert/explorer/public"
            if self.stellar_network == "public"
            else "https://stellar.expert/explorer/testnet"
        )

    # -------------------------------------------------------------------------
    # Guardrails
    # -------------------------------------------------------------------------
    @model_validator(mode="after")
    def _validate_dsn_overrides(self) -> Self:
        """Reject a malformed ``DATABASE_URL`` or ``REDIS_URL`` at boot.

        These are plain strings so that blank can mean "not set" (see
        :func:`_blank_to_none`), which means the scheme check pydantic's ``PostgresDsn``
        used to perform has to happen here instead. A typo'd scheme is otherwise a
        connection error on the first query rather than a message at startup.
        """
        if self.database_url is not None and not self.database_url.startswith(
            ("postgresql://", "postgres://", "postgresql+asyncpg://", "postgresql+psycopg://")
        ):
            raise ValueError(
                "DATABASE_URL must start with postgresql://, postgres://, "
                "postgresql+asyncpg:// or postgresql+psycopg://"
            )
        if self.redis_url is not None and not self.redis_url.startswith(
            ("redis://", "rediss://", "unix://")
        ):
            raise ValueError("REDIS_URL must start with redis://, rediss:// or unix://")
        return self

    @property
    def rate_limit_tiers_eclipsed_by_ip(self) -> dict[str, str]:
        """Tier budgets that :attr:`rate_limit_ip` has made unreachable.

        Both buckets must have room for a request to pass, so a per-IP ceiling set below a
        tier means that tier never binds and the per-IP number is the only real limit.
        That is a legitimate choice - a deployment may want one hard per-source figure -
        but it produces a system whose behaviour does not match what a reader of the
        configuration would describe, and the symptom is intermittent 429s that track how
        many colleagues are online rather than anything the user did.

        A property rather than a validator that logs, because :mod:`app.core.logging`
        imports this module to configure itself - so nothing here can acquire a logger
        while the settings object is still being built. :mod:`app.main` reports it at
        startup instead, where logging is up and an operator will actually see it.

        Not a ``computed_field``: this is a diagnostic, and it has no business appearing in
        a serialised dump of the configuration.

        Compared as rates rather than counts so ``600/hour`` and ``10/minute`` are
        equivalent, which is the comparison that matters.
        """
        ip_rate = _rate_per_second(self.rate_limit_ip)
        if ip_rate is None:  # malformed; the limiter's own fallback reports it
            return {}

        tiers = {
            "RATE_LIMIT_DEFAULT": self.rate_limit_default,
            "RATE_LIMIT_AUTH": self.rate_limit_auth,
            "RATE_LIMIT_AUTH_STRICT": self.rate_limit_auth_strict,
            "RATE_LIMIT_READ": self.rate_limit_read,
            "RATE_LIMIT_WRITE": self.rate_limit_write,
            "RATE_LIMIT_UPLOAD": self.rate_limit_upload,
            "RATE_LIMIT_EXPORT": self.rate_limit_export,
        }
        return {
            name: spec
            for name, spec in tiers.items()
            if (rate := _rate_per_second(spec)) is not None and rate > ip_rate
        }

    @model_validator(mode="after")
    def _allow_platform_hostname(self) -> Self:
        """Add the platform's own hostname to :attr:`allowed_hosts` when it provides one.

        **This closes the worst deployment failure this configuration has.** In production
        ``TrustedHostMiddleware`` refuses any request whose ``Host`` is not on the list,
        before routing, with a plain ``400 Invalid host header``. Deploy to Render without
        remembering ``ALLOWED_HOSTS`` and the result is not an obvious outage: the health
        probes answer 200 (they are exempt - see
        :class:`~app.core.middleware.ProbeExemptTrustedHostMiddleware`), the platform
        reports the service healthy, and **every single API call returns 400**. The app
        looks up and is entirely unusable, and nothing in the logs names the variable.

        ``RENDER_EXTERNAL_HOSTNAME`` is set by Render on every service and is exactly the
        name it serves this process at. Trusting it is not a widening of the control: it
        comes from the platform, not from a request, so it is not something a caller can
        influence - and refusing the hostname the platform is *actually* using was never
        protecting anything.

        Appended rather than substituted, so an explicit ``ALLOWED_HOSTS`` (a custom
        domain, a staging alias) keeps every entry it lists.
        """
        platform_host = (self.render_external_hostname or "").strip()
        if platform_host and platform_host not in self.allowed_hosts:
            self.allowed_hosts.append(platform_host)
        return self

    @model_validator(mode="after")
    def _validate_rate_limit_budgets(self) -> Self:
        """Reject a malformed budget spec at boot, in every environment.

        Newly load-bearing. While these were hard-coded constants, a typo was a syntax-adjacent
        mistake caught in review; now that they come from ``.env``, ``"5/min"`` or ``"5 per
        minute"`` is a plausible thing for an operator to write. Both are wrong -
        :func:`_rate_per_second` wants ``"<count>/<period>"`` with a full period name.

        What made this worth a validator rather than a comment is that the two limiters fail
        *differently* on a bad value, and neither failure names the variable:

        * A **tier** spec that will not parse falls back to
          :data:`app.core.ratelimit.FALLBACK_BUDGET`, so the deployment runs on a limit
          nobody chose - and silently drops out of
          :attr:`rate_limit_tiers_eclipsed_by_ip`, because that skips what it cannot parse.
        * A **per-endpoint** spec is parsed by slowapi when the decorator is applied, so it
          raises during import, from inside a third-party library, before logging is up.

        Not restricted to production: a limit that is silently not the configured one is
        just as misleading on a laptop, and this is the cheapest possible check.
        """
        budgets = {
            "RATE_LIMIT_DEFAULT": self.rate_limit_default,
            "RATE_LIMIT_AUTH": self.rate_limit_auth,
            "RATE_LIMIT_AUTH_STRICT": self.rate_limit_auth_strict,
            "RATE_LIMIT_READ": self.rate_limit_read,
            "RATE_LIMIT_WRITE": self.rate_limit_write,
            "RATE_LIMIT_UPLOAD": self.rate_limit_upload,
            "RATE_LIMIT_EXPORT": self.rate_limit_export,
            "RATE_LIMIT_IP": self.rate_limit_ip,
            "RATE_LIMIT_LOGIN": self.rate_limit_login,
            "RATE_LIMIT_REGISTER": self.rate_limit_register,
            "RATE_LIMIT_MAIL_SENDING": self.rate_limit_mail_sending,
            "RATE_LIMIT_TOKEN_EXCHANGE": self.rate_limit_token_exchange,
            "RATE_LIMIT_HEALTH": self.rate_limit_health,
        }
        problems = [
            f"{name}={spec!r} is not a valid budget"
            for name, spec in budgets.items()
            if _rate_per_second(spec) is None
        ]
        if problems:
            joined = "\n  - ".join(problems)
            raise ValueError(
                f"Invalid rate-limit configuration:\n  - {joined}\n"
                'Write budgets as "<count>/<period>", where period is one of '
                'second, minute, hour, day - for example "10/minute".'
            )
        return self

    @model_validator(mode="after")
    def _enforce_test_safety(self) -> Self:
        """Refuse to run the test suite against a database that is not a test database.

        This exists because of a real, live near-miss rather than a hypothetical.

        ``tests/conftest.py`` isolates itself by setting ``POSTGRES_DB=stellarerp_test``
        and ``REDIS_DB=15``. Both are silently ignored when ``DATABASE_URL`` or
        ``REDIS_URL`` is set, because a full URL wins over the composed parts - and a
        developer whose ``.env`` carries a managed-database URL for a deployment has both.
        The suite then runs ``Base.metadata.drop_all`` and ``redis.flushdb()`` against
        whatever that URL points at.

        The failure is silent, total, and indistinguishable from a normal test run right
        up to the moment the tables are gone. So a name check at boot is worth the two
        lines: a database whose name does not end in ``_test`` is not a database this
        process may be pointed at while ``ENVIRONMENT=test``.
        """
        if self.environment is not Environment.TEST:
            return self

        if not self.database_name.endswith("_test"):
            raise ValueError(
                f"Refusing to run tests against database '{self.database_name}': the test "
                "suite drops every table, so the name must end in '_test'.\n"
                "  DATABASE_URL overrides POSTGRES_DB, so unset it (DATABASE_URL= in the "
                "environment) to fall back to the composed POSTGRES_* parts."
            )
        if self.redis_url is not None:
            raise ValueError(
                "Refusing to run tests with REDIS_URL set: it overrides REDIS_DB, which is "
                "how the suite isolates itself, and the suite calls FLUSHDB.\n"
                "  Unset it (REDIS_URL= in the environment) to fall back to REDIS_HOST/"
                "REDIS_DB."
            )
        return self

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> Self:
        """Fail fast on insecure production configuration.

        Crashing at boot is strictly better than silently serving traffic with a
        placeholder signing key or wildcard CORS.
        """
        if not self.environment.is_production:
            return self

        problems: list[str] = []

        if len(self.secret_key) < 32 or "dev-only" in self.secret_key:
            problems.append("SECRET_KEY must be a real 32+ character secret")
        if self.debug:
            problems.append("DEBUG must be false")
        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS must list explicit origins, not '*'")
        if not self.encryption_key:
            problems.append("ENCRYPTION_KEY is required (2FA secrets are encrypted at rest)")
        # Only meaningful when the DSN is composed from the parts. With an
        # explicit DATABASE_URL the password lives in that URL and this field is
        # never read, so checking it would reject a perfectly good deployment.
        if self.database_url is None and self.postgres_password in (
            "stellarerp",
            "postgres",
            "change-me-in-production",
        ):
            problems.append("POSTGRES_PASSWORD is still the default")
        if "*" in self.allowed_hosts:
            problems.append("ALLOWED_HOSTS must list explicit hosts, not '*'")
        if not self.allowed_hosts:
            problems.append("ALLOWED_HOSTS must not be empty")
        if not self.cors_origins:
            problems.append("CORS_ORIGINS must not be empty")

        # A credentialled session over plain HTTP is a session anyone on the path can
        # read. The refresh cookie is set `Secure` outside local development, so an
        # http:// origin here does not merely weaken the deployment - it produces a
        # frontend that cannot stay signed in, and a confusing bug report instead of a
        # clear boot failure.
        insecure_origins = [
            origin for origin in self.cors_origins if not origin.startswith("https://")
        ]
        if insecure_origins:
            problems.append(f"CORS_ORIGINS must all be https:// - got {insecure_origins}")
        if not self.frontend_url.startswith("https://"):
            problems.append("FRONTEND_URL must be https:// (it is emailed to users)")

        # No gateway-secret check here any more. This service is the public edge, so
        # "only my proxy may reach the API" is not a property it can assert - see the note
        # where the setting used to be. What remains below is every guarantee that does not
        # depend on there being a proxy.
        if not self.rate_limit_enabled:
            problems.append("RATE_LIMIT_ENABLED must be true")
        if not self.enforce_origin:
            problems.append("ENFORCE_ORIGIN must be true")

        if problems:
            joined = "\n  - ".join(problems)
            raise ValueError(f"Refusing to start in production:\n  - {joined}")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so ``.env`` is read once. Tests override by calling
    ``get_settings.cache_clear()`` after patching the environment.
    """
    return Settings()


settings = get_settings()
