"""Authentication business logic.

As with every service module, no FastAPI import: these rules must hold for a
CLI command or a background job, not only for an HTTP request.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import dummy_verify, hash_password, needs_rehash, verify_password
from app.models import User
from app.services.user import get_user_by_email


class InvalidCredentials(Exception):
    """Raised when an email/password pair does not identify an active user.

    ONE exception for "no such user" AND "wrong password", deliberately.

    Separate exceptions would be an invitation to render separate messages,
    and "no account with that email" tells an attacker holding a list of
    addresses exactly which people have accounts here. For a finance product
    that fact is sensitive before any password is involved - it identifies
    customers, and it halves the work of a credential-stuffing run.

    Note the contrast with REGISTRATION, which does return a distinct 409.
    There the leak-free alternative needs transactional email we do not have,
    so the disclosure is accepted consciously. Here it costs nothing to keep
    the secret, so we keep it.
    """


class InactiveUser(Exception):
    """Raised when valid credentials belong to a deactivated account.

    Distinct from InvalidCredentials, and safe to be distinct: it is only ever
    raised AFTER the password has been verified. Telling someone who has
    already proved they own the account that it is deactivated reveals nothing
    they do not know, and telling them "invalid credentials" instead would
    send them into a password-reset loop that cannot possibly help.
    """


async def authenticate_user(session: AsyncSession, *, email: str, password: str) -> User:
    """Verify credentials and return the user.

    Raises:
        InvalidCredentials: unknown email, or wrong password.
        InactiveUser: correct credentials for a deactivated account.
    """
    user = await get_user_by_email(session, email)

    if user is None:
        # CLOSING THE TIMING SIDE CHANNEL.
        #
        # Returning here immediately would make the unknown-email path finish
        # in ~1ms while the wrong-password path takes ~64ms, because only the
        # latter runs Argon2. Both answer "invalid credentials", so the BODY
        # leaks nothing - but the CLOCK leaks everything, and response time is
        # trivially measurable over the network.
        #
        # dummy_verify performs a real Argon2 verification against a throwaway
        # hash, so this branch costs what the other branch costs. It is the
        # only reason that function exists.
        dummy_verify()
        raise InvalidCredentials

    if not verify_password(password, user.hashed_password):
        raise InvalidCredentials

    if not user.is_active:
        raise InactiveUser

    # SILENT HASH UPGRADE.
    #
    # This is the one moment the plaintext password exists in memory, so it is
    # the only moment a stored hash can be re-computed with stronger
    # parameters. We do not hold anyone's password - that is the whole point -
    # so hashes can never be upgraded in bulk.
    #
    # Users therefore migrate one at a time, as they log in, and nobody is
    # ever asked to reset anything. Without this, the cost parameters chosen
    # today are frozen for the lifetime of every existing account.
    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(password)
        await session.commit()

    return user
