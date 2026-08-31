"""Tests for the data logic in migration 94b480cc6d77 (add category_id to
transactions and budgets, backfilled).

See test_migration_categories_backfill.py's module docstring for why this
executes the migration's own UPDATE statements directly against the
standard rolled-back db_session, rather than driving Alembic's
upgrade()/downgrade() machinery.

Transaction and Budget's Python models don't declare category_id yet (that
lands in the refactor commits after this one), so category_id is read back
via raw SQL here, not through the ORM.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Budget, Category, Transaction, User

_BACKFILL_TRANSACTIONS = """
    UPDATE transactions t
    SET category_id = c.id
    FROM categories c
    WHERE c.user_id = t.user_id
      AND t.category IS NOT NULL
      AND lower(c.name) = lower(t.category)
"""

_BACKFILL_BUDGETS = """
    UPDATE budgets b
    SET category_id = c.id
    FROM categories c
    WHERE c.user_id = b.user_id
      AND lower(c.name) = lower(b.category)
"""


def make_user(email: str = "sanjay@example.com") -> User:
    return User(email=email, hashed_password="placeholder-not-a-real-hash")


async def run_backfill(session: AsyncSession) -> None:
    await session.execute(text(_BACKFILL_TRANSACTIONS))
    await session.execute(text(_BACKFILL_BUDGETS))


async def category_id_of(session: AsyncSession, table: str, row_id: uuid.UUID) -> uuid.UUID | None:
    result = (
        await session.execute(
            text(f"SELECT category_id FROM {table} WHERE id = :id"), {"id": row_id}
        )
    ).scalar_one()
    return uuid.UUID(str(result)) if result is not None else None


@pytest.mark.integration
class TestBackfill:
    async def test_transaction_category_id_points_at_the_matching_category(
        self, db_session: AsyncSession
    ) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Groceries")
        db_session.add(category)
        await db_session.flush()
        transaction = Transaction(
            user_id=user.id,
            amount=Decimal("-1"),
            description="x",
            category="Groceries",
            occurred_at=datetime.now(UTC),
        )
        db_session.add(transaction)
        await db_session.flush()

        await run_backfill(db_session)

        assert await category_id_of(db_session, "transactions", transaction.id) == category.id

    async def test_matching_is_case_insensitive(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Groceries")
        db_session.add(category)
        await db_session.flush()
        transaction = Transaction(
            user_id=user.id,
            amount=Decimal("-1"),
            description="x",
            category="groceries",
            occurred_at=datetime.now(UTC),
        )
        db_session.add(transaction)
        await db_session.flush()

        await run_backfill(db_session)

        assert await category_id_of(db_session, "transactions", transaction.id) == category.id

    async def test_null_transaction_category_stays_null(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        transaction = Transaction(
            user_id=user.id,
            amount=Decimal("-1"),
            description="x",
            category=None,
            occurred_at=datetime.now(UTC),
        )
        db_session.add(transaction)
        await db_session.flush()

        await run_backfill(db_session)

        assert await category_id_of(db_session, "transactions", transaction.id) is None

    async def test_budget_category_id_points_at_the_matching_category(
        self, db_session: AsyncSession
    ) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        category = Category(user_id=user.id, name="Groceries")
        db_session.add(category)
        await db_session.flush()
        budget = Budget(user_id=user.id, category="Groceries", limit_amount=Decimal("500"))
        db_session.add(budget)
        await db_session.flush()

        await run_backfill(db_session)

        assert await category_id_of(db_session, "budgets", budget.id) == category.id

    async def test_matching_is_scoped_per_user(self, db_session: AsyncSession) -> None:
        """A transaction must never be backfilled to a DIFFERENT user's
        category of the same name."""
        first_user = make_user(email="first@example.com")
        second_user = make_user(email="second@example.com")
        db_session.add_all([first_user, second_user])
        await db_session.flush()
        first_category = Category(user_id=first_user.id, name="Groceries")
        second_category = Category(user_id=second_user.id, name="Groceries")
        db_session.add_all([first_category, second_category])
        await db_session.flush()
        transaction = Transaction(
            user_id=second_user.id,
            amount=Decimal("-1"),
            description="x",
            category="Groceries",
            occurred_at=datetime.now(UTC),
        )
        db_session.add(transaction)
        await db_session.flush()

        await run_backfill(db_session)

        assert (
            await category_id_of(db_session, "transactions", transaction.id) == second_category.id
        )
