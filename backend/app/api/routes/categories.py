"""Category endpoints.

HTTP only - routing, status codes, and translating domain results into
responses. No business logic lives here; see app/services/category.py.

EVERY HANDLER TAKES `current_user: CurrentUser` AND NOTHING ELSE NAMES THE
OWNER, matching app/api/routes/budgets.py exactly - see that module's
docstring for the full reasoning.
"""

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.services.category import (
    CategoryInUse,
    CategoryNameAlreadyExists,
    CategoryNotFound,
    create_category,
    delete_category,
    get_category,
    list_categories,
    update_category,
)

router = APIRouter(prefix="/categories", tags=["categories"])

# Module-level singleton, not Query(...) inline in the handler signature -
# same reasoning as budgets.py's _MONTH_QUERY: a fresh call in the default
# position is a mutable-default-style footgun FastAPI happens to need for
# query param metadata, and ruff's B008 is right to flag the general
# pattern even though this specific use is safe.
_REASSIGN_TO_QUERY = Query(
    default=None,
    description=(
        "Move this category's transactions here before deleting it. "
        "Required if the category has transactions and no budget."
    ),
)


def _not_found() -> HTTPException:
    """The single 404 for 'no such category of yours', matching budgets.py."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No category with that id.")


def _conflict() -> HTTPException:
    """The single 409 for 'you already have a category with that name'."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A category with this name already exists.",
    )


def _invalid_reassign_target() -> HTTPException:
    """422 for 'reassign_to doesn't refer to one of your categories'.

    Same reasoning as transactions.py/budgets.py's _invalid_category: the
    URL's own resource (the category being deleted) is fine - it's a query
    parameter naming a related entity that doesn't exist.
    """
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="reassign_to does not refer to one of your categories.",
    )


def _in_use(exc: CategoryInUse) -> HTTPException:
    """409 for 'this category is still in use', with a message that says why.

    A budget or goal always blocks (see CategoryInUse's docstring), and a
    category could in principle have both at once; transactions block only
    when the caller didn't supply a usable reassign_to.
    """
    if exc.has_budget or exc.has_goal:
        blockers = [
            name
            for name, present in (("budget", exc.has_budget), ("goal", exc.has_goal))
            if present
        ]
        detail = (
            f"This category has a {' and a '.join(blockers)} targeting it. "
            "Delete or update it before deleting the category."
        )
    else:
        detail = (
            f"{exc.transaction_count} transaction(s) use this category. "
            "Retry with ?reassign_to=<category_id> to move them first."
        )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=CategoryRead,
    summary="Create a category",
    responses={
        status.HTTP_409_CONFLICT: {"description": "A category with this name already exists."},
    },
)
async def create(
    payload: CategoryCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> CategoryRead:
    try:
        category = await create_category(db, user_id=current_user.id, name=payload.name)
    except CategoryNameAlreadyExists:
        raise _conflict() from None
    return CategoryRead.model_validate(category)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    response_model=list[CategoryRead],
    summary="List the authenticated user's categories",
)
async def list_mine(current_user: CurrentUser, db: DbSession) -> list[CategoryRead]:
    categories = await list_categories(db, user_id=current_user.id)
    return [CategoryRead.model_validate(c) for c in categories]


@router.get(
    "/{category_id}",
    status_code=status.HTTP_200_OK,
    response_model=CategoryRead,
    summary="Get one of the authenticated user's categories",
    responses={status.HTTP_404_NOT_FOUND: {"description": "No category with that id."}},
)
async def get_one(
    category_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> CategoryRead:
    category = await get_category(db, user_id=current_user.id, category_id=category_id)
    if category is None:
        raise _not_found()
    return CategoryRead.model_validate(category)


@router.patch(
    "/{category_id}",
    status_code=status.HTTP_200_OK,
    response_model=CategoryRead,
    summary="Rename one of the authenticated user's categories",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No category with that id."},
        status.HTTP_409_CONFLICT: {"description": "A category with this name already exists."},
    },
)
async def update(
    category_id: uuid.UUID,
    payload: CategoryUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> CategoryRead:
    # exclude_unset, not exclude_none: a field the client omitted must be
    # left alone. `name` can never be sent as null - CategoryUpdate's own
    # validator already rejects that - so there is no "clear this field"
    # case to distinguish, only "changed" vs "not mentioned". See
    # services/category.py's _UNSET sentinel, which reads that distinction.
    try:
        category = await update_category(
            db,
            user_id=current_user.id,
            category_id=category_id,
            **payload.model_dump(exclude_unset=True),
        )
    except CategoryNameAlreadyExists:
        raise _conflict() from None
    if category is None:
        raise _not_found()
    return CategoryRead.model_validate(category)


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one of the authenticated user's categories",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No category with that id."},
        status.HTTP_409_CONFLICT: {"description": "This category is still in use."},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "reassign_to does not refer to one of your categories."
        },
    },
)
async def delete(
    category_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
    reassign_to: uuid.UUID | None = _REASSIGN_TO_QUERY,
) -> None:
    try:
        deleted = await delete_category(
            db, user_id=current_user.id, category_id=category_id, reassign_to=reassign_to
        )
    except CategoryNotFound:
        raise _invalid_reassign_target() from None
    except CategoryInUse as exc:
        raise _in_use(exc) from None
    if not deleted:
        raise _not_found()
