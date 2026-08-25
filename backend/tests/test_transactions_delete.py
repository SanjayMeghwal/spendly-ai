"""Tests for DELETE /api/v1/transactions/{id}."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Transaction, User
from app.services.transaction import create_transaction
from app.services.user import create_user

PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


async def add_transaction(session: AsyncSession, user: User) -> Transaction:
    return await create_transaction(
        session,
        user_id=user.id,
        amount=Decimal("-12.50"),
        description="Grocery store",
        occurred_at=datetime.now(UTC),
    )


def url(transaction_id: object) -> str:
    return f"/api/v1/transactions/{transaction_id}"


@pytest.mark.integration
class TestSuccessfulDeletion:
    async def test_returns_204(self, db_client: AsyncClient, db_session: AsyncSession) -> None:
        user = await register(db_session)
        transaction = await add_transaction(db_session, user)

        response = await db_client.delete(url(transaction.id), headers=auth(user))

        assert response.status_code == 204
        assert response.content == b""

    async def test_the_row_is_actually_gone(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Hard delete: the row is removed, not merely marked."""
        user = await register(db_session)
        transaction = await add_transaction(db_session, user)

        await db_client.delete(url(transaction.id), headers=auth(user))

        found = (
            await db_session.execute(select(Transaction).where(Transaction.id == transaction.id))
        ).scalar_one_or_none()
        assert found is None

    async def test_deleting_twice_is_404_the_second_time(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        transaction = await add_transaction(db_session, user)

        first = await db_client.delete(url(transaction.id), headers=auth(user))
        second = await db_client.delete(url(transaction.id), headers=auth(user))

        assert first.status_code == 204
        assert second.status_code == 404


@pytest.mark.integration
class TestOwnershipAndNotFound:
    async def test_a_nonexistent_id_is_404(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.delete(url(uuid.uuid4()), headers=auth(user))

        assert response.status_code == 404

    async def test_cannot_delete_another_users_transaction(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_transaction = await add_transaction(db_session, grace)

        response = await db_client.delete(url(graces_transaction.id), headers=auth(ada))

        assert response.status_code == 404
        found = (
            await db_session.execute(
                select(Transaction).where(Transaction.id == graces_transaction.id)
            )
        ).scalar_one_or_none()
        assert found is not None


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        transaction = await add_transaction(db_session, user)

        response = await db_client.delete(url(transaction.id))

        assert response.status_code == 401
