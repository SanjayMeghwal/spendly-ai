"""Tests for DELETE /api/v1/budgets/{id}."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
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
class TestSuccessfulDeletion:
    async def test_returns_204(self, db_client: AsyncClient, db_session: AsyncSession) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        response = await db_client.delete(url(budget.id), headers=auth(user))

        assert response.status_code == 204
        assert response.content == b""

    async def test_the_row_is_actually_gone(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Hard delete: the row is removed, not merely marked."""
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        await db_client.delete(url(budget.id), headers=auth(user))

        found = (
            await db_session.execute(select(Budget).where(Budget.id == budget.id))
        ).scalar_one_or_none()
        assert found is None

    async def test_deleting_twice_is_404_the_second_time(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        first = await db_client.delete(url(budget.id), headers=auth(user))
        second = await db_client.delete(url(budget.id), headers=auth(user))

        assert first.status_code == 204
        assert second.status_code == 404

    async def test_deleting_a_budget_does_not_delete_its_transactions(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Budget and Transaction are related only by category string, not a
        foreign key - deleting a budget must never touch transaction rows.
        """
        user = await register(db_session)
        budget = await add_budget(db_session, user)
        db_session.add(
            Transaction(
                user_id=user.id,
                amount=Decimal("-10.00"),
                description="test transaction",
                category="Groceries",
                occurred_at=datetime.now(UTC),
            )
        )
        await db_session.commit()

        await db_client.delete(url(budget.id), headers=auth(user))

        remaining = (
            await db_session.execute(select(Transaction).where(Transaction.user_id == user.id))
        ).scalar_one_or_none()
        assert remaining is not None


@pytest.mark.integration
class TestOwnershipAndNotFound:
    async def test_a_nonexistent_id_is_404(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.delete(url(uuid.uuid4()), headers=auth(user))

        assert response.status_code == 404

    async def test_cannot_delete_another_users_budget(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_budget = await add_budget(db_session, grace)

        response = await db_client.delete(url(graces_budget.id), headers=auth(ada))

        assert response.status_code == 404
        found = (
            await db_session.execute(select(Budget).where(Budget.id == graces_budget.id))
        ).scalar_one_or_none()
        assert found is not None


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        response = await db_client.delete(url(budget.id))

        assert response.status_code == 401
