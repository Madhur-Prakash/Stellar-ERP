"""Bring the database schema up to date at startup.

**Why `alembic upgrade head` and not `Base.metadata.create_all`.** The ask is "create the
tables if they are not there", and `create_all` does exactly that and nothing else - which
is the problem. It creates *missing tables* and never touches an existing one, so the first
time a model gains a column, a deployed instance starts cleanly against a table that lacks
it and then fails on a query, with `column ... does not exist` surfacing from whichever
endpoint happened to touch it first. It also never stamps ``alembic_version``, so the next
real ``alembic upgrade head`` tries to create tables that already exist and dies.

Alembic covers both cases in one call: an empty database gets every migration, an existing
one gets only what it is missing, and the revision is recorded either way. Against a fresh
Postgres the effect is exactly "it created the tables", which is what was wanted.

**Serialised with an advisory lock.** Two instances booting together would otherwise both
run the same DDL; Postgres lets one win and fails the other, and a crash-looping second
container is a confusing way to find that out. ``pg_advisory_lock`` makes the loser wait and
then find there is nothing left to do. It is held on a connection of this module's own -
Alembic opens its own, which is fine, because the lock only has to be *held* by someone for
the duration. A session-level lock is released automatically if the process dies mid-run, so
a crashed deploy cannot wedge the next one.

Off by default. Running DDL at boot suits a single-instance deployment and is wrong for
anything that deploys in stages, where migrations belong in a release step that finishes
before new code serves traffic. ``RUN_MIGRATIONS_ON_STARTUP=true`` opts in.
"""

from __future__ import annotations

from typing import Final

import anyio.to_thread
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import BACKEND_DIR, settings
from app.core.logging import get_logger

log = get_logger(__name__)

#: Arbitrary but fixed: any two processes running this code must choose the same number for
#: the lock to mean anything. Deliberately not derived from the database name - two apps
#: sharing one database *should* contend here.
_ADVISORY_LOCK_KEY: Final = 0x50455250  # "PERP"


def _upgrade_to_head() -> None:
    """Blocking. Runs in a worker thread - see :func:`run_migrations`.

    ``migrations/env.py`` builds its own async engine and drives it with
    ``asyncio.run``, so there is no connection to pass in and no sync driver needed
    (this project installs asyncpg only). That `asyncio.run` is also why this cannot be
    awaited from the running loop: it would fail with "cannot be called from a running
    event loop".
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    # `script_location` in alembic.ini is relative, and the working directory of a deployed
    # process is not something this code should assume - Render, Docker and `uv run` from
    # the repo root all differ. Made absolute here.
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(config, "head")


async def run_migrations() -> None:
    """Upgrade to head if configured to. Raises on failure, deliberately.

    A schema that could not be brought up to date is not something to serve traffic
    through: every request touching the missing table would fail, and the cause would be
    one line in a log that scrolled past at boot. Refusing to start puts the error where
    whoever just deployed is already looking.
    """
    if not settings.run_migrations_on_startup:
        return

    log.info("applying database migrations")

    # AUTOCOMMIT because `pg_advisory_lock` must not sit inside a transaction that
    # SQLAlchemy would roll back - a session-level lock taken in an aborted transaction is
    # still held, and releasing it then depends on the connection closing.
    engine = create_async_engine(settings.sqlalchemy_dsn, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY})
            try:
                await anyio.to_thread.run_sync(_upgrade_to_head)
            finally:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY}
                )
    except Exception as exc:
        log.error("database migration failed", extra={"error": str(exc)}, exc_info=True)
        raise
    finally:
        await engine.dispose()

    log.info("database schema is up to date")
