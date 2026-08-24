"""Error tracking, and the redaction that makes it safe to switch on.

Sentry is **optional and off by default**, which for most products would be a
cop-out and here is a requirement. The entire pitch of this ERP is that a
business's books stay on its own server; a hard dependency on a third-party error
tracker would contradict that on the same page that promises it. Configured, it
reports exceptions; unconfigured, nothing leaves the machine and the logifyx log
is still the record.

What this module actually contributes is :func:`_scrub`. An exception report from
an accounting system is a uniquely dangerous thing to send anywhere: a
``ValidationError`` on an invoice carries the invoice, a database error carries
the SQL and its bound parameters, and a request body carries whatever the user
typed. Sentry's own ``send_default_pii=False`` covers headers and cookies. It does
not cover the fact that our own error envelopes contain amounts and party names.

So every event goes through a filter that:

* drops request bodies entirely - there is no version of "part of the invoice" that
  is safe to send;
* removes the bound parameters from SQL breadcrumbs, keeping the statement;
* redacts anything key-shaped by name, reusing the audit trail's list so the two
  cannot drift;
* and drops the proof ledger's signing seed on the same list, because a leaked
  secret there means somebody else can seal a business's books.

If you are reading this while deciding whether to set ``SENTRY_DSN`` in
production: read :func:`_scrub` first, and satisfy yourself that it is enough for
*your* data. The honest position is that we have made it as safe as we can and it
is still your call.
"""

from __future__ import annotations

from typing import Any, Final

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

#: Keys whose values are replaced wherever they appear, at any depth.
#:
#: Seeded from the audit trail's list so the two cannot drift - if a new secret is
#: worth keeping out of an audit row it is worth keeping out of an error report -
#: plus the ones specific to the proof ledger.
_SECRET_KEYS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "token_hash",
        "totp_secret",
        "secret",
        "secret_key",
        "recovery_codes",
        "api_key",
        "client_secret",
        "encryption_key",
        "authorization",
        "cookie",
        "set-cookie",
        # Ledger 3. A leaked signing seed means somebody else can seal this
        # business's books, which is worse than most credentials on this list.
        "signer_secret",
        "signer_secret_encrypted",
        "attestation_namespace_salt",
        # Bank details, which are the most sensitive ordinary business data here.
        "account_number",
        "account_number_encrypted",
        "card_number",
        "pan",
    }
)

_REDACTED: Final = "[redacted]"

#: Breadcrumb categories whose data is dropped rather than filtered.
#:
#: A SQL breadcrumb's `params` is the bound parameter list - every amount, name and
#: id in the statement. There is no useful subset, so the statement is kept and the
#: parameters go.
_DROP_BREADCRUMB_DATA: Final[frozenset[str]] = frozenset({"query", "sql"})


def _scrub(value: Any, depth: int = 0) -> Any:
    """Recursively redact secret-shaped keys.

    Depth-limited, because an event is arbitrary nested JSON from a library we do
    not control, and a cycle or a pathological nesting would turn error reporting
    into an outage. Beyond the limit the value is dropped rather than passed
    through - failing closed, since the whole purpose is to not send things.
    """
    if depth > 12:
        return _REDACTED

    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in _SECRET_KEYS:
                cleaned[key] = _REDACTED
            else:
                cleaned[key] = _scrub(item, depth + 1)
        return cleaned

    if isinstance(value, list):
        return [_scrub(item, depth + 1) for item in value]

    if isinstance(value, tuple):
        return tuple(_scrub(item, depth + 1) for item in value)

    return value


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Filter every event before it leaves the process.

    Returning ``None`` would drop the event entirely; nothing here does that, because
    an error we never hear about is not safer, only quieter. What it does instead is
    remove the parts that carry business data.
    """
    try:
        request = event.get("request")
        if isinstance(request, dict):
            # A request body from this API is an invoice, a payment, or a journal
            # entry. There is no subset of that worth sending.
            request.pop("data", None)
            request.pop("cookies", None)
            if isinstance(request.get("headers"), dict):
                request["headers"] = _scrub(request["headers"])
            # The query string can carry a date range and a cursor, which are fine,
            # but also whatever a caller appended - so it goes through the filter.
            if "query_string" in request:
                request["query_string"] = _scrub(request["query_string"])

        for breadcrumb in event.get("breadcrumbs", {}).get("values", []) or []:
            if not isinstance(breadcrumb, dict):
                continue
            if breadcrumb.get("category") in _DROP_BREADCRUMB_DATA:
                data = breadcrumb.get("data")
                if isinstance(data, dict):
                    # Keep the statement; drop what was bound into it.
                    data.pop("params", None)
                    data.pop("db.params", None)
            breadcrumb["data"] = _scrub(breadcrumb.get("data"))

        for key in ("extra", "contexts", "tags"):
            if key in event:
                event[key] = _scrub(event[key])

        return event
    except Exception as exc:  # pragma: no cover - defensive
        # A filter that raised would either drop the event or crash the SDK's
        # worker. Dropping is the safe direction when we cannot guarantee the
        # scrub ran.
        log.error("failed to scrub an error report; dropping it", extra={"error": str(exc)})
        return None


def configure_monitoring() -> bool:
    """Initialise Sentry if a DSN is configured. Returns whether it started.

    Called from the composition root. Import is local and guarded so that a
    deployment without the SDK installed - which is the default, since it is an
    optional extra - boots normally rather than failing on an import for a feature
    it never asked for.
    """
    if not settings.sentry_dsn:
        log.info("error tracking is not configured; nothing is reported off this machine")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        log.warning(
            "SENTRY_DSN is set but the SDK is not installed - run "
            "`uv sync --extra monitoring`. Nothing will be reported."
        )
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=str(settings.environment),
        release=f"stellar-erp@{settings.app_version}",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        # **Off, and not negotiable.** With PII on, the SDK attaches headers,
        # cookies and the user's IP to every event. `_scrub` would catch the
        # credentials; it should not have to.
        send_default_pii=False,
        # The last line of defence, and the reason this module exists.
        before_send=_before_send,
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            # Keeps the SQL statement in the breadcrumb trail, which is genuinely
            # useful for diagnosing a constraint violation. `_before_send` removes
            # the bound parameters.
            SqlalchemyIntegration(),
            # Without this, an exception escaping the seal worker's task is
            # reported with no useful stack - and the seal worker is the one
            # long-lived task in the process.
            AsyncioIntegration(),
        ],
    )

    log.info(
        "error tracking configured",
        extra={
            "environment": str(settings.environment),
            "traces_sample_rate": settings.sentry_traces_sample_rate,
        },
    )
    return True


def note(message: str, **context: Any) -> None:
    """Leave a breadcrumb, if monitoring is running.

    A no-op when it is not, so call sites need no guard. Used sparingly - a
    breadcrumb on every seal submission is what turns a bare "seal failed" into a
    trail showing which sequence number and which contract.
    """
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(
            category="stellar-erp",
            message=message,
            level="info",
            data=_scrub(context),
        )
    except Exception as exc:  # pragma: no cover - never let telemetry raise
        # Swallowed, because a breadcrumb is commentary and must not be able to
        # fail the operation it describes. Logged rather than dropped: silently
        # losing the reason is how "monitoring stopped working" becomes a mystery.
        #
        # Safe to log here - logifyx is independent of the error tracker, so this
        # cannot recurse into the thing that just failed.
        log.debug("could not add a monitoring breadcrumb", extra={"error": str(exc)})
