"""Async engine, session factory, and the FastAPI session dependency.

Transaction policy - one request, one transaction:

:func:`get_db` yields a session and commits when the handler returns cleanly, or
rolls back if it raises. Handlers and services therefore never call ``commit()``
themselves. The payoff is that a request which writes a journal entry and its
audit record either persists both or neither; partial writes are impossible by
construction, which matters more in accounting than anywhere else.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Environment, settings
from app.core.logging import get_logger

log = get_logger(__name__)


def _engine_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "echo": settings.db_echo,
        "future": True,
        # Verify a connection is alive before handing it out. Without this,
        # connections killed by a PgBouncer/network idle timeout surface as a
        # random failed request instead of a transparent reconnect.
        "pool_pre_ping": True,
    }

    if settings.environment is Environment.TEST:
        # Pooling across the ad-hoc event loops pytest creates leads to
        # "attached to a different loop" errors. Fresh connections instead.
        kwargs["poolclass"] = NullPool
    else:
        kwargs |= {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_recycle": settings.db_pool_recycle,
            "pool_timeout": 30,
        }

    return kwargs


engine: AsyncEngine = create_async_engine(settings.sqlalchemy_dsn, **_engine_kwargs())

SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    # Attributes stay readable after commit, so a handler can still serialise
    # the object it just saved without triggering a lazy refresh on a closed
    # transaction (the classic MissingGreenlet crash in async SQLAlchemy).
    expire_on_commit=False,
    autoflush=False,
    autobegin=True,
)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI dependency yielding a transactional session.

    Commits on success, rolls back on any exception, always closes.
    """
    session = SessionFactory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def check_database_health() -> bool:
    """Cheap ``SELECT 1`` used by the readiness probe."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.error("database health check failed", extra={"error": str(exc)})
        return False


async def dispose_engine() -> None:
    """Close the pool on shutdown so Postgres does not leak backends."""
    await engine.dispose()
    log.info("database engine disposed")
