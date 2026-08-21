"""Tests for the liveness and readiness endpoints.

These encode the behavioural contract of the probes, not merely that they
return 200. Each test names the production consequence it protects against.
"""

import pytest
from httpx import AsyncClient

from app import __version__


class TestLiveness:
    """GET /health - reports only that the process can answer."""

    async def test_returns_ok(self, client: AsyncClient) -> None:
        response = await client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == __version__
        # Asserted against the allowed set rather than a literal, so the test
        # does not break when run under a different ENVIRONMENT. A mutation
        # test found this field was previously unchecked - the endpoint could
        # have returned anything here and no test would have noticed.
        assert body["environment"] in {"local", "test", "production"}

    async def test_does_not_depend_on_the_database(
        self, client: AsyncClient, unavailable_database: None
    ) -> None:
        """Liveness must stay 200 while the database is down.

        This is the whole reason liveness and readiness are separate. If
        liveness checked the database, a brief outage would mark every
        instance dead, an orchestrator would restart all of them at once, and
        they would return with cold pools and stampede the recovering
        database - turning a blip into an outage.
        """
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestReadiness:
    """GET /health/ready - reports whether this instance can serve traffic."""

    @pytest.mark.integration
    async def test_reports_connected_against_a_real_database(self, client: AsyncClient) -> None:
        """Requires a running PostgreSQL. Verifies the full path works.

        Deliberately not mocked: this is the only test that proves the engine,
        pool, driver, and network configuration actually cooperate.
        """
        response = await client.get("/health/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "connected"

    async def test_returns_503_when_database_is_unavailable(
        self, client: AsyncClient, unavailable_database: None
    ) -> None:
        """503, not 500.

        503 tells a load balancer "route around me, I'll be back"; 500 says
        "my code is broken". Returning 500 for a dependency outage causes an
        orchestrator to restart a perfectly healthy container.

        This test guards a real bug: the handler originally caught only
        SQLAlchemyError, but an unreachable database raises
        ConnectionRefusedError from the socket layer before any database-level
        error exists, so the probe returned 500.
        """
        response = await client.get("/health/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["database"] == "unavailable"

    async def test_does_not_leak_internal_details_on_failure(
        self, client: AsyncClient, unavailable_database: None
    ) -> None:
        """Failure responses must not expose infrastructure to the internet.

        Health endpoints are typically reachable without authentication, so
        anything they echo is public. Hostnames, ports, driver names, and
        stack traces all help an attacker map the system.
        """
        response = await client.get("/health/ready")
        text = response.text.lower()

        for leak in ("traceback", "connectionrefused", "asyncpg", "localhost", "5433", "password"):
            assert leak not in text, f"response leaked {leak!r}: {response.text}"


class TestApiSurface:
    """Contract checks on the exposed API surface."""

    async def test_openapi_exposes_only_expected_paths(self, client: AsyncClient) -> None:
        """Catches endpoints added or removed without intent.

        A route accidentally exposed is a security problem; a route
        accidentally removed breaks clients. Either shows up here.

        This list is meant to be edited - but only deliberately, in the same
        commit that adds the route. It failed exactly as intended when
        /api/v1/auth/register was mounted, which is the test doing its job.
        """
        response = await client.get("/openapi.json")

        assert response.status_code == 200
        assert set(response.json()["paths"]) == {
            "/health",
            "/health/ready",
            "/api/v1/auth/register",
            "/api/v1/auth/login",
            "/api/v1/auth/me",
        }

    async def test_docs_are_available_outside_production(self, client: AsyncClient) -> None:
        """Interactive docs are enabled in local and test environments only."""
        response = await client.get("/docs")

        assert response.status_code == 200
