"""Deciding who a request actually came from.

Every IP-based control in the system - rate-limit buckets, the client address on an
audit row, the "signed in from a new location" signal - is only as good as the answer
to "what is this caller's address?". Behind a reverse proxy that answer does not come
from the socket, and the header it does come from is written by whoever is calling.

So this module exists to make the resolution rule explicit and to make it appear in
exactly one place.

**The rule: count hops from the right.**

``X-Forwarded-For`` is a list that each proxy *appends* to, recording the peer it
accepted the connection from. A client can send ``X-Forwarded-For: 1.2.3.4`` and our
own proxy will faithfully append the real address after it::

    X-Forwarded-For: 1.2.3.4, 203.0.113.7
                     ^^^^^^^  ^^^^^^^^^^^
                     attacker  appended by our nginx - the real client

With one proxy in front, the right-most entry is the only one we wrote and therefore
the only one worth reading. With a CDN in front of nginx it is the second from the
right, which is what :attr:`~app.core.config.Settings.trusted_proxy_hops` names.

The left-most entry is the conventional choice and it is wrong: it is precisely the
value an attacker controls. uvicorn's own ``--proxy-headers`` handling takes the
left-most entry when ``--forwarded-allow-ips`` is ``*``, so ``request.client.host`` is
spoofable under that configuration - which is why nothing here reads it when proxy
headers are trusted, and why the shipped Dockerfile no longer passes ``*``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from app.core.config import settings

if TYPE_CHECKING:
    from starlette.requests import Request

#: Returned when there is no socket peer and no usable header - a test transport, or
#: a Unix-socket peer. A literal rather than ``None`` so callers cannot accidentally
#: build a rate-limit key on ``"None"`` and silently pool unrelated traffic together.
UNKNOWN_IP: Final = "unknown"

_XFF_HEADER: Final = "x-forwarded-for"


def peer_ip(request: Request) -> str:
    """The address of the socket we accepted, ignoring every header.

    This is the proxy's address in a proxied deployment, so it is not the client - but
    it *is* unforgeable, which makes it the right input for deciding whether to trust
    forwarding headers at all.
    """
    return request.client.host if request.client else UNKNOWN_IP


def client_ip(request: Request) -> str:
    """The caller's address, resolved per the module docstring.

    Falls back to the socket peer whenever forwarding headers are not trusted or the
    header is absent or unparseable. Never raises: a malformed header must degrade to a
    conservative answer rather than fail the request.
    """
    if not settings.trust_proxy_headers:
        return peer_ip(request)

    forwarded = request.headers.get(_XFF_HEADER)
    if not forwarded:
        return peer_ip(request)

    # A repeated header arrives comma-joined by Starlette, which is exactly the same
    # shape as a single header with multiple entries - so one split covers both.
    hops = [entry.strip() for entry in forwarded.split(",") if entry.strip()]
    if not hops:
        return peer_ip(request)

    # Clamped rather than checked: fewer entries than configured hops means something
    # is in front of us that is not appending, and the left-most value is still the
    # closest thing to an answer we have. Reading past the start of the list would
    # instead raise on every such request.
    index = min(settings.trusted_proxy_hops, len(hops))
    return _strip_port(hops[-index])


def _strip_port(value: str) -> str:
    """Drop a ``:port`` suffix, leaving IPv6 addresses intact.

    Some proxies record ``203.0.113.7:41234``. A bare IPv6 address contains colons of
    its own, so a naive split would truncate it to ``2001`` and pool every IPv6 client
    into one rate-limit bucket.
    """
    if value.startswith("["):  # [::1]:8080
        end = value.find("]")
        return value[1:end] if end != -1 else value
    if value.count(":") == 1:  # host:port, not IPv6
        return value.rsplit(":", 1)[0]
    return value


# `secrets_match` lived here: a constant-time comparison of a request header against a
# configured secret. Its only caller was the gateway-key check, which is gone, so it went
# with it rather than staying as a utility nothing uses.
#
# If a header ever needs comparing against a secret again, the requirement it existed for
# still holds and is one stdlib call: `hmac.compare_digest(supplied, expected)` on encoded
# bytes, never `==`. String `==` short-circuits at the first differing byte, which leaks the
# length of the matching prefix through response timing - enough, over enough requests, to
# recover the secret one byte at a time.

__all__ = ["UNKNOWN_IP", "client_ip", "peer_ip"]
