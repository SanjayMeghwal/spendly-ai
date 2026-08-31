"""Tests for POST /api/v1/transactions.

Run end to end - HTTP request, validation, service, real PostgreSQL - because
what matters here spans all of those layers: a test against the service alone
would not catch a response schema leaking user_id, and one against a mocked
session would not catch the NUMERIC column actually holding the precision it
claims to.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Category, Transaction, User
from app.services.category import create_category
from app.services.user import create_user

TRANSACTIONS_URL = "/api/v1/transactions"
PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {create_access_token(user.id, token_version=user.token_version)}"
    }


async def add_category(session: AsyncSession, user: User, *, name: str = "Groceries") -> Category:
    return await create_category(session, user_id=user.id, name=name)


def payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "amount": "-12.50",
        "description": "Grocery store",
        "occurred_at": "2026-08-20T10:00:00Z",
        "notes": "Weekly shop",
    }
    body.update(overrides)
    return body


@pytest.mark.integration
class TestSuccessfulCreation:
    async def test_returns_201_with_the_created_transaction(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.post(
            TRANSACTIONS_URL, json=payload(category_id=str(category.id)), headers=auth(user)
        )

        assert response.status_code == 201
        body = response.json()
        assert body["amount"] == "-12.50"
        assert body["description"] == "Grocery store"
        assert body["category_id"] == str(category.id)
        assert body["category_name"] == "Groceries"
        assert body["notes"] == "Weekly shop"
        assert body["id"]
        assert body["created_at"]
        assert body["updated_at"]

    async def test_category_and_notes_are_optional(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        body = payload()
        del body["notes"]

        response = await db_client.post(TRANSACTIONS_URL, json=body, headers=auth(user))

        assert response.status_code == 201
        assert response.json()["category_id"] is None
        assert response.json()["category_name"] is None
        assert response.json()["notes"] is None

    async def test_response_never_exposes_user_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """TransactionRead has no user_id field, and this proves it holds.

        Mirrors test_register.py's password-leak check: the schema, not
        vigilance, is what stops a future column from being exposed by
        accident.
        """
        user = await register(db_session)

        response = await db_client.post(TRANSACTIONS_URL, json=payload(), headers=auth(user))

        assert "user_id" not in response.json()

    async def test_transaction_is_persisted_and_owned_by_the_caller(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(TRANSACTIONS_URL, json=payload(), headers=auth(user))

        stored = (
            await db_session.execute(
                select(Transaction).where(Transaction.id == response.json()["id"])
            )
        ).scalar_one()
        assert stored.user_id == user.id
        assert stored.amount == Decimal("-12.50")
        assert stored.occurred_at == datetime(2026, 8, 20, 10, 0, tzinfo=UTC)


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.post(TRANSACTIONS_URL, json=payload())

        assert response.status_code == 401


@pytest.mark.integration
class TestValidation:
    async def test_rejects_an_amount_with_too_many_decimal_places(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(
            TRANSACTIONS_URL, json=payload(amount="12.505"), headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_an_amount_exceeding_the_column_width(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """NUMERIC(12, 2) allows at most 10 digits before the decimal point."""
        user = await register(db_session)

        response = await db_client.post(
            TRANSACTIONS_URL, json=payload(amount="123456789012.00"), headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_a_missing_description(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        body = payload()
        del body["description"]

        response = await db_client.post(TRANSACTIONS_URL, json=body, headers=auth(user))

        assert response.status_code == 422

    async def test_rejects_an_empty_description(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(
            TRANSACTIONS_URL, json=payload(description=""), headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_a_missing_occurred_at(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        body = payload()
        del body["occurred_at"]

        response = await db_client.post(TRANSACTIONS_URL, json=body, headers=auth(user))

        assert response.status_code == 422

    async def test_no_transaction_is_created_when_validation_fails(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        await db_client.post(TRANSACTIONS_URL, json=payload(description=""), headers=auth(user))

        found = (
            await db_session.execute(select(Transaction).where(Transaction.user_id == user.id))
        ).scalar_one_or_none()

        assert found is None

    async def test_rejects_a_nonexistent_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(
            TRANSACTIONS_URL, json=payload(category_id=str(uuid.uuid4())), headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_another_users_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_category = await add_category(db_session, grace)

        response = await db_client.post(
            TRANSACTIONS_URL,
            json=payload(category_id=str(graces_category.id)),
            headers=auth(ada),
        )

        assert response.status_code == 422
