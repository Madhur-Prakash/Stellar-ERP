"""Application entry point and composition root.

This is the only module that knows how all the pieces fit together. Everything
else depends inward on abstractions, which is what lets modules be tested and
replaced independently.

Run with::

    uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.limiter import register_limiter
from app.core.logging import configure_logging, flush_logs, get_logger
from app.core.middleware import (
    BodySizeLimitMiddleware,
    DocsGuardMiddleware,
    OriginGuardMiddleware,
    ProbeExemptTrustedHostMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.redis import close_redis, get_redis
from app.db.migrate import run_migrations
from app.db.session import dispose_engine
from app.modules.health.router import router as health_router

# logifyx must be registered as the global logger class before any module
# acquires a logger, so this is the first thing that happens in the process.
configure_logging()
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Startup and shutdown.

    Redis is touched at startup deliberately: a connection failure should surface
    here, in the logs, at boot - not as the first user's failed login.

    The database is not *probed*, because migrations may legitimately be running against
    it while the app starts; ``/health/ready`` covers that. It may be *migrated*, when
    ``RUN_MIGRATIONS_ON_STARTUP`` is set - which is how a deployment with nowhere to put a
    release step gets a usable schema. Unlike Redis, that one is fatal on failure: see
    :func:`app.db.migrate.run_migrations`.
    """
    log.info(
        "starting %s v%s",
        settings.app_name,
        settings.app_version,
        extra={
            "environment": str(settings.environment),
            "debug": settings.debug,
            "docs": settings.docs_url,
            # The posture, named at boot. Every one of these is a thing an operator will
            # otherwise discover by being attacked: an origin check silently off because
            # the variable was not carried into the new deployment, or a rate limiter
            # disabled for a load test and never turned back on. Config validation
            # refuses to start a production process in any of these states, but staging
            # and development run happily, and those are where the misconfiguration is
            # written before it is copied.
            "origin_enforced": settings.enforce_origin,
            "rate_limiting": settings.rate_limit_enabled,
            "proxy_headers_trusted": settings.trust_proxy_headers,
            # Where uploaded documents go, named at startup because it is derived from
            # whether credentials happen to be configured - and settings are read once per
            # process, so an `.env` edited after the server started has no effect until it
            # restarts. Without this line the only symptom is documents quietly landing
            # somewhere other than where the operator just configured, which is a long way
            # to travel for "it needed a restart".
            "documents": settings.document_storage,
            "document_target": (
                f"{settings.minio_endpoint}/{settings.minio_bucket}"
                if settings.document_storage == "object"
                else f"postgres:{settings.database_name}.document_blob"
            ),
        },
    )

    # Reported here rather than raised in config validation, because it is a tuning
    # choice rather than a mistake - but an invisible one. Both rate-limit buckets must
    # have room, so a per-IP ceiling below a tier makes that tier a dead letter and the
    # per-IP number the only limit that binds. Since that bucket is shared by everyone
    # behind one NAT, the symptom is intermittent 429s that track how many colleagues are
    # online, which is not a trail that leads back to a configuration file.
    if eclipsed := settings.rate_limit_tiers_eclipsed_by_ip:
        log.warning(
            "RATE_LIMIT_IP is below one or more tiers, so those tiers can never bind - "
            "the per-IP ceiling is the effective limit, and it is shared across a NAT",
            extra={"rate_limit_ip": settings.rate_limit_ip, "eclipsed_tiers": eclipsed},
        )

    # Before Redis and before the first request: a missing schema is fatal in a way an
    # unreachable Redis is not. No-op unless RUN_MIGRATIONS_ON_STARTUP is set.
    await run_migrations()

    try:
        await get_redis().ping()
        log.info("redis reachable")
    except Exception as exc:
        # Not fatal: rate limiting fails open, and auth degrades rather than
        # breaking. Better to serve with a warning than refuse to boot.
        log.error("redis unreachable at startup", extra={"error": str(exc)})

    yield

    log.info("shutting down")
    await close_redis()
    await dispose_engine()
    # Drain queued remote/Kafka log records before the process exits.
    flush_logs(timeout=3.0)
    log.info("shutdown complete")


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than a module-level singleton so tests can construct an
    isolated instance with overridden settings.
    """
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "A self-hosted ERP for small businesses.\n\n"
            "Authenticate at `/api/v1/auth/login`, then send "
            "`Authorization: Bearer <access_token>`. Refresh tokens are delivered "
            "as an HttpOnly cookie and rotated on every use."
        ),
        # All three are None in production. `docs_enabled` is the single fact behind
        # them, so there is no way for the schema to be exposed while the viewers are
        # hidden - which is the configuration that looks safe and is not, since the
        # schema is the part worth having. :class:`DocsGuardMiddleware` enforces the same
        # rule at the HTTP layer, so re-adding a route by hand does not reopen it.
        docs_url=settings.docs_url,
        redoc_url=settings.redoc_url,
        openapi_url=settings.openapi_url,
        lifespan=lifespan,
        # Trailing-slash redirects turn a POST into a GET and silently drop the
        # body; better to 404 and make the client fix its URL.
        redirect_slashes=False,
    )

    _register_middleware(app)

    # Before the exception handlers, because it registers one of its own for slowapi's
    # RateLimitExceeded - which must be in place before any decorated route can raise it.
    register_limiter(app)
    register_exception_handlers(app)

    # Unversioned: orchestrators should not have to track an API version.
    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    log.info(
        "application configured",
        extra={"routes": len(app.routes), "api_prefix": settings.api_v1_prefix},
    )
    return app


def _register_middleware(app: FastAPI) -> None:
    """Install middleware.

    Starlette applies middleware in reverse registration order, so the *last*
    registered runs *first* on an incoming request. Reading the calls below
    bottom-up gives the actual request path::

        SecurityHeaders -> TrustedHost -> RequestContext -> DocsGuard
            -> OriginGuard -> CORS -> BodySizeLimit -> RateLimit -> GZip
            -> route handler

    Every position in that list is a decision:

    * **SecurityHeaders outermost.** It is the only layer that touches the response on
      the way out, so being furthest out is what makes the headers unconditional -
      present on a 429 from the limiter, a 404 from a guard, and a 500 from a handler
      alike. Registered innermost (the obvious reading of "applied last") it would only
      ever decorate responses that reached the router, and the rejections - the ones an
      attacker sees most - would go out bare.
    * **TrustedHost before anything expensive.** A forged ``Host`` poisons the absolute
      URLs in password-reset mail, so it is refused before a request id is even minted.
      Disabled locally, where the host legitimately varies (localhost, 127.0.0.1, a LAN
      IP for testing from a phone).
    * **RequestContext next.** Everything below it can reject, and a rejection with no
      request id cannot be correlated with the report that follows it.
    * **The guards before CORS.** DocsGuard refuses ``/docs`` in production without CORS
      first advertising what it would have allowed there.

      This position has a sharp edge, and it has drawn blood: **anything rejected here goes
      out with no CORS headers**, so a browser sees an opaque CORS failure rather than the
      status that was actually sent. A ``GATEWAY_SECRET`` check that once sat in
      ``OriginGuard`` rejected the browser's preflight - which by specification cannot carry
      a custom header - and the whole frontend failed with an error pointing at the CORS
      configuration. Preflights are now explicitly passed through; see
      :class:`~app.core.middleware.OriginGuardMiddleware`. Any future check added here must
      do the same.
    * **CORS above the limiter.** A 429 or a 413 has to be *readable* by the frontend -
      it needs ``Retry-After`` to back off intelligently. Without the CORS headers on
      those responses the browser hands the page an opaque network error instead, and
      the client cannot tell "slow down" from "the server is gone".
    * **RateLimit before the router.** A flood costs one Redis round trip rather than a
      database transaction.
    """
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Required for the refresh cookie to be sent cross-origin, and the reason
        # a wildcard origin is rejected by config validation: browsers forbid
        # `*` together with credentials.
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        # An allow-list, not a wildcard. `*` is invalid alongside credentials anyway,
        # and enumerating the four headers the frontend actually sends means a request
        # carrying anything else is rejected at the preflight.
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=[
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
            "X-RateLimit-Policy",
            "Retry-After",
            "Content-Disposition",
        ],
        max_age=3600,
    )

    app.add_middleware(OriginGuardMiddleware)
    app.add_middleware(DocsGuardMiddleware)
    app.add_middleware(RequestContextMiddleware)

    if settings.environment.is_production:
        # Not Starlette's `TrustedHostMiddleware` directly: it would answer
        # `400 Invalid host header` to a readiness probe whose Host is not in
        # ALLOWED_HOSTS - a load balancer probing by IP, an uptime monitor by hostname -
        # which reads as "the health endpoints are disabled in production" when they are
        # registered and working. The wrapper exempts `/health/live` and `/health/ready`
        # and delegates everything else unchanged.
        app.add_middleware(ProbeExemptTrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    app.add_middleware(SecurityHeadersMiddleware)


app = create_app()
