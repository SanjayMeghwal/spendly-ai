"""Tests for DELETE /api/v1/categories/{id}."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Category, Goal, Transaction, User
from app.services.budget import create_budget
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


async def add_transaction(
    session: AsyncSession, user: User, *, category_id: uuid.UUID
) -> Transaction:
    transaction = Transaction(
        user_id=user.id,
        amount=Decimal("-10.00"),
        description="test transaction",
        category_id=category_id,
        occurred_at=datetime.now(UTC),
    )
    session.add(transaction)
    await session.commit()
    await session.refresh(transaction)
    return transaction


async def add_goal(session: AsyncSession, user: User, *, category_id: uuid.UUID) -> Goal:
    goal = Goal(user_id=user.id, category_id=category_id, target_amount=Decimal("5000.00"))
    session.add(goal)
    await session.commit()
    await session.refresh(goal)
    return goal


def url(category_id: object, **params: object) -> str:
    base = f"/api/v1/categories/{category_id}"
    if not params:
        return base
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{query}"


@pytest.mark.integration
class TestSuccessfulDeletion:
    async def test_returns_204_for_an_unused_category(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.delete(url(category.id), headers=auth(user))

        assert response.status_code == 204
        assert response.content == b""

    async def test_the_row_is_actually_gone(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        await db_client.delete(url(category.id), headers=auth(user))

        found = (
            await db_session.execute(select(Category).where(Category.id == category.id))
        ).scalar_one_or_none()
        assert found is None

    async def test_deleting_twice_is_404_the_second_time(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        first = await db_client.delete(url(category.id), headers=auth(user))
        second = await db_client.delete(url(category.id), headers=auth(user))

        assert first.status_code == 204
        assert second.status_code == 404


@pytest.mark.integration
class TestBudgetBlocksDeletion:
    async def test_a_category_with_a_budget_cannot_be_deleted(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)
        await create_budget(
            db_session, user_id=user.id, category_id=category.id, limit_amount=Decimal("500")
        )

        response = await db_client.delete(url(category.id), headers=auth(user))

        assert response.status_code == 409

    async def test_a_budget_blocks_deletion_even_with_reassign_to(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The one rule with no override: a budget always blocks, since
        merging two budgets' limits isn't automatic."""
        user = await register(db_session)
        category = await add_category(db_session, user, name="Groceries")
        other = await add_category(db_session, user, name="Dining")
        await create_budget(
            db_session, user_id=user.id, category_id=category.id, limit_amount=Decimal("500")
        )

        response = await db_client.delete(
            url(category.id, reassign_to=other.id), headers=auth(user)
        )

        assert response.status_code == 409

    async def test_the_category_still_exists_after_a_blocked_delete(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)
        await create_budget(
            db_session, user_id=user.id, category_id=category.id, limit_amount=Decimal("500")
        )

        await db_client.delete(url(category.id), headers=auth(user))

        found = (
            await db_session.execute(select(Category).where(Category.id == category.id))
        ).scalar_one_or_none()
        assert found is not None


@pytest.mark.integration
class TestGoalBlocksDeletion:
    """Mirrors TestBudgetBlocksDeletion - a goal is the same kind of
    deliberate single-category assignment as a budget, so it gets the same
    treatment in delete_category."""

    async def test_a_category_with_a_goal_cannot_be_deleted(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)
        await add_goal(db_session, user, category_id=category.id)

        response = await db_client.delete(url(category.id), headers=auth(user))

        assert response.status_code == 409

    async def test_a_goal_blocks_deletion_even_with_reassign_to(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user, name="Emergency Fund")
        other = await add_category(db_session, user, name="Dining")
        await add_goal(db_session, user, category_id=category.id)

        response = await db_client.delete(
            url(category.id, reassign_to=other.id), headers=auth(user)
        )

        assert response.status_code == 409

    async def test_the_category_still_exists_after_a_goal_blocked_delete(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)
        await add_goal(db_session, user, category_id=category.id)

        await db_client.delete(url(category.id), headers=auth(user))

        found = (
            await db_session.execute(select(Category).where(Category.id == category.id))
        ).scalar_one_or_none()
        assert found is not None


@pytest.mark.integration
class TestTransactionReassignment:
    async def test_a_category_with_transactions_and_no_reassign_to_is_a_conflict(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)
        await add_transaction(db_session, user, category_id=category.id)

        response = await db_client.delete(url(category.id), headers=auth(user))

        assert response.status_code == 409

    async def test_reassign_to_moves_transactions_and_deletes_the_category(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        groceries = await add_category(db_session, user, name="Groceries")
        dining = await add_category(db_session, user, name="Dining")
        transaction = await add_transaction(db_session, user, category_id=groceries.id)

        response = await db_client.delete(
            url(groceries.id, reassign_to=dining.id), headers=auth(user)
        )

        assert response.status_code == 204
        await db_session.refresh(transaction)
        assert transaction.category_id == dining.id
        found = (
            await db_session.execute(select(Category).where(Category.id == groceries.id))
        ).scalar_one_or_none()
        assert found is None

    async def test_reassign_to_itself_is_treated_as_not_given(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Reassigning a category's transactions to itself is a no-op, not
        a way to bypass the in-use check."""
        user = await register(db_session)
        category = await add_category(db_session, user)
        await add_transaction(db_session, user, category_id=category.id)

        response = await db_client.delete(
            url(category.id, reassign_to=category.id), headers=auth(user)
        )

        assert response.status_code == 409

    async def test_reassign_to_a_nonexistent_category_is_422(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)
        await add_transaction(db_session, user, category_id=category.id)

        response = await db_client.delete(
            url(category.id, reassign_to=uuid.uuid4()), headers=auth(user)
        )

        assert response.status_code == 422

    async def test_reassign_to_another_users_category_is_422(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        category = await add_category(db_session, ada)
        graces_category = await add_category(db_session, grace)
        await add_transaction(db_session, ada, category_id=category.id)

        response = await db_client.delete(
            url(category.id, reassign_to=graces_category.id), headers=auth(ada)
        )

        assert response.status_code == 422

    async def test_reassign_to_is_ignored_when_nothing_is_in_use(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A category with no transactions deletes cleanly even if a
        (pointless) reassign_to is supplied."""
        user = await register(db_session)
        category = await add_category(db_session, user, name="Groceries")
        other = await add_category(db_session, user, name="Dining")

        response = await db_client.delete(
            url(category.id, reassign_to=other.id), headers=auth(user)
        )

        assert response.status_code == 204


@pytest.mark.integration
class TestOwnershipAndNotFound:
    async def test_a_nonexistent_id_is_404(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.delete(url(uuid.uuid4()), headers=auth(user))

        assert response.status_code == 404

    async def test_cannot_delete_another_users_category(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        graces_category = await add_category(db_session, grace)

        response = await db_client.delete(url(graces_category.id), headers=auth(ada))

        assert response.status_code == 404
        found = (
            await db_session.execute(select(Category).where(Category.id == graces_category.id))
        ).scalar_one_or_none()
        assert found is not None


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        category = await add_category(db_session, user)

        response = await db_client.delete(url(category.id))

        assert response.status_code == 401
