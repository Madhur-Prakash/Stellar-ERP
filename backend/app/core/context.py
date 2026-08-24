"""Request context passed from the HTTP layer into services.

Services need the caller's IP and user agent for audit rows and device history,
but they must not import :class:`~fastapi.Request` to get them - that would tie
business logic to HTTP and make every service test construct a fake request.

This small value object is the seam. The router builds one; the service consumes
it. A CLI or worker constructs it just as easily.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from fastapi import Request


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Where a request came from."""

    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    #: Populated from the user agent for display in device history.
    device_label: str | None = None
    device_type: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_request(cls, request: Request) -> Self:
        """Extract context from a Starlette request.

        The client IP is whatever :func:`app.core.net.client_ip` resolved - counting
        forwarding hops from the right, so a client cannot name its own address. It is
        read from ``request.state`` where :class:`~app.core.middleware.RequestContextMiddleware`
        put it, and re-derived only if this is called outside that middleware (a test
        constructing a request by hand).

        Not ``request.client.host``. That is uvicorn's answer, and under
        ``--forwarded-allow-ips '*'`` uvicorn takes the *left-most* ``X-Forwarded-For``
        entry, which is the one the caller wrote. Every audit row would then record an
        address of the attacker's choosing - the opposite of what an audit trail is for.
        """
        from app.core.net import client_ip
        from app.modules.auth.device import describe_device

        user_agent = request.headers.get("user-agent")
        label, device_type = describe_device(user_agent)

        return cls(
            ip_address=getattr(request.state, "client_ip", None) or client_ip(request),
            user_agent=user_agent[:500] if user_agent else None,
            request_id=getattr(request.state, "request_id", None),
            device_label=label,
            device_type=device_type,
        )


#: For background jobs and tests, where there is no HTTP request.
SYSTEM_CONTEXT = RequestContext(device_label="System")
