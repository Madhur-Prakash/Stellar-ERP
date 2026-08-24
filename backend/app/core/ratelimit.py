"""Rate limiting: which budget applies, and the algorithm that enforces it.

Two decisions are made here, and they are separable on purpose.

**Which budget** - :func:`classify` maps a request to a :class:`Tier`. The tiers exist
because "200 requests a minute" is simultaneously far too generous for a login form and
far too mean for a dashboard that fires fifteen reads on open. A single global number
has to be set for the loosest endpoint, which means it protects none of the others.

**How it is enforced** - a token bucket in Redis, evaluated by a Lua script so the read,
the refill, and the decrement are one atomic operation.

The bucket replaces a fixed window, and the reason is a real weakness rather than a
theoretical one: a fixed window keyed on ``floor(now / 60)`` lets a client spend its
whole budget in the last moment of one window and the whole of the next in the first
moment of the following one - twice the limit in a fraction of a second, straddling the
boundary. For the login tier that is 20 password guesses back to back against a budget
that says 10. A bucket has no boundary to straddle: tokens accrue continuously, so the
sustained rate is the limit and the burst is bounded by the bucket's own size.

**Identity.** The bucket keys on the authenticated user when the request carries a
usable token, and on the resolved client IP otherwise. Keying purely on IP puts an
entire NAT'd office in one bucket, and a shared budget is a denial-of-service that
users inflict on each other. A second, wider bucket keyed on IP applies regardless, so
"authenticate first" is not a way to escape source-based limits - see
:attr:`~app.core.config.Settings.rate_limit_ip`.

**Fails open.** If Redis is unreachable the request proceeds. Rate limiting is a
protective layer, and converting a cache outage into a full outage is a strictly worse
trade than serving unthrottled for the duration with an error in the log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from redis.commands.core import AsyncScript

log = get_logger(__name__)


class Tier(StrEnum):
    """A named budget. The value is also the Redis key scope."""

    EXEMPT = "exempt"
    AUTH = "auth"
    AUTH_STRICT = "auth-strict"
    READ = "read"
    WRITE = "write"
    UPLOAD = "upload"
    EXPORT = "export"
    DEFAULT = "default"


# =============================================================================
# Budget parsing
# =============================================================================
_PERIODS: Final = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


@dataclass(frozen=True, slots=True)
class Budget:
    """A parsed ``"<count>/<period>"`` spec.

    ``capacity`` is both the bucket size and the sustained allowance per ``period``, so
    one number describes both the burst and the rate. Splitting them would be more
    expressive and would double the number of knobs an operator has to reason about.
    """

    capacity: int
    period_seconds: int

    @property
    def refill_per_second(self) -> float:
        return self.capacity / self.period_seconds


#: Used when a spec cannot be parsed. Permissive on purpose: a typo in configuration
#: should degrade to loose limiting, not refuse to boot or lock everyone out.
FALLBACK_BUDGET: Final = Budget(capacity=200, period_seconds=60)


def parse_budget(spec: str) -> Budget:
    """Parse ``"200/minute"`` into a :class:`Budget`.

    Accepts a plural period (``200/minutes``) because that is what people write.
    """
    try:
        count, period = spec.split("/", 1)
        capacity = int(count)
        seconds = _PERIODS[period.strip().lower().rstrip("s")]
    except (ValueError, KeyError):
        log.error(
            "malformed rate limit spec - falling back",
            extra={"spec": spec, "fallback": "200/minute"},
        )
        return FALLBACK_BUDGET

    if capacity < 1:
        log.error("rate limit capacity must be positive - falling back", extra={"spec": spec})
        return FALLBACK_BUDGET
    return Budget(capacity=capacity, period_seconds=seconds)


def budgets() -> dict[Tier, Budget]:
    """Resolve every tier's budget from settings. Called once, at startup."""
    return {
        Tier.AUTH: parse_budget(settings.rate_limit_auth),
        Tier.AUTH_STRICT: parse_budget(settings.rate_limit_auth_strict),
        Tier.READ: parse_budget(settings.rate_limit_read),
        Tier.WRITE: parse_budget(settings.rate_limit_write),
        Tier.UPLOAD: parse_budget(settings.rate_limit_upload),
        Tier.EXPORT: parse_budget(settings.rate_limit_export),
        Tier.DEFAULT: parse_budget(settings.rate_limit_default),
    }


# =============================================================================
# Classification
# =============================================================================
#: Never limited. Orchestrator probes fire every few seconds by design, and throttling
#: them turns a busy minute into a container restart.
EXEMPT_PREFIXES: Final = ("/health",)

#: The mail-sending and one-time-secret surfaces. Matched before :data:`AUTH_PATTERNS`,
#: so the order of the two tables is load-bearing.
AUTH_STRICT_PATTERNS: Final = (
    re.compile(r"/auth/forgot-password$"),
    re.compile(r"/auth/reset-password$"),
    re.compile(r"/auth/magic-link$"),
    re.compile(r"/auth/otp$"),
    re.compile(r"/auth/resend-verification$"),
)

#: Credential, token and enumeration surfaces.
#:
#: ``/auth/refresh`` and ``/auth/verify-email`` are here deliberately. Both take an
#: opaque 256-bit token, so neither is guessable, but both are *session-lifecycle*
#: endpoints: refresh mints access tokens and rotates a long-lived credential, and
#: hammering it is a cheap way to churn session rows. Neither belongs on a 200/minute
#: budget just because it did not look like a password form.
AUTH_PATTERNS: Final = (
    re.compile(r"/auth/login"),  # also /auth/login/2fa
    re.compile(r"/auth/register$"),
    re.compile(r"/auth/refresh$"),
    re.compile(r"/auth/verify-email$"),
    re.compile(r"/auth/otp/verify$"),
    re.compile(r"/auth/magic-link/verify$"),
    re.compile(r"/auth/magic-link/device$"),
    re.compile(r"/auth/2fa"),
    re.compile(r"/invitations/[^/]+$"),  # invitation preview: token lookup by URL
)

#: Paths :data:`AUTH_PATTERNS` would otherwise capture but that must not take the
#: tighter budget.
#:
#: The device sign-in poll is called every couple of seconds *by design*, so the
#: credential-guessing budget would fail it within seconds of the screen opening. It is
#: not a guessing surface: the handle is 256 bits, the record expires on its own, and it
#: is destroyed on first success.
AUTH_EXCEPTIONS: Final = (re.compile(r"/auth/magic-link/device/poll$"),)

#: OCR runs inline on upload, so this is the most expensive request in the system.
UPLOAD_PATTERNS: Final = (re.compile(r"/documents$"),)

#: Report rendering: a full statement assembled in memory, as xlsx or pdf.
EXPORT_PATTERNS: Final = (
    re.compile(r"/export$"),
    re.compile(r"\.(xlsx|pdf|csv)$"),
    re.compile(r"/documents/[^/]+/file$"),
)

_READ_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})


def classify(method: str, path: str) -> Tier:
    """Pick the tier for one request.

    Ordered most-specific first: the strict auth surfaces, then the rest of auth, then
    the expensive shapes, then the read/write split. ``OPTIONS`` lands in
    :attr:`Tier.READ` with the other safe methods, which matters because a browser sends
    a preflight before every cross-origin write and those must not consume the write
    budget.
    """
    if path.startswith(EXEMPT_PREFIXES):
        return Tier.EXEMPT

    if any(pattern.search(path) for pattern in AUTH_STRICT_PATTERNS):
        return Tier.AUTH_STRICT

    if any(pattern.search(path) for pattern in AUTH_PATTERNS) and not any(
        pattern.search(path) for pattern in AUTH_EXCEPTIONS
    ):
        return Tier.AUTH

    if method == "POST" and any(pattern.search(path) for pattern in UPLOAD_PATTERNS):
        return Tier.UPLOAD

    if any(pattern.search(path) for pattern in EXPORT_PATTERNS):
        return Tier.EXPORT

    if method in _READ_METHODS:
        return Tier.READ
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return Tier.WRITE
    return Tier.DEFAULT


# =============================================================================
# The bucket
# =============================================================================
#: Token bucket, evaluated server-side so the whole read-refill-decrement is atomic.
#:
#: Two reasons it is a script rather than a WATCH/MULTI loop or an INCR pair. First,
#: atomicity: two requests arriving in the same millisecond must not both read the same
#: token count and both decide there is room. Second, cost - this runs in front of every
#: request, so it has to be one round trip.
#:
#: State is two fields in a hash (tokens, last-refill timestamp) with a TTL sized to the
#: time a bucket needs to refill completely, so idle keys expire on their own and the
#: key space cannot grow without bound as clients come and go.
_BUCKET_SCRIPT: Final = """
local key        = KEYS[1]
local capacity   = tonumber(ARGV[1])
local refill     = tonumber(ARGV[2])   -- tokens per second
local now        = tonumber(ARGV[3])   -- seconds, fractional
local cost       = tonumber(ARGV[4])

local state  = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts     = tonumber(state[2])

if tokens == nil then
  tokens = capacity
  ts = now
end

-- Continuous refill. `now < ts` can happen if the clock steps backwards, so the
-- elapsed time is floored at zero rather than allowed to remove tokens.
local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * refill)

local allowed = 0
local retry_after = 0

if tokens >= cost then
  allowed = 1
  tokens = tokens - cost
else
  -- Whole seconds, rounded up, and never zero: a client told to retry in 0 seconds
  -- retries immediately and is rejected again.
  retry_after = math.ceil((cost - tokens) / refill)
  if retry_after < 1 then retry_after = 1 end
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, math.ceil(capacity / refill) + 1)

return {allowed, math.floor(tokens), retry_after}
"""


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of one bucket evaluation."""

    allowed: bool
    remaining: int
    retry_after: int
    #: The budget that produced this outcome, for the ``X-RateLimit-Limit`` header.
    limit: int


class RateLimiter:
    """Evaluates token buckets in Redis.

    The Lua script is registered once and thereafter invoked by SHA, so the script body
    crosses the wire on first use and never again. ``redis-py`` re-uploads it
    automatically if the server has been restarted or its script cache flushed.
    """

    def __init__(self) -> None:
        self._script: AsyncScript | None = None

    def _get_script(self, redis: Redis) -> AsyncScript:
        if self._script is None:
            self._script = redis.register_script(_BUCKET_SCRIPT)
        return self._script

    async def check(
        self,
        redis: Redis,
        *,
        scope: str,
        identity: str,
        budget: Budget,
        now: float,
        cost: int = 1,
    ) -> Decision:
        """Spend ``cost`` tokens from one bucket.

        The caller supplies ``now`` so that several buckets evaluated for the same
        request share one timestamp - otherwise the per-tier and per-IP buckets refill
        against slightly different clocks, which is harmless but makes the numbers in a
        log line disagree with each other.
        """
        from app.core.redis import RedisKey

        key = RedisKey.rate_limit_bucket(scope, identity)
        allowed, remaining, retry_after = await self._get_script(redis)(
            keys=[key],
            args=[budget.capacity, budget.refill_per_second, now, cost],
        )
        return Decision(
            allowed=bool(int(allowed)),
            remaining=max(0, int(remaining)),
            retry_after=max(1, int(retry_after)) if not int(allowed) else 0,
            limit=budget.capacity,
        )


__all__ = [
    "FALLBACK_BUDGET",
    "Budget",
    "Decision",
    "RateLimiter",
    "Tier",
    "budgets",
    "classify",
    "parse_budget",
]
