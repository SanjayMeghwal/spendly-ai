"""Shared pytest fixtures.

conftest.py is discovered automatically by pytest; fixtures defined here are
available to every test in this directory and below without importing them.
"""

from collections.abc import AsyncGenerator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.config import get_settings
from app.db.session import get_db
from app.main import app as fastapi_app


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client that calls the app in-process.

    ASGITransport routes requests straight into the ASGI application rather
    than over a real socket, so no server needs to be running and there is no
    port to allocate. Routing, dependency injection, validation, and
    serialisation all execute exactly as they would in production - only the
    network hop is skipped.
    """
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


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
def unavailable_database() -> Iterator[None]:
    """Make the application behave as though the database is unreachable.

    Overrides the get_db dependency for the duration of one test. This is why
    handlers take their session via Depends rather than importing a session
    factory directly: an injected dependency can be replaced, an imported one
    cannot.
    """
    fastapi_app.dependency_overrides[get_db] = failing_db
    yield
    fastapi_app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """A throwaway engine that pools nothing.

    WHY NOT JUST REUSE app.db.session.engine? Because that engine keeps up to
    five connections warm, and an asyncpg connection is permanently bound to
    the event loop that opened it. pytest-asyncio gives every test a FRESH
    event loop, so a pooled connection created by one test is handed to a
    later test whose loop is different - and closing it raises
    `RuntimeError: Event loop is closed` during teardown.

    That failure is especially nasty because it surfaces in teardown of an
    unrelated, passing test, so it reads as flakiness rather than as a real
    lifecycle bug. It was a genuine error here before NullPool was introduced.

    NullPool opens a connection per use and closes it immediately, so nothing
    survives to be reused on the wrong loop. The application keeps its real
    pool; only tests opt out. Pooling is a production optimisation, and tests
    are not production.
    """
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """A database session whose writes are always rolled back.

    HOW THE ISOLATION WORKS, because this pattern is not obvious:

    The naive approach - use a normal session and delete the rows afterwards -
    fails badly. A test that raises leaves its rows behind, the next test hits
    a duplicate-key error, and you spend an afternoon debugging a test that is
    not broken.

    Instead we open one connection, begin a transaction on it, and bind the
    session to that connection. Whatever the test writes lives inside that
    outer transaction, and rolling it back at the end returns the database to
    exactly its previous state - even if the test failed.

    `join_transaction_mode="create_savepoint"` is the load-bearing argument.
    Without it, a test calling `session.commit()` would commit our outer
    transaction and the data would persist. With it, commit() releases a
    SAVEPOINT instead, so committing behaves normally FROM THE TEST'S POINT OF
    VIEW - constraints fire, IDs are assigned, flushes happen - while the
    outer rollback still discards everything.

    This is also why tests run against real PostgreSQL: SAVEPOINT semantics,
    CHECK constraints, and TIMESTAMPTZ are precisely what we need to verify,
    and are precisely what SQLite would fake.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
