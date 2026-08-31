"""Tests for POST /api/v1/budgets.

Run end to end - HTTP request, validation, service, real PostgreSQL - same
reasoning as test_transactions_create.py: what matters here spans all of
those layers, including the spend calculation, which needs real transaction
rows and a real database SUM().
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Budget, Transaction, User
from app.services.user import create_user

BUDGETS_URL = "/api/v1/budgets"
PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user.id, token_version=user.token_version)}"
    }


def payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {"category": "Groceries", "limit_amount": "500.00"}
    body.update(overrides)
    return body


async def add_transaction(
    session: AsyncSession,
    *,
    user_id: object,
    amount: Decimal,
    category: str,
    occurred_at: datetime,
) -> None:
    session.add(
        Transaction(
            user_id=user_id,
            amount=amount,
            description="test transaction",
            category=category,
            occurred_at=occurred_at,
        )
    )
    await session.commit()


@pytest.mark.integration
class TestSuccessfulCreation:
    async def test_returns_201_with_the_created_budget(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(BUDGETS_URL, json=payload(), headers=auth(user))

        assert response.status_code == 201
        body = response.json()
        assert body["category"] == "Groceries"
        assert body["limit_amount"] == "500.00"
        assert body["id"]
        assert body["created_at"]
        assert body["updated_at"]

    async def test_fresh_budget_has_zero_spent_and_full_remaining(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(BUDGETS_URL, json=payload(), headers=auth(user))

        body = response.json()
        assert body["spent"] == "0"
        assert body["remaining"] == "500.00"

    async def test_spent_reflects_existing_transactions_in_the_current_month(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        now = datetime.now(UTC)
        await add_transaction(
            db_session,
            user_id=user.id,
            amount=Decimal("-50.00"),
            category="Groceries",
            occurred_at=now,
        )

        response = await db_client.post(BUDGETS_URL, json=payload(), headers=auth(user))

        body = response.json()
        assert body["spent"] == "50.00"
        assert body["remaining"] == "450.00"

    async def test_category_matching_is_case_insensitive(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        now = datetime.now(UTC)
        await add_transaction(
            db_session,
            user_id=user.id,
            amount=Decimal("-50.00"),
            category="groceries",
            occurred_at=now,
        )

        response = await db_client.post(
            BUDGETS_URL, json=payload(category="Groceries"), headers=auth(user)
        )

        assert response.json()["spent"] == "50.00"

    async def test_a_refund_offsets_spend(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        now = datetime.now(UTC)
        await add_transaction(
            db_session,
            user_id=user.id,
            amount=Decimal("-50.00"),
            category="Groceries",
            occurred_at=now,
        )
        await add_transaction(
            db_session,
            user_id=user.id,
            amount=Decimal("20.00"),
            category="Groceries",
            occurred_at=now,
        )

        response = await db_client.post(BUDGETS_URL, json=payload(), headers=auth(user))

        assert response.json()["spent"] == "30.00"

    async def test_transactions_outside_the_current_month_are_not_counted(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await add_transaction(
            db_session,
            user_id=user.id,
            amount=Decimal("-50.00"),
            category="Groceries",
            occurred_at=datetime(2020, 1, 15, tzinfo=UTC),
        )

        response = await db_client.post(BUDGETS_URL, json=payload(), headers=auth(user))

        assert response.json()["spent"] == "0"

    async def test_response_never_exposes_user_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """BudgetRead has no user_id field, and this proves it holds.

        Mirrors test_register.py's password-leak check: the schema, not
        vigilance, is what stops a future column from being exposed by
        accident.
        """
        user = await register(db_session)

        response = await db_client.post(BUDGETS_URL, json=payload(), headers=auth(user))

        assert "user_id" not in response.json()

    async def test_budget_is_persisted_and_owned_by_the_caller(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(BUDGETS_URL, json=payload(), headers=auth(user))

        stored = (
            await db_session.execute(select(Budget).where(Budget.id == response.json()["id"]))
        ).scalar_one()
        assert stored.user_id == user.id
        assert stored.limit_amount == Decimal("500.00")


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.post(BUDGETS_URL, json=payload())

        assert response.status_code == 401


@pytest.mark.integration
class TestValidation:
    async def test_rejects_a_missing_category(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        body = payload()
        del body["category"]

        response = await db_client.post(BUDGETS_URL, json=body, headers=auth(user))

        assert response.status_code == 422

    async def test_rejects_an_empty_category(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(BUDGETS_URL, json=payload(category=""), headers=auth(user))

        assert response.status_code == 422

    async def test_rejects_a_zero_limit_amount(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(
            BUDGETS_URL, json=payload(limit_amount="0.00"), headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_a_negative_limit_amount(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(
            BUDGETS_URL, json=payload(limit_amount="-10.00"), headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_a_limit_amount_exceeding_the_column_width(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(
            BUDGETS_URL, json=payload(limit_amount="123456789012.00"), headers=auth(user)
        )

        assert response.status_code == 422

    async def test_no_budget_is_created_when_validation_fails(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        await db_client.post(BUDGETS_URL, json=payload(category=""), headers=auth(user))

        found = (
            await db_session.execute(select(Budget).where(Budget.user_id == user.id))
        ).scalar_one_or_none()

        assert found is None


@pytest.mark.integration
class TestDuplicateCategory:
    async def test_rejects_a_second_budget_for_the_same_category(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await db_client.post(BUDGETS_URL, json=payload(), headers=auth(user))

        response = await db_client.post(BUDGETS_URL, json=payload(), headers=auth(user))

        assert response.status_code == 409

    async def test_rejects_a_duplicate_category_regardless_of_case(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await db_client.post(BUDGETS_URL, json=payload(category="Groceries"), headers=auth(user))

        response = await db_client.post(
            BUDGETS_URL, json=payload(category="groceries"), headers=auth(user)
        )

        assert response.status_code == 409

    async def test_the_same_category_is_allowed_for_a_different_user(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        first_user = await register(db_session, email="first@example.com")
        second_user = await register(db_session, email="second@example.com")
        await db_client.post(BUDGETS_URL, json=payload(), headers=auth(first_user))

        response = await db_client.post(BUDGETS_URL, json=payload(), headers=auth(second_user))

        assert response.status_code == 201
