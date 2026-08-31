"""Tests for PATCH /api/v1/goals/{id}."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Category, Goal, User
from app.services.category import create_category
from app.services.goal import create_goal, update_goal
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
class TestPartialUpdate:
    async def test_updates_only_the_sent_field(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A PATCH that names one field must leave every other field alone."""
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        response = await db_client.patch(
            url(goal.id), json={"target_amount": "6000.00"}, headers=auth(user)
        )

        assert response.status_code == 200
        body = response.json()
        assert body["target_amount"] == "6000.00"
        assert body["category_id"] == str(goal.category_id)

    async def test_updates_the_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        goal = await add_goal(db_session, user)
        car = await add_category(db_session, user, name="Car")

        response = await db_client.patch(
            url(goal.id), json={"category_id": str(car.id)}, headers=auth(user)
        )

        assert response.status_code == 200
        body = response.json()
        assert body["category_id"] == str(car.id)
        assert body["category_name"] == "Car"
        assert body["target_amount"] == "5000.00"

    async def test_sets_a_target_date(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        response = await db_client.patch(
            url(goal.id), json={"target_date": "2027-06-01"}, headers=auth(user)
        )

        assert response.status_code == 200
        assert response.json()["target_date"] == "2027-06-01"

    async def test_clears_a_target_date_with_explicit_null(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Unlike category_id and target_amount, target_date genuinely can
        be cleared - this is the whole point of it being optional."""
        user = await register(db_session)
        category = await add_category(db_session, user)
        goal = await create_goal(
            db_session,
            user_id=user.id,
            category_id=category.id,
            target_amount=Decimal("5000.00"),
            target_date=date(2027, 1, 1),
        )

        response = await db_client.patch(
            url(goal.id), json={"target_date": None}, headers=auth(user)
        )

        assert response.status_code == 200
        assert response.json()["target_date"] is None

    async def test_empty_body_changes_nothing(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        response = await db_client.patch(url(goal.id), json={}, headers=auth(user))

        assert response.status_code == 200
        assert response.json()["category_id"] == str(goal.category_id)
        assert response.json()["target_amount"] == "5000.00"


@pytest.mark.integration
class TestOwnershipAndNotFound:
    async def test_a_nonexistent_id_is_404(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.patch(
            url(uuid.uuid4()), json={"target_amount": "1.00"}, headers=auth(user)
        )

        assert response.status_code == 404

    async def test_cannot_update_another_users_goal(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_goal = await add_goal(db_session, grace)

        response = await db_client.patch(
            url(graces_goal.id), json={"target_amount": "999.00"}, headers=auth(ada)
        )

        assert response.status_code == 404
        await db_session.refresh(graces_goal)
        assert graces_goal.target_amount == Decimal("5000.00")


@pytest.mark.integration
class TestValidation:
    async def test_rejects_a_malformed_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        response = await db_client.patch(
            url(goal.id), json={"category_id": "not-a-uuid"}, headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_a_nonexistent_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        response = await db_client.patch(
            url(goal.id), json={"category_id": str(uuid.uuid4())}, headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_another_users_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        goal = await add_goal(db_session, ada)
        graces_category = await add_category(db_session, grace)

        response = await db_client.patch(
            url(goal.id), json={"category_id": str(graces_category.id)}, headers=auth(ada)
        )

        assert response.status_code == 422

    async def test_rejects_a_non_positive_target_amount(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        response = await db_client.patch(
            url(goal.id), json={"target_amount": "0.00"}, headers=auth(user)
        )

        assert response.status_code == 422

    @pytest.mark.parametrize("field", ["category_id", "target_amount"])
    async def test_rejects_explicit_null(
        self, db_client: AsyncClient, db_session: AsyncSession, field: str
    ) -> None:
        """Both fields back NOT NULL columns - neither can ever be cleared.
        (target_date is the exception - see test_clears_a_target_date_with_explicit_null.)"""
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        response = await db_client.patch(url(goal.id), json={field: None}, headers=auth(user))

        assert response.status_code == 422


@pytest.mark.integration
class TestDuplicateCategory:
    async def test_switching_to_a_category_with_an_existing_goal_is_a_conflict(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        car = await add_category(db_session, user, name="Car")
        await add_goal(db_session, user, category_id=car.id)
        emergency_fund_goal = await add_goal(db_session, user)

        response = await db_client.patch(
            url(emergency_fund_goal.id), json={"category_id": str(car.id)}, headers=auth(user)
        )

        assert response.status_code == 409

    async def test_switching_to_its_own_current_category_is_not_a_conflict(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A no-op switch must not collide with the row's own existing value."""
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        response = await db_client.patch(
            url(goal.id), json={"category_id": str(goal.category_id)}, headers=auth(user)
        )

        assert response.status_code == 200


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        response = await db_client.patch(url(goal.id), json={"target_amount": "1.00"})

        assert response.status_code == 401


@pytest.mark.integration
class TestServiceLevelIntegrityErrors:
    async def test_a_non_unique_integrity_error_is_not_mislabelled(
        self, db_session: AsyncSession
    ) -> None:
        """Mirrors test_goals_create.py's identical check on create_goal.

        Calling the service directly with a non-positive target_amount
        bypasses GoalUpdate's `gt=0` validation. The CHECK constraint must
        surface as a plain IntegrityError, not get relabelled as
        GoalCategoryAlreadyExists.
        """
        user = await register(db_session)
        goal = await add_goal(db_session, user)

        with pytest.raises(IntegrityError):
            await update_goal(
                db_session,
                user_id=user.id,
                goal_id=goal.id,
                target_amount=Decimal("-10.00"),
            )
