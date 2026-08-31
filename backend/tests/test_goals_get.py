"""Tests for GET /api/v1/goals/{id}."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
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
class TestSuccessfulRetrieval:
    async def test_returns_the_goal(self, db_client: AsyncClient, db_session: AsyncSession) -> None:
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        response = await db_client.get(url(goal.id), headers=auth(user))

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(goal.id)
        assert body["category_name"] == "Emergency Fund"

    async def test_reports_progress_including_old_transactions(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        goal = await add_goal(db_session, user)
        db_session.add(
            Transaction(
                user_id=user.id,
                amount=Decimal("-50.00"),
                description="test transaction",
                category_id=goal.category_id,
                occurred_at=datetime(2019, 3, 15, tzinfo=UTC),
            )
        )
        await db_session.commit()

        response = await db_client.get(url(goal.id), headers=auth(user))

        assert response.json()["progress"] == "50.00"


@pytest.mark.integration
class TestOwnershipAndNotFound:
    async def test_a_nonexistent_id_is_404(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(url(uuid.uuid4()), headers=auth(user))

        assert response.status_code == 404

    async def test_another_users_goal_is_404_not_403(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """404, not 403 - matching test_budgets_get.py's reasoning."""
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_goal = await add_goal(db_session, grace)

        response = await db_client.get(url(graces_goal.id), headers=auth(ada))

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
        goal = await add_goal(db_session, user)

        response = await db_client.get(url(goal.id))

        assert response.status_code == 401
