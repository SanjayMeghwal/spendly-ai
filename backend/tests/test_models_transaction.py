"""Tests for the Transaction model.

Like test_models_user.py, these verify the SCHEMA CONTRACT - what the
DATABASE guarantees - not Python attribute assignment. Every test needs
PostgreSQL: NUMERIC precision, TIMESTAMPTZ, and the foreign key's CASCADE
are exactly what a SQLite stand-in would silently fake.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Transaction, User


def make_user(email: str = "sanjay@example.com") -> User:
    return User(email=email, hashed_password="placeholder-not-a-real-hash")


def make_transaction(user_id: uuid.UUID, **overrides: object) -> Transaction:
    fields: dict[str, object] = {
        "user_id": user_id,
        "amount": Decimal("-12.50"),
        "description": "Grocery store",
        "occurred_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return Transaction(**fields)


@pytest.mark.integration
class TestPersistence:
    """A transaction round-trips through PostgreSQL intact."""

    async def test_transaction_can_be_saved_and_read_back(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        db_session.add(make_transaction(user.id, description="Grocery store"))
        await db_session.commit()

        found = (
            await db_session.execute(
                select(Transaction).where(Transaction.description == "Grocery store")
            )
        ).scalar_one()

        assert found.user_id == user.id
        assert found.amount == Decimal("-12.50")
        assert found.category_id is None
        assert found.notes is None

    async def test_database_supplies_the_defaults(self, db_session: AsyncSession) -> None:
        """id and the timestamps must be filled in for us, not left to the caller."""
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        transaction = make_transaction(user.id)
        db_session.add(transaction)
        await db_session.commit()
        await db_session.refresh(transaction)

        assert isinstance(transaction.id, uuid.UUID)
        assert transaction.created_at is not None
        assert transaction.updated_at is not None

    async def test_amount_keeps_exact_decimal_precision(self, db_session: AsyncSession) -> None:
        """NUMERIC, not float - 0.10 must survive a round trip exactly.

        A binary float cannot represent 0.10 exactly, so a column typed as
        float would round-trip this as something like 0.099999999999999645.
        This test fails loudly if NUMERIC is ever swapped for Float.
        """
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        transaction = make_transaction(user.id, amount=Decimal("0.10"))
        db_session.add(transaction)
        await db_session.commit()
        await db_session.refresh(transaction)

        assert transaction.amount == Decimal("0.10")

    async def test_timestamps_are_timezone_aware_and_utc(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        transaction = make_transaction(user.id)
        db_session.add(transaction)
        await db_session.commit()
        await db_session.refresh(transaction)

        assert transaction.created_at.tzinfo is not None, "created_at lost TIMESTAMPTZ"
        assert abs((datetime.now(UTC) - transaction.created_at).total_seconds()) < 60


@pytest.mark.integration
class TestForeignKey:
    """The database - not application code - enforces ownership integrity."""

    async def test_transaction_requires_an_existing_user(self, db_session: AsyncSession) -> None:
        """A transaction naming a nonexistent user must be rejected.

        Without this constraint, a bug that mishandles a deleted or
        mistyped user id would silently create an orphaned financial record
        instead of failing.
        """
        db_session.add(make_transaction(uuid.uuid4()))

        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "fk_transactions_user_id_users" in str(exc.value)

    async def test_deleting_a_user_deletes_their_transactions(
        self, db_session: AsyncSession
    ) -> None:
        """ondelete=CASCADE, verified against the real database, not assumed.

        This is the guarantee that a hand-run `DELETE FROM users` in psql -
        bypassing the ORM entirely - still cannot leave orphaned transactions
        behind.
        """
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        transaction = make_transaction(user.id)
        db_session.add(transaction)
        await db_session.commit()

        await db_session.delete(user)
        await db_session.commit()

        remaining = (
            await db_session.execute(select(Transaction).where(Transaction.id == transaction.id))
        ).scalar_one_or_none()

        assert remaining is None

    async def test_transaction_requires_an_existing_category(
        self, db_session: AsyncSession
    ) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()

        db_session.add(make_transaction(user.id, category_id=uuid.uuid4()))

        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "fk_transactions_category_id_categories" in str(exc.value)

    async def test_category_id_round_trips(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Groceries")
        db_session.add(category)
        await db_session.flush()

        transaction = make_transaction(user.id, category_id=category.id)
        db_session.add(transaction)
        await db_session.commit()
        await db_session.refresh(transaction)

        assert transaction.category_id == category.id

    async def test_deleting_a_category_still_in_use_is_rejected(
        self, db_session: AsyncSession
    ) -> None:
        """ondelete="RESTRICT", verified against the real database.

        This is the database-level backstop behind delete_category's own
        in-use check - see app/models/transaction.py's category_id.
        """
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Groceries")
        db_session.add(category)
        await db_session.flush()
        db_session.add(make_transaction(user.id, category_id=category.id))
        await db_session.commit()

        await db_session.delete(category)
        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "fk_transactions_category_id_categories" in str(exc.value)


class TestRepr:
    def test_repr_includes_id_user_id_and_amount(self) -> None:
        user_id = uuid.uuid4()
        transaction = make_transaction(user_id)

        rendered = repr(transaction)

        assert str(user_id) in rendered
        assert "-12.50" in rendered
