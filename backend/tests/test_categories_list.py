"""Tests for GET /api/v1/categories."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import User
from app.services.category import create_category
from app.services.user import create_user

CATEGORIES_URL = "/api/v1/categories"
PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


async def add_category(session: AsyncSession, user: User, *, name: str) -> None:
    await create_category(session, user_id=user.id, name=name)


@pytest.mark.integration
class TestSuccessfulListing:
    async def test_returns_only_the_callers_categories(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Mirrors test_budgets_list.py's cross-user check."""
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        await add_category(db_session, ada, name="Ada's Groceries")
        await add_category(db_session, grace, name="Grace's Groceries")

        response = await db_client.get(CATEGORIES_URL, headers=auth(ada))

        assert response.status_code == 200
        names = [c["name"] for c in response.json()]
        assert names == ["Ada's Groceries"]

    async def test_returns_empty_list_when_the_user_has_none(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(CATEGORIES_URL, headers=auth(user))

        assert response.status_code == 200
        assert response.json() == []

    async def test_orders_alphabetically_by_name(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await add_category(db_session, user, name="Utilities")
        await add_category(db_session, user, name="Dining")
        await add_category(db_session, user, name="Groceries")

        response = await db_client.get(CATEGORIES_URL, headers=auth(user))

        names = [c["name"] for c in response.json()]
        assert names == ["Dining", "Groceries", "Utilities"]


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.get(CATEGORIES_URL)

        assert response.status_code == 401
