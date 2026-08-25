"""Tests for GET /api/v1/transactions."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import User
from app.services.transaction import create_transaction
from app.services.user import create_user

TRANSACTIONS_URL = "/api/v1/transactions"
PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


async def add_transaction(
    session: AsyncSession, user: User, *, days_ago: int, description: str = "Item"
) -> None:
    await create_transaction(
        session,
        user_id=user.id,
        amount=Decimal("-1.00"),
        description=description,
        occurred_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


@pytest.mark.integration
class TestSuccessfulListing:
    async def test_returns_only_the_callers_transactions(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The single most important guarantee in this feature.

        Mirrors test_me.py's cross-user check: one person's transactions must
        never be served to another, regardless of query parameters.
        """
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        await add_transaction(db_session, ada, days_ago=0, description="Ada's coffee")
        await add_transaction(db_session, grace, days_ago=0, description="Grace's coffee")

        response = await db_client.get(TRANSACTIONS_URL, headers=auth(ada))

        assert response.status_code == 200
        descriptions = [t["description"] for t in response.json()]
        assert descriptions == ["Ada's coffee"]

    async def test_returns_empty_list_when_the_user_has_none(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(TRANSACTIONS_URL, headers=auth(user))

        assert response.status_code == 200
        assert response.json() == []

    async def test_orders_most_recently_occurred_first(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await add_transaction(db_session, user, days_ago=5, description="oldest")
        await add_transaction(db_session, user, days_ago=0, description="newest")
        await add_transaction(db_session, user, days_ago=2, description="middle")

        response = await db_client.get(TRANSACTIONS_URL, headers=auth(user))

        descriptions = [t["description"] for t in response.json()]
        assert descriptions == ["newest", "middle", "oldest"]


@pytest.mark.integration
class TestPagination:
    async def test_limit_restricts_page_size(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        for day in range(5):
            await add_transaction(db_session, user, days_ago=day)

        response = await db_client.get(TRANSACTIONS_URL, params={"limit": 2}, headers=auth(user))

        assert len(response.json()) == 2

    async def test_offset_skips_the_first_page(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        for day in range(3):
            await add_transaction(db_session, user, days_ago=day, description=f"day-{day}")

        first_page = await db_client.get(
            TRANSACTIONS_URL, params={"limit": 1, "offset": 0}, headers=auth(user)
        )
        second_page = await db_client.get(
            TRANSACTIONS_URL, params={"limit": 1, "offset": 1}, headers=auth(user)
        )

        assert first_page.json()[0]["description"] != second_page.json()[0]["description"]

    async def test_limit_above_the_maximum_is_rejected(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """le=100 caps response size regardless of what a client requests."""
        user = await register(db_session)

        response = await db_client.get(TRANSACTIONS_URL, params={"limit": 101}, headers=auth(user))

        assert response.status_code == 422

    async def test_negative_offset_is_rejected(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(TRANSACTIONS_URL, params={"offset": -1}, headers=auth(user))

        assert response.status_code == 422


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.get(TRANSACTIONS_URL)

        assert response.status_code == 401
