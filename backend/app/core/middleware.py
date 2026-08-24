"""HTTP middleware.

Order is significant. Starlette runs middleware outermost-first on the way in and
innermost-first on the way out, so registration order in :mod:`app.main` is reversed
relative to execution. The stack is arranged so that:

* the cheapest rejections happen first - a request that will be refused for its host, its
  method, its size, or its origin must not reach Redis, let alone the database;
* request-id assignment happens before anything that can reject, so every refusal is
  correlatable in the logs;
* rate limiting runs before any handler work, so a flood costs one Redis round trip;
* security headers are applied last on the way out, so they are present on *every*
  response - including errors produced deeper in the stack.

The full inbound path is documented in :func:`app.main._register_middleware`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Final
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.logging import clear_log_context, get_logger, set_log_context
from app.core.net import client_ip
from app.core.ratelimit import RateLimiter, Tier, budgets, classify, parse_budget

log = get_logger(__name__)

REQUEST_ID_HEADER: Final = "X-Request-ID"

#: Never gated, never limited, never counted. An orchestrator's probes arrive before
#: any proxy is in the picture (a container healthcheck hits localhost directly), so
#: gating them on a proxy-injected header would fail every healthcheck and roll the
#: deployment back.
PROBE_PREFIXES: Final = ("/health",)

#: The two probes that answer an orchestrator, exempt from the production-only
#: trusted-host check as well - see :class:`ProbeExemptTrustedHostMiddleware`.
#:
#: Exact paths, not a prefix. ``/health`` itself is deliberately absent: it reports the
#: version, the environment and whether email is configured, and in production the host
#: check is the only thing standing in front of it. These two report a fixed shape and
#: nothing about the deployment.
HOST_EXEMPT_PROBES: Final = frozenset({"/health/live", "/health/ready"})

#: The interactive documentation and the machine-readable schema behind it.
#:
#: ``/openapi.json`` is the one that matters. The two HTML pages are only viewers for
#: it, so blocking them while leaving the schema reachable accomplishes nothing.
DOCS_PATHS: Final = ("/docs", "/redoc", "/openapi.json")

#: Methods with no legitimate use here, refused before routing.
#:
#: ``TRACE`` reflects the request back verbatim, which historically turned an XSS into
#: a way to read headers the page could not otherwise see (Cross-Site Tracing).
#: ``TRACK`` is the IIS equivalent. Neither is used by any client of this API, so the
#: safe answer is a flat refusal rather than a 405 that confirms the router's shape.
BLOCKED_METHODS: Final = frozenset({"TRACE", "TRACK", "CONNECT"})

#: Methods that change state, and therefore need origin and content-type checks.
UNSAFE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_cors_preflight(request: Request) -> bool:
    """Whether this is a browser's CORS preflight, as opposed to a real ``OPTIONS``.

    The same three-part test :class:`~starlette.middleware.cors.CORSMiddleware` uses, so
    "what the guard lets through" and "what CORS answers" cannot drift apart: the method is
    ``OPTIONS``, and both ``Origin`` and ``Access-Control-Request-Method`` are present.

    Narrow on purpose. A bare ``OPTIONS`` with no ``Origin`` is not a preflight - it is a
    caller asking what this endpoint supports - and it stays subject to every check.
    """
    return (
        request.method == "OPTIONS"
        and "origin" in request.headers
        and "access-control-request-method" in request.headers
    )


def _error(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    """Build a rejection in the application's standard error envelope.

    Middleware sits outside the exception handlers in :mod:`app.core.exceptions`, so it
    has to construct the envelope itself. Worth the duplication: a client that gets a
    differently-shaped body from the middleware layer than from the handler layer has to
    implement two parsers, and the one it forgets is the one that breaks at 3am.
    """
    body: dict[str, object] = {"code": code, "message": message}
    if details:
        body["details"] = details
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(status_code=status_code, content={"error": body}, headers=headers)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id and log every request with its outcome.

    An inbound ``X-Request-ID`` is honoured so a trace can be followed across a reverse
    proxy or a calling service; otherwise one is generated. It is echoed back on the
    response, and appears in the error envelope, so a user reporting a failure can quote
    an id that finds the exact log lines.

    An inbound id is **sanitised before use**. It is attacker-controlled text that ends
    up in a response header and in every log line for the request, so an unfiltered
    value is both a header-injection primitive (a ``\\r\\n`` splits the response) and a
    way to forge convincing log entries. Anything unexpected is replaced rather than
    rejected: the id is a debugging aid, and refusing the request over a malformed one
    would be a worse outcome than ignoring it.

    This replaces uvicorn's access log (silenced in :mod:`app.core.logging`), which
    knows nothing about the authenticated user or the request id.
    """

    #: Long enough for a UUID or a typical trace id, short enough that it cannot be used
    #: to bloat every log line for a request.
    MAX_REQUEST_ID_LENGTH: Final = 64

    @staticmethod
    def _sanitise(candidate: str | None) -> str | None:
        if not candidate:
            return None
        cleaned = candidate.strip()[: RequestContextMiddleware.MAX_REQUEST_ID_LENGTH]
        # Deliberately narrow: the ids we and every tracing system emit are hex, dashes
        # and underscores. Allowing more would mean reasoning about which characters are
        # safe in a header value, a log field, and a JSON body all at once.
        if cleaned and all(char.isalnum() or char in "-_" for char in cleaned):
            return cleaned
        return None

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = self._sanitise(request.headers.get(REQUEST_ID_HEADER)) or str(uuid.uuid4())
        request.state.request_id = request_id
        set_log_context(request_id=request_id)

        # Resolved once and stashed: the rate limiter, the audit trail and this log line
        # all need it, and each re-deriving it would parse the same header three times.
        request.state.client_ip = client_ip(request)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handlers build the response; this only records timing
            # and re-raises so they can do their job.
            duration_ms = (time.perf_counter() - started) * 1000
            log.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise
        finally:
            # ContextVars are per-task, but the worker task is reused, so stale
            # identifiers would otherwise bleed into the next request on it.
            clear_log_context()

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"

        # Health probes fire every few seconds against a live deployment, and those lines
        # bury the real traffic they sit between - so they are logged everywhere except
        # production, where a probe you are debugging can be turned back on with
        # ``LOG_HEALTH_PROBES``. See :attr:`~app.core.config.Settings.log_health_probes`.
        if settings.log_health_probes_enabled or not request.url.path.startswith(PROBE_PREFIXES):
            log.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                    "client_ip": request.state.client_ip,
                },
            )

        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach defence-in-depth response headers.

    These are cheap and each closes a specific class of attack:

    * ``X-Content-Type-Options: nosniff`` - stops a browser from reinterpreting a JSON
      response as HTML and executing it.
    * ``X-Frame-Options: DENY`` and ``frame-ancestors 'none'`` - no framing, so
      clickjacking has nothing to load. Both, because the CSP directive is authoritative
      in current browsers and the header is what older ones understand.
    * ``Referrer-Policy`` - keeps tokens in URLs (magic links) out of the ``Referer``
      header sent to third parties.
    * ``Content-Security-Policy`` - the API returns only JSON, so "load nothing, frame
      nothing, submit nowhere" is both correct and maximally strict.
    * ``Cross-Origin-Opener-Policy`` - severs ``window.opener``, so a page this API
      response is opened from cannot navigate or inspect it.
    * ``Cross-Origin-Resource-Policy: same-origin`` - blocks another site from embedding
      an API response as a subresource, which is the read side of Spectre-style
      cross-origin leaks and of naive JSON-as-script inclusion.
    * ``Cross-Origin-Embedder-Policy`` - refuses to load cross-origin subresources that
      have not opted in. Nothing here loads subresources at all, so this costs nothing
      and removes the request from the class of documents that can be a leak vector.
    * ``Permissions-Policy`` - an explicit, broad deny. An API has no use for any device
      capability, and naming them is what makes a future response that renders HTML
      inherit the denial rather than the default.
    * ``X-Permitted-Cross-Domain-Policies: none`` - refuses the Flash/Acrobat
      ``crossdomain.xml`` mechanism, which is legacy but still honoured by PDF readers.
    * ``Cache-Control: no-store`` - API responses are per-user data. Without it a shared
      proxy or the browser's own disk cache may retain one user's invoices and serve
      them to the next person on the machine.
    * ``Strict-Transport-Security`` - production only; sending it over plain HTTP in
      development would pin localhost to HTTPS in the developer's browser and break
      every other local project on that port.

    A route that already set its own value for one of these **keeps it**. The
    document-download endpoint returns bytes a stranger uploaded and sets a stricter
    ``sandbox`` CSP plus a private, cacheable ``Cache-Control``; clobbering either would
    silently remove a deliberate hardening measure or make every download a fresh
    transfer. That is exactly the regression a middleware that overwrites headers causes,
    and it is invisible in testing.
    """

    #: Set unconditionally - none of these has a legitimate per-route override.
    STATIC_HEADERS: Final = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Cross-Origin-Embedder-Policy": "require-corp",
        "X-Permitted-Cross-Domain-Policies": "none",
        "X-DNS-Prefetch-Control": "off",
        "Permissions-Policy": (
            "accelerometer=(), ambient-light-sensor=(), autoplay=(), battery=(), "
            "camera=(), display-capture=(), document-domain=(), encrypted-media=(), "
            "fullscreen=(), geolocation=(), gyroscope=(), magnetometer=(), "
            "microphone=(), midi=(), payment=(), picture-in-picture=(), "
            "publickey-credentials-get=(), screen-wake-lock=(), serial=(), "
            "usb=(), xr-spatial-tracking=()"
        ),
    }

    #: Load nothing, frame nothing, submit nowhere, and refuse to be upgraded into a
    #: document with a base URI. Correct for a JSON API, where every one of these
    #: capabilities is unused.
    API_CSP: Final = (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'none'; object-src 'none'; script-src 'none'; "
        "style-src 'none'; img-src 'none'; connect-src 'none'; "
        "font-src 'none'; media-src 'none'; worker-src 'none'; sandbox"
    )

    #: Relaxed policy for the interactive docs, which load their own bundle and inline
    #: styles from a CDN. Never served in production - see :class:`DocsGuardMiddleware`.
    DOCS_CSP: Final = (
        "default-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "img-src 'self' data: https://fastapi.tiangolo.com; "
        "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        "worker-src 'self' blob:"
    )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        is_docs = request.url.path.startswith(DOCS_PATHS)

        for header, value in self.STATIC_HEADERS.items():
            if is_docs and header == "Cross-Origin-Embedder-Policy":
                # Swagger UI loads its bundle from a CDN that sends no CORP header, so
                # `require-corp` blocks the page's own assets and renders it blank.
                continue
            response.headers[header] = value

        if "Content-Security-Policy" not in response.headers:
            response.headers["Content-Security-Policy"] = self.DOCS_CSP if is_docs else self.API_CSP

        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"

        # `Server` and `X-Powered-By` name the stack and its version, which is a free
        # CVE shortlist. uvicorn is started with `--no-server-header`, but a proxy or a
        # future ASGI server may add one, so it is removed here as well.
        #
        # `del` guarded by a membership test, not `.pop()`: Starlette's `MutableHeaders`
        # implements neither `pop` nor `KeyError`-free deletion, so both of the obvious
        # spellings raise - and raising here means every response 500s.
        for leaky in ("server", "x-powered-by"):
            if leaky in response.headers:
                del response.headers[leaky]

        if settings.environment.is_production and settings.hsts_max_age > 0:
            directives = [f"max-age={settings.hsts_max_age}", "includeSubDomains"]
            if settings.hsts_preload:
                directives.append("preload")
            response.headers["Strict-Transport-Security"] = "; ".join(directives)

        return response


class ProbeExemptTrustedHostMiddleware:
    """Starlette's ``TrustedHostMiddleware``, minus the orchestrator probes.

    **The host check is the one production-only gate in front of ``/health``.** Every
    other control here already exempts probes - :data:`PROBE_PREFIXES` keeps them out of
    the origin guard and the rate limiter, for the stated reason that a probe reaches the
    app directly and cannot satisfy conditions a proxy would normally arrange. The
    trusted-host check was added wholesale and knows nothing about that, so in production
    anything whose ``Host`` is not in ``ALLOWED_HOSTS`` gets ``400 Invalid host header`` -
    including an uptime monitor or a load balancer probing ``/health/ready`` by hostname
    or IP. The endpoint is registered and working; the request never reaches it.

    So the two probes bypass the check and everything else still goes through the real
    implementation, unchanged, including its ``www.`` redirect behaviour.

    **Why this is safe to exempt.** The Host header is dangerous when it is *reflected* -
    into a generated link, a cache key, a password-reset URL. These two endpoints read no
    header, emit no URL, and return a fixed JSON shape either way. ``/health`` is not
    exempt, because it names the version and environment; see :data:`HOST_EXEMPT_PROBES`.

    Plain ASGI rather than ``BaseHTTPMiddleware``: this only inspects the path and picks a
    branch, and the request/response wrapping that ``BaseHTTPMiddleware`` performs would be
    pure overhead on the readiness probe of every replica.
    """

    def __init__(self, app: ASGIApp, allowed_hosts: Sequence[str]) -> None:
        self.app = app
        self._guarded = TrustedHostMiddleware(app, allowed_hosts=list(allowed_hosts))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "") in HOST_EXEMPT_PROBES:
            await self.app(scope, receive, send)
            return
        await self._guarded(scope, receive, send)


class DocsGuardMiddleware(BaseHTTPMiddleware):
    """Refuse the documentation surface when it is disabled.

    :attr:`~app.core.config.Settings.docs_enabled` already stops FastAPI from
    *registering* these routes in production, so this layer is redundant by design. It
    exists because the failure it guards against is silent and plausible: someone mounts
    a second ``FastAPI()`` for a sub-application, or adds ``/openapi.json`` by hand to
    get a client generator working, and the schema is public again with nothing in the
    diff that reads as a security change. A rule at the HTTP layer holds regardless of
    what the router was persuaded to expose.

    **404, not 403.** A 403 confirms the path exists and is merely withheld, which tells
    a scanner it has found a real deployment of a known framework worth probing further.
    A 404 is indistinguishable from a path that was never there.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not settings.docs_enabled and request.url.path.startswith(DOCS_PATHS):
            log.warning(
                "docs endpoint blocked",
                extra={
                    "path": request.url.path,
                    "client_ip": getattr(request.state, "client_ip", None),
                    "environment": str(settings.environment),
                },
            )
            return _error(
                request,
                status_code=404,
                code="not_found",
                message="Not Found",
            )
        return await call_next(request)


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Refuse dangerous methods, and state-changing requests from a page we do not own.

    **The origin check** answers "did this browser page come from our frontend?".
    ``Origin`` and ``Referer`` are set by the browser and cannot be overridden by page
    script, so a state-changing request from ``evil.example`` is identifiable and refused.
    Defence in depth behind ``SameSite=Strict`` on the refresh cookie.

    **It says nothing about a non-browser caller,** and cannot. ``curl`` simply omits both
    headers, so a request with neither is allowed through - refusing it would break every
    legitimate non-browser client: a healthcheck, a backup script, an operator's terminal.
    What constrains those is authentication and rate limiting, not this.

    That gap used to be covered by a second check in this class - a ``GATEWAY_SECRET`` an
    nginx in front of the service stamped on every forwarded request. It is gone, because
    this service is the public edge rather than something sitting behind a proxy we
    configure. See the note where the setting was declared in
    :mod:`app.core.config` for what it did and did not buy, and what re-adding it would
    require. Do not read its absence as this check having grown to cover more: **the origin
    check is a browser-only control, and always was.**

    Two things are exempt, for the same underlying reason - the caller cannot satisfy them:

    * **Health probes.** A container healthcheck reaches the app directly on localhost, so
      gating it would make every deployment fail its own healthcheck and roll back.
    * **CORS preflights.** A preflight is a question, not a state change, and it must reach
      :class:`~starlette.middleware.cors.CORSMiddleware` to be answered at all.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path

        if request.method in BLOCKED_METHODS:
            log.warning(
                "blocked method",
                extra={"method": request.method, "path": path},
            )
            return _error(
                request,
                status_code=405,
                code="method_not_allowed",
                message="Method not allowed",
                headers={"Allow": "GET, POST, PATCH, PUT, DELETE, OPTIONS"},
            )

        if path.startswith(PROBE_PREFIXES):
            return await call_next(request)

        # A CORS preflight goes straight through to CORSMiddleware.
        #
        # Redundant with the `UNSAFE_METHODS` test below - `OPTIONS` is not in that set - and
        # kept deliberately, as a stated invariant rather than an accident of which methods
        # happen to be listed where. Nothing outside CORSMiddleware may reject a preflight: a
        # preflight is a *question*, changes nothing, and this guard runs outside CORS, so any
        # rejection here goes out with no `Access-Control-Allow-Origin` at all. The browser
        # then reports a CORS failure, which sends you to the CORS configuration, which is
        # correct - and the real cause is here. That already happened once, with a
        # `GATEWAY_SECRET` check a preflight could not possibly satisfy, and it took the whole
        # frontend down. Two lines to make the next such check obviously wrong.
        if is_cors_preflight(request):
            return await call_next(request)

        if settings.enforce_origin and request.method in UNSAFE_METHODS:
            rejection = self._origin_rejection(request)
            if rejection is not None:
                return rejection

        return await call_next(request)

    def _origin_rejection(self, request: Request) -> JSONResponse | None:
        """Refuse a state-changing request from a browser page we do not own.

        ``Origin`` is checked first and ``Referer`` only as a fallback, because a
        handful of privacy tools strip ``Referer`` while leaving ``Origin`` intact, and
        because ``Referer`` carries a full path that has no business being compared.
        """
        raw = request.headers.get("origin") or request.headers.get("referer")
        if not raw:
            # No browser sent this. See the class docstring.
            return None

        origin = self._normalise(raw)
        if origin is None or origin in {self._normalise(o) for o in settings.cors_origins}:
            return None

        log.warning(
            "request rejected: disallowed origin",
            extra={
                "origin": raw[:200],
                "path": request.url.path,
                "method": request.method,
                "client_ip": getattr(request.state, "client_ip", None),
            },
        )
        return _error(
            request,
            status_code=403,
            code="origin_not_allowed",
            message="This origin is not permitted to call the API.",
        )

    @staticmethod
    def _normalise(value: str | None) -> str | None:
        """Reduce a URL to ``scheme://host[:port]``, lowercased.

        A ``Referer`` arrives with a path and possibly a query; a configured origin may
        carry a trailing slash. Comparing the raw strings makes the check fail on
        formatting rather than on identity, and a check that fails open on a formatting
        difference is worse than no check.
        """
        if not value:
            return None
        parts = urlsplit(value.strip())
        if not parts.scheme or not parts.netloc:
            return None
        return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject an oversized request body before reading it.

    ``Content-Length`` is checked up front, so a declared 2 GB JSON payload costs one
    411/413 rather than the memory to buffer it. That is the cheap half.

    The expensive half is a body that arrives *without* a length - a chunked upload can
    stream forever, and a limit that only reads ``Content-Length`` is trivially bypassed
    by omitting it. So the stream is wrapped and counted, and the request is failed the
    moment the running total crosses the ceiling.

    File uploads get their own, much larger ceiling: a scanned invoice is legitimately
    megabytes, and :func:`app.modules.ocr.storage.read_within_limit` already enforces
    :attr:`~app.core.config.Settings.max_upload_bytes` while streaming. The allowance
    here is that ceiling plus room for multipart framing, so this layer bounds the
    transfer and the OCR layer bounds the file.
    """

    #: Headroom over `max_upload_bytes` for multipart boundaries, part headers and the
    #: other form fields that travel with an upload.
    MULTIPART_OVERHEAD: Final = 1024 * 1024

    def _ceiling(self, request: Request) -> int:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            return settings.max_upload_bytes + self.MULTIPART_OVERHEAD
        return settings.max_request_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in UNSAFE_METHODS:
            return await call_next(request)

        ceiling = self._ceiling(request)
        declared = request.headers.get("content-length")

        if declared is not None:
            try:
                if int(declared) > ceiling:
                    return self._too_large(request, ceiling)
            except ValueError:
                return _error(
                    request,
                    status_code=400,
                    code="invalid_content_length",
                    message="Malformed Content-Length header.",
                )
            return await call_next(request)

        # No declared length: count the bytes as they arrive.
        #
        # Replacing `receive` rather than reading the body here is deliberate - reading
        # it would buffer the whole payload in this layer, which is the exact cost the
        # limit exists to avoid, and would break the streaming upload path downstream.
        remaining = ceiling
        original_receive = request.receive
        exceeded = False

        async def counting_receive() -> dict[str, object]:
            nonlocal remaining, exceeded
            message = await original_receive()
            if message["type"] == "http.request":
                remaining -= len(message.get("body", b""))
                if remaining < 0:
                    exceeded = True
                    # Signal end-of-body so the consumer stops rather than hanging; the
                    # request is failed below on the way out.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message  # type: ignore[return-value]

        # Starlette offers no public setter, but `_receive` is the documented ASGI seam
        # every body-inspecting middleware uses.
        request._receive = counting_receive
        response = await call_next(request)
        if exceeded:
            log.warning(
                "request body exceeded limit",
                extra={"path": request.url.path, "limit_bytes": ceiling},
            )
            return self._too_large(request, ceiling)
        return response

    @staticmethod
    def _too_large(request: Request, ceiling: int) -> JSONResponse:
        return _error(
            request,
            status_code=413,
            code="payload_too_large",
            message="Request body is too large.",
            details={"max_bytes": ceiling},
        )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-route, per-identity token-bucket rate limiting.

    The budget and the identity are both chosen in :mod:`app.core.ratelimit`; this class
    is the HTTP shell around it. Two buckets are consulted for every request - the tier
    bucket keyed on the caller's identity, and a wider bucket keyed on the source IP -
    and the request needs room in both. The reasoning for that pairing is in that
    module's docstring.

    **Fails open.** If Redis is unavailable the request proceeds, with an error in the
    log. Fail-closed would convert a cache outage into a total outage, which is a worse
    trade for a protective layer.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        # Parsed once at startup rather than per request: a regex-free dict lookup on
        # the hot path, and a malformed spec announces itself in the boot logs instead
        # of on the thousandth request.
        self._budgets = budgets()
        self._ip_budget = parse_budget(settings.rate_limit_ip)
        self._limiter = RateLimiter()

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)

        tier = classify(request.method, request.url.path)
        if tier is Tier.EXEMPT:
            return await call_next(request)

        budget = self._budgets[tier]
        source = getattr(request.state, "client_ip", None) or "unknown"
        identity = self._identity(request, source)

        try:
            from app.core.redis import get_redis

            redis = get_redis()
            now = time.time()
            tier_decision = await self._limiter.check(
                redis, scope=str(tier), identity=identity, budget=budget, now=now
            )
            ip_decision = await self._limiter.check(
                redis, scope="ip", identity=source, budget=self._ip_budget, now=now
            )
        except Exception as exc:
            log.error("rate limiter unavailable - allowing request", extra={"error": str(exc)})
            return await call_next(request)

        # The tier's numbers are the ones reported, because they are the ones a
        # well-behaved client can plan against; the IP ceiling is a backstop that normal
        # use never approaches. But whichever one refused decides the outcome.
        decision = tier_decision if not tier_decision.allowed else ip_decision

        if not decision.allowed:
            log.warning(
                "rate limit exceeded",
                extra={
                    "client_ip": source,
                    "identity": identity,
                    "path": request.url.path,
                    "method": request.method,
                    "tier": str(tier),
                    "scope": str(tier) if not tier_decision.allowed else "ip",
                    "limit": decision.limit,
                    "retry_after": decision.retry_after,
                },
            )
            return _error(
                request,
                status_code=429,
                code="rate_limit_exceeded",
                message="Too many requests. Slow down.",
                details={"retry_after_seconds": decision.retry_after},
                headers={
                    "Retry-After": str(decision.retry_after),
                    "X-RateLimit-Limit": str(decision.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(decision.retry_after),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(tier_decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(tier_decision.remaining)
        response.headers["X-RateLimit-Policy"] = (
            f"{tier_decision.limit};w={budget.period_seconds};tier={tier}"
        )
        return response

    @staticmethod
    def _identity(request: Request, source: str) -> str:
        """The bucket key: the authenticated user where there is one, else the source IP.

        The token is decoded here, ahead of the dependency that will decode it again for
        real. That is one HMAC verification - no I/O, microseconds - and it buys correct
        accounting: without it every user behind one office NAT shares a bucket, so the
        fifth person to open the dashboard is throttled by the first four.

        Signature verification matters even for a key. An unverified ``sub`` is
        attacker-chosen, which means a flood can mint a fresh identity per request and
        never touch a limit. A revoked-but-unexpired token is fine to key on: it will be
        rejected a layer later, and until then it is a stable, attributable identity.
        """
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return f"ip:{source}"

        from app.core.exceptions import AppError
        from app.core.security import decode_access_token

        try:
            claims = decode_access_token(header[7:].strip())
        except (AppError, ValueError):
            return f"ip:{source}"

        subject = claims.get("sub")
        return f"user:{subject}" if subject else f"ip:{source}"


__all__ = [
    "BLOCKED_METHODS",
    "DOCS_PATHS",
    "PROBE_PREFIXES",
    "REQUEST_ID_HEADER",
    "BodySizeLimitMiddleware",
    "DocsGuardMiddleware",
    "OriginGuardMiddleware",
    "RateLimitMiddleware",
    "RequestContextMiddleware",
    "SecurityHeadersMiddleware",
]
