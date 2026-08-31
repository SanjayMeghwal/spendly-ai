"""Tests for the Goal model.

Like test_models_budget.py, these verify the SCHEMA CONTRACT - what the
DATABASE guarantees - not Python attribute assignment. Every test needs
PostgreSQL: NUMERIC precision, TIMESTAMPTZ, the foreign keys' CASCADE/RESTRICT,
the positive-target check constraint, and the uniqueness index are exactly
what a SQLite stand-in would silently fake or ignore.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Goal, User


def make_user(email: str = "sanjay@example.com") -> User:
    return User(email=email, hashed_password="placeholder-not-a-real-hash")


def make_goal(user_id: uuid.UUID, category_id: uuid.UUID, **overrides: object) -> Goal:
    fields: dict[str, object] = {
        "user_id": user_id,
        "category_id": category_id,
        "target_amount": Decimal("5000.00"),
    }
    fields.update(overrides)
    return Goal(**fields)


@pytest.mark.integration
class TestPersistence:
    """A goal round-trips through PostgreSQL intact."""

    async def test_goal_can_be_saved_and_read_back(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Emergency Fund")
        db_session.add(category)
        await db_session.flush()

        db_session.add(make_goal(user.id, category.id))
        await db_session.commit()

        found = (
            await db_session.execute(select(Goal).where(Goal.category_id == category.id))
        ).scalar_one()

        assert found.user_id == user.id
        assert found.target_amount == Decimal("5000.00")
        assert found.target_date is None

    async def test_target_date_round_trips_when_given(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Emergency Fund")
        db_session.add(category)
        await db_session.flush()

        goal = make_goal(user.id, category.id, target_date=date(2027, 1, 1))
        db_session.add(goal)
        await db_session.commit()
        await db_session.refresh(goal)

        assert goal.target_date == date(2027, 1, 1)

    async def test_database_supplies_the_defaults(self, db_session: AsyncSession) -> None:
        """id and the timestamps must be filled in for us, not left to the caller."""
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Emergency Fund")
        db_session.add(category)
        await db_session.flush()

        goal = make_goal(user.id, category.id)
        db_session.add(goal)
        await db_session.commit()
        await db_session.refresh(goal)

        assert isinstance(goal.id, uuid.UUID)
        assert goal.created_at is not None
        assert goal.updated_at is not None

    async def test_target_amount_keeps_exact_decimal_precision(
        self, db_session: AsyncSession
    ) -> None:
        """NUMERIC, not float - 0.10 must survive a round trip exactly.

        A binary float cannot represent 0.10 exactly, so a column typed as
        float would round-trip this as something like 0.099999999999999645.
        This test fails loudly if NUMERIC is ever swapped for Float.
        """
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Emergency Fund")
        db_session.add(category)
        await db_session.flush()

        goal = make_goal(user.id, category.id, target_amount=Decimal("0.10"))
        db_session.add(goal)
        await db_session.commit()
        await db_session.refresh(goal)

        assert goal.target_amount == Decimal("0.10")

    async def test_timestamps_are_timezone_aware_and_utc(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Emergency Fund")
        db_session.add(category)
        await db_session.flush()

        goal = make_goal(user.id, category.id)
        db_session.add(goal)
        await db_session.commit()
        await db_session.refresh(goal)

        assert goal.created_at.tzinfo is not None, "created_at lost TIMESTAMPTZ"
        assert abs((datetime.now(UTC) - goal.created_at).total_seconds()) < 60


@pytest.mark.integration
class TestForeignKey:
    """The database - not application code - enforces ownership integrity."""

    async def test_goal_requires_an_existing_user(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Emergency Fund")
        db_session.add(category)
        await db_session.flush()

        db_session.add(make_goal(uuid.uuid4(), category.id))

        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "fk_goals_user_id_users" in str(exc.value)

    async def test_goal_requires_an_existing_category(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        db_session.add(make_goal(user.id, uuid.uuid4()))

        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "fk_goals_category_id_categories" in str(exc.value)

    async def test_deleting_a_user_deletes_their_goals(self, db_session: AsyncSession) -> None:
        """ondelete=CASCADE, verified against the real database, not assumed."""
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Emergency Fund")
        db_session.add(category)
        await db_session.flush()

        goal = make_goal(user.id, category.id)
        db_session.add(goal)
        await db_session.commit()

        await db_session.delete(user)
        await db_session.commit()

        remaining = (
            await db_session.execute(select(Goal).where(Goal.id == goal.id))
        ).scalar_one_or_none()

        assert remaining is None

    async def test_deleting_a_category_still_in_use_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        """ondelete="RESTRICT", verified against the real database.

        This is the database-level backstop behind delete_category's own
        in-use check - see app/models/goal.py's category_id.
        """
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Emergency Fund")
        db_session.add(category)
        await db_session.flush()
        db_session.add(make_goal(user.id, category.id))
        await db_session.commit()

        await db_session.delete(category)
        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "fk_goals_category_id_categories" in str(exc.value)


@pytest.mark.integration
class TestConstraints:
    """Constraints the database enforces beyond what Pydantic checks at the API layer."""

    async def test_target_amount_must_be_positive(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Emergency Fund")
        db_session.add(category)
        await db_session.flush()

        db_session.add(make_goal(user.id, category.id, target_amount=Decimal("0.00")))

        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "ck_goals_target_amount_positive" in str(exc.value)

    async def test_category_id_is_unique_per_user(self, db_session: AsyncSession) -> None:
        """A user cannot have two goals for the same category.

        Both would target the same transactions once
        app/services/goal.py matches by category_id, silently splitting
        one category's progress between two goals.
        """
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Emergency Fund")
        db_session.add(category)
        await db_session.flush()

        db_session.add(make_goal(user.id, category.id))
        await db_session.commit()

        db_session.add(make_goal(user.id, category.id))

        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "uq_goals_user_id_category_id" in str(exc.value)

    async def test_same_category_is_allowed_for_different_users(
        self, db_session: AsyncSession
    ) -> None:
        first_user = make_user(email="first@example.com")
        second_user = make_user(email="second@example.com")
        db_session.add_all([first_user, second_user])
        await db_session.flush()
        first_category = Category(user_id=first_user.id, name="Emergency Fund")
        second_category = Category(user_id=second_user.id, name="Emergency Fund")
        db_session.add_all([first_category, second_category])
        await db_session.flush()

        db_session.add(make_goal(first_user.id, first_category.id))
        db_session.add(make_goal(second_user.id, second_category.id))

        await db_session.commit()  # must not raise


class TestRepr:
    def test_repr_includes_id_user_id_and_category_id(self) -> None:
        user_id = uuid.uuid4()
        category_id = uuid.uuid4()
        goal = make_goal(user_id, category_id)

        rendered = repr(goal)

        assert str(user_id) in rendered
        assert str(category_id) in rendered
