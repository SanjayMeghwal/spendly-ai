"""Tests for the data logic in migration a6f87051f1e6 (populate categories
from existing transaction and budget values).

WHY THIS TEST DOESN'T RUN ALEMBIC ITSELF: every other test in this suite
(see conftest.py's db_session fixture) runs against the schema at whatever
revision `alembic upgrade head` last left the shared dev database at, inside
a transaction that's always rolled back - there is no precedent in this
project for a test that drives Alembic's upgrade()/downgrade() through its
global MigrationContext, and building one would mean either mutating the
real dev database's migration state directly (unsafe - this is the same
database every other test and the running app use) or wiring up Alembic's
process-global `context`/`op` proxies by hand, which is meaningfully more
machinery and fragility than anything else in this suite for a migration
that will never run again once applied.

Instead, this executes the SAME SQL the migration's upgrade() runs (copied
here deliberately, not imported - alembic/versions/ filenames aren't valid
Python identifiers, so importing from one requires importlib path-loading
gymnastics that would be more fragile than a small, clearly-labelled copy)
against the standard rolled-back db_session every other test already uses.
This verifies the SQL's actual logic - the canonical-casing tie-break is
the one part of this migration with real room to get subtly wrong - without
inventing a new, riskier way to test migrations.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Budget, Category, Transaction, User

_INSERT_FROM_BUDGETS = """
    INSERT INTO categories (id, user_id, name, created_at, updated_at)
    SELECT gen_random_uuid(), b.user_id, b.category, now(), now()
    FROM (
        SELECT DISTINCT ON (user_id, lower(category)) user_id, category
        FROM budgets
        ORDER BY user_id, lower(category)
    ) b
"""

_INSERT_FROM_TRANSACTIONS = """
    INSERT INTO categories (id, user_id, name, created_at, updated_at)
    SELECT gen_random_uuid(), t.user_id, t.category, now(), now()
    FROM (
        SELECT DISTINCT ON (user_id, lower(category)) user_id, category
        FROM transactions
        WHERE category IS NOT NULL
        ORDER BY user_id, lower(category)
    ) t
    WHERE NOT EXISTS (
        SELECT 1 FROM categories c
        WHERE c.user_id = t.user_id AND lower(c.name) = lower(t.category)
    )
"""


def make_user(email: str = "sanjay@example.com") -> User:
    return User(email=email, hashed_password="placeholder-not-a-real-hash")


async def run_backfill(session: AsyncSession) -> None:
    await session.execute(text(_INSERT_FROM_BUDGETS))
    await session.execute(text(_INSERT_FROM_TRANSACTIONS))


@pytest.mark.integration
class TestBackfill:
    async def test_creates_one_category_per_distinct_budget(self, db_session: AsyncSession) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        db_session.add(Budget(user_id=user.id, category="Groceries", limit_amount=Decimal("1")))
        db_session.add(Budget(user_id=user.id, category="Dining", limit_amount=Decimal("1")))
        await db_session.flush()

        await run_backfill(db_session)

        names = {
            row.name
            for row in (
                await db_session.execute(
                    text("SELECT name FROM categories WHERE user_id = :uid"), {"uid": user.id}
                )
            )
        }
        assert names == {"Groceries", "Dining"}

    async def test_creates_one_category_per_distinct_transaction(
        self, db_session: AsyncSession
    ) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        db_session.add(
            Transaction(
                user_id=user.id,
                amount=Decimal("-1"),
                description="x",
                category="Utilities",
                occurred_at=datetime.now(UTC),
            )
        )
        await db_session.flush()

        await run_backfill(db_session)

        names = {
            row.name
            for row in (
                await db_session.execute(
                    text("SELECT name FROM categories WHERE user_id = :uid"), {"uid": user.id}
                )
            )
        }
        assert names == {"Utilities"}

    async def test_null_transaction_category_creates_nothing(
        self, db_session: AsyncSession
    ) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        db_session.add(
            Transaction(
                user_id=user.id,
                amount=Decimal("-1"),
                description="x",
                category=None,
                occurred_at=datetime.now(UTC),
            )
        )
        await db_session.flush()

        await run_backfill(db_session)

        count = (
            await db_session.execute(
                text("SELECT count(*) FROM categories WHERE user_id = :uid"), {"uid": user.id}
            )
        ).scalar_one()
        assert count == 0

    async def test_budget_and_transaction_agreeing_on_casing_produce_one_category(
        self, db_session: AsyncSession
    ) -> None:
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        db_session.add(Budget(user_id=user.id, category="Groceries", limit_amount=Decimal("1")))
        db_session.add(
            Transaction(
                user_id=user.id,
                amount=Decimal("-1"),
                description="x",
                category="Groceries",
                occurred_at=datetime.now(UTC),
            )
        )
        await db_session.flush()

        await run_backfill(db_session)

        rows = (
            await db_session.execute(
                text("SELECT name FROM categories WHERE user_id = :uid"), {"uid": user.id}
            )
        ).all()
        assert len(rows) == 1

    async def test_budgets_casing_wins_over_transactions_casing(
        self, db_session: AsyncSession
    ) -> None:
        """The one non-obvious rule this migration implements: when a budget
        and a transaction disagree on casing for "the same" category
        (matched case-insensitively), the budget's casing is canonical -
        a budget's category is a deliberate choice, a transaction's is
        whatever was typed at entry time.
        """
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        db_session.add(Budget(user_id=user.id, category="Groceries", limit_amount=Decimal("1")))
        db_session.add(
            Transaction(
                user_id=user.id,
                amount=Decimal("-1"),
                description="x",
                category="groceries",
                occurred_at=datetime.now(UTC),
            )
        )
        await db_session.flush()

        await run_backfill(db_session)

        name = (
            await db_session.execute(
                text("SELECT name FROM categories WHERE user_id = :uid"), {"uid": user.id}
            )
        ).scalar_one()
        assert name == "Groceries"

    async def test_categories_are_scoped_per_user(self, db_session: AsyncSession) -> None:
        first_user = make_user(email="first@example.com")
        second_user = make_user(email="second@example.com")
        db_session.add_all([first_user, second_user])
        await db_session.flush()
        db_session.add(
            Budget(user_id=first_user.id, category="Groceries", limit_amount=Decimal("1"))
        )
        db_session.add(
            Budget(user_id=second_user.id, category="Groceries", limit_amount=Decimal("1"))
        )
        await db_session.flush()

        await run_backfill(db_session)

        count = (
            await db_session.execute(
                text("SELECT count(*) FROM categories WHERE name = 'Groceries'")
            )
        ).scalar_one()
        assert count == 2

    async def test_backfilled_categories_satisfy_the_category_model(
        self, db_session: AsyncSession
    ) -> None:
        """The rows this raw SQL inserts must be indistinguishable from ones
        the ORM would have created - readable back through the Category
        model, not just present in the table."""
        user = make_user()
        db_session.add(user)
        await db_session.flush()
        db_session.add(Budget(user_id=user.id, category="Groceries", limit_amount=Decimal("1")))
        await db_session.flush()

        await run_backfill(db_session)

        category = (
            await db_session.execute(
                text("SELECT id FROM categories WHERE user_id = :uid"), {"uid": user.id}
            )
        ).scalar_one()
        found = await db_session.get(Category, uuid.UUID(str(category)))
        assert found is not None
        assert found.name == "Groceries"
        assert found.created_at is not None
