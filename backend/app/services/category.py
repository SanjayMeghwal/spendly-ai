"""Category business logic.

LAYERING - this module must never import FastAPI, matching services/budget.py.

EVERY QUERY HERE FILTERS BY user_id. Same reasoning as services/budget.py: a
query against this table that omits it is a data leak, and "not mine" must
look identical to "does not exist" - see get_category below.

NOTE ON delete_category: not in this module yet. Deleting a category needs
to check whether any transaction or budget still references it by
category_id - and neither does yet. That FK arrives in a later migration;
delete_category lands in the commit that adds DELETE /categories/{id},
after the cutover, not here.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category

# Sentinel distinguishing "the caller did not mention this field" from "the
# caller sent it as null", matching services/budget.py's _UNSET exactly.
# CategoryUpdate's own reject_explicit_null validator already refuses an
# explicit null before this module ever sees one, but the sentinel is still
# needed to tell "omitted" from "sent", which a plain None default cannot do.
_UNSET: Any = object()


class CategoryNameAlreadyExists(Exception):
    """Raised when a user already has a category with this name.

    A domain exception, deliberately not an HTTPException - same reasoning
    as services/budget.py's BudgetCategoryAlreadyExists: business rules do
    not belong to a transport. The API layer decides this becomes a 409.
    """


# PostgreSQL SQLSTATE for unique_violation - see services/user.py's
# _is_unique_violation for why this is matched by code, not by message text.
_UNIQUE_VIOLATION = "23505"


def _is_unique_violation(exc: IntegrityError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION


async def create_category(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
) -> Category:
    """Create a category owned by user_id and return the persisted row.

    THE DUPLICATE CHECK IS DELIBERATELY DONE TWICE, exactly as in
    services/budget.py's create_budget: catching IntegrityError from the
    database's uq_categories_user_id_name_lower index is what actually
    guarantees uniqueness under concurrent requests. Nothing here
    pre-checks with a SELECT first, because a courtesy check would still
    lose the same race - the commit's exception handler is the only path
    that matters.

    Raises:
        CategoryNameAlreadyExists: user_id already has a category matching
            this name case-insensitively.
    """
    category = Category(user_id=user_id, name=name)
    session.add(category)

    try:
        await session.commit()
    except IntegrityError as exc:
        # Roll back explicitly: after a failed flush the session is in an
        # unusable state, and leaving it that way makes the NEXT query fail
        # with a confusing PendingRollbackError somewhere unrelated.
        await session.rollback()

        # ONLY a unique violation means "this name is taken" - re-raising
        # anything else here would hide a real bug behind a
        # plausible-looking 409, same reasoning as create_budget's.
        if _is_unique_violation(exc):
            raise CategoryNameAlreadyExists(name) from exc
        raise

    # Load created_at / updated_at, which PostgreSQL filled in during the INSERT.
    await session.refresh(category)
    return category


async def get_category(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    category_id: uuid.UUID,
) -> Category | None:
    """Look up one category by id, scoped to its owner.

    Returns None both when the id does not exist at all and when it belongs
    to someone else - the two cases are indistinguishable on purpose, same
    reasoning as get_budget.
    """
    result = await session.execute(
        select(Category).where(Category.id == category_id, Category.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_categories(session: AsyncSession, *, user_id: uuid.UUID) -> list[Category]:
    """Return one user's categories, alphabetically by name.

    No pagination, same reasoning as list_budgets: a user has at most a few
    dozen categories, nothing like an ever-growing transaction history.
    """
    result = await session.execute(
        select(Category).where(Category.user_id == user_id).order_by(Category.name)
    )
    return list(result.scalars().all())


async def update_category(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    category_id: uuid.UUID,
    name: str | None = _UNSET,
) -> Category | None:
    """Apply a rename and return the updated row, or None if not found.

    Callers pass `**payload.model_dump(exclude_unset=True)` from
    CategoryUpdate, so an omitted field never reaches this function and
    keeps its `_UNSET` default - untouched. `name` backs a NOT NULL column,
    and CategoryUpdate's own validator already refuses it as null - the
    assert below makes that guarantee visible to mypy, same as
    update_budget's.

    Raises:
        CategoryNameAlreadyExists: renaming to `name` would collide with
            another category of this user's, case-insensitively.
    """
    category = await get_category(session, user_id=user_id, category_id=category_id)
    if category is None:
        return None

    if name is not _UNSET:
        assert name is not None
        category.name = name

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if _is_unique_violation(exc):
            raise CategoryNameAlreadyExists(name) from exc
        raise

    await session.refresh(category)
    return category
