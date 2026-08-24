"""Tests for the HTTP hardening layer.

Everything here exercises :mod:`app.core.middleware`, :mod:`app.core.net` and
:mod:`app.core.ratelimit` - the controls that decide whether a request is answered at
all, before any handler or permission check is involved.

Most of these need no database. The app is built per test with settings patched, and
requests go to ``/health/live``, which touches no dependency - so the assertions are
about the middleware stack and nothing else. The rate-limit tests are the exception:
a token bucket lives in Redis, so they are marked ``integration``.

**Settings are patched by mutating the singleton, not by re-reading the environment.**
:func:`app.core.config.get_settings` is ``lru_cache``d and every module holds a
reference to the same object, so clearing the cache would leave those references
pointing at the old instance. Mutating in place is what actually takes effect, and the
fixtures below always restore the previous value.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from typing import Any, ClassVar

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.core.config import Environment, settings
from app.core.middleware import DOCS_PATHS, SecurityHeadersMiddleware
from app.core.net import client_ip
from app.core.ratelimit import (
    FALLBACK_BUDGET,
    RateLimiter,
    Tier,
    classify,
    parse_budget,
)
from app.main import create_app


# =============================================================================
# Helpers
# =============================================================================
@pytest.fixture
def patch_settings() -> Iterator[Any]:
    """Temporarily override settings attributes, restoring them afterwards.

    Yields a callable taking keyword arguments. Restoration happens even if the test
    fails, because a leaked ``environment = production`` makes every later test in the
    session refuse to run for reasons that have nothing to do with what it is testing.
    """
    originals: dict[str, Any] = {}

    def apply(**overrides: Any) -> None:
        for key, value in overrides.items():
            if key not in originals:
                originals[key] = getattr(settings, key)
            object.__setattr__(settings, key, value)

    yield apply

    for key, value in originals.items():
        object.__setattr__(settings, key, value)


async def _client(**headers: str) -> AsyncClient:
    """A client against a freshly built app, with no database override.

    Built per call so the middleware stack reflects the settings in force *now* -
    ``_register_middleware`` reads them at construction time, so an app built before a
    patch would still carry the old configuration.
    """
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers=headers,
        follow_redirects=False,
    )


@pytest.fixture
async def probe() -> AsyncGenerator[AsyncClient]:
    """A client for `/health/live`, which touches no dependency."""
    async with await _client() as http:
        yield http


@pytest.fixture
async def redis_client() -> AsyncGenerator[Any]:
    """A Redis handle with the test index flushed either side.

    Necessary because conftest's ``_clean_redis`` fires only for tests whose fixture
    closure reaches Postgres, and everything here reaches Redis *alone*. Without it,
    token buckets survive between tests and between whole runs, so the first assertion in
    each one is really about state the previous run left behind - which is how a bucket
    test starts failing with ``remaining=0`` on a correct implementation.
    """
    from app.core.redis import get_redis

    redis = get_redis()
    await redis.flushdb()
    yield redis
    await redis.flushdb()


# =============================================================================
# Response headers
# =============================================================================
class TestSecurityHeaders:
    async def test_present_on_a_successful_response(self, probe: AsyncClient) -> None:
        response = await probe.get("/health/live")
        assert response.status_code == 200

        for header, expected in SecurityHeadersMiddleware.STATIC_HEADERS.items():
            assert response.headers[header] == expected, header

    async def test_present_on_a_404(self, probe: AsyncClient) -> None:
        """The headers must not depend on reaching a handler.

        This is the case that regressed when the middleware was registered innermost:
        a 404 - the response an attacker sees most often while mapping the surface -
        went out with none of them.
        """
        response = await probe.get("/definitely-not-a-route")
        assert response.status_code == 404
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]

    async def test_csp_denies_everything_for_api_responses(self, probe: AsyncClient) -> None:
        csp = (await probe.get("/health/live")).headers["Content-Security-Policy"]
        for directive in (
            "default-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "object-src 'none'",
        ):
            assert directive in csp

    async def test_api_responses_are_not_cacheable(self, probe: AsyncClient) -> None:
        """A shared proxy must not retain one user's data and serve it to the next."""
        cache_control = (await probe.get("/health/live")).headers["Cache-Control"]
        assert "no-store" in cache_control
        assert "private" in cache_control

    async def test_server_header_removed(self, probe: AsyncClient) -> None:
        response = await probe.get("/health/live")
        assert "server" not in {key.lower() for key in response.headers}
        assert "x-powered-by" not in {key.lower() for key in response.headers}

    async def test_hsts_only_in_production(self, probe: AsyncClient, patch_settings: Any) -> None:
        """Sending HSTS from a dev server pins localhost to HTTPS in the browser."""
        assert "Strict-Transport-Security" not in (await probe.get("/health/live")).headers

        patch_settings(environment=Environment.PRODUCTION)
        async with await _client() as http:
            header = (await http.get("/health/live")).headers["Strict-Transport-Security"]
        assert f"max-age={settings.hsts_max_age}" in header
        assert "includeSubDomains" in header

    async def test_hsts_preload_is_opt_in(self, probe: AsyncClient, patch_settings: Any) -> None:
        """Preload is effectively irreversible, so it must never be a default."""
        patch_settings(environment=Environment.PRODUCTION)
        async with await _client() as http:
            assert (
                "preload"
                not in (await http.get("/health/live")).headers["Strict-Transport-Security"]
            )

        patch_settings(hsts_preload=True)
        async with await _client() as http:
            assert (
                "preload" in (await http.get("/health/live")).headers["Strict-Transport-Security"]
            )

    async def test_route_set_headers_are_not_clobbered(self) -> None:
        """A stricter per-route policy must survive the blanket one.

        The document-download endpoint sets a `sandbox` CSP and a private, cacheable
        `Cache-Control` because it returns bytes a stranger uploaded. Overwriting either
        would silently undo a deliberate hardening measure - the exact regression a
        middleware that assigns unconditionally causes.
        """
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route

        async def sensitive(request: Request) -> PlainTextResponse:
            return PlainTextResponse(
                "bytes",
                headers={
                    "Content-Security-Policy": "sandbox; default-src 'none'",
                    "Cache-Control": "private, max-age=3600",
                },
            )

        app = create_app()
        app.router.routes.append(Route("/_probe-download", sensitive))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get("/_probe-download")

        assert response.headers["Content-Security-Policy"] == "sandbox; default-src 'none'"
        assert response.headers["Cache-Control"] == "private, max-age=3600"
        # The unconditional ones are still applied on top.
        assert response.headers["X-Content-Type-Options"] == "nosniff"


# =============================================================================
# Docs
# =============================================================================
class TestDocsBlocking:
    @pytest.mark.parametrize("path", DOCS_PATHS)
    async def test_served_outside_production(self, probe: AsyncClient, path: str) -> None:
        assert (await probe.get(path)).status_code == 200

    @pytest.mark.parametrize("path", [*DOCS_PATHS, "/docs/oauth2-redirect"])
    async def test_blocked_in_production(self, patch_settings: Any, path: str) -> None:
        patch_settings(environment=Environment.PRODUCTION, allowed_hosts=["test"])
        async with await _client() as http:
            response = await http.get(path)

        # 404, not 403: a 403 confirms the path exists and is merely withheld.
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_blocked_even_if_a_route_is_readded(self, patch_settings: Any) -> None:
        """The middleware, not the router, is what makes this hold.

        `docs_enabled` stops FastAPI registering the routes, so this test mounts one by
        hand to model the realistic mistake - someone adds `/openapi.json` back to get a
        client generator working, and nothing in the diff reads as a security change.
        """
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        patch_settings(environment=Environment.PRODUCTION, allowed_hosts=["test"])
        app = create_app()

        async def schema(request: Request) -> JSONResponse:
            return JSONResponse({"openapi": "3.1.0", "paths": {"leaked": {}}})

        app.router.routes.append(Route("/openapi.json", schema))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get("/openapi.json")

        assert response.status_code == 404
        assert "openapi" not in response.text

    async def test_settings_never_expose_docs_in_production(self, patch_settings: Any) -> None:
        patch_settings(environment=Environment.PRODUCTION)
        assert settings.docs_enabled is False
        assert settings.docs_url is None
        assert settings.redoc_url is None
        assert settings.openapi_url is None


# =============================================================================
# Origin guard: methods, probes, preflights
# =============================================================================
class TestOriginGuard:
    async def test_health_probes_pass(self, probe: AsyncClient) -> None:
        assert (await probe.get("/health/live")).status_code == 200

    @pytest.mark.parametrize("method", ["TRACE", "TRACK"])
    async def test_dangerous_methods_refused(self, probe: AsyncClient, method: str) -> None:
        response = await probe.request(method, "/health/live")
        assert response.status_code == 405
        assert response.json()["error"]["code"] == "method_not_allowed"

    async def test_a_non_browser_request_passes(self, probe: AsyncClient) -> None:
        """No `Origin`, no `Referer` - curl, a script, a healthcheck.

        Allowed on purpose. This guard is a browser control and cannot say anything about a
        caller that simply omits the headers; refusing them would break every legitimate
        non-browser client. Authentication and rate limiting are what constrain those.
        """
        assert (await probe.get("/api/v1/auth/password-policy")).status_code == 200


class TestPreflightReachesCors:
    """A CORS preflight must reach CORSMiddleware, whatever guards sit in front of it.

    These exist because of a regression that took the entire frontend down while looking
    like a CORS misconfiguration. A ``GATEWAY_SECRET`` check used to run here, outside
    CORSMiddleware, and a preflight cannot carry a custom header - by specification, since
    its whole job is to *ask* whether one may be sent. So it was rejected, the rejection went
    out with no ``Access-Control-Allow-Origin``, and the browser reported::

        Response to preflight request doesn't pass access control check:
        No 'Access-Control-Allow-Origin' header is present

    which sends you to the CORS configuration, where nothing is wrong.

    That check is gone now, and the origin check below it never applied to ``OPTIONS``
    anyway - so these tests currently pass without the explicit exemption in
    ``OriginGuardMiddleware``. They are kept as the statement of the invariant: **nothing
    outside CORSMiddleware may reject a preflight.** They are what fails if a future guard
    forgets it.
    """

    PREFLIGHT: ClassVar[dict[str, str]] = {
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }

    async def test_preflight_passes_and_carries_the_cors_headers(self, patch_settings: Any) -> None:
        """The exact request the browser makes before POSTing to /auth/login."""
        patch_settings(cors_origins=["http://localhost:5173"])
        async with await _client() as http:
            response = await http.options("/api/v1/auth/login", headers=self.PREFLIGHT)

        assert response.status_code == 200, response.text
        # The header whose absence produced the original browser error.
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert response.headers["access-control-allow-credentials"] == "true"
        assert "POST" in response.headers["access-control-allow-methods"]

    async def test_preflight_for_refresh_passes(self, patch_settings: Any) -> None:
        """`/auth/refresh` fires on page load, so it failed before login was even tried."""
        patch_settings(cors_origins=["http://localhost:5173"])
        async with await _client() as http:
            response = await http.options("/api/v1/auth/refresh", headers=self.PREFLIGHT)

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

    async def test_the_gateway_header_is_no_longer_advertised(self, patch_settings: Any) -> None:
        """The preflight answer must not still offer a header nothing reads.

        Guards the other half of the removal: `allow_headers` used to include
        `settings.gateway_header`, and leaving it there would tell every client to keep
        sending a credential the server has stopped checking.
        """
        patch_settings(cors_origins=["http://localhost:5173"])
        async with await _client() as http:
            response = await http.options(
                "/api/v1/auth/login",
                headers={**self.PREFLIGHT, "Access-Control-Request-Headers": "authorization"},
            )

        allowed = response.headers.get("access-control-allow-headers", "").lower()
        assert "authorization" in allowed
        assert "gateway" not in allowed

    async def test_a_foreign_origin_preflight_is_answered_but_not_allowed(
        self, patch_settings: Any
    ) -> None:
        """Reaching CORSMiddleware is not the same as being permitted by it.

        The guard stops filtering; CORS decides. An origin outside `CORS_ORIGINS` gets a
        response with no `Access-Control-Allow-Origin`, which is what makes the browser block
        the request that would have followed.
        """
        patch_settings(cors_origins=["http://localhost:5173"])
        async with await _client() as http:
            response = await http.options(
                "/api/v1/auth/login",
                headers={**self.PREFLIGHT, "Origin": "http://evil.example"},
            )

        assert "access-control-allow-origin" not in response.headers

    async def test_a_preflight_is_recognised_only_with_both_headers(self) -> None:
        """`is_cors_preflight` is the same three-part test CORSMiddleware uses.

        Asserted on the predicate rather than over HTTP, because with no guard rejecting
        `OPTIONS` any more, the two cases are indistinguishable end to end - and the
        predicate is what a future guard would call.
        """
        from starlette.datastructures import Headers
        from starlette.requests import Request

        from app.core.middleware import is_cors_preflight

        def request(method: str, **headers: str) -> Request:
            raw = Headers(headers).raw
            return Request({"type": "http", "method": method, "headers": raw, "path": "/"})

        assert is_cors_preflight(
            request("OPTIONS", origin="http://x", **{"access-control-request-method": "POST"})
        )
        assert not is_cors_preflight(request("OPTIONS"))
        assert not is_cors_preflight(request("OPTIONS", origin="http://x"))
        assert not is_cors_preflight(
            request("POST", origin="http://x", **{"access-control-request-method": "POST"})
        )


class TestOriginEnforcement:
    async def test_write_from_a_foreign_origin_is_refused(self, probe: AsyncClient) -> None:
        response = await probe.post(
            "/api/v1/auth/login",
            json={"email": "a@example.com", "password": "x"},
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "origin_not_allowed"

    async def test_write_from_an_allowed_origin_is_permitted(self, probe: AsyncClient) -> None:
        """Reaching validation (422) proves the origin check let it through."""
        response = await probe.post(
            "/api/v1/auth/login",
            json={},
            headers={"Origin": settings.cors_origins[0]},
        )
        assert response.status_code == 422

    async def test_referer_is_used_when_origin_is_absent(self, probe: AsyncClient) -> None:
        response = await probe.post(
            "/api/v1/auth/login",
            json={"email": "a@example.com", "password": "x"},
            headers={"Referer": "https://evil.example/some/path?q=1"},
        )
        assert response.status_code == 403

    async def test_trailing_slash_and_case_do_not_matter(self, probe: AsyncClient) -> None:
        """A check that fails on formatting rather than identity is worse than none."""
        origin = settings.cors_origins[0]
        response = await probe.post(
            "/api/v1/auth/login",
            json={},
            headers={"Referer": f"{origin.upper()}/login"},
        )
        assert response.status_code == 422

    async def test_reads_are_not_origin_checked(self, probe: AsyncClient) -> None:
        """A GET changes nothing, and the response is unreadable cross-origin anyway."""
        response = await probe.get(
            "/api/v1/auth/password-policy",
            headers={"Origin": "https://evil.example"},
        )
        assert response.status_code == 200

    async def test_no_origin_header_is_allowed(self, probe: AsyncClient) -> None:
        """The desktop app, curl, and a backup script all send neither header.

        Refusing them would break every non-browser client, and it is not what this check
        is for. What constrains those callers is authentication and rate limiting - there is
        no header-based control that can distinguish them, which is precisely why the
        gateway secret that used to sit here needed a proxy rather than a client.
        """
        response = await probe.post("/api/v1/auth/login", json={})
        assert response.status_code == 422


# =============================================================================
# Body size
# =============================================================================
class TestBodySizeLimit:
    async def test_oversized_declared_body_refused(self, probe: AsyncClient) -> None:
        response = await probe.post(
            "/api/v1/auth/login",
            content=b"x" * (settings.max_request_bytes + 1),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"

    async def test_normal_body_passes(self, probe: AsyncClient) -> None:
        assert (await probe.post("/api/v1/auth/login", json={})).status_code == 422

    async def test_malformed_content_length_refused(self, probe: AsyncClient) -> None:
        response = await probe.post(
            "/api/v1/auth/login",
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "not-a-number"},
        )
        assert response.status_code == 400

    async def test_uploads_get_the_larger_ceiling(self, probe: AsyncClient) -> None:
        """A multipart body above the JSON limit must not be refused by this layer.

        A scanned invoice is legitimately several megabytes. The upload path has its own
        streaming check against `max_upload_bytes`; this layer only bounds the transfer.
        """
        size = settings.max_request_bytes + 1024
        response = await probe.post(
            "/api/v1/documents",
            content=b"x" * size,
            headers={"Content-Type": "multipart/form-data; boundary=abc"},
        )
        assert response.status_code != 413

    async def test_undeclared_length_is_counted_while_streaming(self, probe: AsyncClient) -> None:
        """A chunked body has no Content-Length, so the header check alone is bypassable."""

        async def oversized() -> AsyncGenerator[bytes]:
            for _ in range((settings.max_request_bytes // 65536) + 2):
                yield b"x" * 65536

        response = await probe.post(
            "/api/v1/auth/login",
            content=oversized(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413


# =============================================================================
# Client IP resolution
# =============================================================================
def _request(headers: dict[str, str], peer: str = "10.0.0.5") -> Request:
    """A minimal ASGI scope - enough for the header and client accessors."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 1234),
        }
    )


class TestClientIp:
    def test_socket_peer_when_no_header(self) -> None:
        assert client_ip(_request({})) == "10.0.0.5"

    def test_takes_the_rightmost_hop(self) -> None:
        """The left-most entry is the one the caller writes.

        `curl -H 'X-Forwarded-For: 1.2.3.4'` behind one nginx produces
        `1.2.3.4, <real>`, and reading from the left hands every IP-keyed control an
        address of the attacker's choosing.
        """
        request = _request({"X-Forwarded-For": "1.2.3.4, 203.0.113.7"})
        assert client_ip(request) == "203.0.113.7"

    def test_spoofed_chain_cannot_win(self) -> None:
        request = _request({"X-Forwarded-For": "9.9.9.9, 8.8.8.8, 203.0.113.7"})
        assert client_ip(request) == "203.0.113.7"

    def test_two_hops_reads_the_second_from_the_right(self, patch_settings: Any) -> None:
        patch_settings(trusted_proxy_hops=2)
        request = _request({"X-Forwarded-For": "1.2.3.4, 198.51.100.9, 203.0.113.7"})
        assert client_ip(request) == "198.51.100.9"

    def test_fewer_entries_than_hops_does_not_raise(self, patch_settings: Any) -> None:
        patch_settings(trusted_proxy_hops=4)
        assert client_ip(_request({"X-Forwarded-For": "203.0.113.7"})) == "203.0.113.7"

    def test_header_ignored_when_proxies_are_not_trusted(self, patch_settings: Any) -> None:
        patch_settings(trust_proxy_headers=False)
        request = _request({"X-Forwarded-For": "1.2.3.4"})
        assert client_ip(request) == "10.0.0.5"

    def test_port_is_stripped(self) -> None:
        assert client_ip(_request({"X-Forwarded-For": "203.0.113.7:41234"})) == "203.0.113.7"

    def test_ipv6_is_not_truncated(self) -> None:
        """A naive `split(":")` turns every IPv6 client into one rate-limit bucket."""
        assert client_ip(_request({"X-Forwarded-For": "2001:db8::1"})) == "2001:db8::1"
        assert client_ip(_request({"X-Forwarded-For": "[2001:db8::1]:443"})) == "2001:db8::1"

    def test_empty_header_falls_back_to_peer(self) -> None:
        assert client_ip(_request({"X-Forwarded-For": "  ,  "})) == "10.0.0.5"


# =============================================================================
# Rate-limit classification
# =============================================================================
class TestClassification:
    @pytest.mark.parametrize(
        ("method", "path", "expected"),
        [
            ("GET", "/health/live", Tier.EXEMPT),
            ("GET", "/health/ready", Tier.EXEMPT),
            # Mail and one-time-secret surfaces get the tightest budget.
            ("POST", "/api/v1/auth/forgot-password", Tier.AUTH_STRICT),
            ("POST", "/api/v1/auth/reset-password", Tier.AUTH_STRICT),
            ("POST", "/api/v1/auth/magic-link", Tier.AUTH_STRICT),
            ("POST", "/api/v1/auth/otp", Tier.AUTH_STRICT),
            ("POST", "/api/v1/auth/resend-verification", Tier.AUTH_STRICT),
            # Credential and token surfaces.
            ("POST", "/api/v1/auth/login", Tier.AUTH),
            ("POST", "/api/v1/auth/login/2fa", Tier.AUTH),
            ("POST", "/api/v1/auth/register", Tier.AUTH),
            ("POST", "/api/v1/auth/refresh", Tier.AUTH),
            ("POST", "/api/v1/auth/verify-email", Tier.AUTH),
            ("POST", "/api/v1/auth/otp/verify", Tier.AUTH),
            ("POST", "/api/v1/auth/magic-link/verify", Tier.AUTH),
            ("GET", "/api/v1/invitations/some-opaque-token", Tier.AUTH),
            # The device poll is called every two seconds by design.
            ("POST", "/api/v1/auth/magic-link/device/poll", Tier.WRITE),
            # Expensive shapes.
            ("POST", "/api/v1/documents", Tier.UPLOAD),
            ("GET", "/api/v1/documents/abc/file", Tier.EXPORT),
            ("GET", "/api/v1/reports/balance-sheet/export", Tier.EXPORT),
            # The read/write split.
            ("GET", "/api/v1/invoices", Tier.READ),
            ("HEAD", "/api/v1/invoices", Tier.READ),
            ("POST", "/api/v1/invoices", Tier.WRITE),
            ("PATCH", "/api/v1/invoices/abc", Tier.WRITE),
            ("DELETE", "/api/v1/invoices/abc", Tier.WRITE),
        ],
    )
    def test_tier(self, method: str, path: str, expected: Tier) -> None:
        assert classify(method, path) is expected

    def test_preflight_does_not_consume_the_write_budget(self) -> None:
        """A browser preflights every cross-origin write.

        Charging those to the write tier would halve the effective write budget for no
        reason - a preflight reaches no handler and changes nothing.
        """
        assert classify("OPTIONS", "/api/v1/invoices") is Tier.READ

    def test_every_unauthenticated_route_is_covered(self) -> None:
        """No public endpoint may fall through to the default tier.

        The public surface is where an unauthenticated attacker operates, so a new one
        added without a matching pattern is exactly the gap worth failing a build over.
        """
        public = [
            ("POST", "/api/v1/auth/login"),
            ("POST", "/api/v1/auth/login/2fa"),
            ("POST", "/api/v1/auth/register"),
            ("POST", "/api/v1/auth/refresh"),
            ("POST", "/api/v1/auth/verify-email"),
            ("POST", "/api/v1/auth/resend-verification"),
            ("POST", "/api/v1/auth/forgot-password"),
            ("POST", "/api/v1/auth/reset-password"),
            ("POST", "/api/v1/auth/magic-link"),
            ("POST", "/api/v1/auth/magic-link/verify"),
            ("POST", "/api/v1/auth/magic-link/device"),
            ("POST", "/api/v1/auth/otp"),
            ("POST", "/api/v1/auth/otp/verify"),
            ("GET", "/api/v1/invitations/tok"),
        ]
        for method, path in public:
            tier = classify(method, path)
            assert tier in {Tier.AUTH, Tier.AUTH_STRICT}, f"{method} {path} -> {tier}"


class TestRateLimitShape:
    """The per-IP ceiling interacts with the tiers, and the interaction is invisible."""

    def test_a_lower_ip_ceiling_eclipses_the_tiers_it_undercuts(self, patch_settings: Any) -> None:
        patch_settings(rate_limit_ip="20/minute", rate_limit_read="300/minute")
        eclipsed = settings.rate_limit_tiers_eclipsed_by_ip
        assert "RATE_LIMIT_READ" in eclipsed
        assert eclipsed["RATE_LIMIT_READ"] == "300/minute"

    def test_a_higher_ip_ceiling_eclipses_nothing(self, patch_settings: Any) -> None:
        patch_settings(
            rate_limit_ip="10000/minute",
            rate_limit_default="200/minute",
            rate_limit_read="300/minute",
            rate_limit_write="60/minute",
            rate_limit_upload="12/minute",
            rate_limit_export="20/minute",
            rate_limit_auth="10/minute",
            rate_limit_auth_strict="3/minute",
        )
        assert settings.rate_limit_tiers_eclipsed_by_ip == {}

    def test_rates_are_compared_not_counts(self, patch_settings: Any) -> None:
        """`600/hour` and `10/minute` are the same rate written two ways."""
        patch_settings(rate_limit_ip="600/hour", rate_limit_read="10/minute")
        assert "RATE_LIMIT_READ" not in settings.rate_limit_tiers_eclipsed_by_ip

        patch_settings(rate_limit_read="11/minute")
        assert "RATE_LIMIT_READ" in settings.rate_limit_tiers_eclipsed_by_ip

    def test_a_malformed_ip_spec_reports_nothing(self, patch_settings: Any) -> None:
        """The limiter's own fallback reports a bad spec; this must not also raise."""
        patch_settings(rate_limit_ip="nonsense")
        assert settings.rate_limit_tiers_eclipsed_by_ip == {}


class TestBudgetParsing:
    @pytest.mark.parametrize(
        ("spec", "capacity", "seconds"),
        [
            ("200/minute", 200, 60),
            ("10/second", 10, 1),
            ("5/hour", 5, 3600),
            ("1000/day", 1000, 86400),
            ("60/minutes", 60, 60),  # plural, because that is what people write
            ("60 / Minute", 60, 60),
        ],
    )
    def test_valid(self, spec: str, capacity: int, seconds: int) -> None:
        budget = parse_budget(spec)
        assert budget.capacity == capacity
        assert budget.period_seconds == seconds

    @pytest.mark.parametrize("spec", ["nonsense", "10/fortnight", "", "0/minute", "-5/minute"])
    def test_malformed_falls_back_rather_than_raising(self, spec: str) -> None:
        """A typo in configuration must not stop the app booting or lock everyone out."""
        assert parse_budget(spec) == FALLBACK_BUDGET

    def test_refill_rate(self) -> None:
        assert parse_budget("60/minute").refill_per_second == pytest.approx(1.0)


# =============================================================================
# The bucket itself
# =============================================================================
@pytest.mark.integration
class TestTokenBucket:
    """Exercises the Lua script against a real Redis.

    A fake would assert the shape of the calls rather than the behaviour, and the
    behaviour - atomic refill arithmetic evaluated server-side - is the entire point.
    """

    async def test_allows_up_to_capacity_then_refuses(self, redis_client: Any) -> None:
        limiter = RateLimiter()
        budget = parse_budget("5/minute")
        redis = redis_client

        for expected_remaining in (4, 3, 2, 1, 0):
            decision = await limiter.check(
                redis, scope="test", identity="bucket-a", budget=budget, now=1000.0
            )
            assert decision.allowed
            assert decision.remaining == expected_remaining

        refused = await limiter.check(
            redis, scope="test", identity="bucket-a", budget=budget, now=1000.0
        )
        assert not refused.allowed
        assert refused.retry_after >= 1

    async def test_refills_over_time(self, redis_client: Any) -> None:
        limiter = RateLimiter()
        budget = parse_budget("60/minute")  # one token per second
        redis = redis_client

        for _ in range(60):
            assert (
                await limiter.check(
                    redis, scope="test", identity="bucket-b", budget=budget, now=2000.0
                )
            ).allowed
        assert not (
            await limiter.check(redis, scope="test", identity="bucket-b", budget=budget, now=2000.0)
        ).allowed

        # Ten seconds later, ten tokens have accrued.
        for _ in range(10):
            assert (
                await limiter.check(
                    redis, scope="test", identity="bucket-b", budget=budget, now=2010.0
                )
            ).allowed
        assert not (
            await limiter.check(redis, scope="test", identity="bucket-b", budget=budget, now=2010.0)
        ).allowed

    async def test_no_boundary_burst(self, redis_client: Any) -> None:
        """The weakness a fixed window has and a bucket does not.

        A fixed window keyed on `floor(now / 60)` allows the full budget in the last
        instant of one window and the full budget again in the first instant of the
        next - twice the limit, back to back. For the login tier that is 20 password
        guesses against a budget of 10.
        """
        limiter = RateLimiter()
        budget = parse_budget("10/minute")
        redis = redis_client

        # Spend the whole budget at 59.9s into a notional window...
        for _ in range(10):
            assert (
                await limiter.check(
                    redis, scope="test", identity="bucket-c", budget=budget, now=59.9
                )
            ).allowed

        # ...and 0.2s later, across the boundary, only the accrued fraction is available.
        # A fixed window would hand over ten more.
        allowed_after = 0
        for _ in range(10):
            decision = await limiter.check(
                redis, scope="test", identity="bucket-c", budget=budget, now=60.1
            )
            allowed_after += int(decision.allowed)
        assert allowed_after == 0

    async def test_buckets_are_isolated_by_identity_and_scope(self, redis_client: Any) -> None:
        limiter = RateLimiter()
        budget = parse_budget("1/minute")
        redis = redis_client

        assert (
            await limiter.check(redis, scope="test", identity="alice", budget=budget, now=3000.0)
        ).allowed
        # Alice is now empty, but Bob is untouched...
        assert not (
            await limiter.check(redis, scope="test", identity="alice", budget=budget, now=3000.0)
        ).allowed
        assert (
            await limiter.check(redis, scope="test", identity="bob", budget=budget, now=3000.0)
        ).allowed
        # ...and so is Alice's bucket in a different scope.
        assert (
            await limiter.check(redis, scope="other", identity="alice", budget=budget, now=3000.0)
        ).allowed

    async def test_clock_stepping_backwards_does_not_drain_the_bucket(
        self, redis_client: Any
    ) -> None:
        """NTP corrections happen. A negative elapsed time must not remove tokens."""
        limiter = RateLimiter()
        budget = parse_budget("10/minute")
        redis = redis_client

        await limiter.check(redis, scope="test", identity="clock", budget=budget, now=5000.0)
        decision = await limiter.check(
            redis, scope="test", identity="clock", budget=budget, now=4000.0
        )
        assert decision.allowed
        assert decision.remaining == 8


@pytest.mark.integration
class TestRateLimitMiddleware:
    """End-to-end: the tier budget applied to a real request path."""

    async def test_auth_strict_budget_is_enforced(
        self, redis_client: Any, patch_settings: Any
    ) -> None:
        patch_settings(rate_limit_enabled=True, rate_limit_auth_strict="2/minute")

        async with await _client() as http:
            statuses = [
                (
                    await http.post("/api/v1/auth/forgot-password", json={"email": "a@example.com"})
                ).status_code
                for _ in range(4)
            ]

        assert statuses[-1] == 429
        assert statuses.count(429) >= 1

    async def test_429_carries_retry_after(self, redis_client: Any, patch_settings: Any) -> None:
        patch_settings(rate_limit_enabled=True, rate_limit_auth_strict="1/minute")

        async with await _client() as http:
            await http.post("/api/v1/auth/magic-link", json={"email": "a@example.com"})
            response = await http.post("/api/v1/auth/magic-link", json={"email": "a@example.com"})

        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) >= 1
        assert response.headers["X-RateLimit-Remaining"] == "0"
        assert response.json()["error"]["code"] == "rate_limit_exceeded"

    async def test_headers_report_the_remaining_budget(
        self, redis_client: Any, patch_settings: Any
    ) -> None:
        patch_settings(rate_limit_enabled=True, rate_limit_read="50/minute")

        async with await _client() as http:
            response = await http.get("/api/v1/auth/password-policy")

        assert response.headers["X-RateLimit-Limit"] == "50"
        assert int(response.headers["X-RateLimit-Remaining"]) < 50
        assert "tier=read" in response.headers["X-RateLimit-Policy"]

    async def test_health_is_never_limited(self, redis_client: Any, patch_settings: Any) -> None:
        patch_settings(rate_limit_enabled=True, rate_limit_read="1/minute")

        async with await _client() as http:
            statuses = [(await http.get("/health/live")).status_code for _ in range(5)]

        assert statuses == [200] * 5

    async def test_a_tight_write_budget_does_not_throttle_reads(self, patch_settings: Any) -> None:
        """The point of tiers: one budget per shape, not one budget for everything."""
        patch_settings(
            rate_limit_enabled=True, rate_limit_write="1/minute", rate_limit_read="100/minute"
        )

        async with await _client() as http:
            await http.post("/api/v1/auth/magic-link/device/poll", json={"handle": "x"})
            await http.post("/api/v1/auth/magic-link/device/poll", json={"handle": "x"})
            read = await http.get("/api/v1/auth/password-policy")

        assert read.status_code == 200

    async def test_ip_ceiling_applies_on_top_of_the_tier(self, patch_settings: Any) -> None:
        """A caller must not escape source limits by staying under every tier."""
        patch_settings(
            rate_limit_enabled=True, rate_limit_read="1000/minute", rate_limit_ip="3/minute"
        )

        async with await _client() as http:
            statuses = [
                (await http.get("/api/v1/auth/password-policy")).status_code for _ in range(6)
            ]

        assert 429 in statuses

    async def test_fails_open_when_redis_is_unreachable(
        self, redis_client: Any, monkeypatch: Any
    ) -> None:
        """A cache outage must not become a total outage."""
        from app.core import middleware as middleware_module

        async def explode(*args: Any, **kwargs: Any) -> None:
            raise ConnectionError("redis is down")

        monkeypatch.setattr(middleware_module.RateLimiter, "check", explode)

        async with await _client() as http:
            assert (await http.get("/api/v1/auth/password-policy")).status_code == 200


# =============================================================================
# The declarative per-endpoint limiter (slowapi)
# =============================================================================
@pytest.fixture
async def slowapi_enabled(redis_client: Any) -> AsyncGenerator[None]:
    """Turn the slowapi limiter on for one test, and only it.

    Two things make this necessary. The limiter is constructed at import time with
    ``enabled=settings.rate_limit_enabled``, which conftest sets to false so the suite's
    rapid-fire auth calls do not trip it - so patching settings afterwards has no effect
    and the flag has to be flipped on the instance. And the middleware limiter is left
    *off*, so a 429 here can only have come from the decorator: that is the whole point
    of the test, since the two layers are meant to be independently effective.
    """
    from app.core.limiter import limiter as slowapi_limiter

    slowapi_limiter.enabled = True
    # slowapi keeps its own key space in the same Redis index, so `redis_client`'s flush
    # clears it too - but only because it flushes the whole db rather than a prefix.
    try:
        yield
    finally:
        slowapi_limiter.enabled = False
        slowapi_limiter.reset()


@pytest.mark.integration
class TestEndpointLimiter:
    """The second, route-declared limiter.

    Marked integration because slowapi is wired to the same Redis as everything else -
    an in-memory store would be per-process, so two replicas behind nginx would each
    enforce their own copy of the budget and the effective limit would be double what is
    configured.
    """

    async def test_login_is_refused_past_its_own_budget(
        self, client: AsyncClient, api: str, slowapi_enabled: None, patch_settings: Any
    ) -> None:
        """The decorator alone must be sufficient, with the middleware limiter off."""
        patch_settings(rate_limit_enabled=False)

        statuses = []
        for _ in range(8):
            response = await client.post(
                f"{api}/auth/login",
                json={"email": "nobody@example.com", "password": "Wrong-Password-1!"},
            )
            statuses.append(response.status_code)

        assert 429 in statuses, statuses
        # The budget is 5/minute, so the first five are answered (401 - no such account)
        # and the rest refused. An assertion on the exact index would be brittle; what
        # matters is that refusal happens and happens early.
        assert statuses.index(429) <= 6

    async def test_rejection_uses_the_application_error_envelope(
        self, client: AsyncClient, api: str, slowapi_enabled: None, patch_settings: Any
    ) -> None:
        """slowapi's own body is `{"error": "Rate limit exceeded: ..."}`.

        The frontend branches on `error.code`, so an un-normalised body arrives as an
        unparseable failure on the login form - the screen where a clear message matters
        most.
        """
        patch_settings(rate_limit_enabled=False)

        response = None
        for _ in range(8):
            response = await client.post(
                f"{api}/auth/login",
                json={"email": "nobody@example.com", "password": "Wrong-Password-1!"},
            )
            if response.status_code == 429:
                break

        assert response is not None
        assert response.status_code == 429
        body = response.json()
        assert body["error"]["code"] == "rate_limit_exceeded"
        assert isinstance(body["error"]["details"]["retry_after_seconds"], int)
        assert int(response.headers["Retry-After"]) >= 1


#: Endpoints carrying an explicit `@limiter.limit`, as (path, handler name).
DECORATED_ENDPOINTS = [
    ("/auth/login", "login"),
    ("/auth/login/2fa", "login_two_factor"),
    ("/auth/register", "register"),
    ("/auth/refresh", "refresh"),
    ("/auth/forgot-password", "forgot_password"),
    ("/auth/reset-password", "reset_password"),
    ("/auth/magic-link", "request_magic_link"),
    ("/auth/otp", "request_otp"),
    ("/auth/otp/verify", "verify_otp"),
    ("/auth/resend-verification", "resend_verification"),
]


class TestEndpointLimiterWiring:
    """Structural checks on the decorator, because getting it wrong is silent."""

    @pytest.mark.parametrize(("path", "handler"), DECORATED_ENDPOINTS)
    def test_a_budget_is_registered(self, path: str, handler: str) -> None:
        from app.core.limiter import limiter as slowapi_limiter

        key = f"app.modules.auth.router.{handler}"
        assert key in slowapi_limiter._route_limits, f"{path} has no registered budget"

    @pytest.mark.parametrize(("path", "handler"), DECORATED_ENDPOINTS)
    def test_the_mounted_endpoint_is_the_wrapper(self, path: str, handler: str) -> None:
        """Guards the decorator *order*, which fails silently when reversed.

        ``@limiter.limit`` has to sit *below* ``@router.post``, so that limit() wraps the
        handler and post() then registers the wrapper. Reversed, ``limit()`` still runs -
        so the budget above is still registered - but the route mounts the *bare*
        function and the wrapper is never called. The endpoint is unlimited, and nothing
        in the code, the logs, or the rest of this suite says so.

        ``functools.wraps`` sets ``__wrapped__`` on slowapi's wrapper, so what is mounted
        is checkable. These handlers carry no other decorators, which is what makes the
        attribute unambiguous here.
        """
        routes = dict(_api_routes(create_app()))
        route = routes.get(f"{settings.api_v1_prefix}{path}")
        assert route is not None, f"{path} is not mounted"
        assert getattr(route.endpoint, "__wrapped__", None) is not None, (
            f"{path} mounts the undecorated handler - @limiter.limit is above "
            f"@router.post instead of below it, so the budget is never enforced"
        )

    def test_the_budgets_come_from_settings(self) -> None:
        """The decorators read configuration rather than literals.

        They were hard-coded, which meant the number a 429 enforced appeared nowhere an
        operator would look - and `RATE_LIMIT_AUTH_STRICT=5/minute` sitting next to a
        hard-coded `3/minute` is how "/forgot-password refuses after three tries" became a
        mystery. Asserted on identity with the settings values so a future edit that pastes a
        literal back in fails here.
        """
        from app.modules.auth import router as auth_router

        assert settings.rate_limit_login == auth_router.LOGIN_LIMIT
        assert settings.rate_limit_register == auth_router.REGISTER_LIMIT
        assert settings.rate_limit_mail_sending == auth_router.MAIL_SENDING_LIMIT
        assert settings.rate_limit_token_exchange == auth_router.TOKEN_EXCHANGE_LIMIT

    def test_mail_sending_is_not_looser_than_the_auth_strict_tier(self) -> None:
        """The tighter limiter binds, so this one has to be the tighter one.

        Both apply to /forgot-password. If the decorator were ever configured *above*
        `RATE_LIMIT_AUTH_STRICT`, the tier would silently become the effective limit and this
        budget would be decoration - the same class of mistake as `RATE_LIMIT_IP` eclipsing a
        tier, and just as invisible.
        """
        from app.core.config import _rate_per_second

        mail = _rate_per_second(settings.rate_limit_mail_sending)
        strict = _rate_per_second(settings.rate_limit_auth_strict)
        assert mail is not None and strict is not None
        assert mail <= strict, (
            f"RATE_LIMIT_MAIL_SENDING ({settings.rate_limit_mail_sending}) is looser than "
            f"RATE_LIMIT_AUTH_STRICT ({settings.rate_limit_auth_strict}), so the tier binds "
            f"first and the per-endpoint budget never applies"
        )


class TestRateLimitBudgetValidation:
    """A malformed budget is refused at boot, in every environment.

    Newly load-bearing: while these were literals in Python, a typo was caught in review.
    Now they arrive from `.env`, where `"5/min"` is a very plausible thing to write - and the
    two limiters fail *differently* on one, neither naming the variable. A tier silently
    falls back to `FALLBACK_BUDGET`; a per-endpoint spec raises from inside slowapi during
    import, before logging exists.
    """

    @staticmethod
    def _build(**overrides: Any) -> Any:
        from app.core.config import Settings

        return Settings(_env_file=None, environment="development", **overrides)

    @pytest.mark.parametrize(
        "spec",
        ["5/min", "5 per minute", "5", "minute/5", "", "5/fortnight", "abc/minute"],
    )
    def test_malformed_specs_are_refused(self, spec: str) -> None:
        with pytest.raises(ValueError, match="not a valid budget"):
            self._build(rate_limit_mail_sending=spec)

    @pytest.mark.parametrize("spec", ["5/minute", "10/second", "600/hour", "1000/day", "5/minutes"])
    def test_valid_specs_are_accepted(self, spec: str) -> None:
        """Plural period names too - `_rate_per_second` strips the trailing `s`."""
        assert self._build(rate_limit_mail_sending=spec).rate_limit_mail_sending == spec

    def test_the_error_names_every_offender_at_once(self) -> None:
        """One boot, one complete list - not a fix-and-retry loop per variable."""
        with pytest.raises(ValueError) as caught:
            self._build(rate_limit_login="nope", rate_limit_register="also-nope")

        message = str(caught.value)
        assert "RATE_LIMIT_LOGIN" in message
        assert "RATE_LIMIT_REGISTER" in message

    def test_tier_budgets_are_validated_too(self) -> None:
        """Not just the new per-endpoint ones - the tiers had the silent-fallback failure."""
        with pytest.raises(ValueError, match="RATE_LIMIT_READ"):
            self._build(rate_limit_read="25 per minute")


def _api_routes(app: Any) -> list[tuple[str, Any]]:
    """Every mounted APIRoute, as ``(full_path, route)``.

    FastAPI 0.140 nests included routers behind ``_IncludedRouter`` objects rather than
    flattening them into ``app.routes``, so one pass over that list finds only the handful
    of top-level routes. The prefix is accumulated on the way down rather than read off
    the route, whose ``path`` stays relative to the router that owns it.
    """
    from fastapi import APIRouter
    from fastapi.routing import APIRoute

    found: list[tuple[str, Any]] = []

    def walk(routes: list[Any], prefix: str) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                found.append((prefix + route.path, route))
            elif isinstance(route, APIRouter):
                walk(route.routes, prefix + (route.prefix or ""))
            elif hasattr(route, "original_router"):
                context = getattr(route, "include_context", None)
                walk(route.original_router.routes, prefix + (getattr(context, "prefix", "") or ""))

    walk(app.routes, "")
    return found


# =============================================================================
# Request id handling
# =============================================================================
class TestRequestId:
    async def test_generated_when_absent(self, probe: AsyncClient) -> None:
        request_id = (await probe.get("/health/live")).headers["X-Request-ID"]
        assert len(request_id) >= 32

    async def test_inbound_id_is_honoured(self, probe: AsyncClient) -> None:
        response = await probe.get("/health/live", headers={"X-Request-ID": "trace-abc-123"})
        assert response.headers["X-Request-ID"] == "trace-abc-123"

    async def test_header_injection_attempt_is_discarded(self, probe: AsyncClient) -> None:
        """The id is echoed into a response header and into every log line for the
        request, so an unfiltered value is both a response-splitting primitive and a way
        to forge convincing log entries."""
        response = await probe.get(
            "/health/live",
            headers={"X-Request-ID": "abc<script>alert(1)</script>"},
        )
        echoed = response.headers["X-Request-ID"]
        assert "<" not in echoed
        assert echoed != "abc<script>alert(1)</script>"

    async def test_absurdly_long_id_is_discarded(self, probe: AsyncClient) -> None:
        response = await probe.get("/health/live", headers={"X-Request-ID": "a" * 5000})
        assert len(response.headers["X-Request-ID"]) <= 64


# =============================================================================
# Configuration guardrails
# =============================================================================
class TestProductionGuardrails:
    """The checks that refuse to start rather than serve traffic misconfigured."""

    BASE: ClassVar[dict[str, Any]] = {
        "environment": "production",
        "debug": False,
        "secret_key": "a" * 48,
        "encryption_key": "8hVw2YXeE9SbgPBVVO2TJX1GnjkV8o9tdI3h9EHshXM=",
        "cors_origins": ["https://app.example.com"],
        "allowed_hosts": ["app.example.com"],
        "frontend_url": "https://app.example.com",
        "postgres_password": "a-real-password",
        "rate_limit_enabled": True,
        "enforce_origin": True,
    }

    def _build(self, **overrides: Any) -> Any:
        from app.core.config import Settings

        # `_env_file=None` so the developer's own .env cannot make this pass or fail.
        return Settings(**{**self.BASE, **overrides}, _env_file=None)

    def test_a_correct_production_config_is_accepted(self) -> None:
        assert self._build().environment is Environment.PRODUCTION

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            ({"debug": True}, "DEBUG"),
            ({"cors_origins": ["*"]}, "CORS_ORIGINS"),
            ({"cors_origins": ["http://app.example.com"]}, "https://"),
            ({"cors_origins": []}, "CORS_ORIGINS"),
            ({"allowed_hosts": ["*"]}, "ALLOWED_HOSTS"),
            ({"allowed_hosts": []}, "ALLOWED_HOSTS"),
            ({"frontend_url": "http://app.example.com"}, "FRONTEND_URL"),
            ({"secret_key": "short"}, "SECRET_KEY"),
            ({"encryption_key": None}, "ENCRYPTION_KEY"),
            ({"rate_limit_enabled": False}, "RATE_LIMIT_ENABLED"),
            ({"enforce_origin": False}, "ENFORCE_ORIGIN"),
        ],
    )
    def test_refuses_to_start(self, overrides: dict[str, Any], expected: str) -> None:
        with pytest.raises(ValueError, match=expected):
            self._build(**overrides)

    def test_development_is_not_subject_to_any_of_this(self) -> None:
        from app.core.config import Settings

        built = Settings(environment="development", _env_file=None)
        assert built.debug is True
        assert built.enforce_origin is True
