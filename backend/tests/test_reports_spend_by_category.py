"""Tests for GET /api/v1/reports/spend-by-category."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Category, Transaction, User
from app.services.category import create_category
from app.services.user import create_user

URL = "/api/v1/reports/spend-by-category"
PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


async def add_category(session: AsyncSession, user: User, *, name: str) -> Category:
    return await create_category(session, user_id=user.id, name=name)


async def add_transaction(
    session: AsyncSession,
    user: User,
    *,
    amount: str,
    category_id: object = None,
    occurred_at: datetime,
) -> None:
    session.add(
        Transaction(
            user_id=user.id,
            amount=Decimal(amount),
            description="test transaction",
            category_id=category_id,
            occurred_at=occurred_at,
        )
    )
    await session.commit()


@pytest.mark.integration
class TestSuccessfulReporting:
    async def test_returns_net_spend_per_category(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        groceries = await add_category(db_session, user, name="Groceries")
        now = datetime.now(UTC)
        await add_transaction(
            db_session, user, amount="-50.00", category_id=groceries.id, occurred_at=now
        )

        response = await db_client.get(URL, headers=auth(user))

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["category_id"] == str(groceries.id)
        assert body[0]["category_name"] == "Groceries"
        assert body[0]["spent"] == "50.00"

    async def test_a_refund_offsets_spend(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user, name="Groceries")
        now = datetime.now(UTC)
        await add_transaction(
            db_session, user, amount="-50.00", category_id=category.id, occurred_at=now
        )
        await add_transaction(
            db_session, user, amount="20.00", category_id=category.id, occurred_at=now
        )

        response = await db_client.get(URL, headers=auth(user))

        assert response.json()[0]["spent"] == "30.00"

    async def test_sorted_largest_spend_first(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        groceries = await add_category(db_session, user, name="Groceries")
        dining = await add_category(db_session, user, name="Dining")
        now = datetime.now(UTC)
        await add_transaction(
            db_session, user, amount="-10.00", category_id=dining.id, occurred_at=now
        )
        await add_transaction(
            db_session, user, amount="-100.00", category_id=groceries.id, occurred_at=now
        )

        response = await db_client.get(URL, headers=auth(user))

        names = [row["category_name"] for row in response.json()]
        assert names == ["Groceries", "Dining"]

    async def test_uncategorized_transactions_form_their_own_bucket(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await add_transaction(
            db_session, user, amount="-15.00", category_id=None, occurred_at=datetime.now(UTC)
        )

        response = await db_client.get(URL, headers=auth(user))

        body = response.json()
        assert len(body) == 1
        assert body[0]["category_id"] is None
        assert body[0]["category_name"] == "Uncategorized"
        assert body[0]["spent"] == "15.00"

    async def test_a_category_with_no_transactions_this_month_is_absent(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Unlike monthly-summary's zero-filled months, an untouched
        category simply doesn't appear - there's no fixed list of
        categories to report on, only what was actually used."""
        user = await register(db_session)
        await add_category(db_session, user, name="Groceries")

        response = await db_client.get(URL, headers=auth(user))

        assert response.json() == []

    async def test_only_the_callers_transactions_count(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_category = await add_category(db_session, grace, name="Groceries")
        await add_transaction(
            db_session,
            grace,
            amount="-50.00",
            category_id=graces_category.id,
            occurred_at=datetime.now(UTC),
        )

        response = await db_client.get(URL, headers=auth(ada))

        assert response.json() == []

    async def test_never_resolves_another_users_category_name(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The one guarantee that matters in the join's ON clause: even a
        transaction whose category_id somehow points at another user's
        category (never possible through the normal API - create/update
        both validate ownership - constructed directly here the same way
        test_categories_delete.py's FK tests do) must not leak that
        user's category name.
        """
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_category = await add_category(db_session, grace, name="Secret")
        await add_transaction(
            db_session,
            ada,
            amount="-50.00",
            category_id=graces_category.id,
            occurred_at=datetime.now(UTC),
        )

        response = await db_client.get(URL, headers=auth(ada))

        body = response.json()
        assert len(body) == 1
        assert body[0]["category_name"] == "Uncategorized"

    async def test_transactions_outside_the_requested_month_are_excluded(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user, name="Groceries")
        await add_transaction(
            db_session,
            user,
            amount="-50.00",
            category_id=category.id,
            occurred_at=datetime(2020, 1, 15, tzinfo=UTC),
        )

        response = await db_client.get(URL, headers=auth(user))

        assert response.json() == []

    async def test_reports_for_an_explicitly_requested_month(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user, name="Groceries")
        await add_transaction(
            db_session,
            user,
            amount="-50.00",
            category_id=category.id,
            occurred_at=datetime(2026, 3, 15, tzinfo=UTC),
        )

        response = await db_client.get(URL, params={"month": "2026-03"}, headers=auth(user))

        assert response.json()[0]["spent"] == "50.00"


@pytest.mark.integration
class TestValidation:
    async def test_rejects_a_malformed_month(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(URL, params={"month": "March-2026"}, headers=auth(user))

        assert response.status_code == 422

    async def test_rejects_an_out_of_range_month_number(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(URL, params={"month": "2026-13"}, headers=auth(user))

        assert response.status_code == 422


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.get(URL)

        assert response.status_code == 401
