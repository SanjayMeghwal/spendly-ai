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

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.tokens import TokenError, decode_access_token
from app.db.session import get_db
from app.models import User
from app.services.user import get_user_by_id

# A database session scoped to the current request.
DbSession = Annotated[AsyncSession, Depends(get_db)]

# Application settings. Injected rather than imported so tests can override it
# through app.dependency_overrides.
SettingsDep = Annotated[Settings, Depends(get_settings)]


# Parses `Authorization: Bearer <token>` and nothing else.
#
# WHY NOT OAuth2PasswordBearer, WHICH THE FASTAPI TUTORIAL USES?
#
# It would give /docs an "Authorize" button, which is genuinely nice - but
# that button posts FORM-ENCODED `username` and `password` to the token URL,
# and our /login accepts JSON with an `email`. The button would fail against
# our own API, and the OpenAPI document would advertise a password flow we do
# not implement. A wrong description of the contract is worse than a plainer
# correct one; HTTPBearer gives /docs a paste-the-token box and describes
# exactly what we accept.
#
# WHY auto_error=False.
#
# VERIFIED against fastapi 0.141.1 rather than taken from folklore. Widely
# repeated advice says HTTPBearer answers a missing header with 403, which
# would be the wrong code - 401 means "you have not authenticated", 403 means
# "you have, and you still may not". That WAS true of older versions and has
# since been fixed: this version raises 401 with a correct
# `WWW-Authenticate: Bearer` header. The status code is no longer the reason.
#
# The reason that survives is the RESPONSE BODY. With auto_error=True a
# missing header returns {"detail": "Not authenticated"} while a bad token
# returns our own message - two different bodies for the same answer, "you
# are not authenticated". With False every authentication failure on this
# application produces one identical response, which is the property
# test_every_rejection_looks_identical pins down.
#
# It also decouples our public error contract from a library default that has
# already changed once. What our API returns should change when WE decide it
# does, not when a dependency revises its mind.
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT from POST /auth/login")

BearerToken = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


def _unauthenticated() -> HTTPException:
    """Build the single 401 used for every authentication failure.

    ONE response for missing, malformed, expired, wrongly-signed, wrong-type,
    and orphaned tokens - the same reasoning as TokenError in core/tokens.py
    and InvalidCredentials in services/auth.py.

    Distinguishing them would build an oracle. An attacker forging tokens
    would learn which part of the forgery to fix next: "expired" confirms the
    signature verified, "unknown user" confirms both the signature AND that
    the id shape is right. Each distinction turns blind guessing into a guided
    search. The client can do nothing differently in any case - obtain a new
    token - so the detail serves only the attacker.

    Returned rather than raised so call sites read `raise _unauthenticated()`,
    which keeps the control flow visible where it happens.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        # Required by RFC 6750 on a 401 from a bearer-protected endpoint.
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(credentials: BearerToken, db: DbSession) -> User:
    """Resolve the bearer token on this request to the user it identifies.

    WHY THIS QUERIES THE DATABASE AT ALL.

    The token already carries the user id, so trusting it alone would cost
    zero queries. We load the row anyway, and the reason is REVOCATION: a
    token is a signed claim about the past, and nothing about it changes when
    the account behind it does. Without this lookup, deactivating a user would
    have no effect until their token expired - up to
    ACCESS_TOKEN_EXPIRE_MINUTES of continued access to a finance account
    somebody has already decided to lock out. The lookup closes that window to
    the next request.

    The cost is one primary-key hit per authenticated request, served from the
    index and usually from the identity map. That is the cheapest query this
    application will ever run, and it is the price of statelessness having an
    off switch.

    Raises:
        HTTPException: 401 if the token is absent or not valid; 403 if it is
            valid but names a deactivated account.
    """
    if credentials is None:
        # No Authorization header, or one that is not a Bearer scheme.
        raise _unauthenticated()

    try:
        user_id = decode_access_token(credentials.credentials)
    except TokenError:
        # Expired, forged, malformed, or a refresh token used as an access
        # token. Deliberately indistinguishable - see _unauthenticated.
        raise _unauthenticated() from None

    user = await get_user_by_id(db, user_id)

    if user is None:
        # A validly-signed token naming a row that no longer exists. Not an
        # error on our side: the account was deleted after the token was
        # issued. Same 401 - the token is simply no longer usable.
        raise _unauthenticated()

    if not user.is_active:
        # 403, not 401, and safe to distinguish for exactly the reason login's
        # InactiveUser is: this is only reachable by someone holding a valid
        # token we issued to this account. They have already proved ownership,
        # so the fact reveals nothing new - and answering "invalid
        # credentials" would send them to re-authenticate, which cannot help.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        )

    return user


# The authenticated user for the current request.
#
# Adding this to a handler's signature is what makes the endpoint protected -
# there is no separate decorator or middleware to remember, and forgetting it
# leaves the endpoint public in a way that is visible in the signature itself.
CurrentUser = Annotated[User, Depends(get_current_user)]
