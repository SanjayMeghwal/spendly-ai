"""Endpoints operating on the authenticated user's own account."""

from fastapi import APIRouter, status

from app.api.deps import CurrentUser
from app.schemas.user import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/me",
    response_model=UserRead,
    summary="Get the current user",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid token."},
        status.HTTP_403_FORBIDDEN: {"description": "The account is deactivated."},
    },
)
async def read_current_user(current_user: CurrentUser) -> UserRead:
    """Return the profile of the authenticated user.

    WHY THE PATH IS `/me` AND NOT `/users/{id}`

    `/me` derives its subject from the token, so there is no ID in the URL for
    a caller to change. `/users/{id}` would accept any ID and would therefore
    need an explicit ownership check on every request - and the day someone
    forgets that check, one user can read another's account. Removing the
    parameter removes the entire class of mistake.

    That principle carries into every later milestone: an endpoint that DOES
    take an ID must verify ownership, not merely that the caller is logged in.
    Authentication is not authorisation.
    """
    return UserRead.model_validate(current_user)
