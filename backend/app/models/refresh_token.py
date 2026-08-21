"""The RefreshToken model - one row per issued refresh token.

WHY THIS TABLE EXISTS AT ALL, GIVEN THAT JWTs ARE MEANT TO BE STATELESS.

Statelessness is a property, not a virtue. What it actually means is "we have
thrown away the ability to change our mind": an access token cannot be
withdrawn, because there is nothing to withdraw it from. For a 15-minute
credential that is an acceptable trade - the damage window is small and the
saving is a database round trip on every request.

For a 30-day credential it is not. A refresh token that cannot be revoked is a
month-long key to a finance account, surviving logout, password changes, and
the user noticing their laptop is gone. So refresh tokens are deliberately
STATEFUL, and this table is the state.

WHAT IS STORED HERE IS NOT A CREDENTIAL.

The row holds the token's id, not the token. The credential is the signed JWT,
and reproducing one requires SECRET_KEY, which lives in the environment and
never in the database. An attacker who dumps this entire table therefore holds
no usable tokens - only a list of which sessions exist. That is the reason the
JWT-plus-`jti` design needs no password-style hashing here, while an opaque
random token would have to be stored hashed.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# ------------------------------------------------------------------------------
# Why a token was revoked.
#
# Stored for AUDIT, never for authorisation - no code path branches on this
# value, because "revoked" is already a complete answer. It exists so that the
# question "why did my session end?" has a truthful answer six months later,
# and so that a spike of REUSE_DETECTED rows is visible as the attack signal
# it is.
#
# Plain module constants rather than a Python Enum or a PostgreSQL ENUM type:
# adding a value to a PostgreSQL enum needs a migration, and this column is
# read by humans, not matched by machines. New reasons are added as the code
# that causes them is written, not in advance.
# ------------------------------------------------------------------------------
REASON_ROTATED = "rotated"
REASON_REUSE_DETECTED = "reuse_detected"


class RefreshToken(Base):
    """One issued refresh token, and its position in a session.

    NOTE there is no `relationship()` to User in either direction, deliberately.
    This project uses async SQLAlchemy, where touching an unloaded relationship
    raises `MissingGreenlet` rather than lazily emitting a query. A convenience
    accessor that explodes unless somebody remembered to eager-load it is worse
    than no accessor: the service layer queries by `user_id` explicitly, which
    is one line and cannot fail at a distance.
    """

    __tablename__ = "refresh_tokens"

    __table_args__ = (
        # Both revocation columns are written together or not at all.
        #
        # Without this, a bug that sets `revoked_at` and forgets the reason
        # produces a session that ended for no recorded cause - and one that
        # sets only the reason produces a token that reads as revoked in the
        # audit trail while still WORKING. The second is a security failure,
        # so the invariant belongs in the database rather than in a code
        # review checklist.
        CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)",
            name="revocation_is_complete",
        ),
    )

    # --- Identity -------------------------------------------------------------
    # This id IS the token's `jti` claim. The token names the row; the row
    # decides whether the token still works.
    #
    # UUIDv4, like User.id, and for a sharper reason here: an enumerable
    # sequential id would let anyone holding one token guess the ids of other
    # people's sessions. Nothing in the API accepts a raw token id, so that is
    # not currently exploitable - but "not currently reachable" is a weak thing
    # to rest a design on when the alternative costs nothing.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="The token's jti claim. Names this row.",
    )

    # --- Ownership ------------------------------------------------------------
    # ondelete="CASCADE" is set on the DATABASE constraint, so PostgreSQL
    # removes these rows when a user is deleted even if the deletion happens in
    # psql rather than through the ORM. Sessions are worthless without their
    # user, and orphaned rows here would be a slow leak of a table that only
    # ever grows.
    #
    # index=True is explicit and load-bearing: every revocation query filters
    # by user_id, and a foreign key does NOT create an index in PostgreSQL (it
    # requires one on the REFERENCED column, not the referencing one). This is
    # the single most commonly missed index in a schema.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="The account this session belongs to.",
    )

    # --- Session lineage ------------------------------------------------------
    # Every rotation issues a NEW row that inherits this value, so a family is
    # one login: the token minted at sign-in, and every token that replaced it.
    #
    # This is what makes reuse detection meaningful. If an already-rotated
    # token is presented, either it was stolen or the legitimate client's
    # replacement never arrived - and we cannot tell which. The safe response
    # is to end the whole lineage, not just refuse the one token, because an
    # attacker who replayed an old token may already hold the current one. The
    # user is logged out of one device and must sign in again; that is the
    # correct price for the alternative being an undetected hijack.
    #
    # Indexed because that revocation query filters on it.
    family_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
        doc="Groups a login and every token rotated from it.",
    )

    # --- Lifetime -------------------------------------------------------------
    # Duplicated in the token's own `exp` claim, and that duplication is the
    # point: the claim lets any JWT library reject an expired token without a
    # query, and this column means expiry survives even if the signing key is
    # ever rotated. The two are written together in services/refresh.py and
    # must always agree.
    #
    # Set from the FAMILY's original expiry on rotation rather than recomputed,
    # which is what makes a session's lifetime absolute instead of sliding.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="UTC expiry. Matches the token's exp claim exactly.",
    )

    # --- Revocation -----------------------------------------------------------
    # NULL means live. A timestamp means dead, and records when.
    #
    # A boolean `is_revoked` would have been simpler and worse: rotation is
    # frequent, so this column is the audit trail of a session's history, and
    # "revoked" without "when" cannot answer whether a compromise happened
    # before or after a password change.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC revocation time. NULL means the token is still live.",
    )

    revoked_reason: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        doc="Audit only. One of the REASON_* constants in this module.",
    )

    # --- Timestamps -----------------------------------------------------------
    # Only created_at: a refresh token row is written once and revoked once,
    # never edited, so an `updated_at` would carry no information that
    # `revoked_at` does not already carry more precisely.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="UTC issue time, assigned by the database.",
    )

    def __repr__(self) -> str:
        """Debug representation.

        Safe to log in full: every value here is an opaque identifier or a
        timestamp. The credential itself - the signed token - is not stored on
        this object and cannot be reconstructed from it.
        """
        state = "revoked" if self.revoked_at is not None else "live"
        return f"<RefreshToken id={self.id} user_id={self.user_id} {state}>"
