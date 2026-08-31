"""Tests for GET /api/v1/budgets."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Category, Transaction, User
from app.services.budget import create_budget
from app.services.category import create_category
from app.services.user import create_user

BUDGETS_URL = "/api/v1/budgets"
PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


async def add_category(session: AsyncSession, user: User, *, name: str) -> Category:
    return await create_category(session, user_id=user.id, name=name)


async def add_budget(
    session: AsyncSession, user: User, *, category_id: object, limit_amount: str = "500.00"
) -> None:
    await create_budget(
        session, user_id=user.id, category_id=category_id, limit_amount=Decimal(limit_amount)
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
    async def test_returns_only_the_callers_budgets(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Mirrors test_transactions_list.py's cross-user check."""
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        ada_category = await add_category(db_session, ada, name="Ada's Groceries")
        grace_category = await add_category(db_session, grace, name="Grace's Groceries")
        await add_budget(db_session, ada, category_id=ada_category.id)
        await add_budget(db_session, grace, category_id=grace_category.id)

        response = await db_client.get(BUDGETS_URL, headers=auth(ada))

        assert response.status_code == 200
        names = [b["category_name"] for b in response.json()]
        assert names == ["Ada's Groceries"]

    async def test_returns_empty_list_when_the_user_has_none(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(BUDGETS_URL, headers=auth(user))

        assert response.status_code == 200
        assert response.json() == []

    async def test_orders_alphabetically_by_category_name(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        utilities = await add_category(db_session, user, name="Utilities")
        dining = await add_category(db_session, user, name="Dining")
        groceries = await add_category(db_session, user, name="Groceries")
        await add_budget(db_session, user, category_id=utilities.id)
        await add_budget(db_session, user, category_id=dining.id)
        await add_budget(db_session, user, category_id=groceries.id)

        response = await db_client.get(BUDGETS_URL, headers=auth(user))

        names = [b["category_name"] for b in response.json()]
        assert names == ["Dining", "Groceries", "Utilities"]

    async def test_each_budget_reports_its_own_spend(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        groceries = await add_category(db_session, user, name="Groceries")
        dining = await add_category(db_session, user, name="Dining")
        await add_budget(db_session, user, category_id=groceries.id)
        await add_budget(db_session, user, category_id=dining.id)
        now = datetime.now(UTC)
        await add_transaction(
            db_session, user, amount="-50.00", category_id=groceries.id, occurred_at=now
        )
        await add_transaction(
            db_session, user, amount="-15.00", category_id=dining.id, occurred_at=now
        )

        response = await db_client.get(BUDGETS_URL, headers=auth(user))

        by_name = {b["category_name"]: b for b in response.json()}
        assert by_name["Groceries"]["spent"] == "50.00"
        assert by_name["Dining"]["spent"] == "15.00"


@pytest.mark.integration
class TestMonthParameter:
    async def test_defaults_to_the_current_month(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user, name="Groceries")
        await add_budget(db_session, user, category_id=category.id)
        await add_transaction(
            db_session,
            user,
            amount="-50.00",
            category_id=category.id,
            occurred_at=datetime.now(UTC),
        )

        response = await db_client.get(BUDGETS_URL, headers=auth(user))

        assert response.json()[0]["spent"] == "50.00"

    async def test_reports_spend_for_an_explicitly_requested_month(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user, name="Groceries")
        await add_budget(db_session, user, category_id=category.id)
        await add_transaction(
            db_session,
            user,
            amount="-50.00",
            category_id=category.id,
            occurred_at=datetime(2026, 3, 15, tzinfo=UTC),
        )

        response = await db_client.get(BUDGETS_URL, params={"month": "2026-03"}, headers=auth(user))

        assert response.json()[0]["spent"] == "50.00"

    async def test_a_month_with_no_transactions_reports_zero_spend(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user, name="Groceries")
        await add_budget(db_session, user, category_id=category.id)
        await add_transaction(
            db_session,
            user,
            amount="-50.00",
            category_id=category.id,
            occurred_at=datetime(2026, 3, 15, tzinfo=UTC),
        )

        response = await db_client.get(BUDGETS_URL, params={"month": "2026-04"}, headers=auth(user))

        assert response.json()[0]["spent"] == "0"

    async def test_handles_december_correctly_without_wrapping_into_the_same_year(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The one calendar boundary _month_bounds has to get right: month=12
        rolls into January of the FOLLOWING year, not month 13 of this one.
        """
        user = await register(db_session)
        category = await add_category(db_session, user, name="Groceries")
        await add_budget(db_session, user, category_id=category.id)
        await add_transaction(
            db_session,
            user,
            amount="-50.00",
            category_id=category.id,
            occurred_at=datetime(2026, 12, 31, 23, 0, tzinfo=UTC),
        )
        await add_transaction(
            db_session,
            user,
            amount="-999.00",
            category_id=category.id,
            occurred_at=datetime(2027, 1, 1, 0, 0, tzinfo=UTC),
        )

        response = await db_client.get(BUDGETS_URL, params={"month": "2026-12"}, headers=auth(user))

        assert response.json()[0]["spent"] == "50.00"

    async def test_rejects_a_malformed_month(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(
            BUDGETS_URL, params={"month": "March-2026"}, headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_an_out_of_range_month_number(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(BUDGETS_URL, params={"month": "2026-13"}, headers=auth(user))

        assert response.status_code == 422


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.get(BUDGETS_URL)

        assert response.status_code == 401
