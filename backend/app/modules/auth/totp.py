"""TOTP (RFC 6238) two-factor authentication.

Standard parameters - 6 digits, 30-second step, SHA-1 - because that is what
Google Authenticator, Authy, 1Password, and every other authenticator app
implements. Deviating (SHA-256, 8 digits) is cryptographically defensible and
practically useless: most apps silently ignore the algorithm parameter in the
provisioning URI and compute SHA-1 anyway, producing codes that never validate.

Two replay defences, addressing different attacks:

* **A one-step window** (``valid_window=1``) tolerates clock skew, accepting the
  previous, current, and next code. Wider windows multiply the guessing surface.
* **Single-use enforcement.** Because a code stays valid for up to 90 seconds, an
  attacker who observes one (shoulder-surfing, a phished form) can replay it.
  Every accepted code is therefore burned in Redis for the rest of its window -
  see :func:`app.core.redis.RedisKey.totp_replay`.
"""

from __future__ import annotations

import base64
import io
from typing import Final
from urllib.parse import quote

import pyotp
import qrcode
from qrcode.image.pil import PilImage

from app.core.config import settings

DIGITS: Final = 6
INTERVAL_SECONDS: Final = 30
#: Accept one step either side of now - ±30s of clock skew.
VALID_WINDOW: Final = 1
#: 160 bits, the RFC 4226 recommendation, as 32 base32 characters.
SECRET_LENGTH: Final = 32


def generate_secret() -> str:
    """Generate a fresh base32 TOTP secret.

    ``pyotp.random_base32`` draws from :mod:`secrets`, so this is CSPRNG-backed.
    Store it encrypted - see :func:`app.core.security.encrypt_secret`.
    """
    return pyotp.random_base32(length=SECRET_LENGTH)


def _totp(secret: str) -> pyotp.TOTP:
    return pyotp.TOTP(secret, digits=DIGITS, interval=INTERVAL_SECONDS)


def build_provisioning_uri(secret: str, *, email: str, issuer: str | None = None) -> str:
    """Build the ``otpauth://`` URI an authenticator app scans.

    The account label includes the issuer prefix (``Personal ERP:priya@acme.com``)
    so the entry is identifiable in an app holding thirty other codes.
    """
    issuer_name = issuer or settings.app_name
    return _totp(secret).provisioning_uri(name=email, issuer_name=quote(issuer_name, safe=""))


def build_qr_code_data_uri(provisioning_uri: str) -> str:
    """Render the provisioning URI as a base64 PNG ``data:`` URI.

    Returned inline so the secret never becomes a separately fetchable URL that
    could be logged by a proxy or leaked via ``Referer``.
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(provisioning_uri)
    qr.make(fit=True)

    image: PilImage = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def verify_code(secret: str, code: str) -> bool:
    """Verify a TOTP code against the secret.

    ``pyotp.verify`` compares in constant time internally. Replay prevention is
    *not* handled here - the service layer must also burn the code in Redis.
    """
    cleaned = code.strip().replace(" ", "").replace("-", "")
    if not cleaned.isdigit() or len(cleaned) != DIGITS:
        return False
    return bool(_totp(secret).verify(cleaned, valid_window=VALID_WINDOW))


def replay_ttl_seconds() -> int:
    """How long a used code must stay burned.

    Covers the whole window the code could still validate in: the accept window
    spans ``2 * VALID_WINDOW + 1`` steps, plus one step of slack for a code
    accepted at the very end of its step.
    """
    return INTERVAL_SECONDS * (2 * VALID_WINDOW + 2)


def normalise_recovery_code(code: str) -> str:
    """Canonicalise a recovery code for comparison.

    Users retype these from paper, so case and the cosmetic hyphen are ignored.
    """
    return code.strip().lower().replace("-", "").replace(" ", "")
