"""Redis client and the small key-space conventions built on top of it.

Redis is the store for everything short-lived and disposable: one-time tokens,
login-attempt counters, rate-limit windows, revoked-token markers. All of it is
reconstructible, so losing Redis degrades the service (users re-authenticate)
rather than corrupting it. Anything that must survive a restart lives in
PostgreSQL.

Every key is built through :class:`RedisKey` so the key space stays greppable
instead of accumulating hand-written f-strings across modules.
"""

from __future__ import annotations

from typing import Final

import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_client: Redis | None = None


class RedisKey:
    """Namespaced key builders. One place to see the entire key space."""

    PREFIX: Final = "personalerp"

    # --- auth: one-time tokens (value -> user id) ---
    @classmethod
    def email_verification(cls, token: str) -> str:
        return f"{cls.PREFIX}:auth:verify:{token}"

    @classmethod
    def magic_link(cls, token: str) -> str:
        return f"{cls.PREFIX}:auth:magic:{token}"

    #: A sign-in started on a client that cannot receive the emailed link - the
    #: desktop app. Keyed by the digest of the handle the client polls with.
    @classmethod
    def device_sign_in(cls, handle_digest: str) -> str:
        return f"{cls.PREFIX}:auth:device-signin:{handle_digest}"

    # --- auth: emailed codes (value -> code digest) ---
    #: ``purpose`` separates the code namespaces, and it is load-bearing rather than
    #: tidiness: signing in and resetting a password both mail a 6-digit code to the
    #: same address, and sharing one key would let a sign-in code be typed into the
    #: reset form. That is a privilege escalation - a code minted to start a session
    #: would instead set a new password - so the two can never collide.
    @classmethod
    def otp(cls, purpose: str, email: str) -> str:
        return f"{cls.PREFIX}:auth:otp:{purpose}:{email.lower()}"

    @classmethod
    def otp_attempts(cls, purpose: str, email: str) -> str:
        return f"{cls.PREFIX}:auth:otp-attempts:{purpose}:{email.lower()}"

    # --- auth: brute-force protection ---
    @classmethod
    def login_attempts(cls, identifier: str) -> str:
        return f"{cls.PREFIX}:auth:attempts:{identifier.lower()}"

    @classmethod
    def login_lockout(cls, identifier: str) -> str:
        return f"{cls.PREFIX}:auth:lockout:{identifier.lower()}"

    # --- auth: token revocation ---
    @classmethod
    def revoked_token(cls, jti: str) -> str:
        return f"{cls.PREFIX}:auth:revoked:{jti}"

    @classmethod
    def revoked_session(cls, session_id: str) -> str:
        """Marks one session's access tokens dead without a database lookup.

        Checked on every authenticated request, so it must be a Redis GET rather
        than a Postgres query.
        """
        return f"{cls.PREFIX}:auth:revoked-sid:{session_id}"

    @classmethod
    def user_token_epoch(cls, user_id: str) -> str:
        """Bumped to invalidate every access token a user currently holds."""
        return f"{cls.PREFIX}:auth:epoch:{user_id}"

    # --- 2FA ---
    @classmethod
    def totp_challenge(cls, challenge_id: str) -> str:
        return f"{cls.PREFIX}:auth:2fa:{challenge_id}"

    @classmethod
    def totp_replay(cls, user_id: str, code: str) -> str:
        """Marks a TOTP code as spent, closing the replay window."""
        return f"{cls.PREFIX}:auth:2fa-used:{user_id}:{code}"

    # --- rate limiting ---
    @classmethod
    def rate_limit(cls, scope: str, identifier: str, window: int) -> str:
        return f"{cls.PREFIX}:rl:{scope}:{identifier}:{window}"

    @classmethod
    def rate_limit_bucket(cls, scope: str, identifier: str) -> str:
        """A token bucket's state - see :mod:`app.core.ratelimit`.

        No window number in the key, unlike :meth:`rate_limit`: a bucket refills
        continuously, so one key per ``(scope, identity)`` lives for as long as the
        client keeps calling and expires on its own once they stop.
        """
        return f"{cls.PREFIX}:rlb:{scope}:{identifier}"

    # --- caching ---
    @classmethod
    def cache(cls, namespace: str, key: str) -> str:
        return f"{cls.PREFIX}:cache:{namespace}:{key}"


def get_redis() -> Redis:
    """Return the shared connection pool, creating it on first use.

    ``redis.asyncio`` multiplexes over an internal pool, so a single client is
    the correct shape for the whole process.
    """
    global _client

    if _client is None:
        _client = aioredis.from_url(
            settings.redis_dsn,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
            socket_timeout=5,
            socket_connect_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        log.info(
            "redis client initialised",
            extra={"host": settings.redis_host, "db": settings.redis_db},
        )

    return _client


async def check_redis_health() -> bool:
    """``PING`` for the readiness probe."""
    try:
        return bool(await get_redis().ping())
    except Exception as exc:
        log.error("redis health check failed", extra={"error": str(exc)})
        return False


async def close_redis() -> None:
    """Release the pool on shutdown."""
    global _client

    if _client is not None:
        await _client.aclose()
        _client = None
        log.info("redis connection closed")
