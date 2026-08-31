"""Tests for services/category.py's get_category_names.

Not owned by any one endpoint - it's the shared bulk-lookup helper both
routes/transactions.py and routes/budgets.py use to denormalize
`category_name` into their responses - so it gets its own test module
rather than living inside test_categories_*.py or test_transactions_*.py.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.category import create_category, get_category_names
from app.services.user import create_user

PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


@pytest.mark.integration
class TestGetCategoryNames:
    async def test_resolves_requested_ids_to_names(self, db_session: AsyncSession) -> None:
        user = await register(db_session)
        groceries = await create_category(db_session, user_id=user.id, name="Groceries")
        dining = await create_category(db_session, user_id=user.id, name="Dining")

        names = await get_category_names(
            db_session, user_id=user.id, category_ids={groceries.id, dining.id}
        )

        assert names == {groceries.id: "Groceries", dining.id: "Dining"}

    async def test_empty_category_ids_returns_empty_dict_without_querying(
        self, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        names = await get_category_names(db_session, user_id=user.id, category_ids=set())

        assert names == {}

    async def test_never_resolves_another_users_category(self, db_session: AsyncSession) -> None:
        """The one guarantee that matters here: even if a caller somehow
        holds another user's category_id, this must not hand back that
        user's category name.
        """
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_category = await create_category(db_session, user_id=grace.id, name="Secret")

        names = await get_category_names(
            db_session, user_id=ada.id, category_ids={graces_category.id}
        )

        assert names == {}

    async def test_a_nonexistent_id_is_simply_absent(self, db_session: AsyncSession) -> None:
        user = await register(db_session)

        names = await get_category_names(db_session, user_id=user.id, category_ids={uuid.uuid4()})

        assert names == {}
