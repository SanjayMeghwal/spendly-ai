"""Shared pytest fixtures.

conftest.py is discovered automatically by pytest; fixtures defined here are
available to every test in this directory and below without importing them.
"""

import os

# ------------------------------------------------------------------------------
# This MUST run before anything imports `app`, which is why it sits above the
# other imports rather than in a fixture.
#
# `app.core.config.get_settings()` is lru_cached and is called at import time by
# app.db.session, so by the time any fixture runs the settings are frozen. An
# actual environment variable takes precedence over a value in `.env`, so this
# flips ENVIRONMENT from the developer's "local" to "test" - which switches off
# SQLAlchemy's echo and keeps test output readable.
#
# setdefault, not assignment: if the environment already says something (as CI
# does), that wins.
#
# ruff: noqa: E402 is not needed because this is a statement, not an import.
# ------------------------------------------------------------------------------
os.environ.setdefault("ENVIRONMENT", "test")

import uuid  # noqa: E402
from collections.abc import AsyncGenerator, Iterator  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models.user import User  # noqa: E402

settings = get_settings()

# The password used by every user fixture. A constant, so a test asserting a
# successful login and a test asserting a failed one cannot drift apart.
TEST_PASSWORD = "correct horse battery staple"
TEST_EMAIL = "ada@example.com"


# ==============================================================================
# Database
# ==============================================================================


@pytest.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """A database engine created fresh for each test, with pooling disabled.

    WHY NOT REUSE THE APPLICATION'S ENGINE

    pytest-asyncio gives every test its own event loop (see
    asyncio_default_fixture_loop_scope in pyproject.toml). asyncpg binds each
    connection to the loop that opened it, so a pooled connection created in
    test A and handed back out in test B belongs to a loop that no longer
    exists - which surfaces as "attached to a different loop" or a hang, in a
    test that has nothing to do with the one that actually poisoned the pool.

    NullPool opens a real connection on checkout and closes it on release, so
    nothing outlives the test or its loop. It costs a connection handshake per
    test (tens of milliseconds) and buys complete isolation.
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_connection(test_engine: AsyncEngine) -> AsyncGenerator[AsyncConnection, None]:
    """An open connection wrapped in a transaction that is ALWAYS rolled back.

    This is the outer half of the isolation strategy. Everything a test does -
    through the fixture session, through an HTTP request, through the service
    layer - happens inside this one transaction, and it is discarded when the
    test ends.

    That is what lets the suite run against the real PostgreSQL container
    without tests contaminating each other: without it, the first test to
    register ada@example.com makes every later test that registers her fail on
    a unique-constraint violation, and the failure appears in whichever test
    happens to run second.

    The rollback is in a `finally`, so it happens even when the test fails -
    a failing test must not leave rows behind for the next one to trip over.
    """
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


@pytest.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    """A session bound to the test transaction, whose commits do not persist.

    THE IMPORTANT ARGUMENT IS `join_transaction_mode`.

    Our service layer calls `await session.commit()` for real - that is the
    behaviour under test, and a fixture that stubbed it out would be testing
    something we do not ship. But a genuine COMMIT would end the outer
    transaction and make the rollback above a no-op.

    "create_savepoint" makes the session join the transaction that is already
    open on this connection and issue SAVEPOINT / RELEASE SAVEPOINT where it
    would otherwise issue BEGIN / COMMIT. The service's commit behaves
    correctly - the write becomes visible to subsequent queries in this test -
    while the enclosing transaction stays open and still rolls back.

    expire_on_commit=False mirrors the application's session factory; see
    app/db/session.py for why it is mandatory in async code.
    """
    async with AsyncSession(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
        autoflush=False,
    ) as session:
        yield session


# ==============================================================================
# HTTP clients
# ==============================================================================


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client that calls the app in-process, sharing the test transaction.

    ASGITransport routes requests straight into the ASGI application rather
    than over a real socket, so no server needs to be running and there is no
    port to allocate. Routing, dependency injection, validation, and
    serialisation all execute exactly as they would in production - only the
    network hop is skipped.

    Overriding get_db to yield the fixture session is what ties the two halves
    together: a row the test creates directly is visible to the endpoint, a row
    the endpoint creates is visible to the test, and neither survives the
    rollback.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    fastapi_app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def client_using_app_engine() -> AsyncGenerator[AsyncClient, None]:
    """A client with NO database override, using the application's real engine.

    Used by exactly one test: the readiness probe's integration test, whose
    entire purpose is to prove that the engine, pool, driver, and connection
    settings we actually ship cooperate against a live database. Routing that
    test through the fixture engine would leave the real configuration
    untested.
    """
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ==============================================================================
# Users and authentication
# ==============================================================================


@pytest.fixture
async def registered_user(db_session: AsyncSession) -> User:
    """An active user who already exists, with a known password.

    Created through the ORM rather than by calling the register endpoint, so
    that a bug in registration cannot cascade into every login and /me test and
    obscure where the real failure is.
    """
    user = User(
        email=TEST_EMAIL,
        hashed_password=hash_password(TEST_PASSWORD),
        full_name="Ada Lovelace",
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.fixture
async def inactive_user(db_session: AsyncSession) -> User:
    """A deactivated account. Valid credentials, but not allowed to act."""
    user = User(
        email="deactivated@example.com",
        hashed_password=hash_password(TEST_PASSWORD),
        is_active=False,
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.fixture
def auth_headers(registered_user: User) -> dict[str, str]:
    """Authorization header for `registered_user`.

    The token is minted directly rather than obtained by calling /auth/login,
    so tests of protected endpoints fail only for reasons to do with those
    endpoints. Login itself is tested separately, on its own terms.
    """
    return {"Authorization": f"Bearer {create_access_token(subject=registered_user.id)}"}


@pytest.fixture
def token_for_unknown_user() -> str:
    """A perfectly valid token naming a user who does not exist.

    Represents a deleted account whose token has not yet expired - the case
    that proves get_current_user really does verify the user still exists
    rather than trusting the token's claims alone.
    """
    return create_access_token(subject=uuid.uuid4())


# ==============================================================================
# Dependency-override plumbing
# ==============================================================================


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Iterator[None]:
    """Reset FastAPI dependency overrides after every test.

    `autouse=True` applies this to every test without it being requested.
    Overrides mutate application-level state, so one test that forgets to
    clean up would silently corrupt every test that runs after it - and the
    failure would appear in an unrelated test, which is miserable to debug.
    """
    yield
    fastapi_app.dependency_overrides.clear()


class FailingSession:
    """Stands in for an AsyncSession whose connection is refused.

    Used to exercise the readiness endpoint's failure path without stopping
    the real database container. The error raised is deliberately
    ConnectionRefusedError - an OSError from the socket layer, NOT a
    SQLAlchemyError - because that is what an unreachable database actually
    raises, and catching only SQLAlchemyError was a real bug this test now
    guards against.
    """

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise ConnectionRefusedError("simulated database outage")


async def failing_db() -> AsyncGenerator[FailingSession, None]:
    """Dependency override yielding a session that fails on use."""
    yield FailingSession()


@pytest.fixture
def unavailable_database(client: AsyncClient) -> Iterator[None]:
    """Make the application behave as though the database is unreachable.

    Overrides the get_db dependency for the duration of one test. This is why
    handlers take their session via Depends rather than importing a session
    factory directly: an injected dependency can be replaced, an imported one
    cannot.

    It takes `client` as an argument even though it never uses it. That is not
    an oversight: `client` installs its own get_db override, and pytest
    guarantees a fixture is built before anything that depends on it. Declaring
    the dependency makes "this override is applied last, and therefore wins"
    a property of the fixture graph rather than of the order in which a test
    happens to list its arguments.
    """
    fastapi_app.dependency_overrides[get_db] = failing_db
    yield
    fastapi_app.dependency_overrides.pop(get_db, None)
