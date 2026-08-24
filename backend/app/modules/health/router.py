"""Health and readiness probes.

Three endpoints, because orchestrators ask three different questions:

* ``/health/live`` - "is the process up?" Never touches a dependency. If this
  checked PostgreSQL, a brief database blip would make Docker/Kubernetes kill and
  restart every healthy app container, turning a recoverable outage into a
  cascading one.
* ``/health/ready`` - "can it serve traffic?" Checks dependencies and returns 503
  when they are down, so the load balancer stops routing to this instance without
  restarting it.
* ``/health`` - a human-readable summary for dashboards.

None of them require authentication, and none leak version or configuration
detail beyond what is already public.

**They are rate limited, and the two details below are both load-bearing.**
:class:`~app.core.middleware.RateLimitMiddleware` skips everything under ``/health``
(:data:`~app.core.middleware.PROBE_PREFIXES`), so without a decorator these are the only
unmetered endpoints in the application - and ``/ready`` pings PostgreSQL and Redis on
every call, which makes it the cheapest way to make this deployment do real work.

* **The decorator goes below the route decorator.** Decorators apply bottom-up, so
  ``@router.get`` must be the outer one to register the *limited* function. Written the
  other way round, the route is registered first and the limit wraps an object nothing
  calls - a limiter that reads as configured and enforces nothing.
* **Every handler takes ``request: Request``**, even where it goes unused. slowapi
  inspects the signature and raises ``No "request" or "websocket" argument`` at import
  time otherwise, which fails the process at startup rather than at the endpoint.

The budget is :attr:`~app.core.config.Settings.rate_limit_health`, which is required like
every other budget - so it must be set wherever this boots, a platform dashboard
included. Size it against the probe interval rather than against a user's patience: the
caller here is a machine on a timer, and a 429 it collects is read as "unhealthy".
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request, Response, status

from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import get_logger
from app.core.redis import check_redis_health
from app.core.schemas import HealthStatus
from app.db.session import check_database_health

log = get_logger(__name__)

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live", summary="Liveness probe", status_code=status.HTTP_200_OK)
@limiter.limit(settings.rate_limit_health)
async def liveness(request: Request) -> dict[str, str]:
    """Is the process alive? No dependency checks - see the module docstring."""
    return {"status": "alive"}


@router.get("/ready", summary="Readiness probe")
@limiter.limit(settings.rate_limit_health)
async def readiness(request: Request, response: Response) -> dict[str, object]:
    """Can this instance serve traffic?

    PostgreSQL and Redis are probed concurrently: sequential checks would make
    the probe's latency the sum of both timeouts, risking a spurious timeout of
    the probe itself.
    """
    database_ok, redis_ok = await asyncio.gather(
        check_database_health(),
        check_redis_health(),
    )

    ready = database_ok and redis_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        log.error(
            "readiness probe failed",
            extra={"database": database_ok, "redis": redis_ok},
        )

    return {
        "status": "ready" if ready else "not_ready",
        "checks": {
            "database": "up" if database_ok else "down",
            "redis": "up" if redis_ok else "down",
        },
    }


@router.get("", response_model=HealthStatus, summary="Service status summary")
@limiter.limit(settings.rate_limit_health)
async def health(request: Request, response: Response) -> HealthStatus:
    """Human-readable status for dashboards and smoke tests."""
    database_ok, redis_ok = await asyncio.gather(
        check_database_health(),
        check_redis_health(),
    )

    healthy = database_ok and redis_ok
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthStatus(
        status="healthy" if healthy else "degraded",
        version=settings.app_version,
        environment=str(settings.environment),
        checks={
            "database": "up" if database_ok else "down",
            "redis": "up" if redis_ok else "down",
            "email": "configured" if settings.emails_enabled else "log-only",
        },
    )
