"""The declarative, per-endpoint rate limiter (slowapi), on shared Redis.

There are two rate limiters in this application and they are not redundant. This one
sits *at the route*, the other in front of every request:

+---------------------------+------------------------------------+--------------------+
| Layer                     | Scope                              | Where              |
+===========================+====================================+====================+
| ``RateLimitMiddleware``   | every request, by tier             | core/middleware.py |
| ``limiter`` (this module) | one endpoint, by explicit decorator| the route itself   |
+---------------------------+------------------------------------+--------------------+

**Why both.** The middleware classifies by pattern (:func:`app.core.ratelimit.classify`),
which is what makes it exhaustive - a route added tomorrow is limited without anyone
remembering to limit it. The cost of that is indirection: the budget for
``POST /auth/login`` lives in a regex table in another module. A decorator on the handler
states the endpoint's own budget where a reader of the endpoint will see it, and it fires
independently, so a mistake in the classification table - a pattern that stops matching
after a path is renamed - does not silently remove the protection from the endpoints
where it matters most.

They also disagree usefully: the middleware fails open on a Redis error because it
guards *everything* and a cache outage must not be a total outage. Here, on a handful of
credential endpoints, the same trade is available to be made differently if an operator
wants it - see :data:`SWALLOW_STORAGE_ERRORS`.

**Why this one is not the blanket layer.** slowapi 0.1.10 drives the *synchronous*
``limits`` storage, so each check is a blocking Redis round trip inside the event loop.
On the endpoints below that is invisible: every one of them is already dominated by an
Argon2 verification costing ~50 ms of CPU, against a sub-millisecond local Redis call. In
front of every request it would serialise the whole worker on the network. That is the
entire reason the middleware limiter is hand-written against ``redis.asyncio`` rather
than being this library.

**Shared storage, not per-process.** ``storage_uri`` points at the same Redis as
everything else. slowapi's default is in-memory, which with two replicas behind nginx
means each replica enforces its own copy of the budget - so the effective limit is
double what is configured, and it changes when you scale.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.requests import Request
    from starlette.responses import JSONResponse

log = get_logger(__name__)

#: Allow the request through when Redis cannot be reached.
#:
#: True, matching the middleware limiter, and for the same reason: this is a protective
#: layer, and turning a cache outage into an authentication outage locks every legitimate
#: user out to inconvenience an attacker who is not currently attacking. Flip it to False
#: to fail closed on the credential endpoints specifically - a defensible choice for a
#: deployment where Redis is as reliable as the database.
SWALLOW_STORAGE_ERRORS: Final = True

#: Keeps these keys out of the middleware limiter's namespace.
#:
#: Load-bearing, not cosmetic: both limiters would otherwise build a key from a scope and
#: an identity, and a collision means two different algorithms mutating one another's
#: counters - which produces limits that are wrong in a way no log line explains.
KEY_PREFIX: Final = "personalerp:slowapi"


def rate_limit_key(request: Request) -> str:
    """The identity a per-endpoint budget is charged to.

    Deliberately **not** slowapi's ``get_remote_address``, which returns
    ``request.client.host``. That is uvicorn's answer, and behind a proxy it is either the
    proxy's own address - putting every user on earth in one bucket - or, under
    ``--forwarded-allow-ips '*'``, the left-most ``X-Forwarded-For`` entry, which the
    caller writes. Either way the limit is not measuring what it claims to.

    Reads the value :class:`~app.core.middleware.RequestContextMiddleware` already
    resolved, so both limiters agree on who is calling, and falls back to resolving it
    directly if this is reached outside that middleware.
    """
    from app.core.net import client_ip

    resolved: str | None = getattr(request.state, "client_ip", None)
    return resolved or client_ip(request)


#: The limiter instance the decorators reference.
#:
#: ``moving-window`` rather than the default ``fixed-window``: a fixed window permits the
#: full budget at the end of one window and the full budget again at the start of the
#: next, so a "5 per minute" password-reset limit passes 10 requests back to back across
#: a boundary. The middleware's token bucket has the same property for the same reason,
#: and two layers that disagree about burst behaviour would make the observed limit
#: depend on which one happened to reject first.
#:
#: ``headers_enabled=False``: the middleware owns ``X-RateLimit-*``. Two writers produce
#: contradictory numbers on one response, and a client that backs off according to the
#: wrong one either hammers a closed door or waits far longer than it needs to.
#:
#: ``default_limits=[]``: nothing is limited implicitly here. The blanket layer is the
#: middleware's job, and an implicit default in both places is how an endpoint ends up
#: with a budget nobody chose.
limiter = Limiter(
    key_func=rate_limit_key,
    default_limits=[],
    strategy="moving-window",
    storage_uri=settings.redis_dsn,
    key_prefix=KEY_PREFIX,
    headers_enabled=False,
    swallow_errors=SWALLOW_STORAGE_ERRORS,
    enabled=settings.rate_limit_enabled,
)


async def _rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render slowapi's rejection in the application's own error envelope.

    Without this the endpoints carrying a decorator answer with slowapi's
    ``{"error": "Rate limit exceeded: 5 per 1 minute"}`` while every other endpoint
    answers with ``{"error": {"code": ..., "message": ...}}``. The frontend branches on
    ``error.code``, so the shape it cannot parse is the one that arrives on the
    endpoints it most needs to handle gracefully - and it would surface as a blank
    failure on the login form.
    """
    from app.core.exceptions import RateLimitExceededError

    detail = getattr(exc, "detail", "") or ""
    # slowapi expresses the window in the limit object; converting it to whole seconds
    # gives the client something to actually wait for rather than a sentence to display.
    limit = getattr(exc, "limit", None)
    window = getattr(getattr(limit, "limit", None), "GRANULARITY", None)
    retry_after = int(getattr(window, "seconds", 60) or 60)

    log.warning(
        "endpoint rate limit exceeded",
        extra={
            "path": request.url.path,
            "method": request.method,
            "identity": rate_limit_key(request),
            "limit": str(detail),
        },
    )
    return RateLimitExceededError(retry_after=retry_after).to_response()


def register_limiter(app: FastAPI) -> None:
    """Attach the limiter to the application.

    ``app.state.limiter`` is not optional decoration - slowapi's decorator resolves the
    limiter off the application state at request time, so a decorated endpoint raises
    ``AttributeError`` on its first call without it. Registering here, in the composition
    root, means an app built by the test suite is wired identically to one built by
    uvicorn.
    """
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


__all__ = ["KEY_PREFIX", "limiter", "rate_limit_key", "register_limiter"]
