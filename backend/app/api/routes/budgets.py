"""Budget endpoints.

HTTP only - routing, status codes, and translating domain results into
responses. No business logic lives here; see app/services/budget.py.

EVERY HANDLER TAKES `current_user: CurrentUser` AND NOTHING ELSE NAMES THE
OWNER, matching app/api/routes/transactions.py exactly - see that module's
docstring for the full reasoning.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession
from app.models import Budget
from app.schemas.budget import BudgetCreate, BudgetRead
from app.services.budget import (
    BudgetCategoryAlreadyExists,
    create_budget,
    list_budgets,
    spent_for_category,
)

router = APIRouter(prefix="/budgets", tags=["budgets"])

# YYYY-MM, matching what _resolve_month below parses. Enforced here, not in
# _resolve_month, so an invalid month reaches the caller as a clean 422 from
# FastAPI's own query validation, rather than a ValueError raised deep in
# int(month.split("-")[1]).
_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


def _resolve_month(month: str | None) -> tuple[int, int]:
    """(year, month) for the requested period, defaulting to the current UTC month."""
    if month is None:
        now = datetime.now(UTC)
        return now.year, now.month
    year_str, month_str = month.split("-")
    return int(year_str), int(month_str)


async def _to_read_model(db: DbSession, budget: Budget, *, year: int, month: int) -> BudgetRead:
    """Attach one month's spend/remaining to a budget row.

    Shared by every handler that returns a BudgetRead, so the "spent isn't a
    column, compute it live" rule from app/schemas/budget.py's BudgetRead
    docstring has exactly one implementation.
    """
    spent = await spent_for_category(
        db, user_id=budget.user_id, category=budget.category, year=year, month=month
    )
    return BudgetRead(
        id=budget.id,
        category=budget.category,
        limit_amount=budget.limit_amount,
        spent=spent,
        remaining=budget.limit_amount - spent,
        created_at=budget.created_at,
        updated_at=budget.updated_at,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=BudgetRead,
    summary="Set a spending limit for a category",
    responses={
        status.HTTP_409_CONFLICT: {"description": "A budget for this category already exists."},
    },
)
async def create(
    payload: BudgetCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> BudgetRead:
    try:
        budget = await create_budget(
            db,
            user_id=current_user.id,
            category=payload.category,
            limit_amount=payload.limit_amount,
        )
    except BudgetCategoryAlreadyExists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A budget for this category already exists.",
        ) from None
    # A freshly created budget is always reported against the CURRENT month -
    # there is no month to choose yet from the request, unlike GET.
    year, month = _resolve_month(None)
    return await _to_read_model(db, budget, year=year, month=month)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    response_model=list[BudgetRead],
    summary="List the authenticated user's budgets, with this month's status",
)
async def list_mine(
    current_user: CurrentUser,
    db: DbSession,
    month: str | None = Query(
        default=None,
        pattern=_MONTH_PATTERN,
        description="YYYY-MM. Defaults to the current UTC month.",
        examples=["2026-08"],
    ),
) -> list[BudgetRead]:
    year, resolved_month = _resolve_month(month)
    budgets = await list_budgets(db, user_id=current_user.id)
    return [await _to_read_model(db, budget, year=year, month=resolved_month) for budget in budgets]
