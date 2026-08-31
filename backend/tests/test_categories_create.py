"""Tests for POST /api/v1/categories.

Run end to end - HTTP request, validation, service, real PostgreSQL - same
reasoning as test_budgets_create.py.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Category, User
from app.services.user import create_user

CATEGORIES_URL = "/api/v1/categories"
PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user.id, token_version=user.token_version)}"
    }


def payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {"name": "Groceries"}
    body.update(overrides)
    return body


@pytest.mark.integration
class TestSuccessfulCreation:
    async def test_returns_201_with_the_created_category(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(CATEGORIES_URL, json=payload(), headers=auth(user))

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Groceries"
        assert body["id"]
        assert body["created_at"]
        assert body["updated_at"]

    async def test_response_never_exposes_user_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """CategoryRead has no user_id field, and this proves it holds.

        Mirrors test_register.py's password-leak check: the schema, not
        vigilance, is what stops a future column from being exposed by
        accident.
        """
        user = await register(db_session)

        response = await db_client.post(CATEGORIES_URL, json=payload(), headers=auth(user))

        assert "user_id" not in response.json()

    async def test_category_is_persisted_and_owned_by_the_caller(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(CATEGORIES_URL, json=payload(), headers=auth(user))

        stored = (
            await db_session.execute(select(Category).where(Category.id == response.json()["id"]))
        ).scalar_one()
        assert stored.user_id == user.id
        assert stored.name == "Groceries"


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.post(CATEGORIES_URL, json=payload())

        assert response.status_code == 401


@pytest.mark.integration
class TestValidation:
    async def test_rejects_a_missing_name(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        body = payload()
        del body["name"]

        response = await db_client.post(CATEGORIES_URL, json=body, headers=auth(user))

        assert response.status_code == 422

    async def test_rejects_an_empty_name(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(CATEGORIES_URL, json=payload(name=""), headers=auth(user))

        assert response.status_code == 422

    async def test_no_category_is_created_when_validation_fails(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        await db_client.post(CATEGORIES_URL, json=payload(name=""), headers=auth(user))

        found = (
            await db_session.execute(select(Category).where(Category.user_id == user.id))
        ).scalar_one_or_none()

        assert found is None


@pytest.mark.integration
class TestDuplicateName:
    async def test_rejects_a_second_category_with_the_same_name(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await db_client.post(CATEGORIES_URL, json=payload(), headers=auth(user))

        response = await db_client.post(CATEGORIES_URL, json=payload(), headers=auth(user))

        assert response.status_code == 409

    async def test_rejects_a_duplicate_name_regardless_of_case(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await db_client.post(CATEGORIES_URL, json=payload(name="Groceries"), headers=auth(user))

        response = await db_client.post(
            CATEGORIES_URL, json=payload(name="groceries"), headers=auth(user)
        )

        assert response.status_code == 409

    async def test_the_same_name_is_allowed_for_a_different_user(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        first_user = await register(db_session, email="first@example.com")
        second_user = await register(db_session, email="second@example.com")
        await db_client.post(CATEGORIES_URL, json=payload(), headers=auth(first_user))

        response = await db_client.post(CATEGORIES_URL, json=payload(), headers=auth(second_user))

        assert response.status_code == 201
