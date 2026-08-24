"""Cryptographic primitives: password hashing, JWTs, one-time tokens, encryption.

Every security-sensitive operation in the app funnels through this module, so
the choices are made once and audited in one place.

Decisions worth stating explicitly:

* **Argon2id** for passwords - memory-hard, so GPU/ASIC cracking gains far less
  than it does against bcrypt or PBKDF2. Parameters are configurable and the
  stored hash records them, so raising the cost later re-hashes users on their
  next successful login instead of locking anyone out.
* **Opaque random refresh tokens, hashed before storage.** Refresh tokens are
  long-lived, so they are stored as SHA-256 digests: a database leak yields
  nothing usable. Access tokens stay stateless JWTs for cheap verification.
* **SHA-256, not Argon2, for token digests.** These are 256-bit random values,
  not human passwords - there is no dictionary to attack, so a slow KDF buys
  nothing and would add latency to every refresh.
* **Fernet (AES-128-CBC + HMAC) for TOTP secrets at rest.** A stolen database
  must not hand over working second factors.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import hashlib
import hmac
import secrets
import uuid
from enum import StrEnum
from typing import Any, Final

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.exceptions import InvalidTokenError, TokenExpiredError
from app.core.logging import get_logger

log = get_logger(__name__)

JWT_ISSUER: Final = "personalerp"
JWT_AUDIENCE: Final = "personalerp-api"


class TokenType(StrEnum):
    """The ``typ`` claim. Prevents a token minted for one purpose being
    replayed for another (e.g. a refresh token used as a bearer credential).
    """

    ACCESS = "access"
    REFRESH = "refresh"


# =============================================================================
# Passwords
# =============================================================================
_hasher = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_cost,
    parallelism=settings.argon2_parallelism,
    hash_len=32,
    salt_len=16,
)

# Argon2 rejects inputs above this; bcrypt-style silent truncation would be
# worse, so cap explicitly and surface it as a validation error upstream.
MAX_PASSWORD_BYTES: Final = 1024


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id.

    The returned string embeds the algorithm, parameters, and salt, so
    verification needs nothing else stored alongside it.
    """
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError("Password exceeds maximum length")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against its hash. False on any mismatch or malformed hash."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False
    except Exception:
        # A corrupt hash must fail closed, never raise into the login path.
        log.error("password verification error", exc_info=True)
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True when a hash was made with weaker parameters than we now require.

    Call after a successful login; if true, re-hash the supplied plaintext and
    store it. This upgrades the whole user base transparently over time.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return True


#: Hashed once at import so the timing-equalisation path costs the same as a
#: real verification without re-hashing on every miss.
_DUMMY_HASH: Final = _hasher.hash("dummy-password-for-timing-equalisation")


def dummy_password_verify() -> None:
    """Burn a hash cycle on a fixed dummy value.

    Called when login hits a non-existent user so response time does not reveal
    whether the account exists. Without it, "user not found" returns in
    microseconds while a real user costs ~50ms - a trivially measurable
    enumeration oracle.
    """
    # Always fails - the point is the elapsed time, not the result.
    with contextlib.suppress(VerificationError):
        _hasher.verify(_DUMMY_HASH, "not-the-password")


# =============================================================================
# JWTs
# =============================================================================
def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def create_access_token(
    *,
    user_id: uuid.UUID | str,
    session_id: uuid.UUID | str,
    organization_id: uuid.UUID | str | None = None,
    permissions: list[str] | None = None,
    epoch: int = 0,
    expires_in: dt.timedelta | None = None,
) -> tuple[str, str, dt.datetime]:
    """Mint a short-lived access JWT.

    Permissions are embedded so authorization needs no database round trip on
    every request. The trade-off is staleness bounded by the token TTL
    (15 minutes by default); ``epoch`` is the escape hatch - bumping a user's
    epoch in Redis invalidates their outstanding tokens immediately, which is
    what role changes and forced logout use.

    Returns ``(token, jti, expires_at)``.
    """
    issued_at = _now()
    ttl = expires_in or dt.timedelta(minutes=settings.access_token_ttl_minutes)
    expires_at = issued_at + ttl
    jti = str(uuid.uuid4())

    claims: dict[str, Any] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": jti,
        "typ": TokenType.ACCESS.value,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": int(issued_at.timestamp()),
        "nbf": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "epoch": epoch,
    }
    if organization_id is not None:
        claims["org"] = str(organization_id)
    if permissions is not None:
        claims["perms"] = permissions

    token = jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify and decode an access JWT.

    Raises :class:`TokenExpiredError` or :class:`InvalidTokenError`. The
    expired case is distinct because the client should respond by refreshing
    rather than by logging the user out.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "jti", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.InvalidTokenError as exc:
        # Covers bad signature, wrong audience/issuer, malformed payload.
        raise InvalidTokenError() from exc

    if claims.get("typ") != TokenType.ACCESS.value:
        # A refresh token presented as a bearer credential lands here.
        raise InvalidTokenError("Wrong token type")

    return claims


# =============================================================================
# Opaque tokens (refresh, email verification, invites, magic links)
# =============================================================================
TOKEN_BYTES: Final = 32  # 256 bits


def generate_token() -> str:
    """A URL-safe 256-bit random token. Safe in links and headers."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 digest for at-rest storage of an opaque token.

    Deterministic (unsalted) on purpose: lookup is by digest, so the same token
    must always hash identically. Safe here precisely because the input is
    high-entropy random - see the module docstring.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    """Constant-time comparison, to avoid leaking a prefix match via timing."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def generate_otp(length: int | None = None) -> str:
    """A numeric one-time code drawn from a CSPRNG.

    ``secrets.randbelow`` per digit, not ``random`` - the latter is a Mersenne
    Twister whose future output is predictable from past samples.
    """
    digits = length or settings.otp_length
    return "".join(str(secrets.randbelow(10)) for _ in range(digits))


#: Alphabet for :func:`generate_user_code`. ``I``, ``O``, ``0`` and ``1`` are absent
#: because this code is read off one screen and compared against another, and those
#: four are the pairs people get wrong.
_USER_CODE_ALPHABET: Final = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_user_code(length: int = 4) -> str:
    """A short code for confirming that two screens describe the same request.

    Not a credential and not sized like one: it is shown in the app that started a
    device sign-in and repeated in the email, so the person clicking can tell whether
    the request came from the device in front of them. The secret in that flow is the
    256-bit handle, which never leaves the app.
    """
    return "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(length))


def generate_recovery_codes(count: int = 10) -> list[str]:
    """Human-transcribable 2FA backup codes, formatted ``xxxx-xxxx``."""
    codes: list[str] = []
    for _ in range(count):
        raw = secrets.token_hex(4)
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


# =============================================================================
# Symmetric encryption (TOTP secrets, future API credentials)
# =============================================================================
def _fernet() -> Fernet:
    """Build the Fernet cipher from ``ENCRYPTION_KEY``.

    In development the key may be absent, so one is derived from ``SECRET_KEY``
    to keep the app runnable. Production config validation rejects a missing
    ``ENCRYPTION_KEY`` outright, so that fallback can never ship.
    """
    key = settings.encryption_key
    if not key:
        derived = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(derived).decode("utf-8")
    return Fernet(key.encode("utf-8") if isinstance(key, str) else key)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a value for storage in a database column."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a value written by :func:`encrypt_secret`.

    Raises :class:`InvalidTokenError` if the ciphertext was tampered with or
    encrypted under a different key (Fernet is authenticated).
    """
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        log.error("failed to decrypt secret - wrong key or tampered ciphertext")
        raise InvalidTokenError("Could not decrypt stored secret") from exc
