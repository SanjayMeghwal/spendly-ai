"""Business logic for registering and authenticating users.

No FastAPI imports here, by design (see app/services/exceptions.py). These
functions take a session and plain values, and return models or raise domain
errors. They are callable from a request handler, a test, a CLI command, or a
worker without change.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    fake_verify_password,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.models.user import User
from app.schemas.user import UserCreate
from app.services.exceptions import EmailAlreadyRegisteredError

logger = logging.getLogger(__name__)


def _normalise_email(email: str) -> str:
    """Canonicalise an address the same way the schema does.

    The Pydantic schema already lowercases registration input, but these
    functions are also callable directly - from a test, a CLI command, a future
    admin tool - and a lookup for "Ada@example.com" must find the row stored as
    "ada@example.com". Normalising here means correctness does not depend on
    the caller having gone through the API.
    """
    return email.strip().lower()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Return the user with this email, or None."""
    result = await db.execute(select(User).where(User.email == _normalise_email(email)))
    # scalar_one_or_none, not first(): it raises if the query somehow returns
    # more than one row. Given the UNIQUE constraint that should be impossible,
    # and if it ever happens we want a loud failure rather than an arbitrary
    # pick between two accounts.
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Return the user with this ID, or None.

    Used on every authenticated request, to turn the `sub` claim of a token
    back into a live user row.
    """
    return await db.get(User, user_id)


async def register_user(db: AsyncSession, user_in: UserCreate) -> User:
    """Create a new account.

    Raises:
        EmailAlreadyRegisteredError: an account with that email exists.
    """
    email = _normalise_email(user_in.email)

    # The friendly path: look first, so the ordinary duplicate gets a clean
    # domain error rather than a database exception.
    if await get_user_by_email(db, email) is not None:
        raise EmailAlreadyRegisteredError(email)

    user = User(
        email=email,
        # The one place a plaintext password is converted. `user_in.password`
        # is not stored, not logged, and goes out of scope with this function.
        hashed_password=hash_password(user_in.password),
        full_name=user_in.full_name,
    )
    db.add(user)

    try:
        # The service owns the transaction boundary - get_db deliberately does
        # not auto-commit, so what is persisted is always visible in the code
        # that intended it.
        await db.commit()
    except IntegrityError as exc:
        # THE CHECK ABOVE IS NOT ENOUGH, AND THIS IS NOT DEFENSIVE PADDING.
        #
        # Two concurrent registrations for the same address can both run their
        # SELECT before either INSERTs. Both see "no such user", both proceed,
        # and the second INSERT violates the UNIQUE constraint. The window is
        # small but it is real, and under a signup burst it will be hit.
        #
        # The database constraint is the only true guarantee of uniqueness; the
        # SELECT is an optimisation for the common case. Catching the violation
        # here converts a 500 into the same clean 409 the friendly path returns.
        await db.rollback()
        logger.info("Registration lost a race on duplicate email; returning conflict")
        raise EmailAlreadyRegisteredError(email) from exc

    logger.info("Registered new user %s", user.id)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    """Return the user if these credentials are valid, otherwise None.

    A single None covers "no such account", "wrong password", and "account
    deactivated". That is deliberate: distinguishing them in the response would
    turn the login endpoint into a user-enumeration oracle, letting anyone test
    whether an address has an account here. For a finance product, membership
    alone is information worth protecting.
    """
    user = await get_user_by_email(db, email)

    if user is None:
        # Hash a throwaway value anyway. Returning immediately would make the
        # unknown-email path measurably faster than the wrong-password path,
        # and that timing difference is itself the enumeration oracle the
        # generic error message was meant to close.
        fake_verify_password()
        return None

    if not verify_password(password, user.hashed_password):
        return None

    if not user.is_active:
        logger.warning("Deactivated user %s attempted to log in", user.id)
        return None

    # A successful login is the only moment the plaintext password exists in
    # memory, so it is the only opportunity to upgrade a hash that was made
    # with weaker parameters than we now use.
    if password_needs_rehash(user.hashed_password):
        logger.info("Upgrading password hash parameters for user %s", user.id)
        user.hashed_password = hash_password(password)
        await db.commit()

    return user
