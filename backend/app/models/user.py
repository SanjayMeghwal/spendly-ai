"""The User model - the first real table in the schema.

Everything a person owns in this application (transactions, budgets, goals,
categories) hangs off a row in this table. That makes two of the choices
below effectively permanent, so both are documented rather than left to be
rediscovered later.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """An authenticated account.

    NOTE ON WHAT IS DELIBERATELY ABSENT: there is no `is_superuser` or `role`
    column yet. Nothing in the application reads one, and a permissions flag
    that nothing enforces is worse than no flag - it reads like a security
    control while granting nothing. It arrives with the code that checks it.
    """

    __tablename__ = "users"

    __table_args__ = (
        # Enforce the lowercase-email rule in the DATABASE, not just in Python.
        #
        # The unique constraint on `email` is case-SENSITIVE, so without this
        # check "Sanjay@example.com" and "sanjay@example.com" are two distinct
        # rows - two accounts for one mailbox, and a login that resolves to
        # whichever row is found first. Normalising in the service layer
        # prevents that only for code paths that remember to normalise.
        #
        # This constraint makes the invariant unbreakable: a mixed-case insert
        # fails loudly at the database instead of silently creating the
        # duplicate. Chosen over the `citext` extension because it keeps the
        # rule visible in the schema rather than hidden in a column type, and
        # needs no extension installed in every environment.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    # --- Identity -------------------------------------------------------------
    # UUIDv4 rather than an auto-incrementing integer.
    #
    # Sequential integers are ENUMERABLE. If /api/v1/users/1 exists, an
    # attacker learns that user 2 and user 500 probably exist too, and can
    # infer how many customers we have from the highest id that responds. In
    # a finance product that is both a privacy leak and a scraping map.
    #
    # `default=uuid.uuid4` (the function, NOT uuid.uuid4()) is generated in
    # Python at INSERT time. Passing the called form would evaluate it once at
    # import and hand every user the same id.
    #
    # Trade-off accepted: random UUIDs scatter B-tree index inserts rather
    # than appending in order, which costs write throughput at very large
    # scale. UUIDv7 would fix that but needs a third-party package on Python
    # 3.12 / PostgreSQL 17. Revisit only if writes ever actually hurt.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Unguessable public identifier, safe to expose in URLs.",
    )

    # --- Credentials ----------------------------------------------------------
    # 320 characters is the RFC 5321 maximum: 64 for the local part, 1 for the
    # '@', 255 for the domain.
    #
    # unique=True creates the constraint that makes duplicate registration a
    # database error rather than a race condition. Checking "does this email
    # exist?" in Python first is NOT sufficient: two simultaneous requests can
    # both find nothing and both insert. The constraint is the only real
    # guarantee; the Python check just produces a friendlier message.
    #
    # Addresses are normalised to lowercase before they reach this column, so
    # this plain unique constraint is genuinely case-insensitive in practice.
    # index=True is implied by unique=True in PostgreSQL (a unique constraint
    # is backed by an index), so it is not specified twice.
    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        nullable=False,
        doc="Lowercased email address. Also the login identifier.",
    )

    # The ARGON2 HASH, never the password. Named `hashed_password` and not
    # `password` on purpose: the name makes an accidental assignment of a
    # plaintext value look wrong at the call site.
    #
    # 255 characters leaves generous headroom. An argon2id hash at current
    # defaults is ~97 characters, but the encoded form embeds the cost
    # parameters, so it grows if we raise them.
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Argon2id hash. Never logged, never returned by any API.",
    )

    # --- Profile --------------------------------------------------------------
    # Optional: requiring a real name to sign up costs conversions and we do
    # not need it. Mapped[str | None] is what makes the column NULLable -
    # SQLAlchemy 2 infers nullability from the type annotation.
    full_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Display name. Optional.",
    )

    # --- Status ---------------------------------------------------------------
    # Soft deactivation. Deleting a user row would cascade to their financial
    # history, which is exactly what we must not do - accounting records
    # outlive accounts, and users ask for their data back.
    #
    # server_default is set as well as default because `default` only applies
    # to rows SQLAlchemy inserts. A row created by a migration backfill or by
    # hand in psql would otherwise be NULL, and NULL is neither active nor
    # inactive.
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        doc="False disables login without destroying financial history.",
    )

    # --- Session control ------------------------------------------------------
    # THE OFF SWITCH FOR ACCESS TOKENS.
    #
    # Access tokens carry no server-side state, which is what makes them cheap
    # to verify - and also what makes them impossible to revoke. Usually that
    # is the right trade, bounded by a 15-minute lifetime. Twice it is not:
    # "log me out everywhere" and "I am changing my password" both mean
    # "assume someone else has my session, NOW", and answering "in up to
    # fifteen minutes" is not an answer.
    #
    # Every access token carries this number in a `ver` claim. Incrementing
    # the column invalidates every token issued before it, in one write, with
    # no per-token state - a counter rather than a blocklist.
    #
    # WHY A COUNTER AND NOT A TIMESTAMP. The obvious alternative is
    # `tokens_valid_after`, rejecting any token whose `iat` predates it. It
    # fails on a technicality that is easy to miss: JWT timestamps are whole
    # SECONDS (RFC 7519 NumericDate), so a token minted in the same second as
    # the change is ambiguous - it either survives when it should not, or dies
    # when it should not, depending on rounding. An integer has no
    # granularity to lose, and comparing it is exact.
    #
    # Costs nothing to check: the CurrentUser dependency already loads this
    # row on every authenticated request.
    token_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        # server_default as well as default, for the same reason as is_active:
        # `default` only applies to rows SQLAlchemy inserts, so a row created
        # by a migration backfill or by hand would otherwise be NULL - and a
        # NULL here would compare unequal to every token's claim, silently
        # locking that user out of every endpoint.
        server_default="1",
        doc="Incremented to invalidate every access token issued so far.",
    )

    # --- Timestamps -----------------------------------------------------------
    # timezone=True makes these TIMESTAMPTZ, not TIMESTAMP. This is not
    # optional in a finance application: a naive timestamp is a number with no
    # meaning attached, and "was this transaction in March or April?" becomes
    # unanswerable across a timezone boundary. PostgreSQL stores TIMESTAMPTZ
    # normalised to UTC.
    #
    # server_default=func.now() means POSTGRES supplies the value, not the
    # application. Application clocks drift and differ between instances; the
    # database is one clock that every writer shares.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="UTC creation time, assigned by the database.",
    )

    # onupdate is applied by SQLALCHEMY when it issues an UPDATE - it is not a
    # database trigger. A hand-written `UPDATE users SET ...` in psql will NOT
    # refresh this column. That is an accepted limitation: a trigger is the
    # only airtight fix and is not worth the complexity while every write goes
    # through the ORM.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        doc="UTC time of the last ORM-issued update.",
    )

    def __repr__(self) -> str:
        """Debug representation.

        Deliberately shows the id and email but NEVER hashed_password.
        __repr__ output lands in logs, tracebacks, and error trackers, and a
        password hash reaching any of those is a credential leak - offline
        cracking becomes possible the moment an attacker holds the hash.
        """
        return f"<User id={self.id} email={self.email!r}>"
