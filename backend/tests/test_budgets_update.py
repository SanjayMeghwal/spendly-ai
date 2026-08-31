"""Tests for PATCH /api/v1/budgets/{id}."""

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Budget, Category, User
from app.services.budget import create_budget, update_budget
from app.services.category import create_category
from app.services.user import create_user

PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


async def add_category(session: AsyncSession, user: User, *, name: str = "Groceries") -> Category:
    return await create_category(session, user_id=user.id, name=name)


async def add_budget(
    session: AsyncSession, user: User, *, category_id: uuid.UUID | None = None
) -> Budget:
    if category_id is None:
        category_id = (await add_category(session, user)).id
    return await create_budget(
        session, user_id=user.id, category_id=category_id, limit_amount=Decimal("500.00")
    )


def url(budget_id: object) -> str:
    return f"/api/v1/budgets/{budget_id}"


@pytest.mark.integration
class TestPartialUpdate:
    async def test_updates_only_the_sent_field(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A PATCH that names one field must leave every other field alone."""
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        response = await db_client.patch(
            url(budget.id), json={"limit_amount": "600.00"}, headers=auth(user)
        )

        assert response.status_code == 200
        body = response.json()
        assert body["limit_amount"] == "600.00"
        assert body["category_id"] == str(budget.category_id)

    async def test_updates_the_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)
        household = await add_category(db_session, user, name="Household")

        response = await db_client.patch(
            url(budget.id), json={"category_id": str(household.id)}, headers=auth(user)
        )

        assert response.status_code == 200
        body = response.json()
        assert body["category_id"] == str(household.id)
        assert body["category_name"] == "Household"
        assert body["limit_amount"] == "500.00"

    async def test_updates_both_fields_at_once(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)
        household = await add_category(db_session, user, name="Household")

        response = await db_client.patch(
            url(budget.id),
            json={"category_id": str(household.id), "limit_amount": "250.00"},
            headers=auth(user),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["category_id"] == str(household.id)
        assert body["limit_amount"] == "250.00"

    async def test_empty_body_changes_nothing(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        response = await db_client.patch(url(budget.id), json={}, headers=auth(user))

        assert response.status_code == 200
        assert response.json()["category_id"] == str(budget.category_id)
        assert response.json()["limit_amount"] == "500.00"


@pytest.mark.integration
class TestOwnershipAndNotFound:
    async def test_a_nonexistent_id_is_404(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.patch(
            url(uuid.uuid4()), json={"limit_amount": "1.00"}, headers=auth(user)
        )

        assert response.status_code == 404

    async def test_cannot_update_another_users_budget(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_budget = await add_budget(db_session, grace)

        response = await db_client.patch(
            url(graces_budget.id), json={"limit_amount": "999.00"}, headers=auth(ada)
        )

        assert response.status_code == 404
        await db_session.refresh(graces_budget)
        assert graces_budget.limit_amount == Decimal("500.00")


@pytest.mark.integration
class TestValidation:
    async def test_rejects_a_malformed_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        response = await db_client.patch(
            url(budget.id), json={"category_id": "not-a-uuid"}, headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_a_nonexistent_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        response = await db_client.patch(
            url(budget.id), json={"category_id": str(uuid.uuid4())}, headers=auth(user)
        )

        assert response.status_code == 422

    async def test_rejects_another_users_category_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        budget = await add_budget(db_session, ada)
        graces_category = await add_category(db_session, grace)

        response = await db_client.patch(
            url(budget.id), json={"category_id": str(graces_category.id)}, headers=auth(ada)
        )

        assert response.status_code == 422

    async def test_rejects_a_non_positive_limit_amount(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        response = await db_client.patch(
            url(budget.id), json={"limit_amount": "0.00"}, headers=auth(user)
        )

        assert response.status_code == 422

    @pytest.mark.parametrize("field", ["category_id", "limit_amount"])
    async def test_rejects_explicit_null(
        self, db_client: AsyncClient, db_session: AsyncSession, field: str
    ) -> None:
        """Both fields back NOT NULL columns - neither can ever be cleared."""
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        response = await db_client.patch(url(budget.id), json={field: None}, headers=auth(user))

        assert response.status_code == 422


@pytest.mark.integration
class TestDuplicateCategory:
    async def test_switching_to_a_category_with_an_existing_budget_is_a_conflict(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        dining = await add_category(db_session, user, name="Dining")
        await add_budget(db_session, user, category_id=dining.id)
        groceries_budget = await add_budget(db_session, user)

        response = await db_client.patch(
            url(groceries_budget.id), json={"category_id": str(dining.id)}, headers=auth(user)
        )

        assert response.status_code == 409

    async def test_switching_to_its_own_current_category_is_not_a_conflict(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A no-op switch must not collide with the row's own existing value."""
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        response = await db_client.patch(
            url(budget.id), json={"category_id": str(budget.category_id)}, headers=auth(user)
        )

        assert response.status_code == 200


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        response = await db_client.patch(url(budget.id), json={"limit_amount": "1.00"})

        assert response.status_code == 401


@pytest.mark.integration
class TestServiceLevelIntegrityErrors:
    async def test_a_non_unique_integrity_error_is_not_mislabelled(
        self, db_session: AsyncSession
    ) -> None:
        """Mirrors test_budgets_create.py's identical check on create_budget.

        Calling the service directly with a non-positive limit_amount
        bypasses BudgetUpdate's `gt=0` validation. The CHECK constraint must
        surface as a plain IntegrityError, not get relabelled as
        BudgetCategoryAlreadyExists.
        """
        user = await register(db_session)
        budget = await add_budget(db_session, user)

        with pytest.raises(IntegrityError):
            await update_budget(
                db_session,
                user_id=user.id,
                budget_id=budget.id,
                limit_amount=Decimal("-10.00"),
            )
