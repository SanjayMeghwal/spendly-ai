"""Goal endpoints.

HTTP only - routing, status codes, and translating domain results into
responses. No business logic lives here; see app/services/goal.py.

EVERY HANDLER TAKES `current_user: CurrentUser` AND NOTHING ELSE NAMES THE
OWNER, matching app/api/routes/budgets.py exactly - see that module's
docstring for the full reasoning.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.models import Goal
from app.schemas.goal import GoalCreate, GoalRead, GoalUpdate
from app.services.category import CategoryNotFound, get_category_names
from app.services.goal import (
    GoalCategoryAlreadyExists,
    create_goal,
    get_goal,
    list_goals,
    progress_for_category,
    update_goal,
)

router = APIRouter(prefix="/goals", tags=["goals"])


def _not_found() -> HTTPException:
    """The single 404 for 'no such goal of yours', matching budgets.py."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No goal with that id.")


def _conflict() -> HTTPException:
    """The single 409 for 'you already have a goal for that category'.

    Shared by create (a brand new category collides) and update (switching
    to a colliding category), matching budgets.py's identical helper.
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A goal for this category already exists.",
    )


def _invalid_category() -> HTTPException:
    """The single 422 for 'category_id isn't one of your categories', matching budgets.py."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="category_id does not refer to one of your categories.",
    )


async def _to_read_model(goal: Goal, category_name: str, *, progress: Decimal) -> GoalRead:
    """Assemble a GoalRead from a goal row plus its already-looked-up
    category name and progress - so building a page of results never
    queries once per row for the name."""
    return GoalRead(
        id=goal.id,
        category_id=goal.category_id,
        category_name=category_name,
        target_amount=goal.target_amount,
        target_date=goal.target_date,
        progress=progress,
        remaining=goal.target_amount - progress,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=GoalRead,
    summary="Create a savings goal for a category",
    responses={
        status.HTTP_409_CONFLICT: {"description": "A goal for this category already exists."},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "category_id does not refer to one of your categories."
        },
    },
)
async def create(
    payload: GoalCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> GoalRead:
    try:
        goal = await create_goal(
            db,
            user_id=current_user.id,
            category_id=payload.category_id,
            target_amount=payload.target_amount,
            target_date=payload.target_date,
        )
    except CategoryNotFound:
        raise _invalid_category() from None
    except GoalCategoryAlreadyExists:
        raise _conflict() from None
    names = await get_category_names(db, user_id=current_user.id, category_ids={goal.category_id})
    progress = await progress_for_category(
        db, user_id=current_user.id, category_id=goal.category_id
    )
    return await _to_read_model(goal, names[goal.category_id], progress=progress)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    response_model=list[GoalRead],
    summary="List the authenticated user's goals, with progress",
)
async def list_mine(current_user: CurrentUser, db: DbSession) -> list[GoalRead]:
    goals = await list_goals(db, user_id=current_user.id)
    names = await get_category_names(
        db, user_id=current_user.id, category_ids={g.category_id for g in goals}
    )
    results = [
        await _to_read_model(
            goal,
            names[goal.category_id],
            progress=await progress_for_category(
                db, user_id=current_user.id, category_id=goal.category_id
            ),
        )
        for goal in goals
    ]
    # list_goals has no natural ordering of its own (category is a
    # category_id, not a string) - sorting on the resolved name happens
    # here, once the name is actually known, same as budgets.py's list_mine.
    return sorted(results, key=lambda r: r.category_name)


@router.get(
    "/{goal_id}",
    status_code=status.HTTP_200_OK,
    response_model=GoalRead,
    summary="Get one of the authenticated user's goals, with progress",
    responses={status.HTTP_404_NOT_FOUND: {"description": "No goal with that id."}},
)
async def get_one(
    goal_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> GoalRead:
    goal = await get_goal(db, user_id=current_user.id, goal_id=goal_id)
    if goal is None:
        raise _not_found()
    names = await get_category_names(db, user_id=current_user.id, category_ids={goal.category_id})
    progress = await progress_for_category(
        db, user_id=current_user.id, category_id=goal.category_id
    )
    return await _to_read_model(goal, names[goal.category_id], progress=progress)


@router.patch(
    "/{goal_id}",
    status_code=status.HTTP_200_OK,
    response_model=GoalRead,
    summary="Update one of the authenticated user's goals",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No goal with that id."},
        status.HTTP_409_CONFLICT: {"description": "A goal for this category already exists."},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "category_id does not refer to one of your categories."
        },
    },
)
async def update(
    goal_id: uuid.UUID,
    payload: GoalUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> GoalRead:
    # exclude_unset, not exclude_none: a field the client omitted must be
    # left alone, while target_date sent explicitly as null must clear it.
    # Only exclude_unset tells those two apart - see services/goal.py's
    # _UNSET sentinel, which is what actually reads this distinction.
    try:
        goal = await update_goal(
            db,
            user_id=current_user.id,
            goal_id=goal_id,
            **payload.model_dump(exclude_unset=True),
        )
    except CategoryNotFound:
        raise _invalid_category() from None
    except GoalCategoryAlreadyExists:
        raise _conflict() from None
    if goal is None:
        raise _not_found()
    names = await get_category_names(db, user_id=current_user.id, category_ids={goal.category_id})
    progress = await progress_for_category(
        db, user_id=current_user.id, category_id=goal.category_id
    )
    return await _to_read_model(goal, names[goal.category_id], progress=progress)
