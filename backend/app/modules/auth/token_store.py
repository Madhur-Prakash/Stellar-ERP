"""Redis-backed store for short-lived auth artefacts.

Everything here is ephemeral and reconstructible: one-time tokens, OTP codes,
failed-login counters, 2FA challenges, revocation markers. Redis is the right
home because each one carries a natural TTL, and expiry-as-a-feature means no
cleanup job and no table of dead rows.

Only token *digests* are used as keys, never the tokens themselves. A dump of
Redis therefore leaks nothing replayable - the same reasoning as hashing refresh
tokens in PostgreSQL.

Consumption is atomic (``GETDEL``). A one-time token that could be read and then
deleted in two steps is a race: two concurrent requests both read it, both
succeed, and "one-time" becomes "twice".
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import RedisKey, get_redis
from app.core.security import generate_otp, generate_token, generate_user_code, hash_token

log = get_logger(__name__)

#: Cap on OTP verification attempts before the code is destroyed. Without it, a
#: 6-digit code is brute-forceable in ~500k requests.
MAX_OTP_ATTEMPTS: Final = 5


class OneTimeTokenStore:
    """Issue and atomically consume single-use tokens."""

    def __init__(self, key_builder: Any, ttl: dt.timedelta) -> None:
        self._key = key_builder
        self._ttl = ttl

    async def issue(self, payload: dict[str, Any]) -> str:
        """Mint a token and store its payload under the token's digest.

        Returns the *plaintext* token - the only time it exists in the clear.
        """
        token = generate_token()
        await get_redis().set(
            self._key(hash_token(token)),
            json.dumps(payload),
            ex=int(self._ttl.total_seconds()),
        )
        return token

    async def consume(self, token: str) -> dict[str, Any] | None:
        """Atomically fetch and delete. ``None`` if unknown, expired, or spent."""
        raw = await get_redis().getdel(self._key(hash_token(token)))
        if raw is None:
            return None
        try:
            payload: dict[str, Any] = json.loads(raw)
            return payload
        except json.JSONDecodeError:
            log.error("corrupt token payload in redis")
            return None

    async def peek(self, token: str) -> dict[str, Any] | None:
        """Read without consuming. For pre-flight UI checks ("is this link still
        valid?") where consuming would break the subsequent real submission."""
        raw = await get_redis().get(self._key(hash_token(token)))
        if raw is None:
            return None
        try:
            payload: dict[str, Any] = json.loads(raw)
            return payload
        except json.JSONDecodeError:
            return None

    async def revoke(self, token: str) -> bool:
        return bool(await get_redis().delete(self._key(hash_token(token))))


def email_verification_store() -> OneTimeTokenStore:
    return OneTimeTokenStore(
        RedisKey.email_verification,
        dt.timedelta(hours=settings.email_verification_ttl_hours),
    )


def magic_link_store() -> OneTimeTokenStore:
    return OneTimeTokenStore(
        RedisKey.magic_link,
        dt.timedelta(minutes=settings.magic_link_ttl_minutes),
    )


# =============================================================================
# Device sign-in
# =============================================================================
#: How long an approved sign-in waits to be collected.
#:
#: Measured from approval, not from the request, so a link clicked in the last second
#: of its own window still leaves the app time to poll. Short because an approved
#: record is a session waiting to be claimed.
_DEVICE_CLAIM_TTL: Final = dt.timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class DeviceSignIn:
    """A sign-in started on one client and approved from another."""

    user_code: str
    email: str
    #: ``None`` while pending. Set once the emailed link has been opened.
    user_id: str | None


class DeviceSignInStore:
    """Sign-ins for a client that cannot receive the emailed link.

    The desktop app can send a magic link but never sees it: the link opens in a
    browser, and the browser is the only client that could consume it. So the app
    instead opens a record here, shows the user a short code, and polls until the
    link is opened somewhere else - on this machine or another one entirely.

    **The handle is the credential; the code is not.** The 256-bit handle is minted
    for the app and never leaves it - not into the email, not into the URL - and only
    its digest is stored, so a Redis dump yields nothing pollable. The four-character
    ``user_code`` exists so the person opening the mail can tell whether the request
    came from the device in front of them: without it, anyone who knows an address
    could start a sign-in and hope the owner clicks the link, which would hand a
    session to the attacker's app rather than merely signing the owner in.

    :meth:`approve` deliberately takes a *digest* rather than a handle. The approving
    request is the browser's, and it knows the magic-link token, not the handle - the
    digest travels inside that token's payload. Nothing in the approval path can
    reconstruct a pollable handle.
    """

    async def open(self, email: str) -> tuple[str, str]:
        """Start a pending sign-in. Returns ``(handle, user_code)``.

        Called before the address is known to exist, and that is the point: the
        endpoint answers identically either way, so a record with nothing to approve
        it is the neutral outcome for an address with no account.
        """
        handle = generate_token()
        user_code = generate_user_code()
        await get_redis().set(
            RedisKey.device_sign_in(hash_token(handle)),
            json.dumps({"user_code": user_code, "email": email.strip().lower(), "user_id": None}),
            ex=int(dt.timedelta(minutes=settings.magic_link_ttl_minutes).total_seconds()),
        )
        return handle, user_code

    async def read(self, handle: str) -> DeviceSignIn | None:
        """The current state, or ``None`` if unknown, expired, or already claimed."""
        return self._decode(await get_redis().get(RedisKey.device_sign_in(hash_token(handle))))

    async def read_by_digest(self, handle_digest: str) -> DeviceSignIn | None:
        """As :meth:`read`, for the approving request - which holds only the digest.

        Read-only on purpose: this is the path the browser is on, and it must be able
        to show the ``user_code`` without gaining anything it could poll with.
        """
        return self._decode(await get_redis().get(RedisKey.device_sign_in(handle_digest)))

    async def approve(self, handle_digest: str, user_id: uuid.UUID) -> bool:
        """Record that the emailed link was opened. ``False`` if it had expired.

        Read-then-write rather than atomic, which is safe here: the only concurrent
        writer is a second click on the same link, and it writes the same value.
        """
        key = RedisKey.device_sign_in(handle_digest)
        record = self._decode(await get_redis().get(key))
        if record is None:
            return False

        await get_redis().set(
            key,
            json.dumps(
                {
                    "user_code": record.user_code,
                    "email": record.email,
                    "user_id": str(user_id),
                }
            ),
            ex=int(_DEVICE_CLAIM_TTL.total_seconds()),
        )
        return True

    async def close(self, handle: str) -> None:
        """Destroy the record. Called the moment it is claimed, so it is single-use."""
        await get_redis().delete(RedisKey.device_sign_in(hash_token(handle)))

    @staticmethod
    def _decode(raw: bytes | str | None) -> DeviceSignIn | None:
        # `bytes | str` because that is what redis-py's `get` is typed as - decoding is
        # configured on the client, but the annotation covers both.
        if raw is None:
            return None
        try:
            payload: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            log.error("corrupt device sign-in payload in redis")
            return None
        user_id = payload.get("user_id")
        return DeviceSignIn(
            user_code=str(payload.get("user_code", "")),
            email=str(payload.get("email", "")),
            user_id=str(user_id) if user_id else None,
        )


# =============================================================================
# OTP codes
# =============================================================================
class OtpStore:
    """Email OTP codes, keyed by address rather than by an opaque token.

    A user types a code without any accompanying identifier, so the address is
    the only available lookup key. Requesting a new code overwrites the old one,
    which keeps "the code from my most recent email" true - the behaviour users
    expect.

    **One instance per purpose.** ``purpose`` is part of every key, so a sign-in
    code and a password-reset code for the same address are different entries with
    independent attempt budgets. Sharing a namespace would mean a code mailed to
    start a session could be typed into the reset form instead - the weaker intent
    buying the stronger capability. Requesting one also must not silently invalidate
    the other, which a shared key would do.

    The TTL is read through a callable rather than captured at construction: these
    are module-level singletons built at import, and a test that overrides
    ``settings`` afterwards would otherwise keep the old window.
    """

    def __init__(self, purpose: str, ttl_minutes: Callable[[], int]) -> None:
        self._purpose = purpose
        self._ttl_minutes = ttl_minutes

    def _ttl_seconds(self) -> int:
        return int(dt.timedelta(minutes=self._ttl_minutes()).total_seconds())

    async def issue(self, email: str) -> str:
        code = generate_otp()
        redis = get_redis()

        pipe = redis.pipeline()
        # Store the digest, not the code: an OTP is low-entropy enough that a
        # Redis dump would otherwise hand over live codes.
        pipe.set(RedisKey.otp(self._purpose, email), hash_token(code), ex=self._ttl_seconds())
        # Fresh code, fresh budget.
        pipe.delete(RedisKey.otp_attempts(self._purpose, email))
        await pipe.execute()
        return code

    async def check(self, email: str, code: str) -> bool:
        """Is this the current code? Enforces the attempt budget, but does **not** spend it.

        Split from :meth:`consume` because "the code is correct" and "the request that
        presented it succeeded" are different facts, and collapsing them cost a user their
        code every time anything *after* the check rejected the request. See
        :meth:`~app.modules.auth.service.AuthService.reset_password` for the case that
        matters: a correct code with a password the policy refuses.

        ``MAX_OTP_ATTEMPTS`` wrong guesses are permitted; the failure that exhausts the
        budget destroys the code immediately. Deferring destruction to the *next* request
        would leave a spent code sitting in Redis, still redeemable, whenever an attacker
        simply stops guessing.

        **A correct code does not spend budget.** The counter exists to bound *guessing*,
        and a caller who presented the right code is not guessing. Without the refund below,
        five policy-rejected passwords would destroy a perfectly good code - the attempt
        limiter punishing the one person it is not aimed at.
        """
        redis = get_redis()
        code_key = RedisKey.otp(self._purpose, email)
        attempts_key = RedisKey.otp_attempts(self._purpose, email)

        # `incr` first, and atomically: a `get`-then-`incr` lets concurrent guesses read the
        # same value and each conclude it has budget left.
        attempts = await redis.incr(attempts_key)
        if attempts == 1:
            await redis.expire(attempts_key, self._ttl_seconds())

        if attempts > MAX_OTP_ATTEMPTS:
            # Budget was already spent by an earlier request.
            await redis.delete(code_key)
            return False

        stored = await redis.get(code_key)
        if stored is None or stored != hash_token(code.strip()):
            if attempts >= MAX_OTP_ATTEMPTS:
                await redis.delete(code_key)
                log.warning(
                    "otp attempt budget exhausted - code destroyed",
                    extra={"email": email, "purpose": self._purpose, "attempts": attempts},
                )
            return False

        await redis.decr(attempts_key)
        return True

    async def consume(self, email: str) -> None:
        """Spend the code, so it cannot be presented again.

        Separate from :meth:`check` so a caller with more work to do can defer this until
        that work has succeeded. Idempotent - deleting an absent key is not an error.
        """
        redis = get_redis()
        pipe = redis.pipeline()
        pipe.delete(RedisKey.otp(self._purpose, email))
        pipe.delete(RedisKey.otp_attempts(self._purpose, email))
        await pipe.execute()

    async def verify(self, email: str, code: str) -> bool:
        """Check a code and spend it in one step.

        The right call when nothing after it can fail in a way the user could correct - the
        sign-in flow, where the next step is issuing a session. When something *can* reject
        the request afterwards, use :meth:`check` and :meth:`consume` instead.
        """
        if not await self.check(email, code):
            return False
        await self.consume(email)
        return True


# =============================================================================
# Brute-force protection
# =============================================================================
class LoginThrottle:
    """Per-identifier failed-login counter with a lockout.

    Keyed on the email rather than the IP: an attacker rotates IPs trivially, and
    IP-based locking punishes everyone behind one NAT. Rate limiting by IP is a
    separate layer, handled in middleware.
    """

    async def is_locked(self, identifier: str) -> int:
        """Remaining lockout in seconds, or ``0`` if not locked."""
        ttl = await get_redis().ttl(RedisKey.login_lockout(identifier))
        return max(0, ttl)

    async def record_failure(self, identifier: str) -> tuple[int, int]:
        """Count a failure, locking out at the threshold.

        Returns ``(attempts, lockout_seconds)`` where ``lockout_seconds`` is
        non-zero only on the attempt that triggers the lock.
        """
        redis = get_redis()
        window = int(dt.timedelta(minutes=settings.login_lockout_minutes).total_seconds())

        attempts = await redis.incr(RedisKey.login_attempts(identifier))
        if attempts == 1:
            await redis.expire(RedisKey.login_attempts(identifier), window)

        if attempts >= settings.max_login_attempts:
            await redis.set(RedisKey.login_lockout(identifier), "1", ex=window)
            await redis.delete(RedisKey.login_attempts(identifier))
            log.warning(
                "account locked after repeated failures",
                extra={"identifier": identifier, "attempts": attempts},
            )
            return attempts, window

        return attempts, 0

    async def reset(self, identifier: str) -> None:
        """Clear counters after a successful login."""
        pipe = get_redis().pipeline()
        pipe.delete(RedisKey.login_attempts(identifier))
        pipe.delete(RedisKey.login_lockout(identifier))
        await pipe.execute()

    async def remaining_attempts(self, identifier: str) -> int:
        used = await get_redis().get(RedisKey.login_attempts(identifier))
        return max(0, settings.max_login_attempts - int(used or 0))


# =============================================================================
# Two-factor challenges
# =============================================================================
class TwoFactorChallengeStore:
    """Holds the interstitial state between "password OK" and "code OK".

    The challenge id is what the client echoes back with the TOTP code. It exists
    so the second step needs no re-transmission of the password, and so the
    partial authentication expires on its own if abandoned.
    """

    #: Short by design: this is an interstitial, not a session.
    TTL = dt.timedelta(minutes=5)

    async def create(self, user_id: uuid.UUID, context: dict[str, Any] | None = None) -> str:
        challenge_id = str(uuid.uuid4())
        await get_redis().set(
            RedisKey.totp_challenge(challenge_id),
            json.dumps({"user_id": str(user_id), **(context or {})}),
            ex=int(self.TTL.total_seconds()),
        )
        return challenge_id

    async def resolve(self, challenge_id: str) -> dict[str, Any] | None:
        """Read without consuming, so a mistyped code can be retried."""
        raw = await get_redis().get(RedisKey.totp_challenge(challenge_id))
        if raw is None:
            return None
        try:
            payload: dict[str, Any] = json.loads(raw)
            return payload
        except json.JSONDecodeError:
            return None

    async def discard(self, challenge_id: str) -> None:
        await get_redis().delete(RedisKey.totp_challenge(challenge_id))

    async def burn_code(self, user_id: uuid.UUID, code: str, ttl_seconds: int) -> bool:
        """Mark a TOTP code spent. ``False`` means it was already used.

        ``SET NX`` makes claim-and-check a single atomic operation; checking then
        setting would let two concurrent requests both win.
        """
        claimed = await get_redis().set(
            RedisKey.totp_replay(str(user_id), hash_token(code)),
            "1",
            ex=ttl_seconds,
            nx=True,
        )
        return bool(claimed)


# =============================================================================
# Access-token invalidation
# =============================================================================
class TokenEpochStore:
    """Per-user counter that invalidates outstanding access tokens.

    Access tokens are stateless JWTs, so they cannot be individually revoked
    without a database lookup per request - which would defeat the point. Instead
    each token carries the user's epoch, and bumping the epoch makes every
    already-issued token stale immediately.

    Used for: password change, "sign out everywhere", role changes, and account
    deactivation.
    """

    #: Outlives the longest possible access token; nothing older can still exist.
    TTL = dt.timedelta(days=1)

    async def current(self, user_id: uuid.UUID | str) -> int:
        raw = await get_redis().get(RedisKey.user_token_epoch(str(user_id)))
        return int(raw or 0)

    async def bump(self, user_id: uuid.UUID | str) -> int:
        redis = get_redis()
        key = RedisKey.user_token_epoch(str(user_id))
        epoch = await redis.incr(key)
        await redis.expire(key, int(self.TTL.total_seconds()))
        log.info("token epoch bumped", extra={"user_id": str(user_id), "epoch": epoch})
        return int(epoch)


class SessionRevocationStore:
    """Marks individual sessions as revoked for the access-token layer.

    The epoch counter is the right tool for revoking *all* of a user's tokens,
    but too blunt for "sign out this one device" - bumping the epoch would log
    the user out everywhere. This store handles the single-session case.

    Entries only need to outlive the longest-lived access token: once the JWT
    expires it is rejected on its own, and the marker becomes redundant. The
    session row in PostgreSQL remains the durable record.
    """

    def _ttl_seconds(self) -> int:
        # One extra minute of slack for clock skew between app and Redis hosts.
        return (settings.access_token_ttl_minutes + 1) * 60

    async def revoke(self, session_id: uuid.UUID | str) -> None:
        await get_redis().set(
            RedisKey.revoked_session(str(session_id)), "1", ex=self._ttl_seconds()
        )

    async def revoke_many(self, session_ids: list[uuid.UUID | str]) -> None:
        if not session_ids:
            return
        pipe = get_redis().pipeline()
        for session_id in session_ids:
            pipe.set(RedisKey.revoked_session(str(session_id)), "1", ex=self._ttl_seconds())
        await pipe.execute()

    async def is_revoked(self, session_id: uuid.UUID | str) -> bool:
        return bool(await get_redis().exists(RedisKey.revoked_session(str(session_id))))


# Module-level singletons - all are stateless wrappers over the shared pool.
device_sign_ins = DeviceSignInStore()

#: The sign-in code.
otp_store = OtpStore("login", lambda: settings.otp_ttl_minutes)

#: The password-reset code. A separate namespace from :data:`otp_store` - see
#: :class:`OtpStore` - and it borrows ``password_reset_ttl_minutes`` so the reset
#: window stays tunable independently of the sign-in one.
password_reset_otp_store = OtpStore("password-reset", lambda: settings.password_reset_ttl_minutes)
revoked_sessions = SessionRevocationStore()
login_throttle = LoginThrottle()
two_factor_challenges = TwoFactorChallengeStore()
token_epochs = TokenEpochStore()
