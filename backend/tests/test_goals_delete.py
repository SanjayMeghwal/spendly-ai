"""Tests for DELETE /api/v1/goals/{id}."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Category, Goal, Transaction, User
from app.services.category import create_category
from app.services.goal import create_goal
from app.services.user import create_user

PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


async def add_category(
    session: AsyncSession, user: User, *, name: str = "Emergency Fund"
) -> Category:
    return await create_category(session, user_id=user.id, name=name)


async def add_goal(
    session: AsyncSession, user: User, *, category_id: uuid.UUID | None = None
) -> Goal:
    if category_id is None:
        category_id = (await add_category(session, user)).id
    return await create_goal(
        session, user_id=user.id, category_id=category_id, target_amount=Decimal("5000.00")
    )


def url(goal_id: object) -> str:
    return f"/api/v1/goals/{goal_id}"


@pytest.mark.integration
class TestSuccessfulDeletion:
    async def test_returns_204(self, db_client: AsyncClient, db_session: AsyncSession) -> None:
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        response = await db_client.delete(url(goal.id), headers=auth(user))

        assert response.status_code == 204
        assert response.content == b""

    async def test_the_row_is_actually_gone(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Hard delete: the row is removed, not merely marked."""
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        await db_client.delete(url(goal.id), headers=auth(user))

        found = (
            await db_session.execute(select(Goal).where(Goal.id == goal.id))
        ).scalar_one_or_none()
        assert found is None

    async def test_deleting_twice_is_404_the_second_time(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        first = await db_client.delete(url(goal.id), headers=auth(user))
        second = await db_client.delete(url(goal.id), headers=auth(user))

        assert first.status_code == 204
        assert second.status_code == 404

    async def test_deleting_a_goal_does_not_delete_its_transactions(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Goal and Transaction both reference a category independently -
        there is no FK between them - so deleting a goal must never touch
        transaction rows.
        """
        user = await register(db_session)
        goal = await add_goal(db_session, user)
        db_session.add(
            Transaction(
                user_id=user.id,
                amount=Decimal("-10.00"),
                description="test transaction",
                category_id=goal.category_id,
                occurred_at=datetime.now(UTC),
            )
        )
        await db_session.commit()

        await db_client.delete(url(goal.id), headers=auth(user))

        remaining = (
            await db_session.execute(select(Transaction).where(Transaction.user_id == user.id))
        ).scalar_one_or_none()
        assert remaining is not None


@pytest.mark.integration
class TestOwnershipAndNotFound:
    async def test_a_nonexistent_id_is_404(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.delete(url(uuid.uuid4()), headers=auth(user))

        assert response.status_code == 404

    async def test_cannot_delete_another_users_goal(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_goal = await add_goal(db_session, grace)

        response = await db_client.delete(url(graces_goal.id), headers=auth(ada))

        assert response.status_code == 404
        found = (
            await db_session.execute(select(Goal).where(Goal.id == graces_goal.id))
        ).scalar_one_or_none()
        assert found is not None


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        response = await db_client.delete(url(goal.id))

        assert response.status_code == 401
