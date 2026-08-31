"""Budget endpoints.

HTTP only - routing, status codes, and translating domain results into
responses. No business logic lives here; see app/services/budget.py.

EVERY HANDLER TAKES `current_user: CurrentUser` AND NOTHING ELSE NAMES THE
OWNER, matching app/api/routes/transactions.py exactly - see that module's
docstring for the full reasoning.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession
from app.models import Budget
from app.schemas.budget import BudgetCreate, BudgetRead, BudgetUpdate
from app.services.budget import (
    BudgetCategoryAlreadyExists,
    create_budget,
    get_budget,
    list_budgets,
    spent_for_category,
    update_budget,
)

router = APIRouter(prefix="/budgets", tags=["budgets"])

# Shared query parameter definition for every handler that accepts ?month=,
# so GET /budgets and GET /budgets/{id} document and validate it identically.
# The pattern is enforced here, not in _resolve_month, so an invalid month
# reaches the caller as a clean 422 from FastAPI's own query validation,
# rather than a ValueError raised deep in int(month.split("-")[1]).
_MONTH_QUERY = Query(
    default=None,
    pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    description="YYYY-MM. Defaults to the current UTC month.",
    examples=["2026-08"],
)


def _not_found() -> HTTPException:
    """The single 404 for 'no such budget of yours', matching transactions.py."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No budget with that id.")


def _conflict() -> HTTPException:
    """The single 409 for 'you already have a budget for that category'.

    Shared by create (a brand new category collides) and update (renaming
    into a category collides), since both raise the same
    BudgetCategoryAlreadyExists domain exception.
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A budget for this category already exists.",
    )


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
        raise _conflict() from None
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
    month: str | None = _MONTH_QUERY,
) -> list[BudgetRead]:
    year, resolved_month = _resolve_month(month)
    budgets = await list_budgets(db, user_id=current_user.id)
    return [await _to_read_model(db, budget, year=year, month=resolved_month) for budget in budgets]


@router.get(
    "/{budget_id}",
    status_code=status.HTTP_200_OK,
    response_model=BudgetRead,
    summary="Get one of the authenticated user's budgets, with this month's status",
    responses={status.HTTP_404_NOT_FOUND: {"description": "No budget with that id."}},
)
async def get_one(
    budget_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
    month: str | None = _MONTH_QUERY,
) -> BudgetRead:
    budget = await get_budget(db, user_id=current_user.id, budget_id=budget_id)
    if budget is None:
        raise _not_found()
    year, resolved_month = _resolve_month(month)
    return await _to_read_model(db, budget, year=year, month=resolved_month)


@router.patch(
    "/{budget_id}",
    status_code=status.HTTP_200_OK,
    response_model=BudgetRead,
    summary="Update one of the authenticated user's budgets",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No budget with that id."},
        status.HTTP_409_CONFLICT: {"description": "A budget for this category already exists."},
    },
)
async def update(
    budget_id: uuid.UUID,
    payload: BudgetUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> BudgetRead:
    # exclude_unset, not exclude_none: a field the client omitted must be
    # left alone. Unlike TransactionUpdate, neither field here can ever be
    # sent as null - BudgetUpdate's own validator already rejects that - so
    # there is no "clear this field" case to distinguish, only "changed" vs
    # "not mentioned". See services/budget.py's _UNSET sentinel, which reads
    # that distinction.
    try:
        budget = await update_budget(
            db,
            user_id=current_user.id,
            budget_id=budget_id,
            **payload.model_dump(exclude_unset=True),
        )
    except BudgetCategoryAlreadyExists:
        raise _conflict() from None
    if budget is None:
        raise _not_found()
    year, month = _resolve_month(None)
    return await _to_read_model(db, budget, year=year, month=month)
