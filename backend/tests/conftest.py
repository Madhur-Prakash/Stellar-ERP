"""Shared pytest fixtures.

Isolation strategy - **one transaction per test, always rolled back**:

Each test gets a session bound to a connection with an open transaction.
``join_transaction_mode="create_savepoint"`` means the application's own
``commit()`` calls (in :func:`app.db.session.get_db`) become savepoint releases
rather than real commits, so production code runs its normal transaction
boundaries while the outer rollback still erases everything afterwards.

The alternative - truncating tables between tests - is slower, races when tests
run in parallel, and quietly leaves sequences and cached plans behind. Rollback
gives byte-identical starting state every time.

Redis gets its own database index, flushed between tests, for the same reason.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Environment must be set BEFORE any `app.*` import: app.core.config builds its
# settings singleton at module import time, and it is lru_cached.
# ---------------------------------------------------------------------------
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DEBUG": "true",
        "POSTGRES_DB": "stellarerp_test",
        # ---------------------------------------------------------------------
        # Neutralise the full-URL overrides. This is the most important pair of
        # lines in the file.
        #
        # `DATABASE_URL` and `REDIS_URL` take priority over the composed
        # `POSTGRES_*`/`REDIS_*` parts, so setting `POSTGRES_DB` above and
        # `REDIS_DB` below achieves *nothing* while either URL is present - and a
        # developer whose `.env` holds a managed-database URL for a real
        # deployment has both. The suite runs `Base.metadata.drop_all` and
        # `redis.flushdb()`, so under that `.env` `make test` drops every table in
        # the deployment's database and flushes its Redis, with no warning and no
        # way to tell from the output that it happened.
        #
        # A `.env` entry cannot be removed from here, only overridden, so the
        # override is the empty string - which `app.core.config` maps back to
        # "not set" (see `_blank_to_none`). `Settings._enforce_test_safety` then
        # refuses to start at all if the resolved database name does not end in
        # `_test`, so this cannot be quietly undone.
        # ---------------------------------------------------------------------
        "DATABASE_URL": "",
        "REDIS_URL": "",
        # Dedicated Redis index so a test run cannot touch development data.
        "REDIS_DB": "15",
        "SECRET_KEY": "test-secret-key-not-used-in-production-0123456789abcdef",
        # Keep Argon2 at its floor: the default parameters cost ~50ms per hash,
        # which dominates the runtime of an auth-heavy suite.
        "ARGON2_TIME_COST": "1",
        "ARGON2_MEMORY_COST": "8192",
        "ARGON2_PARALLELISM": "1",
        # No Gmail credentials: the mailer logs the body instead of sending.
        "GMAIL_CREDENTIALS_B64": "",
        # Off, or the suite's rapid-fire auth calls trip the limiter.
        "RATE_LIMIT_ENABLED": "false",
    }
)

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.util.concurrency import in_greenlet

from app.core.config import settings
from app.core.redis import close_redis, get_redis
from app.db.registry import Base
from app.db.session import get_db
from app.main import create_app
from app.modules.organizations.models import (
    MemberStatus,
    Organization,
    OrganizationMember,
)
from app.modules.rbac.permissions import SystemRole
from app.modules.rbac.repository import RoleRepository
from app.modules.users.models import User

#: Password satisfying the policy, reused across tests.
TEST_PASSWORD = "Tr0ubador-Fenwick-92"

#: RFC 2606 documentation domain. Not `.test`, `.local`, or `.invalid`: the
#: `email-validator` behind `EmailStr` rejects special-use TLDs, which is correct
#: behaviour for production and means those domains cannot be used in fixtures.
TEST_DOMAIN = "example.com"


# =============================================================================
# Infrastructure gating
# =============================================================================
#: Requesting any of these means the test talks to Postgres or Redis.
#:
#: Much of this codebase is deliberately pure - the password policy, inventory
#: valuation, GST resolution, OCR extraction - and those tests should run with
#: nothing installed but Python. When the database and Redis fixtures were
#: `autouse`, a stopped container failed all 56 pure tests with a connection
#: error, which hides real failures behind an environment problem.
INFRA_FIXTURES: frozenset[str] = frozenset(
    {"engine", "connection", "db", "client", "_create_test_database"}
)


def _uses_infra(request: pytest.FixtureRequest) -> bool:
    """Whether this test's fixture closure reaches Postgres or Redis.

    ``fixturenames`` is the full transitive closure, so a test requesting
    ``client`` is correctly detected through ``db`` → ``connection`` → ``engine``.
    """
    return not INFRA_FIXTURES.isdisjoint(request.fixturenames)


# =============================================================================
# Database lifecycle
# =============================================================================
@pytest.fixture(scope="session")
async def _create_test_database() -> AsyncGenerator[None]:
    """Create the test database and schema once per session.

    ``create_all`` rather than ``alembic upgrade head``: the schema under test
    should be the one the models describe, so a stale migration cannot make the
    suite pass against a schema the code no longer matches. Migration
    correctness is verified separately by ``alembic check`` in CI.

    Not ``autouse``: :func:`engine` depends on it, so it runs for exactly the
    tests that need a database and no others. See :data:`INFRA_FIXTURES`.
    """
    # Belt and braces with `Settings._enforce_test_safety`, which has already refused to
    # construct if this is wrong. Restated at the call site because this is the line that
    # does the destroying, and a future refactor that moves the settings check has to
    # trip over this one to get here.
    assert settings.database_name.endswith("_test"), (
        f"refusing to drop_all against '{settings.database_name}'"
    )

    admin_dsn = settings.sqlalchemy_dsn.rsplit("/", 1)[0] + "/postgres"
    admin_engine = create_async_engine(admin_dsn, isolation_level="AUTOCOMMIT")

    async with admin_engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": "stellarerp_test"},
        )
        if not exists:
            await conn.execute(text('CREATE DATABASE "stellarerp_test"'))

    await admin_engine.dispose()

    engine = create_async_engine(settings.sqlalchemy_dsn)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    yield


@pytest.fixture(scope="session")
async def engine(_create_test_database: None) -> AsyncGenerator:
    """Session-scoped engine for the test database."""
    test_engine = create_async_engine(settings.sqlalchemy_dsn, poolclass=None)
    yield test_engine
    await test_engine.dispose()


@pytest.fixture
async def connection(engine) -> AsyncGenerator[AsyncConnection]:
    """A connection with an outer transaction that is always rolled back."""
    async with engine.connect() as conn:
        transaction = await conn.begin()
        yield conn
        await transaction.rollback()


class LazyLoadDetected(AssertionError):
    """Raised when ORM code triggers an implicit lazy load.

    Async SQLAlchemy **cannot** lazy-load: emitting IO from an attribute access
    outside a greenlet context fails with ``MissingGreenlet``, whose message says
    nothing about which relationship was at fault. That error has already cost real
    debugging time in this codebase - traversing ``payment.customer`` and
    ``payment.allocations`` on freshly-constructed rows.

    This turns that class of bug into an immediate, named failure at the exact
    attribute access, so the fix (an eager ``selectinload``, or passing the object
    you already have) is obvious.
    """


def _fail_on_lazy_load(orm_execute_state: Any) -> None:
    """``do_orm_execute`` hook that rejects *unsafe* lazy loads.

    Two filters, and the second one matters:

    ``lazy_loaded_from`` is set only when a statement is emitted on behalf of a
    lazy loader, so eager strategies (``selectinload``, ``joinedload``) pass
    through untouched.

    ``in_greenlet()`` distinguishes a bug from a legitimate load. Some async ORM
    operations are coroutines *precisely so* they can lazy-load safely -
    ``await session.delete(obj)`` has to load the relationship collections to
    apply cascade rules, and it runs inside greenlet context where that works.
    Flagging those was the first version of this hook and it produced seven false
    positives.

    Outside greenlet context the same load raises ``MissingGreenlet``, whose
    message names no relationship and no attribute. That is the case worth
    intercepting: same failure, but pointing at the cause.
    """
    # `lazy_loaded_from` is only meaningful for a SELECT. Reading it on an
    # INSERT/UPDATE/DELETE raises InvalidRequestError ("no load options"), which is
    # how the first version of this hook broke six unrelated tests.
    if not orm_execute_state.is_select:
        return
    if orm_execute_state.lazy_loaded_from is None:
        return
    if in_greenlet():
        # Inside an awaited ORM operation - the load will complete normally.
        return

    state = orm_execute_state.lazy_loaded_from
    raise LazyLoadDetected(
        f"Implicit lazy load on {state.class_.__name__} outside greenlet context - "
        f"this raises MissingGreenlet in production.\n"
        f"Fix it by eager-loading the relationship (selectinload) in the "
        f"repository, or by using an object you already hold rather than "
        f"traversing the relationship."
    )


@pytest.fixture
async def db(connection: AsyncConnection) -> AsyncGenerator[AsyncSession]:
    """A session enrolled in the test's outer transaction.

    ``create_savepoint`` is what lets production code commit normally while the
    outer rollback still discards everything.

    A ``do_orm_execute`` listener makes any accidental lazy load fail loudly - see
    :class:`LazyLoadDetected`.
    """
    session = AsyncSession(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
        autoflush=False,
    )
    event.listen(session.sync_session, "do_orm_execute", _fail_on_lazy_load)
    try:
        yield session
    finally:
        event.remove(session.sync_session, "do_orm_execute", _fail_on_lazy_load)
        await session.close()


@pytest.fixture(autouse=True)
async def _clean_redis(request: pytest.FixtureRequest) -> AsyncGenerator[None]:
    """Flush the test Redis index around every test that touches infrastructure.

    Auth state (lockout counters, one-time tokens, token epochs) lives in Redis,
    so leakage between tests would make them order-dependent - the worst kind of
    flake to debug.

    Skipped for pure tests. That is a gate on the *fixture closure*, not on
    whether a connection happens to succeed: silently swallowing a Redis error
    would let state leak between the tests that do depend on it.
    """
    if not _uses_infra(request):
        yield
        return

    redis = get_redis()
    await redis.flushdb()
    yield
    await redis.flushdb()


@pytest.fixture(scope="session", autouse=True)
async def _close_redis_at_end() -> AsyncGenerator[None]:
    yield
    await close_redis()


# =============================================================================
# HTTP client
# =============================================================================
@pytest.fixture
async def client(db: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """An HTTP client wired to the app, sharing the test's transaction.

    All requests in a test share one session so that fixture rows and request
    writes are mutually visible, and the outer rollback erases everything.

    **Known limitation, stated because it hid a real bug.** This does not model
    production's rollback-on-exception: a handler that mutates state and then
    raises leaves those mutations visible here, where production would discard
    them. Modelling it faithfully requires a session per request, and two
    sessions on one connection break savepoint visibility for the fixtures.

    Consequence: any behaviour that depends on surviving a failing request -
    today, only refresh-reuse revocation, which commits explicitly for exactly
    this reason - must be verified against a real deployment, not only here.
    """
    app = create_app()

    async def _override_get_db() -> AsyncGenerator[AsyncSession]:
        yield db
        await db.commit()  # a savepoint release, undone by the outer rollback

    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as http_client:
        yield http_client

    app.dependency_overrides.clear()


@pytest.fixture
def api() -> str:
    """The versioned API prefix, so tests do not hard-code it."""
    return settings.api_v1_prefix


# =============================================================================
# Domain fixtures
# =============================================================================
@pytest.fixture
async def user(db: AsyncSession) -> User:
    """A verified, active user with a password and no organization."""
    from app.core.security import hash_password

    record = User(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        full_name="Jhon Doe",
        password_hash=hash_password(TEST_PASSWORD),
        email_verified_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    db.add(record)
    await db.flush()
    return record


@pytest.fixture
async def unverified_user(db: AsyncSession) -> User:
    from app.core.security import hash_password

    record = User(
        email=f"unverified-{uuid.uuid4().hex[:8]}@example.com",
        full_name="Rahul Verma",
        password_hash=hash_password(TEST_PASSWORD),
    )
    db.add(record)
    await db.flush()
    return record


@pytest.fixture
async def organization(db: AsyncSession, user: User) -> Organization:
    """An organization owned by ``user``, with system roles seeded."""
    import datetime as dt

    org = Organization(name="Acme Trading Co", slug=f"acme-{uuid.uuid4().hex[:6]}")
    db.add(org)
    await db.flush()

    seeded = await RoleRepository(db).seed_system_roles(org.id)
    db.add(
        OrganizationMember(
            organization_id=org.id,
            user_id=user.id,
            role_id=seeded[SystemRole.OWNER].id,
            is_owner=True,
            status=MemberStatus.ACTIVE,
            joined_at=dt.datetime.now(dt.UTC),
        )
    )
    user.last_organization_id = org.id
    await db.flush()
    return org


@pytest.fixture
async def authed_client(
    client: AsyncClient, api: str, user: User, organization: Organization
) -> AsyncClient:
    """A client authenticated as the organization owner.

    Signs in through the real ``/auth/login`` endpoint rather than forging a
    token, so every test exercises the genuine token-issuing path.
    """
    response = await client.post(
        f"{api}/auth/login",
        json={"email": user.email, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client
