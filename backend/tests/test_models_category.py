"""Tests for the Category model.

Like test_models_budget.py, these verify the SCHEMA CONTRACT - what the
DATABASE guarantees - not Python attribute assignment. Every test needs
PostgreSQL: TIMESTAMPTZ, the foreign key's CASCADE, and the case-insensitive
uniqueness index are exactly what a SQLite stand-in would silently fake or
ignore.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, User


def make_user(email: str = "sanjay@example.com") -> User:
    return User(email=email, hashed_password="placeholder-not-a-real-hash")


def make_category(user_id: uuid.UUID, **overrides: object) -> Category:
    fields: dict[str, object] = {"user_id": user_id, "name": "Groceries"}
    fields.update(overrides)
    return Category(**fields)


@pytest.mark.integration
class TestPersistence:
    """A category round-trips through PostgreSQL intact."""

    async def test_category_can_be_saved_and_read_back(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        db_session.add(make_category(user.id, name="Groceries"))
        await db_session.commit()

        found = (
            await db_session.execute(select(Category).where(Category.name == "Groceries"))
        ).scalar_one()

        assert found.user_id == user.id
        assert found.name == "Groceries"

    async def test_database_supplies_the_defaults(self, db_session: AsyncSession) -> None:
        """id and the timestamps must be filled in for us, not left to the caller."""
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        category = make_category(user.id)
        db_session.add(category)
        await db_session.commit()
        await db_session.refresh(category)

        assert isinstance(category.id, uuid.UUID)
        assert category.created_at is not None
        assert category.updated_at is not None

    async def test_timestamps_are_timezone_aware_and_utc(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        category = make_category(user.id)
        db_session.add(category)
        await db_session.commit()
        await db_session.refresh(category)

        assert category.created_at.tzinfo is not None, "created_at lost TIMESTAMPTZ"
        assert abs((datetime.now(UTC) - category.created_at).total_seconds()) < 60


@pytest.mark.integration
class TestForeignKey:
    """The database - not application code - enforces ownership integrity."""

    async def test_category_requires_an_existing_user(self, db_session: AsyncSession) -> None:
        db_session.add(make_category(uuid.uuid4()))

        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "fk_categories_user_id_users" in str(exc.value)

    async def test_deleting_a_user_deletes_their_categories(self, db_session: AsyncSession) -> None:
        """ondelete=CASCADE, verified against the real database, not assumed."""
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        category = make_category(user.id)
        db_session.add(category)
        await db_session.commit()

        await db_session.delete(user)
        await db_session.commit()

        remaining = (
            await db_session.execute(select(Category).where(Category.id == category.id))
        ).scalar_one_or_none()

        assert remaining is None


@pytest.mark.integration
class TestConstraints:
    async def test_name_is_unique_per_user_case_insensitively(
        self, db_session: AsyncSession
    ) -> None:
        """ "Groceries" and "groceries" must not coexist for one user.

        Both would be indistinguishable to a human reading a category list,
        and once transactions/budgets reference a category by id, having
        two ids for "the same" name defeats the entire point of this
        milestone.
        """
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        db_session.add(make_category(user.id, name="Groceries"))
        await db_session.commit()

        db_session.add(make_category(user.id, name="groceries"))

        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "uq_categories_user_id_name_lower" in str(exc.value)

    async def test_same_name_is_allowed_for_different_users(self, db_session: AsyncSession) -> None:
        first_user = make_user(email="first@example.com")
        second_user = make_user(email="second@example.com")
        db_session.add_all([first_user, second_user])
        await db_session.flush()

        db_session.add(make_category(first_user.id, name="Groceries"))
        db_session.add(make_category(second_user.id, name="Groceries"))

        await db_session.commit()  # must not raise


class TestRepr:
    def test_repr_includes_id_user_id_and_name(self) -> None:
        user_id = uuid.uuid4()
        category = make_category(user_id, name="Groceries")

        rendered = repr(category)

        assert str(user_id) in rendered
        assert "Groceries" in rendered
