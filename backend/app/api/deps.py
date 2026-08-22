"""Shared FastAPI dependencies, exposed as reusable type aliases.

WHY ALIASES RATHER THAN INLINE Depends(...)

The older style puts the dependency in the default value:

    async def handler(db: AsyncSession = Depends(get_db)) -> ...:

That works, but it repeats the wiring in every handler, and it means the
parameter has a default - so no non-default parameter may follow it.

The `Annotated` form keeps the dependency in the TYPE rather than the default:

    async def handler(db: DbSession) -> ...:

Declared once here and reused everywhere. Handlers read as ordinary typed
functions, parameter ordering is unconstrained, and mypy checks them normally.
This is the form the FastAPI docs now recommend.
"""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import InvalidTokenError, decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.services import user_service

logger = logging.getLogger(__name__)

settings = get_settings()

# A database session scoped to the current request.
DbSession = Annotated[AsyncSession, Depends(get_db)]

# Application settings. Injected rather than imported so tests can override it
# through app.dependency_overrides.
SettingsDep = Annotated[Settings, Depends(get_settings)]


# ------------------------------------------------------------------------------
# Authentication
# ------------------------------------------------------------------------------
#
# OAuth2PasswordBearer does two jobs. At runtime it pulls the credential out of
# the `Authorization: Bearer <token>` header and rejects a request that has no
# such header. At documentation time it declares a security scheme in the
# OpenAPI spec, which is what puts the "Authorize" button in /docs and makes
# every protected endpoint testable from the browser.
#
# `tokenUrl` is not a route registration - it is metadata telling clients where
# to exchange credentials for a token. It must match the real login path, so it
# is built from the same setting the router is mounted under rather than being
# written out by hand and silently drifting.
# ------------------------------------------------------------------------------
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")

TokenDep = Annotated[str, Depends(oauth2_scheme)]


def _unauthorised(detail: str) -> HTTPException:
    """Build a 401 carrying the header the HTTP spec requires.

    RFC 7235: a 401 response MUST include `WWW-Authenticate`, naming the scheme
    the client should use. Omitting it is a genuine (if quiet) protocol
    violation, and well-behaved clients rely on it to know how to retry.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(db: DbSession, token: TokenDep) -> User:
    """Resolve the bearer token into the user who owns it.

    WHY THIS HITS THE DATABASE ON EVERY REQUEST

    The token already carries the user's ID, correctly signed - so trusting it
    outright and skipping the query is tempting, and it is what "stateless
    auth" is often taken to mean. It is wrong here.

    A token is a snapshot of the moment it was minted. Between then and now the
    account may have been deactivated or deleted. Skipping the lookup means a
    deactivated user keeps full access until their token happens to expire, and
    a deleted user's ID is attached to writes against rows that no longer have
    an owner. One indexed primary-key lookup is a small price for the guarantee
    that the caller still exists and is still allowed in.

    Raises:
        HTTPException: 401 if the token is unusable or the account cannot act.
    """
    try:
        claims = decode_access_token(token)
    except InvalidTokenError as exc:
        # Logged at debug, not warning: an expired token is entirely routine -
        # it is what every client hits after 30 idle minutes. Logging it louder
        # would bury real signals in noise.
        logger.debug("Rejected token: %s", exc)
        # The detail is deliberately vague. "Signature verification failed"
        # versus "token expired" tells an attacker which part of a forgery
        # attempt to fix next.
        raise _unauthorised("Could not validate credentials") from exc

    user = await user_service.get_user_by_id(db, claims.subject)

    if user is None:
        # Correctly signed, but names a user who no longer exists - a deleted
        # account, or a token minted against a different database.
        logger.warning("Valid token for unknown user %s", claims.subject)
        raise _unauthorised("Could not validate credentials")

    if not user.is_active:
        # 403, not 401: the credentials ARE valid and re-authenticating will
        # not help. 401 means "who are you?", 403 means "I know who you are and
        # the answer is still no". Returning 401 here would send clients into a
        # pointless login loop.
        logger.info("Deactivated user %s presented a valid token", user.id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is inactive",
        )

    return user


# The authenticated, active user for the current request. Every endpoint that
# touches user-owned data takes this - and must still filter its queries by
# `current_user.id`. Authentication answers "who is this?"; it does not by
# itself stop one user reading another's rows.
CurrentUser = Annotated[User, Depends(get_current_user)]
