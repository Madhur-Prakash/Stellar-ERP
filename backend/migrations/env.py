"""Alembic environment.

Two deliberate choices:

* **The DSN comes from :mod:`app.core.config`**, not ``alembic.ini``. One place
  builds database URLs, and no credentials live in a committed file.
* **Migrations run over asyncpg**, the same driver the application uses. The
  obvious alternative - strip ``+asyncpg`` and let SQLAlchemy fall back to
  psycopg2 - means installing and maintaining a second PostgreSQL driver whose
  only job is migrations, and being exposed to behavioural differences between
  the two exactly where correctness matters most. Alembic's autogenerate is
  synchronous, so it is driven through ``connection.run_sync``.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings

# Importing the registry is what populates Base.metadata. Without it,
# autogenerate produces an empty migration and silently drops every table.
from app.db.registry import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.sqlalchemy_dsn)

target_metadata = Base.metadata


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Filter which database objects autogenerate considers.

    Excludes Alembic's own bookkeeping table, which is not part of the model and
    would otherwise be proposed for deletion on every run.
    """
    return not (type_ == "table" and name == "alembic_version")


#: Shared by both modes so offline SQL and online DDL cannot drift apart.
_COMMON_OPTIONS = {
    "target_metadata": target_metadata,
    "include_object": include_object,
    # Detect column type and server-default changes, not just added and dropped
    # columns. Both are off by default, and their absence is the usual reason
    # "autogenerate found nothing" when a column's type really did change.
    "compare_type": True,
    "compare_server_default": True,
}


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it.

    ``alembic upgrade head --sql`` produces a script a DBA can review and run by
    hand - how production changes are applied where the application role has no
    DDL rights. No connection is opened, so the async driver is irrelevant here.
    """
    context.configure(
        url=settings.sqlalchemy_dsn,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_COMMON_OPTIONS,
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    """Synchronous body, executed inside ``run_sync`` on the async connection."""
    context.configure(
        connection=connection,
        # One transaction per migration, so a failure part-way through a batch
        # leaves the schema at a known revision rather than in between.
        transaction_per_migration=True,
        **_COMMON_OPTIONS,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Apply migrations against a live database."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        # A migration run is short-lived and single-connection, so a pool would
        # only leave connections open after the work is done.
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
