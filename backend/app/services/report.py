"""Reporting business logic - read-only aggregation over `transactions`.

LAYERING - this module must never import FastAPI, matching every other
service module. It also does not import app/schemas/report.py: services
return plain data, and routes assemble the response schema - the same
division of labour app/api/routes/budgets.py already uses (spent_for_category
returns a Decimal; the route decides how that becomes a BudgetRead).

EVERY QUERY HERE FILTERS BY user_id. Same reasoning as every other service
module: a query against this table that omits it is a data leak.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Transaction


class CategorySpendRow(NamedTuple):
    """One category's net spend for one month, as returned by spend_by_category.

    `category_name` is None exactly for the synthetic "Uncategorized"
    bucket - deciding what to display for that is a presentation concern,
    left to the route, same as every other "raw data in, schema out" split
    in this codebase.
    """

    category_id: uuid.UUID | None
    category_name: str | None
    spent: Decimal


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """The [start, end) UTC range covering one calendar month.

    Deliberately duplicated from services/budget.py's identical helper
    rather than imported - it's three lines, and importing it would couple
    this module to budget.py's private (`_`-prefixed) internals for no
    real benefit. See CLAUDE.md: "Three similar lines is better than a
    premature abstraction."
    """
    start = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(year, month + 1, 1, tzinfo=UTC)
    return start, end


async def spend_by_category(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    year: int,
    month: int,
) -> list[CategorySpendRow]:
    """Net spend per category for one calendar month, largest first.

    `spent` is `-SUM(amount)` per category - the same sign flip
    spent_for_category (services/budget.py) applies for the same reason:
    Transaction.amount is negative for expenses, so negating it makes
    "money spent" read as a positive number. A refund (a positive amount,
    same category) still nets against it rather than being ignored - one
    sign convention across the whole app, not a reporting-specific one.

    A category with no transactions this month is simply absent from the
    result - there is no "spent 0" row to report for categories nobody
    touched, unlike Budget/Goal, which always report on one specific,
    named category the caller asked about.

    The LEFT JOIN's ON clause carries `Category.user_id == user_id`, not
    just the WHERE clause on Transaction - putting it in WHERE would
    silently exclude uncategorized transactions (whose joined Category
    columns are already NULL, failing any WHERE-clause equality check).
    Uncategorized transactions - `category_id IS NULL` - collapse into one
    row with `category_id=None, category_name=None`, the "Uncategorized"
    bucket the route labels explicitly.
    """
    start, end = _month_bounds(year, month)
    spent_expr = -func.sum(Transaction.amount)
    result = await session.execute(
        select(Transaction.category_id, Category.name, spent_expr.label("spent"))
        .select_from(Transaction)
        .outerjoin(
            Category,
            and_(Transaction.category_id == Category.id, Category.user_id == user_id),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
        )
        .group_by(Transaction.category_id, Category.name)
        .order_by(spent_expr.desc())
    )
    return [CategorySpendRow(*row) for row in result.all()]
