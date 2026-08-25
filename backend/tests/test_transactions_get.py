"""Tests for GET /api/v1/transactions/{id}."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
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
class TestSuccessfulRetrieval:
    async def test_returns_the_transaction(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        transaction = await add_transaction(db_session, user)

        response = await db_client.get(url(transaction.id), headers=auth(user))

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(transaction.id)
        assert body["description"] == "Grocery store"


@pytest.mark.integration
class TestOwnershipAndNotFound:
    async def test_a_nonexistent_id_is_404(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(url(uuid.uuid4()), headers=auth(user))

        assert response.status_code == 404

    async def test_another_users_transaction_is_404_not_403(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """404, not 403 - confirming the id belongs to someone else would be

        exactly the information an id-probing attacker wants. See
        app/services/transaction.py's module docstring.
        """
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_transaction = await add_transaction(db_session, grace)

        response = await db_client.get(url(graces_transaction.id), headers=auth(ada))

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
        transaction = await add_transaction(db_session, user)

        response = await db_client.get(url(transaction.id))

        assert response.status_code == 401
