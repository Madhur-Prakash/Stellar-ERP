"""Transactional email through the Gmail API.

**One transport, in one file.** Everything here - loading the credential, minting
access tokens, posting the message, and the six message templates - is the single
path an email takes out of this application. There is no SMTP fallback and no
local mail catcher: a second transport means two code paths that can render the
same email differently, and the one that is not exercised is the one that breaks.

Two behaviours, chosen by whether ``GMAIL_CREDENTIALS_B64`` is configured:

* **Configured** - sends through the Gmail API.
* **Not configured** - renders the message and writes it to the logifyx log,
  including the verification and sign-in links and the emailed codes. This is not a
  transport; it is what makes the test suite and a fresh checkout work with no
  credentials at all, and it is the only way to complete those flows without them.

**Why the Gmail API rather than SMTP.** Google refuses plain passwords, and app
passwords require 2FA plus a per-account secret that any Workspace admin can
switch off org-wide. A refresh token scoped to ``gmail.send`` grants exactly one
capability - sending - so a leaked credential cannot read the mailbox it sends
from.

**The official client, not hand-rolled HTTP.** ``google-api-python-client`` owns the
send and ``google-auth`` owns the token lifecycle, so neither is reimplemented here
and neither drifts when Google changes it. The catch is that both are synchronous -
httplib2 underneath - so a send must leave the event loop or it stalls every other
request while it waits; see :func:`_send_sync`.

Configuration is a single base64 line holding a **pickled**
:class:`google.oauth2.credentials.Credentials` - the ``token.pickle`` the Gmail
quickstart writes. Base64 because a ``.env`` value cannot hold arbitrary bytes or
newlines, and quoting a credential through docker compose, a shell and pydantic is
a reliable source of corrupted secrets. One opaque line has no such edges. Mint the
value with ``uv run python scripts/mint_gmail_token.py``.

**The pickle is trusted input, and that is a real constraint.** ``pickle.loads``
executes whatever the payload tells it to, so this blob is not data - it carries the
same authority as the code in this repository. It is safe here because it comes from
the operator's own ``.env`` or secret store, written by the operator. It would not be
safe sourced from a database, an upload, an API request, or a shared/untrusted config
service. :func:`_load_credentials` does not validate the payload before unpickling
it, so that constraint is the only thing keeping this safe - if the value ever starts
arriving from somewhere the operator does not control, this function has to change
first.

Sending never raises into a request. A signup that succeeded must not report
failure because Google was briefly unreachable - the user can always request
another verification email, but a rolled-back registration is unrecoverable. A
transient failure is retried a few times and logged as a warning; a permanent one,
or the last attempt, is logged at error level for alerting.

**Links and codes, and which is which.** Signing in offers both: a magic link
(:func:`send_magic_link_email`) and a 6-digit code (:func:`send_otp_email`).
Resetting a password is a code only - never a link.

The distinction is not stylistic. A link in an inbox is a bearer credential: it
authenticates whoever opens it, including a mail scanner that prefetches URLs. That
risk is acceptable for *signing in*, where the link grants a session that 2FA still
gates and the user can revoke from device history - and it buys the one-tap flow
that makes passwordless worth having. It is not acceptable for a password reset,
where the same prefetch would hand over permanent control of the account. So the
reset code is typed back into the page that asked for it, and never leaves the
browser it was requested from.

The two codes are *not* interchangeable - see :mod:`app.modules.auth.token_store`,
where each purpose gets its own namespace, so a sign-in code cannot be replayed to
reset a password.

Templates are inline Jinja2 rather than files: Stage 1 has six emails, and a
template directory plus loader configuration is machinery for a problem that does
not exist yet. Extracting them is mechanical when the count grows.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import pickle
import threading
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from typing import Any, Final
from urllib.parse import urlencode

import anyio.to_thread
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from jinja2 import Environment, select_autoescape
from markupsafe import Markup

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

#: The scope the refresh token must carry. Not requested here - it is fixed when the
#: token is minted - but named so the error path can say what is missing.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

#: Attempts per message, and the waits between them.
#:
#: Deliberately short. Sends are awaited inside the request that triggered them -
#: registration waits for its own verification email - so every second here is a
#: second the user waits. Three quick attempts ride out a blip; a longer ladder
#: would just make a failing signup feel broken. Only transient failures are
#: retried at all, so a bad credential still fails on the first attempt.
_MAX_ATTEMPTS: Final = 3
_RETRY_WAITS: Final = (1.0, 2.0)


class GmailConfigurationError(RuntimeError):
    """The configured credential is absent, malformed, or not a usable credential.

    Separate from a delivery failure because it is never worth retrying and never
    fixes itself: every instance is a deployment problem with a specific fix.
    """


# =============================================================================
# Credentials
# =============================================================================
def _load_credentials() -> Any:
    """Unpickle the configured credential and build an authorised Gmail client.

    Returns the *client*, not the credential - hence ``Any``: `googleapiclient` has
    no stubs, so `build()` is untyped, and annotating this `-> Credentials` made
    mypy reject the `service.users()` call at the only call site.
    """
    b64 = settings.gmail_credentials_b64
    if not b64:
        raise ValueError("GMAIL_TOKEN_B64 environment variable is not set.")
    # S301: the blob is the operator's own configuration, which carries the same
    # authority as this repository's code - see the module docstring. It must never
    # be sourced from a database, an upload, or an untrusted config service.
    creds = pickle.loads(base64.b64decode(b64))  # noqa: S301

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return build("gmail", "v1", credentials=creds)


#: Sends are serialised.
#:
#: Neither the httplib2 connection inside a service object nor a shared
#: ``Credentials`` is thread-safe, and two worker threads refreshing the same
#: credential at once is a race on the token. Transactional mail is a handful of
#: messages per signup, so serialising costs nothing measurable and removes the
#: whole class of problem.
_send_lock = threading.Lock()


def _send_sync(message: EmailMessage) -> None:
    """Post one message. Blocking - the whole reason this runs in a thread."""
    # `raw` is a complete RFC 5322 message, so it is serialised with the SMTP
    # policy: CRLF line endings and folded headers, the same bytes any mail
    # transport would put on the wire.
    raw = base64.urlsafe_b64encode(message.as_bytes(policy=SMTP_POLICY)).decode("ascii")
    with _send_lock:
        service = _load_credentials()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()


def _is_transient(exc: BaseException) -> bool:
    """Whether retrying this failure could plausibly succeed.

    The distinction matters because the caller is a user waiting on a response.
    Retrying a revoked token or a missing scope cannot work - it only adds delay to a
    failure that is already certain - so those give up immediately.
    """
    if isinstance(exc, (GmailConfigurationError, RefreshError)):
        # A malformed blob, or a refresh token Google has rejected. Neither is fixed
        # by asking again.
        return False
    if isinstance(exc, HttpError):
        status = exc.status_code
        # 429 and 5xx are Google saying "not now". Every other 4xx is "not ever".
        return status is not None and (status == 429 or status >= 500)
    # A DNS failure, a dropped connection, a timeout.
    return isinstance(exc, (TransportError, OSError, TimeoutError))


def _describe(exc: BaseException) -> str:
    """A one-line reason, carrying Google's own wording where there is one."""
    if isinstance(exc, RefreshError):
        detail = f"RefreshError: {exc}"
        if "invalid_grant" in str(exc):
            # The one Gmail failure whose message says nothing useful. `invalid_grant`
            # on a refresh means Google has rejected the refresh token itself, so no
            # amount of retrying or reconfiguring helps - it has to be replaced. The
            # causes are listed because the first is overwhelmingly the common one and
            # is invisible from the error: an OAuth consent screen left in "Testing"
            # expires every refresh token it issued after seven days.
            return (
                f"{detail} - Google has rejected the refresh token itself; it is dead "
                "rather than misconfigured. Causes, in order of likelihood: the OAuth "
                "consent screen is still in Testing (Google expires those refresh "
                "tokens after 7 days - publish the app to stop it), the token was "
                "revoked from the Google account's third-party access, the account "
                "password changed, the OAuth client was deleted or recreated."
            )
        return detail
    if isinstance(exc, HttpError):
        return f"HTTP {exc.status_code}: {exc}"
    return f"{type(exc).__name__}: {exc}"


# =============================================================================
# Rendering
# =============================================================================
#: ``autoescape`` is non-negotiable: user-supplied names go into these bodies, and
#: an unescaped one is HTML injection into whatever the recipient's client renders.
_jinja = Environment(autoescape=select_autoescape(["html", "xml"]), enable_async=False)

_BASE_STYLES: Final = """
  body{margin:0;padding:0;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,
    'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;color:#18181b;}
  .wrap{max-width:520px;margin:0 auto;padding:40px 24px;}
  .card{background:#fff;border:1px solid #e4e4e7;border-radius:14px;padding:32px;}
  .brand{font-size:15px;font-weight:600;letter-spacing:-.01em;margin:0 0 28px;color:#18181b;}
  .brand span{color:#6366f1;}
  h1{font-size:20px;font-weight:600;letter-spacing:-.02em;margin:0 0 12px;}
  p{font-size:14px;line-height:1.6;color:#52525b;margin:0 0 16px;}
  .btn{display:inline-block;background:#18181b;color:#fff!important;text-decoration:none;
    font-size:14px;font-weight:500;padding:11px 20px;border-radius:8px;margin:8px 0 20px;}
  .code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:30px;font-weight:600;
    letter-spacing:.18em;background:#f4f4f5;border-radius:10px;padding:18px;text-align:center;
    margin:20px 0;color:#18181b;}
  .fallback{font-size:12px;color:#71717a;word-break:break-all;}
  .foot{font-size:12px;color:#a1a1aa;margin:24px 0 0;padding-top:20px;border-top:1px solid #f4f4f5;}
"""

_LAYOUT: Final = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ subject }}</title><style>{{ styles }}</style></head>
<body><div class="wrap"><div class="card">
  <p class="brand">Personal <span>ERP</span></p>
  {{ body }}
  {# `true` as the second argument, and it is load-bearing: Jinja's `default` filter
     substitutes only for an *undefined* variable, and `_render` always passes `footer`
     - as `None` when the caller omitted it. Without the flag, every email that does not
     name its own footer rendered the literal word "None" where the footer belongs.
     The flag widens it to any falsy value, which also covers an empty string. #}
  <p class="foot">{{ footer|default("You are receiving this because someone used this
    address to sign in to Stellar ERP. If that was not you, you can ignore this email.",
    true) }}</p>
</div></div></body></html>
"""


def _render(body_template: str, *, subject: str, footer: str | None = None, **context: Any) -> str:
    """Render a body template, then wrap it in the shared layout.

    **`Markup` is what makes the two stages work.** `render()` returns a plain
    `str`, and dropping a plain `str` into the layout's `{{ body }}` escapes it a
    second time - which turned every email into a wall of visible source, `&lt;h1&gt;`
    and all, with the verification button rendered as text rather than a link. The
    styles went the same way: `'Segoe UI'` arrived as `&#39;Segoe UI&#39;` and the
    font declaration died with it.

    This is safe rather than a hole punched in the autoescaping, and the ordering is
    the reason: the inner render escapes every value in `context` as it interpolates
    it, so by the time the result is marked safe, the only markup left in it is the
    template's own. `footer` is deliberately *not* marked - it is prose, and leaving
    it escaped means a caller cannot smuggle markup in through it.
    """
    body = _jinja.from_string(body_template).render(**context)
    return _jinja.from_string(_LAYOUT).render(
        subject=subject,
        # S704 is the right rule in general - `Markup` on a computed string is how
        # XSS arrives - and it cannot see either argument's provenance. `_BASE_STYLES`
        # is a module constant, and `body` was escaped by the render above; see the
        # docstring. Silenced per line, so the next `Markup` still has to argue for
        # itself.
        styles=Markup(_BASE_STYLES),  # noqa: S704
        body=Markup(body),  # noqa: S704
        footer=footer,
    )


# =============================================================================
# Send
# =============================================================================
async def send_email(
    *,
    to: str,
    subject: str,
    html: str,
    text: str,
    category: str = "transactional",
) -> bool:
    """Send one message. Returns success; never raises.

    A plaintext alternative always accompanies the HTML - some clients refuse to
    render HTML, and multipart messages score better with spam filters.
    """
    if not settings.emails_enabled:
        # Development: the link in `text` is the whole point of this branch.
        log.warning(
            "email suppressed (GMAIL_CREDENTIALS_B64 not set) - body follows",
            extra={"to": to, "subject": subject, "category": category, "body": text},
        )
        return True

    message = EmailMessage()
    # Omitted rather than guessed when unset: Gmail fills in the authorised
    # mailbox itself, and inventing an address here would either be rewritten or
    # rejected. Setting GMAIL_SENDER is what buys the display name.
    if settings.gmail_sender:
        message["From"] = f"{settings.email_from_name} <{settings.gmail_sender}>"
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    context = {"to": to, "subject": subject, "category": category}

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            # `googleapiclient` is synchronous - httplib2 under the hood - so it has
            # to leave the event loop or it stalls every other request for the
            # duration of an SMTP-speed round trip. `anyio` rather than
            # `asyncio.to_thread` because that is the pool FastAPI already sizes and
            # instruments.
            await anyio.to_thread.run_sync(_send_sync, message)
            log.info("email sent", extra=context)
            return True
        except Exception as exc:
            retryable = _is_transient(exc) and attempt < _MAX_ATTEMPTS
            # Swallowed either way - see the module docstring. A retryable failure is
            # a warning because it may yet succeed; the last one is the error that
            # should page someone.
            log.log(
                logging.WARNING if retryable else logging.ERROR,
                "email delivery failed",
                extra={
                    **context,
                    "attempt": attempt,
                    "attempts": _MAX_ATTEMPTS,
                    "error": _describe(exc),
                    "will_retry": retryable,
                },
                exc_info=not retryable,
            )
            if not retryable:
                return False
            await asyncio.sleep(_RETRY_WAITS[attempt - 1])

    return False


def _frontend_url(path: str, **params: str) -> str:
    base = settings.frontend_url.rstrip("/")
    query = f"?{urlencode(params)}" if params else ""
    return f"{base}{path}{query}"


# =============================================================================
# Messages
# =============================================================================
async def send_verification_email(*, to: str, name: str, token: str) -> bool:
    link = _frontend_url("/verify-email", token=token)
    hours = settings.email_verification_ttl_hours

    html = _render(
        """
        <h1>Confirm your email</h1>
        <p>Hi {{ name }}, welcome to Stellar ERP. Confirm this address to activate
        your account.</p>
        <a class="btn" href="{{ link }}">Verify email address</a>
        <p class="fallback">Or paste this into your browser:<br>{{ link }}</p>
        <p>This link expires in {{ hours }} hours.</p>
        """,
        subject="Confirm your email",
        name=name,
        link=link,
        hours=hours,
    )
    text = (
        f"Hi {name},\n\nConfirm your email address to activate your Stellar ERP account:\n"
        f"{link}\n\nThis link expires in {hours} hours.\n"
    )
    return await send_email(
        to=to, subject="Confirm your email", html=html, text=text, category="verification"
    )


async def send_password_reset_email(*, to: str, name: str, code: str) -> bool:
    """The reset code. Deliberately not a link - see the module docstring.

    The subject leads with the code so it is readable from a notification, which is
    what stops people opening the mail client at all.
    """
    minutes = settings.password_reset_ttl_minutes
    subject = f"{code} is your password reset code"

    html = _render(
        """
        <h1>Reset your password</h1>
        <p>Hi {{ name }}, enter this code on the reset page to choose a new
           password.</p>
        <div class="code">{{ code }}</div>
        <p>This code expires in {{ minutes }} minutes and can be used once. If you
           did not request it, no action is needed - your password has not changed.</p>
        """,
        subject=subject,
        name=name,
        code=code,
        minutes=minutes,
    )
    text = (
        f"Hi {name},\n\nYour Stellar ERP password reset code is: {code}\n\n"
        f"Enter it on the reset page. It expires in {minutes} minutes and can be used "
        "once.\n\nIf you did not request it, ignore this email - your password has not "
        "changed.\n"
    )
    return await send_email(to=to, subject=subject, html=html, text=text, category="password_reset")


async def send_magic_link_email(
    *,
    to: str,
    name: str,
    token: str,
    user_code: str | None = None,
    device_label: str | None = None,
) -> bool:
    """The one-click sign-in link.

    Points at ``/magic-link/verify``, not ``/magic-link``. The latter is the *request*
    form, which discards search parameters - an emailed link aimed there silently did
    nothing but re-show the form.

    ``user_code`` is set when the sign-in was started from an app that cannot receive
    this link (see :class:`~app.modules.auth.token_store.DeviceSignInStore`). Opening
    the link then signs that app in as well, so the mail has to say so and give the
    reader something to check it against: an unexpected link that would sign in
    *someone else's* app is the one failure mode this flow adds, and the code is what
    lets the reader notice it.
    """
    link = _frontend_url("/magic-link/verify", token=token)
    minutes = settings.magic_link_ttl_minutes

    html = _render(
        """
        <h1>Your sign-in link</h1>
        <p>Hi {{ name }}, tap below to sign in. No password needed.</p>
        {% if user_code %}
        <p>This will also sign in the app{% if device_label %} on
           <strong>{{ device_label }}</strong>{% endif %} that asked for it. That app
           is showing this code - check it matches before you continue:</p>
        <div class="code">{{ user_code }}</div>
        <p><strong>If you are not looking at a Stellar ERP app showing that code, do
           not open the link.</strong> Someone else may have entered your address, and
           opening it would sign their app in as you.</p>
        {% endif %}
        <a class="btn" href="{{ link }}">Sign in to Stellar ERP</a>
        <p class="fallback">Or paste this into your browser:<br>{{ link }}</p>
        <p>This link expires in {{ minutes }} minutes and can be used once.</p>
        """,
        subject="Your sign-in link",
        name=name,
        link=link,
        minutes=minutes,
        user_code=user_code,
        device_label=device_label,
    )
    text = (
        f"Hi {name},\n\nSign in to Stellar ERP:\n{link}\n\n"
        + (
            f"This will also sign in the app"
            f"{f' on {device_label}' if device_label else ''} that asked for it. That "
            f"app is showing the code {user_code} - check it matches before you "
            "continue.\n\nIf you are not looking at a Stellar ERP app showing that "
            "code, do not open the link: someone else may have entered your address, "
            "and opening it would sign their app in as you.\n\n"
            if user_code
            else ""
        )
        + f"This link expires in {minutes} minutes and can be used once.\n"
    )
    return await send_email(
        to=to, subject="Your sign-in link", html=html, text=text, category="magic_link"
    )


async def send_otp_email(*, to: str, name: str, code: str) -> bool:
    minutes = settings.otp_ttl_minutes

    html = _render(
        """
        <h1>Your sign-in code</h1>
        <p>Hi {{ name }}, enter this code to finish signing in.</p>
        <div class="code">{{ code }}</div>
        <p>This code expires in {{ minutes }} minutes. Never share it with anyone.</p>
        """,
        subject=f"{code} is your sign-in code",
        name=name,
        code=code,
        minutes=minutes,
    )
    text = (
        f"Hi {name},\n\nYour Stellar ERP sign-in code is: {code}\n\n"
        f"It expires in {minutes} minutes. Never share it with anyone.\n"
    )
    return await send_email(
        to=to, subject=f"{code} is your sign-in code", html=html, text=text, category="otp"
    )


async def send_invitation_email(
    *,
    to: str,
    organization_name: str,
    inviter_name: str,
    role_name: str,
    token: str,
    message: str | None = None,
) -> bool:
    link = _frontend_url("/accept-invite", token=token)
    days = settings.invite_ttl_days

    html = _render(
        """
        <h1>Join {{ organization_name }}</h1>
        <p>{{ inviter_name }} has invited you to join
           <strong>{{ organization_name }}</strong> on Stellar ERP as
           <strong>{{ role_name }}</strong>.</p>
        {% if message %}<p style="padding:12px 14px;background:#f4f4f5;border-radius:8px;
           font-style:italic;">"{{ message }}"</p>{% endif %}
        <a class="btn" href="{{ link }}">Accept invitation</a>
        <p class="fallback">Or paste this into your browser:<br>{{ link }}</p>
        <p>This invitation expires in {{ days }} days.</p>
        """,
        subject=f"{inviter_name} invited you to {organization_name}",
        footer=(
            "You are receiving this because someone invited this address to an "
            "organization on Stellar ERP. If this was unexpected, you can ignore it."
        ),
        organization_name=organization_name,
        inviter_name=inviter_name,
        role_name=role_name,
        link=link,
        days=days,
        message=message,
    )
    text = (
        f"{inviter_name} invited you to join {organization_name} on Stellar ERP "
        f"as {role_name}.\n\n"
        + (f'Their message: "{message}"\n\n' if message else "")
        + f"Accept the invitation:\n{link}\n\nThis invitation expires in {days} days.\n"
    )
    return await send_email(
        to=to,
        subject=f"{inviter_name} invited you to {organization_name}",
        html=html,
        text=text,
        category="invitation",
    )


async def send_password_changed_email(*, to: str, name: str) -> bool:
    """Security notification. Not optional.

    If an attacker changes the password, this is the account owner's only signal
    that it happened while they can still act on it.
    """
    html = _render(
        """
        <h1>Your password was changed</h1>
        <p>Hi {{ name }}, the password on your Stellar ERP account was just changed,
           and every other session was signed out.</p>
        <p>If this was not you, reset your password immediately and review your
           active devices.</p>
        <a class="btn" href="{{ link }}">Reset password</a>
        """,
        subject="Your password was changed",
        name=name,
        link=_frontend_url("/forgot-password"),
    )
    text = (
        f"Hi {name},\n\nThe password on your Stellar ERP account was just changed and all "
        f"other sessions were signed out.\n\nIf this was not you, reset your password "
        f"immediately: {_frontend_url('/forgot-password')}\n"
    )
    return await send_email(
        to=to, subject="Your password was changed", html=html, text=text, category="security"
    )
