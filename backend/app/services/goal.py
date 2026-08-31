"""Goal business logic.

LAYERING - this module must never import FastAPI, matching services/budget.py.

EVERY QUERY HERE FILTERS BY user_id. Same reasoning as services/budget.py: a
query against this table that omits it is a data leak, and "not mine" must
look identical to "does not exist" - see get_goal below.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Goal, Transaction
from app.services.category import CategoryNotFound, get_category

# Sentinel distinguishing "the caller did not mention this field" from "the
# caller sent it as null", matching services/budget.py's _UNSET exactly.
# Unlike category_id and target_amount, target_date genuinely CAN be sent
# as null (clearing a goal's deadline) - the sentinel is still needed to
# tell that apart from "omitted", which a plain None default cannot do.
_UNSET: Any = object()


class GoalCategoryAlreadyExists(Exception):
    """Raised when a user already has a goal for this category.

    A domain exception, deliberately not an HTTPException - same reasoning
    as services/budget.py's BudgetCategoryAlreadyExists: business rules do
    not belong to a transport. The API layer decides this becomes a 409.
    """


# PostgreSQL SQLSTATE for unique_violation - see services/user.py's
# _is_unique_violation for why this is matched by code, not by message text.
_UNIQUE_VIOLATION = "23505"


def _is_unique_violation(exc: IntegrityError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION


async def create_goal(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    category_id: uuid.UUID,
    target_amount: Decimal,
    target_date: date | None = None,
) -> Goal:
    """Create a goal owned by user_id and return the persisted row.

    THE DUPLICATE CHECK IS DELIBERATELY DONE TWICE, exactly as in
    services/budget.py's create_budget: catching IntegrityError from the
    database's uq_goals_user_id_category_id index is what actually
    guarantees uniqueness under concurrent requests. Nothing here
    pre-checks with a SELECT first, because a courtesy check would still
    lose the same race - the commit's exception handler is the only path
    that matters.

    Raises:
        CategoryNotFound: category_id doesn't belong to user_id.
        GoalCategoryAlreadyExists: user_id already has a goal for this
            category.
    """
    if await get_category(session, user_id=user_id, category_id=category_id) is None:
        raise CategoryNotFound(category_id)

    goal = Goal(
        user_id=user_id,
        category_id=category_id,
        target_amount=target_amount,
        target_date=target_date,
    )
    session.add(goal)

    try:
        await session.commit()
    except IntegrityError as exc:
        # Roll back explicitly: after a failed flush the session is in an
        # unusable state, and leaving it that way makes the NEXT query fail
        # with a confusing PendingRollbackError somewhere unrelated.
        await session.rollback()

        # ONLY a unique violation means "this category is taken". The check
        # constraint on target_amount is also an IntegrityError, and
        # Pydantic already rejects a non-positive target_amount before this
        # is ever reached - re-raising anything else here would hide a real
        # bug behind a plausible-looking 409.
        if _is_unique_violation(exc):
            raise GoalCategoryAlreadyExists(category_id) from exc
        raise

    # Load created_at / updated_at, which PostgreSQL filled in during the INSERT.
    await session.refresh(goal)
    return goal


async def get_goal(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    goal_id: uuid.UUID,
) -> Goal | None:
    """Look up one goal by id, scoped to its owner.

    Returns None both when the id does not exist at all and when it belongs
    to someone else - the two cases are indistinguishable on purpose, same
    reasoning as get_budget.
    """
    result = await session.execute(select(Goal).where(Goal.id == goal_id, Goal.user_id == user_id))
    return result.scalar_one_or_none()


async def list_goals(session: AsyncSession, *, user_id: uuid.UUID) -> list[Goal]:
    """Return one user's goals.

    No pagination, same reasoning as list_budgets: a user has at most a few
    dozen categories, nothing like an ever-growing transaction history.
    """
    result = await session.execute(select(Goal).where(Goal.user_id == user_id))
    return list(result.scalars().all())


async def update_goal(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    goal_id: uuid.UUID,
    category_id: uuid.UUID | None = _UNSET,
    target_amount: Decimal | None = _UNSET,
    target_date: date | None = _UNSET,
) -> Goal | None:
    """Apply a partial update and return the updated row, or None if not found.

    Callers pass `**payload.model_dump(exclude_unset=True)` from
    GoalUpdate, so a field absent from the request body never reaches this
    function and keeps its `_UNSET` default - untouched. category_id and
    target_amount back NOT NULL columns, and GoalUpdate's own validator
    already refuses either as null - the asserts below make that guarantee
    visible to mypy, same as update_budget's. target_date has no such
    assert: it is genuinely nullable, so an explicit null is applied as-is,
    clearing the goal's deadline.

    Raises:
        CategoryNotFound: category_id was sent and doesn't belong to user_id.
        GoalCategoryAlreadyExists: switching to category_id would collide
            with another goal of this user's.
    """
    goal = await get_goal(session, user_id=user_id, goal_id=goal_id)
    if goal is None:
        return None

    if category_id is not _UNSET:
        assert category_id is not None
        if await get_category(session, user_id=user_id, category_id=category_id) is None:
            raise CategoryNotFound(category_id)
        goal.category_id = category_id
    if target_amount is not _UNSET:
        assert target_amount is not None
        goal.target_amount = target_amount
    if target_date is not _UNSET:
        goal.target_date = target_date

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if _is_unique_violation(exc):
            raise GoalCategoryAlreadyExists(category_id) from exc
        raise

    await session.refresh(goal)
    return goal


async def delete_goal(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    goal_id: uuid.UUID,
) -> bool:
    """Delete a goal. Returns whether a row was actually deleted.

    Hard delete, same reasoning as delete_budget: a goal is removed only
    when its owner asks to stop tracking it, and there is no audit or
    undelete feature yet that would need the row to survive.
    """
    goal = await get_goal(session, user_id=user_id, goal_id=goal_id)
    if goal is None:
        return False

    await session.delete(goal)
    await session.commit()
    return True


async def progress_for_category(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    category_id: uuid.UUID,
) -> Decimal:
    """How much has been put toward a goal, cumulatively.

    Reuses Budget.spent_for_category's exact sign convention, for
    consistency across the app: Transaction.amount is signed (expenses
    negative), so this negates the sum - money flowing INTO a category
    reads as a positive progress figure, comparable directly to a positive
    target_amount, the same way a budget's "spent" is comparable to its
    limit. The one real difference from Budget's version: no month window.
    A goal's progress is the running total since the category's first
    transaction, not a per-period figure that resets - so there is no
    year/month argument here at all.

    A category with no matching transactions returns Decimal("0"), not
    None - SUM() over zero rows is NULL in SQL, but "no progress yet" is
    the correct domain answer, not "unknown".
    """
    result = await session.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.user_id == user_id,
            Transaction.category_id == category_id,
        )
    )
    total = result.scalar_one()
    return -total if total is not None else Decimal("0")
