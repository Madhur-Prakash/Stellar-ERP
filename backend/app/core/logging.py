"""Logging - backed entirely by `logifyx <https://pypi.org/project/logifyx/>`_.

Every log line in the backend goes through logifyx. Nothing calls
``logging.getLogger`` or ``print`` directly.

What logifyx gives us out of the box, and why we lean on it here:

* coloured console output in dev, single-line JSON in production (``LOG_JSON``)
* rotating files under ``LOG_DIR`` (multi-process safe)
* automatic redaction of passwords/tokens/secrets (``LOG_MASK``) - this is the
  reason we can log request payload metadata without leaking credentials
* optional HTTP / Kafka fan-out for aggregators, config-only

logifyx owns its own ``LOG_*`` configuration namespace (see ``.env.example``);
application settings live in :mod:`app.core.config` and the two never overlap.

Usage::

    from app.core.logging import get_logger

    log = get_logger(__name__)
    log.info("charge captured", extra={"invoice_id": invoice.id})

Inside a request, prefer ``request.state.log`` (or :func:`get_context_logger`)
so ``request_id`` / ``user_id`` / ``org_id`` ride along on every line.
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

from logifyx import (
    ContextLoggerAdapter,
    Logifyx,
    flush,
    get_logify_logger,
    setup_logify,
    shutdown,
)

from app.core.config import ROOT_DIR, settings

__all__ = [
    "ContextLoggerAdapter",
    "clear_log_context",
    "configure_logging",
    "current_log_context",
    "flush_logs",
    "get_context_logger",
    "get_logger",
    "set_log_context",
    "shutdown_logging",
]

# Root namespace for every logger in the process. Keeps our lines visually
# distinct from third-party ones and gives us one prefix to filter on.
LOGGER_NAMESPACE = "stellarerp"

# Third-party loggers we re-point at logifyx's handlers so that *all* output -
# ours and the framework's - lands in the same file/stream with one format.
_BRIDGED_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "fastapi",
    "sqlalchemy.engine",
    "sqlalchemy.pool",
    "alembic",
)

# ---------------------------------------------------------------------------
# Request-scoped context
#
# ContextVars rather than arguments so that a service five calls deep can be
# correlated to its request without every signature growing a `request_id`.
# ---------------------------------------------------------------------------
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)
_org_id: ContextVar[str | None] = ContextVar("org_id", default=None)

_configured = False


def _resolve_log_file() -> str:
    """Single shared log file for the whole app.

    logifyx defaults to ``<logger name>.log`` per logger, which would scatter
    output across a file per module. We pass an explicit filename so every
    logger converges on one file, while still honouring ``LOG_FILE`` if the
    operator set it. ``LOG_FILE`` belongs to logifyx's config namespace, so it
    is read here rather than in :mod:`app.core.config`.
    """
    return settings.log_file


def configure_logging() -> None:
    """Register logifyx as the global logger class and bridge third parties.

    Must run before any :func:`get_logger` call - ``get_logify_logger`` raises
    ``TypeError`` if ``setup_logify()`` has not registered the logger class
    first. The application lifespan calls this as its very first step.

    Idempotent: safe to call from both the app factory and test fixtures.
    """
    global _configured

    if _configured:
        return

    # Registers Logifyx as the class Python's logging manager instantiates.
    setup_logify()
    _configured = True

    root = get_logify_logger(
        LOGGER_NAMESPACE,
        # Point logifyx at the repo-root .env explicitly: relying on the process
        # CWD would break when running under Alembic, Celery, or pytest.
        env_file=str(ROOT_DIR / ".env"),
        file=_resolve_log_file(),
    )

    _bridge_third_party_loggers(root)

    root.info(
        "logging initialised via logifyx",
        extra={
            "environment": str(settings.environment),
            "log_file": _resolve_log_file(),
        },
    )


def _bridge_third_party_loggers(root: Logifyx) -> None:
    """Re-point stdlib loggers at logifyx's handlers.

    logifyx configures handlers on its own logger instances and sets
    ``propagate = False``. Libraries that grabbed a plain ``logging.Logger`` at
    import time (uvicorn, SQLAlchemy) would otherwise keep their own formatting
    or vanish entirely. Copying the handlers across gives one unified stream.
    """
    for name in _BRIDGED_LOGGERS:
        bridged = logging.getLogger(name)
        bridged.handlers = list(root.handlers)
        bridged.propagate = False

    # SQL statement logging is deafening; it is opt-in via DB_ECHO.
    sql_level = logging.INFO if settings.db_echo else logging.WARNING
    logging.getLogger("sqlalchemy.engine").setLevel(sql_level)

    # uvicorn's access log duplicates our own request-logging middleware, which
    # records more (duration, request id, user). Silence the duplicate.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def _render_value(value: Any) -> str:
    """Render one structured field value, quoting it if it contains spaces."""
    text = "None" if value is None else str(value)
    return f'"{text}"' if (" " in text or "=" in text) else text


class StructuredLogger(logging.LoggerAdapter):  # type: ignore[type-arg]
    """Folds ``extra={...}`` and the request context into the message text.

    This exists because of a concrete limitation in logifyx's formatters: both
    the console formatter and ``CompactJsonFormatter`` build their output from a
    fixed set of record attributes (``timestamp``, ``level``, ``logger``,
    ``function``, ``line``, ``message``). Anything passed via ``extra=`` is
    attached to the ``LogRecord`` and then **silently dropped** - the call
    succeeds, no error is raised, and the structured context simply never
    appears anywhere.

    Rather than rewrite several hundred call sites to interpolate their own
    values, the merge happens here, once. ``log.info("x", extra={"a": 1})``
    renders as ``x | a=1``, which is visible, greppable, and - because logifyx
    masks the final message string - still redacted.

    The trade-off is honest: in JSON mode these fields live inside ``message``
    rather than as separate keys, so a log aggregator cannot index them
    individually. Fixing that properly means a formatter change in logifyx
    itself; this makes the data *present* in the meantime.
    """

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, Any]:
        fields: dict[str, Any] = dict(current_log_context())

        # Consume `extra` so it is not also set as LogRecord attributes, where a
        # key colliding with a reserved name ("message", "name") would raise.
        supplied = kwargs.pop("extra", None)
        if supplied:
            fields.update(supplied)

        if fields:
            rendered = " ".join(f"{key}={_render_value(value)}" for key, value in fields.items())
            msg = f"{msg} | {rendered}"

        return msg, kwargs


def get_logger(name: str | None = None) -> StructuredLogger:
    """Return a namespaced logger (one underlying logifyx singleton per name).

    ``name`` is normally ``__name__``; the ``app.`` prefix is swapped for the
    ``stellarerp.`` namespace so ``app.modules.auth.service`` logs as
    ``stellarerp.modules.auth.service``.

    Wrapped in :class:`StructuredLogger` so ``extra=`` actually reaches the
    output - see that class for why it is necessary.
    """
    if not _configured:
        # Defensive: an import-time logger in a module loaded before lifespan
        # should not blow up with TypeError.
        configure_logging()

    if not name or name == LOGGER_NAMESPACE:
        full_name = LOGGER_NAMESPACE
    else:
        suffix = name.removeprefix("app.")
        full_name = f"{LOGGER_NAMESPACE}.{suffix}"

    underlying = get_logify_logger(
        full_name,
        env_file=str(ROOT_DIR / ".env"),
        file=_resolve_log_file(),
    )
    return StructuredLogger(underlying, {})


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------
def set_log_context(
    *,
    request_id: str | None = None,
    user_id: str | None = None,
    org_id: str | None = None,
) -> None:
    """Attach identifiers to the current async context.

    Called by the request-id middleware and again by the auth dependency once
    the caller is known.
    """
    if request_id is not None:
        _request_id.set(request_id)
    if user_id is not None:
        _user_id.set(user_id)
    if org_id is not None:
        _org_id.set(org_id)


def clear_log_context() -> None:
    """Reset request-scoped identifiers. Used by tests and worker loops."""
    _request_id.set(None)
    _user_id.set(None)
    _org_id.set(None)


def current_log_context() -> dict[str, Any]:
    """Snapshot the non-empty context identifiers."""
    context: dict[str, Any] = {}
    if (request_id := _request_id.get()) is not None:
        context["request_id"] = request_id
    if (user_id := _user_id.get()) is not None:
        context["user_id"] = user_id
    if (org_id := _org_id.get()) is not None:
        context["org_id"] = org_id
    return context


def get_context_logger(name: str | None = None) -> StructuredLogger:
    """Logger bound to the current request context.

    Retained as a distinct name for call-site clarity, but identical to
    :func:`get_logger`: :class:`StructuredLogger` already merges the
    request-scoped identifiers into every line, so there is nothing extra to
    bind.
    """
    return get_logger(name)


def flush_logs(timeout: float = 5.0) -> bool:
    """Drain queued async (remote/Kafka) log records without tearing down.

    Correct call for a running server - unlike :func:`shutdown_logging`, the
    logger stays usable afterwards.

    ``bool(...)`` because logifyx ships ``.pyi`` stubs but no ``py.typed``
    marker, so mypy treats its exports as ``Any``.
    """
    return bool(flush(timeout=timeout))


def shutdown_logging() -> None:
    """Flush and stop logging. logifyx also registers this via ``atexit``."""
    shutdown()
