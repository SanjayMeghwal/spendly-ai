"""Shared pytest fixtures.

conftest.py is discovered automatically by pytest; fixtures defined here are
available to every test in this directory and below without importing them.
"""

from collections.abc import AsyncGenerator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

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
