"""Tests for the Budget model.

Like test_models_transaction.py, these verify the SCHEMA CONTRACT - what the
DATABASE guarantees - not Python attribute assignment. Every test needs
PostgreSQL: NUMERIC precision, TIMESTAMPTZ, the foreign key's CASCADE, the
positive-limit check constraint, and the case-insensitive uniqueness index
are exactly what a SQLite stand-in would silently fake or ignore.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Budget, User


def make_user(email: str = "sanjay@example.com") -> User:
    return User(email=email, hashed_password="placeholder-not-a-real-hash")


def make_budget(user_id: uuid.UUID, **overrides: object) -> Budget:
    fields: dict[str, object] = {
        "user_id": user_id,
        "category": "Groceries",
        "limit_amount": Decimal("500.00"),
    }
    fields.update(overrides)
    return Budget(**fields)


@pytest.mark.integration
class TestPersistence:
    """A budget round-trips through PostgreSQL intact."""

    async def test_budget_can_be_saved_and_read_back(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        db_session.add(make_budget(user.id, category="Groceries"))
        await db_session.commit()

        found = (
            await db_session.execute(select(Budget).where(Budget.category == "Groceries"))
        ).scalar_one()

        assert found.user_id == user.id
        assert found.limit_amount == Decimal("500.00")

    async def test_database_supplies_the_defaults(self, db_session: AsyncSession) -> None:
        """id and the timestamps must be filled in for us, not left to the caller."""
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        budget = make_budget(user.id)
        db_session.add(budget)
        await db_session.commit()
        await db_session.refresh(budget)

        assert isinstance(budget.id, uuid.UUID)
        assert budget.created_at is not None
        assert budget.updated_at is not None

    async def test_limit_amount_keeps_exact_decimal_precision(
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

        budget = make_budget(user.id, limit_amount=Decimal("0.10"))
        db_session.add(budget)
        await db_session.commit()
        await db_session.refresh(budget)

        assert budget.limit_amount == Decimal("0.10")

    async def test_timestamps_are_timezone_aware_and_utc(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        budget = make_budget(user.id)
        db_session.add(budget)
        await db_session.commit()
        await db_session.refresh(budget)

        assert budget.created_at.tzinfo is not None, "created_at lost TIMESTAMPTZ"
        assert abs((datetime.now(UTC) - budget.created_at).total_seconds()) < 60


@pytest.mark.integration
class TestForeignKey:
    """The database - not application code - enforces ownership integrity."""

    async def test_budget_requires_an_existing_user(self, db_session: AsyncSession) -> None:
        db_session.add(make_budget(uuid.uuid4()))

        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "fk_budgets_user_id_users" in str(exc.value)

    async def test_deleting_a_user_deletes_their_budgets(self, db_session: AsyncSession) -> None:
        """ondelete=CASCADE, verified against the real database, not assumed."""
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        budget = make_budget(user.id)
        db_session.add(budget)
        await db_session.commit()

        await db_session.delete(user)
        await db_session.commit()

        remaining = (
            await db_session.execute(select(Budget).where(Budget.id == budget.id))
        ).scalar_one_or_none()

        assert remaining is None


@pytest.mark.integration
class TestConstraints:
    """Constraints the database enforces beyond what Pydantic checks at the API layer."""

    async def test_limit_amount_must_be_positive(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        db_session.add(make_budget(user.id, limit_amount=Decimal("0.00")))

        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "ck_budgets_limit_amount_positive" in str(exc.value)

    async def test_category_is_unique_per_user_case_insensitively(
        self, db_session: AsyncSession
    ) -> None:
        """ "Groceries" and "groceries" must not coexist as two budgets for one user.

        Both would match the same transactions once app/services/budget.py
        does its case-insensitive comparison, silently splitting one
        category's spend between two limits.
        """
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        db_session.add(make_budget(user.id, category="Groceries"))
        await db_session.commit()

        db_session.add(make_budget(user.id, category="groceries"))

        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "uq_budgets_user_id_category_lower" in str(exc.value)

    async def test_same_category_is_allowed_for_different_users(
        self, db_session: AsyncSession
    ) -> None:
        first_user = make_user(email="first@example.com")
        second_user = make_user(email="second@example.com")
        db_session.add_all([first_user, second_user])
        await db_session.flush()

        db_session.add(make_budget(first_user.id, category="Groceries"))
        db_session.add(make_budget(second_user.id, category="Groceries"))

        await db_session.commit()  # must not raise


class TestRepr:
    def test_repr_includes_id_user_id_and_category(self) -> None:
        user_id = uuid.uuid4()
        budget = make_budget(user_id, category="Groceries")

        rendered = repr(budget)

        assert str(user_id) in rendered
        assert "Groceries" in rendered
