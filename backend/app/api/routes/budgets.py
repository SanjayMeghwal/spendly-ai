"""Budget endpoints.

HTTP only - routing, status codes, and translating domain results into
responses. No business logic lives here; see app/services/budget.py.

EVERY HANDLER TAKES `current_user: CurrentUser` AND NOTHING ELSE NAMES THE
OWNER, matching app/api/routes/transactions.py exactly - see that module's
docstring for the full reasoning.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.budget import BudgetCreate, BudgetRead
from app.services.budget import BudgetCategoryAlreadyExists, create_budget, spent_for_category

router = APIRouter(prefix="/budgets", tags=["budgets"])


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
    # there is no month to choose yet from the request, unlike GET, which
    # will accept ?month=YYYY-MM once that endpoint exists.
    now = datetime.now(UTC)
    spent = await spent_for_category(
        db, user_id=current_user.id, category=budget.category, year=now.year, month=now.month
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
