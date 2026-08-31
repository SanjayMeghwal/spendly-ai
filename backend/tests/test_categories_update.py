"""Tests for PATCH /api/v1/categories/{id}."""

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
class TestRename:
    async def test_renames_the_category(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.patch(
            url(category.id), json={"name": "Household"}, headers=auth(user)
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Household"

    async def test_empty_body_changes_nothing(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.patch(url(category.id), json={}, headers=auth(user))

        assert response.status_code == 200
        assert response.json()["name"] == "Groceries"


@pytest.mark.integration
class TestOwnershipAndNotFound:
    async def test_a_nonexistent_id_is_404(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.patch(url(uuid.uuid4()), json={"name": "x"}, headers=auth(user))

        assert response.status_code == 404

    async def test_cannot_rename_another_users_category(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_category = await add_category(db_session, grace)

        response = await db_client.patch(
            url(graces_category.id), json={"name": "hijacked"}, headers=auth(ada)
        )

        assert response.status_code == 404
        await db_session.refresh(graces_category)
        assert graces_category.name == "Groceries"


@pytest.mark.integration
class TestValidation:
    async def test_rejects_an_empty_name(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.patch(url(category.id), json={"name": ""}, headers=auth(user))

        assert response.status_code == 422

    async def test_rejects_explicit_null(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """name backs a NOT NULL column - it can never be cleared."""
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.patch(url(category.id), json={"name": None}, headers=auth(user))

        assert response.status_code == 422


@pytest.mark.integration
class TestDuplicateName:
    async def test_renaming_into_an_existing_name_is_a_conflict(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await add_category(db_session, user, name="Dining")
        groceries = await add_category(db_session, user, name="Groceries")

        response = await db_client.patch(
            url(groceries.id), json={"name": "Dining"}, headers=auth(user)
        )

        assert response.status_code == 409

    async def test_renaming_into_an_existing_name_regardless_of_case_is_a_conflict(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await add_category(db_session, user, name="Dining")
        groceries = await add_category(db_session, user, name="Groceries")

        response = await db_client.patch(
            url(groceries.id), json={"name": "dining"}, headers=auth(user)
        )

        assert response.status_code == 409

    async def test_renaming_to_its_own_current_name_is_not_a_conflict(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A no-op rename must not collide with the row's own existing value."""
        user = await register(db_session)
        category = await add_category(db_session, user, name="Groceries")

        response = await db_client.patch(
            url(category.id), json={"name": "Groceries"}, headers=auth(user)
        )

        assert response.status_code == 200


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.patch(url(category.id), json={"name": "x"})

        assert response.status_code == 401
