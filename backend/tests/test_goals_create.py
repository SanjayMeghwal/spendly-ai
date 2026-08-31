"""Tests for POST /api/v1/goals.

Run end to end - HTTP request, validation, service, real PostgreSQL - same
reasoning as test_budgets_create.py: what matters here spans all of those
layers, including the progress calculation, which needs real transaction
rows and a real database SUM().
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Category, Goal, Transaction, User
from app.services.category import create_category
from app.services.goal import create_goal
from app.services.user import create_user

GOALS_URL = "/api/v1/goals"
PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user.id, token_version=user.token_version)}"
    }


async def add_category(
    session: AsyncSession, user: User, *, name: str = "Emergency Fund"
) -> Category:
    return await create_category(session, user_id=user.id, name=name)


def payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {"target_amount": "5000.00"}
    body.update(overrides)
    return body


async def add_transaction(
    session: AsyncSession,
    *,
    user_id: object,
    amount: Decimal,
    category_id: object,
    occurred_at: datetime,
) -> None:
    session.add(
        Transaction(
            user_id=user_id,
            amount=amount,
            description="test transaction",
            category_id=category_id,
            occurred_at=occurred_at,
        )
    )
    await session.commit()


@pytest.mark.integration
class TestSuccessfulCreation:
    async def test_returns_201_with_the_created_goal(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(category.id)), headers=auth(user)
        )

        assert response.status_code == 201
        body = response.json()
        assert body["category_id"] == str(category.id)
        assert body["category_name"] == "Emergency Fund"
        assert body["target_amount"] == "5000.00"
        assert body["target_date"] is None
        assert body["id"]
        assert body["created_at"]
        assert body["updated_at"]

    async def test_target_date_is_optional_but_stored_when_given(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.post(
            GOALS_URL,
            json=payload(category_id=str(category.id), target_date="2027-01-01"),
            headers=auth(user),
        )

        assert response.status_code == 201
        assert response.json()["target_date"] == "2027-01-01"

    async def test_fresh_goal_has_zero_progress_and_full_remaining(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(category.id)), headers=auth(user)
        )

        body = response.json()
        assert body["progress"] == "0"
        assert body["remaining"] == "5000.00"

    async def test_progress_reflects_existing_transactions(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)
        await add_transaction(
            db_session,
            user_id=user.id,
            amount=Decimal("-200.00"),
            category_id=category.id,
            occurred_at=datetime.now(UTC),
        )

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(category.id)), headers=auth(user)
        )

        body = response.json()
        assert body["progress"] == "200.00"
        assert body["remaining"] == "4800.00"

    async def test_progress_is_cumulative_not_limited_to_the_current_month(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The one real behavioral difference from Budget: a goal's
        progress counts transactions from any time, not just this month."""
        user = await register(db_session)
        category = await add_category(db_session, user)
        await add_transaction(
            db_session,
            user_id=user.id,
            amount=Decimal("-200.00"),
            category_id=category.id,
            occurred_at=datetime(2020, 1, 15, tzinfo=UTC),
        )

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(category.id)), headers=auth(user)
        )

        assert response.json()["progress"] == "200.00"

    async def test_only_transactions_in_the_same_category_count(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        emergency_fund = await add_category(db_session, user, name="Emergency Fund")
        dining = await add_category(db_session, user, name="Dining")
        await add_transaction(
            db_session,
            user_id=user.id,
            amount=Decimal("-200.00"),
            category_id=dining.id,
            occurred_at=datetime.now(UTC),
        )

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(emergency_fund.id)), headers=auth(user)
        )

        assert response.json()["progress"] == "0"

    async def test_a_withdrawal_offsets_progress(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)
        now = datetime.now(UTC)
        await add_transaction(
            db_session,
            user_id=user.id,
            amount=Decimal("-200.00"),
            category_id=category.id,
            occurred_at=now,
        )
        await add_transaction(
            db_session,
            user_id=user.id,
            amount=Decimal("50.00"),
            category_id=category.id,
            occurred_at=now,
        )

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(category.id)), headers=auth(user)
        )

        assert response.json()["progress"] == "150.00"

    async def test_remaining_is_shown_uncapped_when_overshot(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Exceeding a goal is a fact worth showing, not hiding."""
        user = await register(db_session)
        category = await add_category(db_session, user)
        await add_transaction(
            db_session,
            user_id=user.id,
            amount=Decimal("-6000.00"),
            category_id=category.id,
            occurred_at=datetime.now(UTC),
        )

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(category.id)), headers=auth(user)
        )

        body = response.json()
        assert body["progress"] == "6000.00"
        assert body["remaining"] == "-1000.00"

    async def test_response_never_exposes_user_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """GoalRead has no user_id field, and this proves it holds.

        Mirrors test_register.py's password-leak check: the schema, not
        vigilance, is what stops a future column from being exposed by
        accident.
        """
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(category.id)), headers=auth(user)
        )

        assert "user_id" not in response.json()

    async def test_goal_is_persisted_and_owned_by_the_caller(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(category.id)), headers=auth(user)
        )

        stored = (
            await db_session.execute(select(Goal).where(Goal.id == response.json()["id"]))
        ).scalar_one()
        assert stored.user_id == user.id
        assert stored.target_amount == Decimal("5000.00")


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.post(GOALS_URL, json=payload(category_id=str(uuid.uuid4())))

        assert response.status_code == 401


@pytest.mark.integration
class TestValidation:
    async def test_rejects_a_missing_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(GOALS_URL, json=payload(), headers=auth(user))

        assert response.status_code == 422

    async def test_rejects_a_malformed_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(
            GOALS_URL, json=payload(category_id="not-a-uuid"), headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_a_nonexistent_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(uuid.uuid4())), headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_another_users_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_category = await add_category(db_session, grace)

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(graces_category.id)), headers=auth(ada)
        )

        assert response.status_code == 422

    async def test_rejects_a_zero_target_amount(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.post(
            GOALS_URL,
            json=payload(category_id=str(category.id), target_amount="0.00"),
            headers=auth(user),
        )

        assert response.status_code == 422

    async def test_rejects_a_negative_target_amount(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.post(
            GOALS_URL,
            json=payload(category_id=str(category.id), target_amount="-10.00"),
            headers=auth(user),
        )

        assert response.status_code == 422

    async def test_rejects_a_target_amount_exceeding_the_column_width(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.post(
            GOALS_URL,
            json=payload(category_id=str(category.id), target_amount="123456789012.00"),
            headers=auth(user),
        )

        assert response.status_code == 422

    async def test_no_goal_is_created_when_validation_fails(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        await db_client.post(
            GOALS_URL,
            json=payload(category_id=str(category.id), target_amount="0.00"),
            headers=auth(user),
        )

        found = (
            await db_session.execute(select(Goal).where(Goal.user_id == user.id))
        ).scalar_one_or_none()

        assert found is None


@pytest.mark.integration
class TestDuplicateCategory:
    async def test_rejects_a_second_goal_for_the_same_category(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)
        await db_client.post(
            GOALS_URL, json=payload(category_id=str(category.id)), headers=auth(user)
        )

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(category.id)), headers=auth(user)
        )

        assert response.status_code == 409

    async def test_the_same_category_is_allowed_for_a_different_user(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        first_user = await register(db_session, email="first@example.com")
        second_user = await register(db_session, email="second@example.com")
        first_category = await add_category(db_session, first_user)
        second_category = await add_category(db_session, second_user)
        await db_client.post(
            GOALS_URL, json=payload(category_id=str(first_category.id)), headers=auth(first_user)
        )

        response = await db_client.post(
            GOALS_URL, json=payload(category_id=str(second_category.id)), headers=auth(second_user)
        )

        assert response.status_code == 201


@pytest.mark.integration
class TestServiceLevelIntegrityErrors:
    async def test_a_non_unique_integrity_error_is_not_mislabelled(
        self, db_session: AsyncSession
    ) -> None:
        """A CHECK violation must NOT be reported as a duplicate category.

        Calling the service directly with a non-positive target_amount
        bypasses GoalCreate's `gt=0` validation, exactly as a careless
        caller would. The `target_amount > 0` CHECK constraint then rejects
        it - and that must surface as a plain IntegrityError, not get
        mislabelled as GoalCategoryAlreadyExists. Mirrors
        test_budgets_create.py's identical check on create_budget.
        """
        user = await register(db_session)
        category = await add_category(db_session, user)

        with pytest.raises(IntegrityError):
            await create_goal(
                db_session,
                user_id=user.id,
                category_id=category.id,
                target_amount=Decimal("-10.00"),
            )
