"""Authentication endpoints.

HTTP only. Routing, status codes, and the translation of domain exceptions
into responses. No business logic lives here - see app/services/user.py.
"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.tokens import create_access_token
from app.models import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.schemas.user import UserCreate, UserRead
from app.services.auth import InactiveUser, InvalidCredentials, authenticate_user
from app.services.refresh import (
    InvalidRefreshToken,
    issue_refresh_token,
    log_out,
    log_out_everywhere,
    rotate_refresh_token,
)
from app.services.user import EmailAlreadyRegistered, create_user

router = APIRouter(prefix="/auth", tags=["authentication"])


def _issued(user: User, refresh_token: str) -> TokenResponse:
    """Build the response both /login and /refresh return.

    Shared so the two endpoints cannot drift. A client must not have to care
    whether a token pair came from a password or from a rotation - the shape,
    the field names, and the advertised access-token lifetime are identical,
    which is what lets client code have exactly one path for storing them.

    Takes the USER rather than a user id because an access token now carries
    the account's `token_version`, and the only honest source of that number
    is the row we just loaded. Passing an id would mean either querying again
    or guessing.
    """
    settings = get_settings()

    return TokenResponse(
        access_token=create_access_token(user.id, token_version=user.token_version),
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def _unauthenticated() -> HTTPException:
    """The single 401 for a credential that cannot be validated.

    Mirrors app/api/deps.py deliberately: an attacker probing /refresh must
    not be able to tell a forged token from an expired one from one that was
    already used. The last of those is the valuable signal - "already used"
    would confirm the token was genuine - and it is exactly what this hides.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        # Required by RFC 6750 on a 401 from a bearer-protected endpoint.
        headers={"WWW-Authenticate": "Bearer"},
    )


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


@router.post(
    "/login",
    # 200, not 201. Logging in creates no resource - it exchanges credentials
    # for a token. The token is not a thing at a URL that can be fetched again.
    status_code=status.HTTP_200_OK,
    response_model=TokenResponse,
    summary="Exchange credentials for a token pair",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Invalid email or password."},
        status.HTTP_403_FORBIDDEN: {"description": "Account is deactivated."},
    },
)
async def login(credentials: LoginRequest, db: DbSession) -> TokenResponse:
    """Authenticate and issue an access token plus a refresh token.

    ONE ERROR MESSAGE FOR BOTH FAILURE MODES.

    An unknown email and a wrong password both return exactly the same 401
    with exactly the same body. Saying "no account with that email" would
    confirm which addresses are registered here, letting an attacker turn a
    list of emails into a list of customers - sensitive on its own for a
    finance product, and the first half of a credential-stuffing run.

    The service also equalises the TIMING of the two paths, since a response
    that arrives 60x faster leaks the same fact the message would have. See
    dummy_verify in app/core/security.py.

    This is the opposite call from /register, which does return a distinct
    409, and the difference is deliberate: there, hiding the fact would need
    transactional email we do not have, so the disclosure is accepted
    consciously. Here, hiding it is free.

    403 for a deactivated account is safe to distinguish, because it is only
    reachable AFTER the password has been verified. Someone who has proved
    they own the account learns nothing new, and answering "invalid
    credentials" would send them into a password-reset loop that cannot help.
    """
    try:
        user = await authenticate_user(
            db,
            email=credentials.email,
            password=credentials.password.get_secret_value(),
        )
    except InvalidCredentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            # Identical for both causes - see the docstring.
            detail="Incorrect email or password.",
            # RFC 6750 requires this header on a 401 from a bearer-token
            # endpoint. It tells a client HOW to authenticate rather than
            # leaving it to guess.
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except InactiveUser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        ) from None

    # A successful password check is the ONLY place a new session begins.
    # Every other token in the system is descended from this call by rotation,
    # which is what makes "when did this session start, and from what?"
    # answerable.
    return _issued(user, await issue_refresh_token(db, user.id))


@router.post(
    "/refresh",
    status_code=status.HTTP_200_OK,
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new token pair",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Refresh token is missing or unusable."},
        status.HTTP_403_FORBIDDEN: {"description": "Account is deactivated."},
    },
)
async def refresh(payload: RefreshRequest, db: DbSession) -> TokenResponse:
    """Rotate a refresh token, returning a new access token and a new refresh token.

    THIS ENDPOINT IS NOT AUTHENTICATED IN THE USUAL SENSE, AND MUST NOT BE.

    There is no `CurrentUser` here, which looks like an omission and is not.
    The whole purpose of refreshing is to recover from an EXPIRED access
    token, so requiring a valid one would make the endpoint useless precisely
    when it is needed. The refresh token in the body is the credential, and it
    is a stronger one: it is checked against a database row, which an access
    token never is.

    THE OLD TOKEN IS DEAD WHEN THIS RETURNS.

    A client must replace its stored refresh token with the one in this
    response. Retrying this call with the old value - after a dropped
    connection, or an over-eager retry interceptor - is indistinguishable from
    a stolen token being replayed, and is answered the same way: the entire
    session is revoked and the user must sign in again. That is a deliberate,
    documented cost of reuse detection. Being unable to tell an honest retry
    from a theft, the safe assumption is theft.
    """
    try:
        session = await rotate_refresh_token(db, payload.refresh_token.get_secret_value())
    except InvalidRefreshToken:
        raise _unauthenticated() from None
    except InactiveUser:
        # Consistent with /login and with the CurrentUser dependency: 403, not
        # 401, and safe to distinguish because it is only reachable by someone
        # holding a live token we issued to this account.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated.",
        ) from None

    return _issued(session.user, session.refresh_token)


@router.post(
    "/logout",
    # 204, not 200. There is nothing useful to return - the client already
    # knows which token it sent - and a body would only invite a client to
    # parse it for a distinction this endpoint deliberately does not make.
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the session a refresh token belongs to",
    responses={
        status.HTTP_204_NO_CONTENT: {
            "description": "The session is ended, or was never valid. Both answer the same."
        },
    },
)
async def logout(payload: RefreshRequest, db: DbSession) -> None:
    """Revoke a refresh token and every token rotated from it.

    WHY LOGOUT NEEDS A SERVER AT ALL.

    The tempting implementation is to delete the tokens in the browser and
    call it done. That is not logout, it is forgetting: the refresh token
    remains valid for up to 30 days, so anyone who captured it - from a log, a
    backup, a shared machine, a proxy - can still mint access tokens long
    after the user believes they signed out. "Log out" has to mean the
    credential stops working, and only the server can make that true.

    WHY THIS ALWAYS ANSWERS 204.

    Valid, expired, already revoked, unknown, forged, or the wrong kind of
    token: all 204. RFC 7009 specifies exactly this for revocation endpoints,
    and the reason is that any distinction here would be a signature-checking
    oracle - paste a token, read the status, learn whether it was ever real.
    /refresh reveals nothing of the sort, and logout must not be the softer
    door into the same question.

    WHAT THIS DOES NOT DO.

    The client's ACCESS token keeps working until it expires - up to
    ACCESS_TOKEN_EXPIRE_MINUTES. Nothing can revoke it; having no server-side
    state is precisely what makes it cheap to verify. The window is bounded
    and the client should discard the token, but an attacker who already
    holds a copy keeps it until it dies on its own. When that is not good
    enough - a password change, or a laptop that is genuinely gone - the
    answer is /auth/logout-all, which ends every session at once.

    Not authenticated, deliberately. Requiring a live access token would mean
    a user whose access token had just expired could not log out, which is
    both absurd and the exact moment they are most likely to try.
    """
    await log_out(db, payload.refresh_token.get_secret_value())


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End every session on every device",
    responses={
        status.HTTP_204_NO_CONTENT: {"description": "Every session for this account is ended."},
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid access token."},
        status.HTTP_403_FORBIDDEN: {"description": "Account is deactivated."},
    },
)
async def logout_all(current_user: CurrentUser, db: DbSession) -> None:
    """Revoke every refresh token for this account, and every access token with it.

    THE DIFFERENCE FROM /auth/logout, WHICH IS THE WHOLE POINT.

    Ordinary logout ends one session and leaves that client's access token
    working until it expires - a bounded, deliberate gap. This endpoint is
    what a user reaches for when they believe someone ELSE has their session,
    and there the gap is unacceptable: fifteen more minutes of access to a
    finance account is exactly what they are trying to stop.

    So this also increments the account's `token_version`, which invalidates
    every access token ever issued to it - including the one used to make this
    request. That is intended, not a bug: the caller must log in again, and so
    must the attacker, who cannot.

    AUTHENTICATED, unlike /auth/logout.

    A refresh token in the body would identify one session; this operation
    affects all of them, so it requires proof of current control of the
    account rather than possession of one credential. A caller whose access
    token has expired can refresh first - and a caller who cannot refresh has
    already lost nothing, since their sessions are what this would have ended.
    """
    await log_out_everywhere(db, current_user)


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    response_model=UserRead,
    summary="Get the authenticated user's own account",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid access token."},
        status.HTTP_403_FORBIDDEN: {"description": "Account is deactivated."},
    },
)
async def read_current_user(current_user: CurrentUser) -> User:
    """Return the account belonging to the presented token.

    The whole endpoint is one line, and that is the point: every check that
    matters - header present, signature valid, not expired, right token type,
    user still exists, user still active - happened in the CurrentUser
    dependency before this function was entered. A handler cannot forget a
    step it does not perform.

    NOTE THIS TAKES NO USER ID, AND MUST NOT.

    The obvious-looking alternative, `GET /users/{id}`, invites the worst bug
    in this class of API: reading the id from the URL and trusting it. Then
    anyone authenticated can fetch anyone else's account by changing a number
    - authenticated, but not authorised. Here the identity comes from the
    SIGNED TOKEN, which the caller cannot alter, so there is no id to confuse
    and no ownership check to forget.

    That principle generalises to every user-owned resource in this project:
    the owner is derived from the token, never accepted from the request.
    """
    return current_user
