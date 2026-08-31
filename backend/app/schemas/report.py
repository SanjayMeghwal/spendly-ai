"""Response schemas for the reporting API.

No request schemas here - both endpoints are pure GETs, parameterized only
by query strings. No model behind either one either: these are read-only
aggregations over `transactions`, not owned resources with their own
identity - see app/services/report.py.
"""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class CategorySpend(BaseModel):
    """One category's net spend for one month.

    `category_id` is null exactly for the synthetic "Uncategorized" row -
    every other row corresponds to a real category the user owns. `spent`
    is not a stored value; it is `-SUM(amount)` for that category and
    month, the same sign convention as BudgetRead.spent and
    GoalRead.progress: money flowing out reads positive.
    """

    category_id: UUID | None = Field(description="Null for the synthetic 'Uncategorized' row.")
    category_name: str
    spent: Decimal


class MonthlySummary(BaseModel):
    """One calendar month's income, expenses, and net.

    `income` and `expenses` are both non-negative magnitudes - `expenses`
    is the positive size of that month's outflow, not a negative number -
    so a bar chart never has to flip a sign. `net = income - expenses`,
    which can be negative for a month that spent more than it took in.
    """

    month: str = Field(description="YYYY-MM.")
    income: Decimal
    expenses: Decimal
    net: Decimal
