"""Reporting endpoints.

HTTP only - routing, status codes, and translating domain results into
responses. No business logic lives here; see app/services/report.py.

EVERY HANDLER TAKES `current_user: CurrentUser` AND NOTHING ELSE NAMES THE
OWNER, matching every other routes module in this project.

Unlike transactions/budgets/goals/categories, there is no single resource
by id here to 404 on or collide with - both endpoints are unconditionally-
successful reads over the caller's own data, closer in shape to
routes/health.py than to routes/budgets.py.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.report import CategorySpend
from app.services.report import spend_by_category

router = APIRouter(prefix="/reports", tags=["reports"])

# Same pattern as routes/budgets.py's _MONTH_QUERY: a module-level Query()
# singleton, not a fresh call in the handler signature - see that module
# for why (ruff's B008).
_MONTH_QUERY = Query(
    default=None,
    pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    description="YYYY-MM. Defaults to the current UTC month.",
    examples=["2026-08"],
)


def _resolve_month(month: str | None) -> tuple[int, int]:
    """(year, month) for the requested period, defaulting to the current UTC month.

    Deliberately duplicated from routes/budgets.py's identical helper - a
    six-line query-parsing function, not worth coupling this module to
    budgets.py's internals over. See services/report.py's _month_bounds
    for the same call made on the service side.
    """
    if month is None:
        now = datetime.now(UTC)
        return now.year, now.month
    year_str, month_str = month.split("-")
    return int(year_str), int(month_str)


@router.get(
    "/spend-by-category",
    status_code=status.HTTP_200_OK,
    response_model=list[CategorySpend],
    summary="Net spend per category for one month, largest first",
)
async def get_spend_by_category(
    current_user: CurrentUser,
    db: DbSession,
    month: str | None = _MONTH_QUERY,
) -> list[CategorySpend]:
    year, resolved_month = _resolve_month(month)
    rows = await spend_by_category(db, user_id=current_user.id, year=year, month=resolved_month)
    return [
        CategorySpend(
            category_id=row.category_id,
            category_name=row.category_name if row.category_name is not None else "Uncategorized",
            spent=row.spent,
        )
        for row in rows
    ]
