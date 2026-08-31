"""Tests for GET /api/v1/goals."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Category, Transaction, User
from app.services.category import create_category
from app.services.goal import create_goal
from app.services.user import create_user

GOALS_URL = "/api/v1/goals"
PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


async def add_category(session: AsyncSession, user: User, *, name: str) -> Category:
    return await create_category(session, user_id=user.id, name=name)


async def add_goal(
    session: AsyncSession, user: User, *, category_id: object, target_amount: str = "5000.00"
) -> None:
    await create_goal(
        session, user_id=user.id, category_id=category_id, target_amount=Decimal(target_amount)
    )


async def add_transaction(
    session: AsyncSession,
    user: User,
    *,
    amount: str,
    category_id: object,
    occurred_at: datetime,
) -> None:
    session.add(
        Transaction(
            user_id=user.id,
            amount=Decimal(amount),
            description="test transaction",
            category_id=category_id,
            occurred_at=occurred_at,
        )
    )
    await session.commit()


@pytest.mark.integration
class TestSuccessfulListing:
    async def test_returns_only_the_callers_goals(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Mirrors test_budgets_list.py's cross-user check."""
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        ada_category = await add_category(db_session, ada, name="Ada's Fund")
        grace_category = await add_category(db_session, grace, name="Grace's Fund")
        await add_goal(db_session, ada, category_id=ada_category.id)
        await add_goal(db_session, grace, category_id=grace_category.id)

        response = await db_client.get(GOALS_URL, headers=auth(ada))

        assert response.status_code == 200
        names = [g["category_name"] for g in response.json()]
        assert names == ["Ada's Fund"]

    async def test_returns_empty_list_when_the_user_has_none(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(GOALS_URL, headers=auth(user))

        assert response.status_code == 200
        assert response.json() == []

    async def test_orders_alphabetically_by_category_name(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        vacation = await add_category(db_session, user, name="Vacation")
        car = await add_category(db_session, user, name="Car")
        emergency = await add_category(db_session, user, name="Emergency Fund")
        await add_goal(db_session, user, category_id=vacation.id)
        await add_goal(db_session, user, category_id=car.id)
        await add_goal(db_session, user, category_id=emergency.id)

        response = await db_client.get(GOALS_URL, headers=auth(user))

        names = [g["category_name"] for g in response.json()]
        assert names == ["Car", "Emergency Fund", "Vacation"]

    async def test_each_goal_reports_its_own_progress(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        car = await add_category(db_session, user, name="Car")
        vacation = await add_category(db_session, user, name="Vacation")
        await add_goal(db_session, user, category_id=car.id)
        await add_goal(db_session, user, category_id=vacation.id)
        now = datetime.now(UTC)
        await add_transaction(
            db_session, user, amount="-500.00", category_id=car.id, occurred_at=now
        )
        await add_transaction(
            db_session, user, amount="-150.00", category_id=vacation.id, occurred_at=now
        )

        response = await db_client.get(GOALS_URL, headers=auth(user))

        by_name = {g["category_name"]: g for g in response.json()}
        assert by_name["Car"]["progress"] == "500.00"
        assert by_name["Vacation"]["progress"] == "150.00"

    async def test_progress_includes_transactions_from_any_time(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Unlike GET /budgets, there is no month window to worry about here
        at all - confirming a goal reflects old transactions with no
        special query parameter needed."""
        user = await register(db_session)
        category = await add_category(db_session, user, name="Emergency Fund")
        await add_goal(db_session, user, category_id=category.id)
        await add_transaction(
            db_session,
            user,
            amount="-300.00",
            category_id=category.id,
            occurred_at=datetime(2019, 6, 1, tzinfo=UTC),
        )

        response = await db_client.get(GOALS_URL, headers=auth(user))

        assert response.json()[0]["progress"] == "300.00"


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.get(GOALS_URL)

        assert response.status_code == 401
