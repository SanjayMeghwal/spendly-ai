"""Budget business logic.

LAYERING - this module must never import FastAPI, matching services/transaction.py.

EVERY QUERY HERE FILTERS BY user_id. Same reasoning as
services/transaction.py: a query against this table that omits it is a data
leak, and "not mine" must look identical to "does not exist" - see
get_budget once it's added in a later commit.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Budget, Transaction


class BudgetCategoryAlreadyExists(Exception):
    """Raised when a user already has a budget for this category.

    A domain exception, deliberately not an HTTPException - same reasoning
    as services/user.py's EmailAlreadyRegistered: business rules do not
    belong to a transport. The API layer decides this becomes a 409.
    """


# PostgreSQL SQLSTATE for unique_violation - see services/user.py's
# _is_unique_violation for why this is matched by code, not by message text.
_UNIQUE_VIOLATION = "23505"


def _is_unique_violation(exc: IntegrityError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION


async def create_budget(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    category: str,
    limit_amount: Decimal,
) -> Budget:
    """Create a budget owned by user_id and return the persisted row.

    THE DUPLICATE CHECK IS DELIBERATELY DONE TWICE, exactly as in
    services/user.py's create_user: catching IntegrityError from the
    database's uq_budgets_user_id_category_lower index is what actually
    guarantees uniqueness under concurrent requests. Nothing here pre-checks
    with a SELECT first, because a courtesy check would still lose the same
    race - the commit's exception handler is the only path that matters.

    Raises:
        BudgetCategoryAlreadyExists: user_id already has a budget for a
            category matching this one case-insensitively.
    """
    budget = Budget(user_id=user_id, category=category, limit_amount=limit_amount)
    session.add(budget)

    try:
        await session.commit()
    except IntegrityError as exc:
        # Roll back explicitly: after a failed flush the session is in an
        # unusable state, and leaving it that way makes the NEXT query fail
        # with a confusing PendingRollbackError somewhere unrelated.
        await session.rollback()

        # ONLY a unique violation means "this category is taken". The check
        # constraint on limit_amount is also an IntegrityError, and Pydantic
        # already rejects a non-positive limit_amount before this is ever
        # reached - re-raising anything else here would hide a real bug
        # behind a plausible-looking 409.
        if _is_unique_violation(exc):
            raise BudgetCategoryAlreadyExists(category) from exc
        raise

    # Load created_at / updated_at, which PostgreSQL filled in during the INSERT.
    await session.refresh(budget)
    return budget


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """The [start, end) UTC range covering one calendar month.

    `end` is the first instant of the FOLLOWING month, not the last instant
    of this one - a half-open range needs no "23:59:59.999999" fudge and
    composes cleanly with a plain `<` comparison, matching how
    Transaction.occurred_at is always stored: UTC, per CLAUDE.md.
    """
    start = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, month + 1, 1, tzinfo=UTC)
    return start, end


async def spent_for_category(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    category: str,
    year: int,
    month: int,
) -> Decimal:
    """How much of a budget's limit one user has used up in one month.

    Transaction.amount is signed - expenses are negative - so a plain
    SUM(amount) over a spending-heavy category comes back negative (e.g. one
    $50 purchase sums to -50.00). That is the right sign for a balance but
    the wrong sign to compare against a positive limit_amount, so this
    negates the sum: a $50 purchase reads as spent=50.00, comparable
    directly to the limit. "Net", not "expenses only": a refund (a positive
    amount, same category) still reduces spent rather than being ignored -
    a $50 purchase plus a $20 refund nets to spent=30.00. Matching is
    case-insensitive, matching the uniqueness rule on Budget.category itself
    - see app/models/budget.py.

    A month with no matching transactions returns Decimal("0"), not None -
    SUM() over zero rows is NULL in SQL, but "spent nothing" is the correct
    domain answer, not "unknown".
    """
    start, end = _month_bounds(year, month)
    result = await session.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.user_id == user_id,
            func.lower(Transaction.category) == category.lower(),
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
        )
    )
    total = result.scalar_one()
    return -total if total is not None else Decimal("0")
