"""The User ORM model - the first real table in the schema.

Every user-owned table added later (transactions, budgets, goals) will carry a
foreign key to this one, so its shape is worth getting right now.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import uuid7
from app.db.base import Base

# RFC 5321 caps a full email address at 254 characters. The limit is declared
# so the database enforces it too - validation in Pydantic protects the API,
# but not a script or a migration writing directly to the table.
EMAIL_MAX_LENGTH = 254

# An argon2id hash in its standard encoded form is ~95 characters. 255 leaves
# headroom for stronger parameters (higher memory cost lengthens the string)
# without a migration.
PASSWORD_HASH_MAX_LENGTH = 255


class User(Base):
    """A registered account.

    Note what is NOT here: no plaintext password field, ever. The API receives
    a password, hashes it, and stores only the hash. This class has no way to
    represent a plaintext password, which makes storing one by accident
    impossible rather than merely discouraged.
    """

    __tablename__ = "users"

    __table_args__ = (
        # Defence in depth for case-insensitive email.
        #
        # "Alice@example.com" and "alice@example.com" are the same mailbox, but
        # to a UNIQUE index they are two different strings - so without
        # normalisation the same person can register twice and log in to the
        # wrong account.
        #
        # We normalise to lowercase in the Pydantic schema, at the edge. This
        # constraint makes the database refuse a non-normalised value even if
        # something bypasses that path (a migration, a fixture, a script). The
        # schema is the convenience; this is the guarantee.
        #
        # The alternative is PostgreSQL's CITEXT type, which we skip: it needs
        # a CREATE EXTENSION, behaves surprisingly in joins against plain text
        # columns, and buys us nothing a lowercase invariant does not.
        CheckConstraint("email = lower(email)", name="email_is_lowercase"),
    )

    # --- Identity -------------------------------------------------------------
    # `default` (not `server_default`): the ID is generated in Python, before
    # the INSERT. That means the application knows the ID without a round trip
    # and can build related rows in one flush. See app/core/ids.py for why v7.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid7,
    )

    # --- Credentials ----------------------------------------------------------
    # unique=True is what makes duplicate registration impossible. Note there is
    # deliberately no `index=True` alongside it: in PostgreSQL a UNIQUE
    # constraint is *implemented* as a unique index, so adding one would create
    # a second, redundant index - extra disk and a slower write on every insert
    # and update, for no read benefit.
    email: Mapped[str] = mapped_column(
        String(EMAIL_MAX_LENGTH),
        unique=True,
        nullable=False,
    )

    # Named `hashed_password`, never `password`. The name is the documentation:
    # anyone reading a query, a log line, or a migration can see at a glance
    # that no plaintext is involved.
    hashed_password: Mapped[str] = mapped_column(
        String(PASSWORD_HASH_MAX_LENGTH),
        nullable=False,
    )

    # --- Profile --------------------------------------------------------------
    # Optional: requiring a real name at signup adds friction and collects data
    # we do not need in order to run the product.
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- Status ---------------------------------------------------------------
    # Deactivating beats deleting. A deleted user would orphan or cascade away
    # their financial history; a deactivated one keeps the data intact while
    # every login and every authenticated request is refused.
    #
    # server_default (not just default) so rows created outside the ORM - by a
    # migration backfill or a psql session - still get the right value.
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    is_superuser: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    # --- Timestamps -----------------------------------------------------------
    # timezone=True gives TIMESTAMPTZ. A naive TIMESTAMP would record "14:30"
    # with no way to know whose 14:30 - and a finance app that cannot order
    # events across time zones cannot produce a correct statement.
    #
    # func.now() is evaluated by PostgreSQL, not Python, so the clock that
    # matters is the database server's. Application servers drift; a single
    # database clock keeps every row consistently ordered.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # onupdate fires on ORM updates; server_onupdate would need a trigger, which
    # is not worth it while all writes go through the application.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        """Debug representation.

        Deliberately excludes hashed_password. Reprs end up in log files, error
        trackers, and terminal scrollback - none of which should ever hold
        credential material, even hashed.
        """
        return f"<User id={self.id} email={self.email!r} active={self.is_active}>"
