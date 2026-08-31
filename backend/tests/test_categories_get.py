"""Tests for GET /api/v1/categories/{id}."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Category, User
from app.services.category import create_category
from app.services.user import create_user

PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


async def add_category(session: AsyncSession, user: User, *, name: str = "Groceries") -> Category:
    return await create_category(session, user_id=user.id, name=name)


def url(category_id: object) -> str:
    return f"/api/v1/categories/{category_id}"


@pytest.mark.integration
class TestSuccessfulRetrieval:
    async def test_returns_the_category(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.get(url(category.id), headers=auth(user))

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(category.id)
        assert body["name"] == "Groceries"


@pytest.mark.integration
class TestOwnershipAndNotFound:
    async def test_a_nonexistent_id_is_404(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(url(uuid.uuid4()), headers=auth(user))

        assert response.status_code == 404

    async def test_another_users_category_is_404_not_403(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """404, not 403 - matching test_budgets_get.py's reasoning."""
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_category = await add_category(db_session, grace)

        response = await db_client.get(url(graces_category.id), headers=auth(ada))

        assert response.status_code == 404

    async def test_a_malformed_id_is_422_not_500(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(url("not-a-uuid"), headers=auth(user))

        assert response.status_code == 422


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.get(url(category.id))

        assert response.status_code == 401
