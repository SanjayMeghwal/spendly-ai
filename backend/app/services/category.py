"""Category business logic.

LAYERING - this module must never import FastAPI, matching services/budget.py.

EVERY QUERY HERE FILTERS BY user_id. Same reasoning as services/budget.py: a
query against this table that omits it is a data leak, and "not mine" must
look identical to "does not exist" - see get_category below.
"""

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Budget, Category, Goal, Transaction

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


class CategoryNotFound(Exception):
    """Raised when a referenced category_id doesn't exist or isn't owned by the caller.

    Shared by services/transaction.py and services/budget.py: creating or
    updating a transaction/budget with a category_id that doesn't resolve
    via get_category is a 422, not a 404 - the URL's own resource is fine,
    it's the request body that names something that isn't there. Defined
    here, not duplicated in each caller, since both mean exactly the same
    thing: "this category_id isn't one of yours." Also raised by
    delete_category when `reassign_to` doesn't resolve.
    """


class CategoryInUse(Exception):
    """Raised when delete_category is blocked by something still referencing it.

    Three distinct reasons, all surfaced as 409 by the route with a
    different message:
      - `has_budget`: a budget targets this category.
      - `has_goal`: a goal targets this category.
      - `transaction_count`: this many transactions reference the category
        and no usable `reassign_to` was given.

    `has_budget` and `has_goal` ALWAYS block, regardless of `reassign_to` -
    merging two budgets' limits, or two goals' progress, isn't something to
    do automatically; the caller deletes or repoints the budget/goal first
    via its own API, same reasoning for both.
    """

    def __init__(self, *, has_budget: bool, has_goal: bool, transaction_count: int) -> None:
        self.has_budget = has_budget
        self.has_goal = has_goal
        self.transaction_count = transaction_count
        super().__init__(
            f"has_budget={has_budget} has_goal={has_goal} transaction_count={transaction_count}"
        )


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


async def get_category_names(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    category_ids: set[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """Bulk-resolve category ids to names, scoped to their owner.

    Used by Transaction/Budget reads to denormalize `category_name` without
    an N+1 query per row - one call resolves every distinct category_id on
    a whole page of transactions, not one lookup per transaction. An id that
    doesn't resolve (shouldn't happen: category_id's ondelete="RESTRICT"
    means a category in use can never actually be deleted) is simply absent
    from the returned dict rather than raising - the caller decides what
    "missing" means, same as get_category returning None rather than
    raising.

    An empty `category_ids` returns an empty dict without querying - every
    caller passes the *ids actually present* on the rows it's building, and
    a page of entirely uncategorized rows should not need this to touch the
    database at all.
    """
    if not category_ids:
        return {}
    result = await session.execute(
        select(Category.id, Category.name).where(
            Category.user_id == user_id, Category.id.in_(category_ids)
        )
    )
    # Not dict(result): Result has its own .keys() method (the column
    # names), so dict()'s mapping-protocol check picks THAT up instead of
    # treating result as an iterable of (id, name) pairs, and fails trying
    # to subscript it. The comprehension sidesteps that ambiguity entirely.
    return {category_id: name for category_id, name in result}  # noqa: C416


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
        # Unlike create_category's identical branch (reachable via a
        # foreign-key violation - see test_categories_create.py), this one
        # has no way to be exercised today: Category carries no CHECK
        # constraint, and update_category never touches user_id, so a
        # unique violation is the only IntegrityError this commit can ever
        # raise. Kept anyway for defensive symmetry with create_category
        # and in case a future constraint on Category makes it reachable -
        # re-raising unconditionally, rather than assuming every
        # IntegrityError is a name collision, is what makes that safe.
        raise  # pragma: no cover

    await session.refresh(category)
    return category


async def delete_category(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    category_id: uuid.UUID,
    reassign_to: uuid.UUID | None = None,
) -> bool:
    """Delete a category, optionally reassigning its transactions first.

    Returns whether a row was actually deleted - False means "not found or
    not yours", same as delete_budget/delete_transaction.

    A category with an active BUDGET or GOAL always blocks deletion,
    regardless of `reassign_to` - see CategoryInUse. A category with
    TRANSACTIONS blocks deletion only if no `reassign_to` is given; with
    one, every matching transaction is moved to it in the same transaction
    as the delete, so the two can never observably happen apart.
    `reassign_to` equal to `category_id` itself is treated as if it were
    not given at all - reassigning something to itself is a no-op, not an
    error.

    Raises:
        CategoryNotFound: `reassign_to` was given and doesn't belong to
            user_id.
        CategoryInUse: a budget or goal targets this category, or
            transactions do and no usable `reassign_to` was given.
    """
    category = await get_category(session, user_id=user_id, category_id=category_id)
    if category is None:
        return False

    if reassign_to == category_id:
        reassign_to = None

    has_budget = (
        await session.execute(
            select(Budget.id)
            .where(Budget.user_id == user_id, Budget.category_id == category_id)
            .limit(1)
        )
    ).scalar_one_or_none() is not None
    has_goal = (
        await session.execute(
            select(Goal.id).where(Goal.user_id == user_id, Goal.category_id == category_id).limit(1)
        )
    ).scalar_one_or_none() is not None
    if has_budget or has_goal:
        raise CategoryInUse(has_budget=has_budget, has_goal=has_goal, transaction_count=0)

    transaction_count = (
        await session.execute(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.user_id == user_id, Transaction.category_id == category_id)
        )
    ).scalar_one()

    if transaction_count > 0:
        if reassign_to is None:
            raise CategoryInUse(
                has_budget=False, has_goal=False, transaction_count=transaction_count
            )
        if await get_category(session, user_id=user_id, category_id=reassign_to) is None:
            raise CategoryNotFound(reassign_to)
        await session.execute(
            update(Transaction)
            .where(Transaction.user_id == user_id, Transaction.category_id == category_id)
            .values(category_id=reassign_to)
        )

    await session.delete(category)
    await session.commit()
    return True
