"""User business logic.

LAYERING - this module must never import FastAPI.

Business rules do not belong to a transport. Keeping HTTP out of here means
this code can be called from a CLI command, a background job, or a test
without a request object, and it means the rules can be tested without an HTTP
client at all.

The practical consequence is that failures are raised as DOMAIN exceptions,
not as HTTPException. "This email is taken" is a fact about our data; that it
becomes a 409 is a decision the API layer makes. Raising HTTPException here
would quietly weld the rules to the web.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import User


class EmailAlreadyRegistered(Exception):
    """Raised when an email address already belongs to an account.

    A domain exception, deliberately not an HTTPException - see the module
    docstring. The API layer translates it into a status code.
    """


# PostgreSQL SQLSTATE for unique_violation.
#
# These five-character codes are part of the documented PostgreSQL error
# protocol and are stable across versions, which is what makes them safe to
# branch on. Matching the constraint NAME out of the error message would work
# today and break the first time a message is reworded or localised.
_UNIQUE_VIOLATION = "23505"


def _is_unique_violation(exc: IntegrityError) -> bool:
    """True if this IntegrityError is a duplicate-key failure specifically.

    `exc.orig` is the driver's own exception - asyncpg's - which carries the
    SQLSTATE. IntegrityError covers EVERY integrity constraint, so without
    this check a CHECK or NOT NULL or FOREIGN KEY failure would be caught by
    the same `except` clause.
    """
    return getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Look up a user by email address, or None.

    The caller is responsible for passing a normalised (lowercased) address.
    Every stored address is lowercase - the CHECK constraint on the table
    guarantees it - so a mixed-case argument here simply finds nothing.
    """
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str | None = None,
) -> User:
    """Register a new user and return the persisted row.

    Keyword-only arguments (note the `*`) are deliberate: `create_user(db, a,
    b)` gives no clue at the call site which of `a` and `b` is the email and
    which is the password, and swapping them would store an email address as a
    password hash. Naming them makes that mistake impossible to write.

    THE DUPLICATE CHECK IS DELIBERATELY DONE TWICE.

    The `get_user_by_email` call below is NOT what guarantees uniqueness. It
    is a courtesy: it produces a clean error without a failed INSERT. Between
    that SELECT and the INSERT, another request can register the same address
    - two concurrent registrations both find nothing, both proceed, and both
    insert. That is a time-of-check-to-time-of-use race, and no amount of
    Python-side checking closes it.

    The UNIQUE constraint is the actual guarantee, and catching IntegrityError
    is how we handle losing that race. Both paths raise the same exception, so
    the caller cannot tell - and does not need to - which one fired.

    Raises:
        EmailAlreadyRegistered: the address already belongs to an account.
    """
    if await get_user_by_email(session, email) is not None:
        raise EmailAlreadyRegistered(email)

    user = User(
        email=email,
        # The plaintext password exists only in this expression. It is never
        # assigned to the model, never stored, and never logged.
        hashed_password=hash_password(password),
        full_name=full_name,
    )
    session.add(user)

    try:
        await session.commit()
    except IntegrityError as exc:
        # Roll back explicitly: after a failed flush the session is in an
        # unusable state, and leaving it that way makes the NEXT query fail
        # with a confusing PendingRollbackError somewhere unrelated.
        await session.rollback()

        # ONLY a unique violation means "this email is taken".
        #
        # IntegrityError covers every constraint on the table. Reporting all
        # of them as EmailAlreadyRegistered would be actively harmful: a
        # violation of the `email = lower(email)` CHECK means normalisation
        # was bypassed somewhere - a real bug - and answering "that email is
        # already registered" would hide it behind a plausible 409 forever.
        #
        # A mutation test found this. With the schema's lowercase validator
        # removed, the duplicate-detection test still PASSED, because the
        # CHECK violation was being relabelled as a duplicate. A test that
        # passes for the wrong reason is worse than one that fails.
        if _is_unique_violation(exc):
            raise EmailAlreadyRegistered(email) from exc
        raise

    # Load the server-assigned defaults (created_at, updated_at) that
    # PostgreSQL filled in during the INSERT.
    await session.refresh(user)
    return user
