"""Tests for scripts/backfill_embeddings.py.

Exercises backfill_embeddings(session) directly against real PostgreSQL,
same as any other service-shaped function in this codebase - it is not an
HTTP test because the script has no route, and there is nothing here an ORM
mock would catch better than the real embedding column.

Deliberately NOT scoped to one user - see the module docstring on why this
script, unlike everything else in services/, is allowed to walk every user's
data.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transaction, User
from app.services.category import create_category
from app.services.user import create_user
from scripts.backfill_embeddings import backfill_embeddings

PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


async def add_transaction_without_embedding(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    description: str = "Grocery store",
    amount: Decimal = Decimal("-42.50"),
    category_id: uuid.UUID | None = None,
) -> Transaction:
    """Insert a Transaction the same way pre-M10 data, or a row that failed
    to embed at write time, would actually look: a real committed row with
    embedding IS NULL. Bypasses create_transaction deliberately - that
    function always tries to embed, which is exactly the case this test
    module needs to NOT be true.
    """
    transaction = Transaction(
        user_id=user_id,
        amount=amount,
        description=description,
        occurred_at=datetime(2026, 1, 15, tzinfo=UTC),
        category_id=category_id,
    )
    session.add(transaction)
    await session.commit()
    await session.refresh(transaction)
    return transaction


@pytest.mark.integration
class TestBackfillEmbeddings:
    async def test_embeds_a_transaction_missing_one(self, db_session: AsyncSession) -> None:
        user = await register(db_session)
        transaction = await add_transaction_without_embedding(db_session, user_id=user.id)

        embedded, still_missing = await backfill_embeddings(db_session)

        assert (embedded, still_missing) == (1, 0)
        await db_session.refresh(transaction)
        assert transaction.embedding is not None
        assert len(transaction.embedding) == 768

    async def test_leaves_an_already_embedded_transaction_untouched(
        self, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        transaction = await add_transaction_without_embedding(db_session, user_id=user.id)
        transaction.embedding = [0.2] * 768
        await db_session.commit()

        embedded, still_missing = await backfill_embeddings(db_session)

        assert (embedded, still_missing) == (0, 0)
        await db_session.refresh(transaction)
        assert transaction.embedding == [0.2] * 768

    async def test_includes_the_category_name_when_the_transaction_has_one(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = await register(db_session)
        category = await create_category(db_session, user_id=user.id, name="Groceries")
        await add_transaction_without_embedding(
            db_session, user_id=user.id, category_id=category.id
        )

        seen_category_names: list[str | None] = []

        async def spy(
            *, description: str, amount: Decimal, category_name: str | None
        ) -> list[float]:
            seen_category_names.append(category_name)
            return [0.1] * 768

        monkeypatch.setattr("scripts.backfill_embeddings.embed_transaction_or_none", spy)

        await backfill_embeddings(db_session)

        assert seen_category_names == ["Groceries"]

    async def test_uncategorized_transaction_embeds_with_no_category_name(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = await register(db_session)
        await add_transaction_without_embedding(db_session, user_id=user.id, category_id=None)

        seen_category_names: list[str | None] = []

        async def spy(
            *, description: str, amount: Decimal, category_name: str | None
        ) -> list[float]:
            seen_category_names.append(category_name)
            return [0.1] * 768

        monkeypatch.setattr("scripts.backfill_embeddings.embed_transaction_or_none", spy)

        await backfill_embeddings(db_session)

        assert seen_category_names == [None]

    async def test_counts_a_row_as_still_missing_when_ollama_stays_unreachable(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = await register(db_session)
        transaction = await add_transaction_without_embedding(db_session, user_id=user.id)

        async def always_fails(**kwargs: object) -> None:
            return None

        monkeypatch.setattr("scripts.backfill_embeddings.embed_transaction_or_none", always_fails)

        embedded, still_missing = await backfill_embeddings(db_session)

        assert (embedded, still_missing) == (0, 1)
        await db_session.refresh(transaction)
        assert transaction.embedding is None

    async def test_returns_zero_zero_when_nothing_needs_backfilling(
        self, db_session: AsyncSession
    ) -> None:
        embedded, still_missing = await backfill_embeddings(db_session)

        assert (embedded, still_missing) == (0, 0)

    async def test_spans_every_user_not_just_one(self, db_session: AsyncSession) -> None:
        """Deliberately NOT scoped to a single user - unlike every service in
        services/transaction.py, this script is a whole-database maintenance
        pass, not a request served on one user's behalf. See the module
        docstring for why that's the correct trust boundary here.
        """
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        await add_transaction_without_embedding(db_session, user_id=ada.id)
        await add_transaction_without_embedding(db_session, user_id=grace.id)

        embedded, still_missing = await backfill_embeddings(db_session)

        assert (embedded, still_missing) == (2, 0)
