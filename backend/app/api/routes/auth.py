"""Registration and login.

This layer does HTTP and nothing else: parse the request, call a service,
translate the outcome into a status code. Any business rule that appears here
belongs in app/services/ instead.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import DbSession, SettingsDep
from app.core.security import create_access_token
from app.schemas.token import Token
from app.schemas.user import UserCreate, UserRead
from app.services import user_service
from app.services.exceptions import EmailAlreadyRegisteredError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    responses={
        status.HTTP_409_CONFLICT: {"description": "That email is already registered."},
        # UNPROCESSABLE_CONTENT, not the older UNPROCESSABLE_ENTITY alias:
        # Starlette deprecated the latter. Same 422, current name.
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Validation failed: malformed email, or password too short."
        },
    },
)
async def register(user_in: UserCreate, db: DbSession) -> UserRead:
    """Register a new user.

    201, not 200: a new resource was created. The distinction is not pedantry -
    caches, client libraries, and API consumers all key behaviour off it.

    The response is a UserRead, which cannot express `hashed_password`. Even
    though the service returns a fully-populated ORM object, only the fields
    declared on that schema are serialised.
    """
    try:
        user = await user_service.register_user(db, user_in)
    except EmailAlreadyRegisteredError as exc:
        # 409 Conflict, not 400: the request is perfectly well-formed, it just
        # conflicts with the current state of the server.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from exc

    return UserRead.model_validate(user)


@router.post(
    "/login",
    response_model=Token,
    summary="Exchange credentials for an access token",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid credentials."},
    },
)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
    settings: SettingsDep,
) -> Token:
    """Authenticate and return a bearer token.

    WHY A FORM, AND WHY THE FIELD IS CALLED `username`

    This endpoint takes `application/x-www-form-urlencoded`, not JSON, and its
    identity field is named `username` even though we send an email. Both come
    from RFC 6749, and both are what FastAPI's OAuth2PasswordRequestForm and
    the "Authorize" button in /docs expect. A tidier JSON body with an `email`
    field would break the interactive docs and every off-the-shelf OAuth2
    client. Conforming to the standard is worth the cosmetic oddity.
    """
    user = await user_service.authenticate_user(
        db,
        email=form_data.username,
        password=form_data.password,
    )

    if user is None:
        # ONE message for every failure - unknown email, wrong password, and
        # deactivated account alike. Saying "no account with that email" would
        # let anyone check whether a given person banks here, which is
        # information worth protecting on its own.
        #
        # The matching timing defence lives in the service layer, which hashes
        # a dummy value when no user is found.
        logger.info("Failed login attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return Token(
        access_token=create_access_token(subject=user.id),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
