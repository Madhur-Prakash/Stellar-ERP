"""Unit tests for cryptographic primitives.

No database or Redis - these are pure functions, and the properties asserted here
are the ones the whole auth system rests on.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid

import pytest

from app.core.exceptions import InvalidTokenError, TokenExpiredError
from app.core.security import (
    create_access_token,
    decode_access_token,
    decrypt_secret,
    dummy_password_verify,
    encrypt_secret,
    generate_otp,
    generate_recovery_codes,
    generate_token,
    hash_password,
    hash_token,
    tokens_equal,
    verify_password,
)
from app.db.base import uuid7


class TestPasswordHashing:
    def test_hash_verifies(self) -> None:
        digest = hash_password("Correct-Horse-Battery-9")
        assert verify_password("Correct-Horse-Battery-9", digest)

    def test_wrong_password_rejected(self) -> None:
        digest = hash_password("Correct-Horse-Battery-9")
        assert not verify_password("wrong-password", digest)

    def test_uses_argon2id(self) -> None:
        """Argon2id specifically - not argon2i or argon2d."""
        assert hash_password("Correct-Horse-Battery-9").startswith("$argon2id$")

    def test_salted_so_hashes_differ(self) -> None:
        """Identical passwords must not produce identical hashes.

        Otherwise a leaked table reveals which users share a password.
        """
        a = hash_password("Correct-Horse-Battery-9")
        b = hash_password("Correct-Horse-Battery-9")
        assert a != b
        assert verify_password("Correct-Horse-Battery-9", a)
        assert verify_password("Correct-Horse-Battery-9", b)

    def test_malformed_hash_fails_closed(self) -> None:
        """A corrupt hash must return False, never raise into the login path."""
        assert not verify_password("anything", "not-a-valid-hash")
        assert not verify_password("anything", "")

    def test_rejects_oversized_password(self) -> None:
        with pytest.raises(ValueError, match="maximum length"):
            hash_password("x" * 2000)

    def test_dummy_verify_does_not_raise(self) -> None:
        """The timing-equalisation helper must swallow its expected failure."""
        dummy_password_verify()

    def test_dummy_verify_comparable_to_real(self) -> None:
        """Timing must not reveal whether an account exists.

        A generous bound: this asserts the same order of magnitude, which is what
        defeats a practical enumeration oracle, without being flaky on a loaded
        CI runner.
        """
        digest = hash_password("Correct-Horse-Battery-9")

        start = time.perf_counter()
        verify_password("wrong-password", digest)
        real = time.perf_counter() - start

        start = time.perf_counter()
        dummy_password_verify()
        dummy = time.perf_counter() - start

        assert 0.2 < (dummy / real) < 5.0, f"real={real:.4f}s dummy={dummy:.4f}s"


class TestAccessTokens:
    def test_round_trip_preserves_claims(self) -> None:
        user_id, session_id, org_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        token, jti, expires_at = create_access_token(
            user_id=user_id,
            session_id=session_id,
            organization_id=org_id,
            permissions=["invoice:read", "invoice:write"],
            epoch=7,
        )
        claims = decode_access_token(token)

        assert claims["sub"] == str(user_id)
        assert claims["sid"] == str(session_id)
        assert claims["org"] == str(org_id)
        assert claims["jti"] == jti
        assert claims["typ"] == "access"
        assert claims["epoch"] == 7
        assert claims["perms"] == ["invoice:read", "invoice:write"]
        assert expires_at > dt.datetime.now(dt.UTC)

    def test_tampered_signature_rejected(self) -> None:
        token, _, _ = create_access_token(user_id=uuid.uuid4(), session_id=uuid.uuid4())
        with pytest.raises(InvalidTokenError):
            decode_access_token(token[:-4] + "AAAA")

    def test_malformed_token_rejected(self) -> None:
        with pytest.raises(InvalidTokenError):
            decode_access_token("not.a.jwt")

    def test_expired_token_raises_distinct_error(self) -> None:
        """Expiry must be distinguishable from invalidity.

        The client's correct response differs: refresh on expiry, sign out on
        invalidity.
        """
        token, _, _ = create_access_token(
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            expires_in=dt.timedelta(seconds=-10),
        )
        with pytest.raises(TokenExpiredError):
            decode_access_token(token)

    def test_signed_with_different_key_rejected(self) -> None:
        """A token minted under another secret must not verify."""
        import jwt

        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "sid": str(uuid.uuid4()),
                "jti": str(uuid.uuid4()),
                "typ": "access",
                "iss": "personalerp",
                "aud": "personalerp-api",
                "iat": int(time.time()),
                "exp": int(time.time()) + 900,
            },
            "an-entirely-different-secret",
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError):
            decode_access_token(forged)

    def test_wrong_audience_rejected(self) -> None:
        """Guards against a token issued for another service being replayed."""
        import jwt

        from app.core.config import settings

        foreign = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "sid": str(uuid.uuid4()),
                "jti": str(uuid.uuid4()),
                "typ": "access",
                "iss": "personalerp",
                "aud": "some-other-service",
                "iat": int(time.time()),
                "exp": int(time.time()) + 900,
            },
            settings.secret_key,
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError):
            decode_access_token(foreign)

    def test_refresh_typ_rejected_as_access(self) -> None:
        """A token of the wrong type must not be usable as a bearer credential."""
        import jwt

        from app.core.config import settings

        wrong_type = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "sid": str(uuid.uuid4()),
                "jti": str(uuid.uuid4()),
                "typ": "refresh",
                "iss": "personalerp",
                "aud": "personalerp-api",
                "iat": int(time.time()),
                "exp": int(time.time()) + 900,
            },
            settings.secret_key,
            algorithm="HS256",
        )
        with pytest.raises(InvalidTokenError, match="Wrong token type"):
            decode_access_token(wrong_type)


class TestOpaqueTokens:
    def test_tokens_are_unique_and_long_enough(self) -> None:
        tokens = {generate_token() for _ in range(1000)}
        assert len(tokens) == 1000
        # 32 random bytes -> 43 base64url characters.
        assert all(len(token) >= 43 for token in tokens)

    def test_hash_is_deterministic_and_not_the_input(self) -> None:
        token = generate_token()
        assert hash_token(token) == hash_token(token)
        assert hash_token(token) != token
        assert len(hash_token(token)) == 64  # sha256 hex

    def test_constant_time_comparison(self) -> None:
        assert tokens_equal("abc123", "abc123")
        assert not tokens_equal("abc123", "abc124")
        assert not tokens_equal("abc", "abcdef")

    def test_otp_is_numeric_and_correct_length(self) -> None:
        codes = [generate_otp() for _ in range(200)]
        assert all(code.isdigit() and len(code) == 6 for code in codes)
        # A CSPRNG over 10^6 values should not repeat much in 200 draws.
        assert len(set(codes)) > 150

    def test_recovery_codes_are_formatted_and_unique(self) -> None:
        codes = generate_recovery_codes()
        assert len(codes) == 10
        assert len(set(codes)) == 10
        assert all(len(code) == 9 and code[4] == "-" for code in codes)


class TestEncryption:
    def test_round_trip(self) -> None:
        secret = "JBSWY3DPEHPK3PXP"
        assert decrypt_secret(encrypt_secret(secret)) == secret

    def test_ciphertext_differs_per_call(self) -> None:
        """Fernet uses a fresh IV, so identical plaintexts differ on the wire."""
        secret = "JBSWY3DPEHPK3PXP"
        assert encrypt_secret(secret) != encrypt_secret(secret)

    def test_tampered_ciphertext_rejected(self) -> None:
        """Fernet is authenticated: modification must be detected, not decrypted."""
        ciphertext = encrypt_secret("JBSWY3DPEHPK3PXP")
        with pytest.raises(InvalidTokenError):
            decrypt_secret(ciphertext[:-6] + "AAAAAA")


class TestUuid7:
    def test_version_and_variant(self) -> None:
        for _ in range(100):
            value = uuid7()
            assert value.version == 7
            assert value.variant == "specified in RFC 4122"

    def test_time_ordered(self) -> None:
        """The property that makes UUIDv7 usable as a cursor and index key."""
        values = []
        for _ in range(8):
            values.append(str(uuid7()))
            time.sleep(0.003)
        assert values == sorted(values)

    def test_timestamp_is_embedded(self) -> None:
        decoded = dt.datetime.fromtimestamp((uuid7().int >> 80) / 1000, dt.UTC)
        assert abs((dt.datetime.now(dt.UTC) - decoded).total_seconds()) < 5

    def test_no_collisions(self) -> None:
        assert len({uuid7() for _ in range(20_000)}) == 20_000
