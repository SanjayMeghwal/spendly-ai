"""Tests for PATCH /api/v1/transactions/{id}."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Category, Transaction, User
from app.services.category import create_category
from app.services.transaction import create_transaction
from app.services.user import create_user

PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


async def add_category(session: AsyncSession, user: User, *, name: str = "Groceries") -> Category:
    return await create_category(session, user_id=user.id, name=name)


async def add_transaction(session: AsyncSession, user: User, **overrides: object) -> Transaction:
    fields: dict[str, object] = {
        "amount": Decimal("-12.50"),
        "description": "Grocery store",
        "notes": "Weekly shop",
        "occurred_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return await create_transaction(session, user_id=user.id, **fields)  # type: ignore[arg-type]


def url(transaction_id: object) -> str:
    return f"/api/v1/transactions/{transaction_id}"


@pytest.mark.integration
class TestPartialUpdate:
    async def test_updates_only_the_sent_field(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A PATCH that names one field must leave every other field alone."""
        user = await register(db_session)
        category = await add_category(db_session, user)
        transaction = await add_transaction(db_session, user, category_id=category.id)

        response = await db_client.patch(
            url(transaction.id), json={"description": "Updated"}, headers=auth(user)
        )

        assert response.status_code == 200
        body = response.json()
        assert body["description"] == "Updated"
        assert body["amount"] == "-12.50"
        assert body["category_id"] == str(category.id)
        assert body["notes"] == "Weekly shop"

    async def test_updates_multiple_fields_at_once(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        transaction = await add_transaction(db_session, user)

        response = await db_client.patch(
            url(transaction.id),
            json={"amount": "100.00", "description": "Refund"},
            headers=auth(user),
        )

        assert response.status_code == 200
        assert response.json()["amount"] == "100.00"
        assert response.json()["description"] == "Refund"

    async def test_updates_occurred_at(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        transaction = await add_transaction(db_session, user)
        new_date = "2020-01-01T00:00:00Z"

        response = await db_client.patch(
            url(transaction.id), json={"occurred_at": new_date}, headers=auth(user)
        )

        assert response.status_code == 200
        assert response.json()["occurred_at"] == new_date

    async def test_updates_notes(self, db_client: AsyncClient, db_session: AsyncSession) -> None:
        user = await register(db_session)
        transaction = await add_transaction(db_session, user, notes="original")

        response = await db_client.patch(
            url(transaction.id), json={"notes": "revised"}, headers=auth(user)
        )

        assert response.status_code == 200
        assert response.json()["notes"] == "revised"

    async def test_updates_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)
        transaction = await add_transaction(db_session, user)

        response = await db_client.patch(
            url(transaction.id), json={"category_id": str(category.id)}, headers=auth(user)
        )

        assert response.status_code == 200
        assert response.json()["category_id"] == str(category.id)
        assert response.json()["category_name"] == "Groceries"

    async def test_explicit_null_clears_a_nullable_field(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The distinction this endpoint exists to get right.

        Omitting `category_id` must leave it alone (see the test above);
        sending it as null must clear it. If exclude_unset were replaced by
        exclude_none, this would be indistinguishable from omitting the field
        and would silently stop working.
        """
        user = await register(db_session)
        category = await add_category(db_session, user)
        transaction = await add_transaction(db_session, user, category_id=category.id)

        response = await db_client.patch(
            url(transaction.id), json={"category_id": None}, headers=auth(user)
        )

        assert response.status_code == 200
        assert response.json()["category_id"] is None
        assert response.json()["category_name"] is None

    async def test_empty_body_changes_nothing(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        transaction = await add_transaction(db_session, user)

        response = await db_client.patch(url(transaction.id), json={}, headers=auth(user))

        assert response.status_code == 200
        assert response.json()["description"] == "Grocery store"


@pytest.mark.integration
class TestOwnershipAndNotFound:
    async def test_a_nonexistent_id_is_404(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.patch(
            url(uuid.uuid4()), json={"description": "x"}, headers=auth(user)
        )

        assert response.status_code == 404

    async def test_cannot_update_another_users_transaction(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_transaction = await add_transaction(db_session, grace)

        response = await db_client.patch(
            url(graces_transaction.id), json={"description": "hijacked"}, headers=auth(ada)
        )

        assert response.status_code == 404
        await db_session.refresh(graces_transaction)
        assert graces_transaction.description == "Grocery store"


@pytest.mark.integration
class TestValidation:
    async def test_rejects_an_empty_description(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        transaction = await add_transaction(db_session, user)

        response = await db_client.patch(
            url(transaction.id), json={"description": ""}, headers=auth(user)
        )

        assert response.status_code == 422

    @pytest.mark.parametrize("field", ["amount", "description", "occurred_at"])
    async def test_rejects_explicit_null_for_a_required_field(
        self, db_client: AsyncClient, db_session: AsyncSession, field: str
    ) -> None:
        """amount, description, and occurred_at back NOT NULL columns.

        Without TransactionUpdate's validator, this would reach the database
        as a constraint violation - an unhandled 500 - instead of a clean 422
        telling the client what went wrong.
        """
        user = await register(db_session)
        transaction = await add_transaction(db_session, user)

        response = await db_client.patch(
            url(transaction.id), json={field: None}, headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_a_nonexistent_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        transaction = await add_transaction(db_session, user)

        response = await db_client.patch(
            url(transaction.id),
            json={"category_id": str(uuid.uuid4())},
            headers=auth(user),
        )

        assert response.status_code == 422

    async def test_rejects_another_users_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_category = await add_category(db_session, grace)
        transaction = await add_transaction(db_session, ada)

        response = await db_client.patch(
            url(transaction.id),
            json={"category_id": str(graces_category.id)},
            headers=auth(ada),
        )

        assert response.status_code == 422


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        transaction = await add_transaction(db_session, user)

        response = await db_client.patch(url(transaction.id), json={"description": "x"})

        assert response.status_code == 401
