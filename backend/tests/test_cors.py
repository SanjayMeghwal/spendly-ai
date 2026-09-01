"""Tests for CORS - the frontend dev server's ability to call this API at all.

Without CORSMiddleware, a browser blocks a cross-origin request before it
ever reaches a route, regardless of authentication - this is a separate
layer from everything auth-related, so it gets its own tiny test rather
than being folded into test_health.py's unrelated API-surface checks.
"""

from httpx import AsyncClient


class TestCors:
    async def test_allows_the_configured_frontend_origin(self, client: AsyncClient) -> None:
        response = await client.get("/health", headers={"Origin": "http://localhost:5173"})

        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"

    async def test_does_not_allow_an_unlisted_origin(self, client: AsyncClient) -> None:
        response = await client.get("/health", headers={"Origin": "http://evil.example"})

        assert "access-control-allow-origin" not in response.headers
