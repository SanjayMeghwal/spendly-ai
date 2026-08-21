"""Refresh-token sessions: issuing, rotating, and refusing them.

THE PROBLEM THIS SOLVES.

An access token is a bearer credential with no revocation, so its lifetime is
its entire containment strategy - 15 minutes here. But nobody wants to type a
password four times an hour, so something must be able to mint new access
tokens without the password. That something is the refresh token, and it is
necessarily long-lived, which makes it the most valuable credential in the
system: a stolen one is a month of access.

Three mechanisms make that acceptable, and they only work together:

  1. ROTATION - a refresh token may be used exactly once. Using it returns a
     new one and kills the old.
  2. REUSE DETECTION - presenting an already-used token means there are two
     copies in the world. We cannot tell which caller is the thief, so we end
     the whole session and make both re-authenticate.
  3. REVOCATION - every token has a row, so a session can be switched off.

Rotation alone is not enough: without detection, a thief who uses a stolen
token first simply takes over the session and the real user is quietly logged
out, which reads as an ordinary glitch. Detection is what turns theft into a
visible event rather than a silent handover.

As with every service module, no FastAPI import - these rules must hold for a
CLI command or a background job, not only for an HTTP request.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.tokens import TokenError, create_refresh_token, decode_refresh_token
from app.models import RefreshToken, User
from app.models.refresh_token import (
    REASON_LOGOUT,
    REASON_LOGOUT_ALL,
    REASON_REUSE_DETECTED,
    REASON_ROTATED,
)
from app.services.auth import InactiveUser
from app.services.user import get_user_by_id


class InvalidRefreshToken(Exception):
    """Raised for every way a refresh token can fail to be usable.

    ONE exception for: forged, expired, malformed, wrong type, unknown to us,
    already used, revoked, or belonging to an account that no longer exists.

    The same reasoning as TokenError and InvalidCredentials elsewhere in this
    codebase. Distinguishing "already used" from "bad signature" would tell an
    attacker probing with stolen or guessed tokens exactly how close they are:
    "already used" confirms the token was REAL. The client can do nothing
    differently in any case - log in again - so the detail would serve only
    the attacker.

    Note what is NOT folded in here: InactiveUser, which is raised separately
    and answered with a 403. That is safe to distinguish because it is only
    reachable by someone holding a valid, unused token we issued to that
    account - they have already proved ownership.
    """


class RefreshedSession(NamedTuple):
    """The result of a successful rotation."""

    user: User

    # The REPLACEMENT refresh token. The one that was presented is dead by the
    # time this is returned, so a caller that forgets to hand this back to the
    # client has logged the user out.
    refresh_token: str


def _expiry_from_now() -> datetime:
    """When a session started right now would end."""
    settings = get_settings()
    return datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)


async def issue_refresh_token(session: AsyncSession, user_id: uuid.UUID) -> str:
    """Start a new session and return its first refresh token.

    Called on LOGIN - the moment a password was verified. Every call creates a
    new `family_id`, so one family is one sign-in on one device, and revoking
    a family logs out that device and only that device.

    The row is written BEFORE the token is signed, and that order matters: a
    token whose row does not exist is refused by rotate_refresh_token, so a
    crash between the two lines costs the user a login rather than creating a
    credential we cannot revoke.
    """
    token_id = uuid.uuid4()
    expires_at = _expiry_from_now()

    session.add(
        RefreshToken(
            id=token_id,
            user_id=user_id,
            # A brand-new lineage. Rotation reuses this value; nothing else
            # ever generates one.
            family_id=uuid.uuid4(),
            expires_at=expires_at,
        )
    )
    await session.commit()

    return create_refresh_token(user_id, token_id=token_id, expires_at=expires_at)


async def revoke_family(
    session: AsyncSession,
    family_id: uuid.UUID,
    *,
    reason: str,
) -> None:
    """Revoke every live token in one session lineage.

    A single bulk UPDATE rather than loading rows and mutating them: this must
    be one statement so that no window exists in which half a family is
    revoked. In practice a family has one live token, but a concurrent
    rotation can briefly produce two, and this closes both.

    `revoked_at IS NULL` in the WHERE clause is deliberate. Tokens revoked
    earlier keep their ORIGINAL timestamp and reason, so the audit trail
    records why each token actually died rather than being overwritten by
    whatever ended the session last.

    Does not commit. The caller decides the transaction boundary, because
    revocation is almost always part of a larger change - rotating a token,
    changing a password - and committing here would let those changes come
    apart.
    """
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )


async def revoke_all_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    reason: str,
) -> None:
    """Revoke every live token belonging to one user, across all their sessions.

    The `user_id` sibling of revoke_family: that one ends a device, this one
    ends an account's sessions everywhere. Both leave already-revoked rows
    untouched so the audit trail keeps the reason each token actually died of.

    Does not commit - see revoke_family. This is always part of a larger
    change (a logout-everywhere, a password change) that must land atomically
    with it.
    """
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )


async def log_out_everywhere(
    session: AsyncSession,
    user: User,
    *,
    reason: str = REASON_LOGOUT_ALL,
) -> None:
    """End every session this user has, and invalidate their access tokens too.

    WHY THIS IS NOT JUST revoke_all_for_user.

    Revoking refresh tokens ends the ability to mint NEW access tokens. It
    does nothing to the ones already issued, which keep working until they
    expire - up to ACCESS_TOKEN_EXPIRE_MINUTES of continued access to a
    finance account somebody has just said they want locked out of.

    For ordinary logout that gap is an acceptable trade. Here it is not: this
    is what a user reaches for when they believe someone else has their
    session, and "you are logged out everywhere, in fifteen minutes" is not an
    answer. Bumping `token_version` closes it on the next request, because
    every access token carries the version it was minted under.

    THE INCREMENT IS DONE IN SQL, not as `user.token_version += 1`.

    The Python version reads, adds, and writes - so two concurrent calls both
    read the same value and both write the same result, and one of the two
    bumps is silently lost. `token_version + 1` evaluated by PostgreSQL under
    the row lock cannot lose an update. It matters here specifically because
    both callers of this function are security actions a worried user is
    likely to trigger twice.

    Both writes commit together: a session revocation without the version bump
    would leave access tokens alive, and a bump without the revocation would
    leave refresh tokens able to mint new ones.
    """
    await revoke_all_for_user(session, user.id, reason=reason)

    await session.execute(
        update(User).where(User.id == user.id).values(token_version=User.token_version + 1)
    )

    await session.commit()


async def log_out(session: AsyncSession, token: str) -> None:
    """End the session a refresh token belongs to. Never fails.

    WHY THIS RETURNS NOTHING AND RAISES NOTHING.

    A caller cannot tell whether the token was live, already spent, unknown,
    forged, or the wrong type - every case ends the same way. That is
    deliberate and it is what RFC 7009 (OAuth 2.0 Token Revocation) specifies:
    a revocation endpoint answers success even for an invalid token, because
    the client's goal - "this token must not work" - is satisfied either way.

    The alternative leaks. If a forged token produced an error while a genuine
    but already-revoked one produced success, this endpoint would become a
    free signature-validation oracle: paste a candidate token, read the status
    code, learn whether it was ever real. /refresh gives an attacker nothing
    of the sort, and logout must not be the softer door.

    The cost, stated plainly: a client that sends the WRONG token - an access
    token, say - is told the logout succeeded when nothing was revoked. That
    is a real usability trap and it is the price of the property above.

    NOTE what this does NOT do: invalidate the ACCESS token the client is
    holding. Nothing can - an access token has no server-side state, which is
    the trade that makes it cheap. It expires on its own within
    ACCESS_TOKEN_EXPIRE_MINUTES, and the client should discard it. The gap is
    bounded and deliberate; see /auth/logout-all for the case where it is not
    acceptable.

    An already-revoked family is left exactly as it was - see revoke_family -
    so logging out twice is a no-op rather than an overwritten audit trail.
    """
    try:
        claims = decode_refresh_token(token)
    except TokenError:
        # Not a token we issued, or no longer a valid one. Nothing to revoke,
        # and nothing to report.
        return

    stored = await session.get(RefreshToken, claims.token_id)

    if stored is None or stored.user_id != claims.user_id:
        # Names no row, or names a row belonging to someone else. Refusing to
        # act on a mismatch is what stops a forged `sub` from ending another
        # user's session.
        return

    # Deliberately NOT treated as reuse, even when the token was already
    # spent. A client whose refresh failed and then logs out is holding a
    # spent token through completely normal use, and the outcome is identical
    # anyway: the family ends. Recording it as an attack would fill the audit
    # trail with false positives and hide the real ones.
    await revoke_family(session, stored.family_id, reason=REASON_LOGOUT)
    await session.commit()


async def rotate_refresh_token(session: AsyncSession, token: str) -> RefreshedSession:
    """Exchange a refresh token for its replacement.

    Raises:
        InvalidRefreshToken: the token is not usable, for any reason.
        InactiveUser: the token is valid but the account is deactivated.
    """
    # 1. CRYPTOGRAPHY FIRST, because it is free.
    #
    # Rejecting a forged or expired token here costs no database round trip,
    # which means garbage traffic cannot be turned into database load. Only a
    # token we actually signed gets to touch a table.
    try:
        claims = decode_refresh_token(token)
    except TokenError:
        raise InvalidRefreshToken from None

    # 2. THE ROW IS THE AUTHORITY.
    #
    # `with_for_update` takes a row-level lock held until this transaction
    # ends, which is what makes rotation safe under concurrency. Without it,
    # two simultaneous requests carrying the same token both read an unrevoked
    # row, both revoke it, and both mint a replacement - leaving two live
    # tokens in one family and, worse, no reuse detected. With the lock the
    # second request blocks, then reads the row the first one revoked, and
    # correctly treats it as reuse.
    stored = await session.get(RefreshToken, claims.token_id, with_for_update=True)

    if stored is None:
        # A token we signed naming a row that does not exist. Either the row
        # was deleted with its user, or the database was reset. Nothing to
        # rotate and nothing to revoke.
        raise InvalidRefreshToken

    if stored.user_id != claims.user_id:
        # The signed `sub` and the row disagree about who owns this token.
        # Unreachable without our signing key, so reaching it means either the
        # key leaked or our own issuing code is inconsistent. Refuse, and
        # never trust the claim over the row.
        raise InvalidRefreshToken

    if stored.revoked_at is not None:
        # REUSE DETECTED - the security-critical branch.
        #
        # This token was already exchanged (or the session was ended). Since a
        # refresh token is used exactly once, a second use means two parties
        # hold the same token: the legitimate client and someone who copied
        # it. We cannot tell which one is calling now.
        #
        # Refusing only this request would be a mistake. If the thief rotated
        # first, they hold the CURRENT token and this is the real user being
        # turned away - and leaving the thief's session live is the worst
        # possible outcome. Killing the whole family costs the honest user one
        # login and costs the attacker everything.
        await revoke_family(session, stored.family_id, reason=REASON_REUSE_DETECTED)
        await session.commit()
        raise InvalidRefreshToken

    now = datetime.now(UTC)

    if stored.expires_at <= now:
        # Defence in depth: the `exp` claim has already been checked above, so
        # this fires only if the claim and the column disagree - which would
        # mean a token was signed with a lifetime the row never granted. The
        # row wins, always. It is the half of the credential an attacker
        # cannot influence.
        raise InvalidRefreshToken

    user = await get_user_by_id(session, stored.user_id)

    if user is None:  # pragma: no cover - the CASCADE foreign key prevents it
        # A token row whose user does not exist. Unreachable while
        # `refresh_tokens.user_id` carries ON DELETE CASCADE: deleting a user
        # deletes their rows, so a deleted account is caught by the
        # `stored is None` branch above instead.
        #
        # Kept anyway, and it is not dead weight. get_user_by_id returns
        # `User | None`, and the alternative to this branch is an assert that
        # would turn a schema change into a 500. If the constraint is ever
        # relaxed, this refuses safely rather than crashing.
        raise InvalidRefreshToken

    if not user.is_active:
        # Deactivation takes effect on the next refresh as well as on the next
        # authenticated request. Distinguished from InvalidRefreshToken, and
        # safe to distinguish - see the class docstring.
        raise InactiveUser

    # 3. ROTATE - revoke and replace, in ONE transaction.
    #
    # Both writes commit together or neither does. Committing the revocation
    # separately would mean a crash in between leaves the user holding a dead
    # token with no replacement: logged out by an implementation detail.
    stored.revoked_at = now
    stored.revoked_reason = REASON_ROTATED

    replacement_id = uuid.uuid4()
    session.add(
        RefreshToken(
            id=replacement_id,
            user_id=user.id,
            # SAME family - this is a continuation of one login, not a new one.
            family_id=stored.family_id,
            # SAME expiry, deliberately not recomputed. The session ends 30
            # days after sign-in however often it is refreshed; recomputing
            # here would make the window slide forward forever, so a stolen
            # token could be renewed indefinitely and the user would never be
            # asked for their password again.
            expires_at=stored.expires_at,
        )
    )
    await session.commit()

    return RefreshedSession(
        user=user,
        refresh_token=create_refresh_token(
            user.id,
            token_id=replacement_id,
            expires_at=stored.expires_at,
        ),
    )
