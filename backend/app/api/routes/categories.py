"""Category endpoints.

HTTP only - routing, status codes, and translating domain results into
responses. No business logic lives here; see app/services/category.py.

EVERY HANDLER TAKES `current_user: CurrentUser` AND NOTHING ELSE NAMES THE
OWNER, matching app/api/routes/budgets.py exactly - see that module's
docstring for the full reasoning.
"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.category import CategoryCreate, CategoryRead
from app.services.category import CategoryNameAlreadyExists, create_category, list_categories

router = APIRouter(prefix="/categories", tags=["categories"])


def _conflict() -> HTTPException:
    """The single 409 for 'you already have a category with that name'."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A category with this name already exists.",
    )


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
