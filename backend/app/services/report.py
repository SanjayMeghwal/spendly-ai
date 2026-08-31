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

from sqlalchemy import and_, case, func, select
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


class MonthlySummaryRow(NamedTuple):
    """One calendar month's income and expenses, as returned by monthly_summary."""

    year: int
    month: int
    income: Decimal
    expenses: Decimal


def _months_back(year: int, month: int, n: int) -> list[tuple[int, int]]:
    """The n calendar months ending at (year, month) inclusive, oldest first.

    Plain integer arithmetic rather than a date library helper - `month`
    wraps from 1 to 12 and `year` decrements on the wrap, the same
    December-into-January case _month_bounds handles, just walked backward
    instead of forward.
    """
    months = []
    y, m = year, month
    for _ in range(n):
        months.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(months))


async def monthly_summary(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    months: int,
) -> list[MonthlySummaryRow]:
    """Income, expenses, and (implicitly) net for each of the last `months`
    calendar months, ending with the current UTC month, oldest first.

    `income` is the sum of positive amounts and `expenses` is the sum of
    negative amounts negated to a positive magnitude - unlike
    spend_by_category's single net figure, a trend chart wants both bars
    visible, not just their difference. `net` is not computed here; the
    route derives it as `income - expenses`, the same "service returns raw
    data, route assembles the schema" split used throughout this codebase.

    A month with no transactions at all is absent from the SQL result
    (GROUP BY only returns months that have rows) but is NOT absent from
    the return value - it is zero-filled here so a quiet month shows as a
    flat zero in a trend chart instead of vanishing from the x-axis.
    """
    month_list = _months_back(*_current_year_month(), months)
    start, _ = _month_bounds(*month_list[0])
    _, end = _month_bounds(*month_list[-1])

    month_expr = func.date_trunc("month", Transaction.occurred_at)
    income_expr = func.sum(case((Transaction.amount > 0, Transaction.amount), else_=0))
    expenses_expr = -func.sum(case((Transaction.amount < 0, Transaction.amount), else_=0))
    result = await session.execute(
        select(
            month_expr.label("month"),
            income_expr.label("income"),
            expenses_expr.label("expenses"),
        )
        .where(
            Transaction.user_id == user_id,
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
        )
        .group_by(month_expr)
    )
    rows_by_month = {(row.month.year, row.month.month): row for row in result.all()}

    return [
        MonthlySummaryRow(
            year=y,
            month=m,
            income=rows_by_month[(y, m)].income if (y, m) in rows_by_month else Decimal("0"),
            expenses=rows_by_month[(y, m)].expenses if (y, m) in rows_by_month else Decimal("0"),
        )
        for y, m in month_list
    ]


def _current_year_month() -> tuple[int, int]:
    now = datetime.now(UTC)
    return now.year, now.month
