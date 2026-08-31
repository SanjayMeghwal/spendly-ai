"""Tests for GET /api/v1/reports/monthly-summary."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Transaction, User
from app.services.user import create_user

URL = "/api/v1/reports/monthly-summary"
PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


def _months_ago(n: int) -> datetime:
    """The 15th of the month n months before the current UTC month.

    The 15th, not the 1st or 28th, so this never lands on a month boundary
    regardless of which month "now" happens to be - a fixed calendar-safe
    midpoint.
    """
    now = datetime.now(UTC)
    year, month = now.year, now.month
    for _ in range(n):
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return datetime(year, month, 15, tzinfo=UTC)


async def add_transaction(
    session: AsyncSession, user: User, *, amount: str, occurred_at: datetime
) -> None:
    session.add(
        Transaction(
            user_id=user.id,
            amount=Decimal(amount),
            description="test transaction",
            occurred_at=occurred_at,
        )
    )
    await session.commit()


@pytest.mark.integration
class TestSuccessfulReporting:
    async def test_current_month_income_expenses_and_net(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await add_transaction(db_session, user, amount="-50.00", occurred_at=_months_ago(0))
        await add_transaction(db_session, user, amount="2000.00", occurred_at=_months_ago(0))

        response = await db_client.get(URL, params={"months": 1}, headers=auth(user))

        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["income"] == "2000.00"
        assert body[0]["expenses"] == "50.00"
        assert body[0]["net"] == "1950.00"

    async def test_a_quiet_month_is_zero_filled(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The middle month has no transactions at all - it must still
        appear as a zero row, not be missing from the response."""
        user = await register(db_session)
        await add_transaction(db_session, user, amount="-10.00", occurred_at=_months_ago(2))
        await add_transaction(db_session, user, amount="-20.00", occurred_at=_months_ago(0))

        response = await db_client.get(URL, params={"months": 3}, headers=auth(user))

        body = response.json()
        assert len(body) == 3
        middle = body[1]
        assert middle["income"] == "0"
        assert middle["expenses"] == "0"
        assert middle["net"] == "0"

    async def test_ordered_oldest_to_newest(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await add_transaction(db_session, user, amount="-10.00", occurred_at=_months_ago(1))
        await add_transaction(db_session, user, amount="-20.00", occurred_at=_months_ago(0))

        response = await db_client.get(URL, params={"months": 2}, headers=auth(user))

        months = [row["month"] for row in response.json()]
        assert months == sorted(months)

    async def test_only_the_requested_window_is_included(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        await add_transaction(db_session, user, amount="-10.00", occurred_at=_months_ago(12))
        await add_transaction(db_session, user, amount="-20.00", occurred_at=_months_ago(0))

        response = await db_client.get(URL, params={"months": 1}, headers=auth(user))

        body = response.json()
        assert len(body) == 1
        assert body[0]["expenses"] == "20.00"

    async def test_defaults_to_six_months(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(URL, headers=auth(user))

        assert len(response.json()) == 6

    async def test_only_the_callers_transactions_count(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        await add_transaction(db_session, grace, amount="-500.00", occurred_at=_months_ago(0))

        response = await db_client.get(URL, params={"months": 1}, headers=auth(ada))

        body = response.json()
        assert len(body) == 1
        assert body[0]["expenses"] == "0"


@pytest.mark.integration
class TestValidation:
    async def test_rejects_zero_months(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(URL, params={"months": 0}, headers=auth(user))

        assert response.status_code == 422

    async def test_rejects_more_than_twenty_four_months(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(URL, params={"months": 25}, headers=auth(user))

        assert response.status_code == 422


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.get(URL)

        assert response.status_code == 401
