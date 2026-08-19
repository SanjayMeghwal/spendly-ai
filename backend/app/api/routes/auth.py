"""Authentication endpoints.

HTTP only. Routing, status codes, and the translation of domain exceptions
into responses. No business logic lives here - see app/services/user.py.
"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.models import User
from app.schemas.user import UserCreate, UserRead
from app.services.user import EmailAlreadyRegistered, create_user

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post(
    "/register",
    # 201 Created, not 200. The status line is part of the API contract: a
    # client, a cache, and a monitoring dashboard all treat "created a
    # resource" differently from "here is a response".
    status_code=status.HTTP_201_CREATED,
    # This is the security control, not documentation. FastAPI serialises the
    # returned object THROUGH this schema and drops every attribute the schema
    # does not declare - and UserRead does not declare hashed_password.
    # Exposing the hash would require adding a field here deliberately.
    response_model=UserRead,
    summary="Register a new account",
    responses={
        status.HTTP_409_CONFLICT: {"description": "Email address already registered."},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "Invalid email or password."},
    },
)
async def register(payload: UserCreate, db: DbSession) -> User:
    """Create an account.

    ON THE 409 - A DELIBERATE, DOCUMENTED PRIVACY TRADE-OFF.

    Telling the caller "this email is already registered" confirms that the
    address HAS an account here. That is user enumeration: an attacker with a
    list of addresses can discover which people use this service, and for a
    finance product that fact is sensitive before any password is involved.

    The leak-free alternative is to always answer 201 and send an email -
    either "welcome" or "someone tried to register with your address" - so the
    response reveals nothing. That is genuinely better, and it requires email
    infrastructure this project does not yet have.

    So we accept the disclosure, consciously, because the alternative is not
    "keep the secret" but "silently fail to create the account and confuse
    every honest user who mistyped". Revisit when transactional email exists.

    NOTE this reasoning does NOT extend to LOGIN. There, the leak-free
    alternative costs nothing - a single "invalid email or password" for both
    cases - so login must not distinguish them. That is slice 4.
    """
    try:
        return await create_user(
            db,
            email=payload.email,
            # The only point in the application where the plaintext is
            # unwrapped. `.get_secret_value()` is intentionally explicit, so
            # this line is easy to find in review.
            password=payload.password.get_secret_value(),
            full_name=payload.full_name,
        )
    except EmailAlreadyRegistered:
        # `from None` suppresses exception chaining. Without it, an unhandled
        # error downstream could surface the original traceback - which names
        # our modules and query structure - in a response or a log aggregator.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists.",
        ) from None
