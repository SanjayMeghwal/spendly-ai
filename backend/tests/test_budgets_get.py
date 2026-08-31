"""Tests for GET /api/v1/budgets/{id}."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Budget, Transaction, User
from app.services.budget import create_budget
from app.services.user import create_user

PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


async def add_budget(session: AsyncSession, user: User, *, category: str = "Groceries") -> Budget:
    return await create_budget(
        session, user_id=user.id, category=category, limit_amount=Decimal("500.00")
    )


def url(budget_id: object) -> str:
    return f"/api/v1/budgets/{budget_id}"


@pytest.mark.integration
class TestSuccessfulRetrieval:
    async def test_returns_the_budget(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        response = await db_client.get(url(budget.id), headers=auth(user))

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(budget.id)
        assert body["category"] == "Groceries"

    async def test_reports_spend_for_the_requested_month(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)
        db_session.add(
            Transaction(
                user_id=user.id,
                amount=Decimal("-50.00"),
                description="test transaction",
                category="Groceries",
                occurred_at=datetime(2026, 3, 15, tzinfo=UTC),
            )
        )
        await db_session.commit()

        response = await db_client.get(
            url(budget.id), params={"month": "2026-03"}, headers=auth(user)
        )

        assert response.json()["spent"] == "50.00"


@pytest.mark.integration
class TestOwnershipAndNotFound:
    async def test_a_nonexistent_id_is_404(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(url(uuid.uuid4()), headers=auth(user))

        assert response.status_code == 404

    async def test_another_users_budget_is_404_not_403(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """404, not 403 - matching test_transactions_get.py's reasoning."""
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_budget = await add_budget(db_session, grace)

        response = await db_client.get(url(graces_budget.id), headers=auth(ada))

        assert response.status_code == 404

    async def test_a_malformed_id_is_422_not_500(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(url("not-a-uuid"), headers=auth(user))

        assert response.status_code == 422


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        response = await db_client.get(url(budget.id))

        assert response.status_code == 401
