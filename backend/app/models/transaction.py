"""The Transaction model - the first resource a user owns.

Every row belongs to exactly one user and records one movement of money: a
purchase, a paycheck, a refund. Milestone 3's whole point is proving out the
pattern every future owned resource (budgets, goals, ...) will repeat: filter
by user_id, never trust an id from the client alone, keep money exact.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Transaction(Base):
    """One movement of money belonging to one user.

    NOTE ON WHAT IS DELIBERATELY ABSENT: there is no `relationship()` to
    User or Category, for the same reason RefreshToken has none - this
    project is async SQLAlchemy, where touching an unloaded relationship
    raises `MissingGreenlet` rather than lazily querying. The service layer
    filters by `user_id` explicitly, and joins to Category explicitly when a
    read needs the category's name - see category_id below.
    """

    __tablename__ = "transactions"

    __table_args__ = (
        # Every listing query is "this user's transactions, newest first" -
        # exactly this column pair, in this order. A single-column index on
        # user_id would still find the right rows, but PostgreSQL would then
        # sort them separately; this composite index lets it walk the index
        # in the order the query already wants.
        Index("ix_transactions_user_id_occurred_at", "user_id", "occurred_at"),
    )

    # --- Identity ---------------------------------------------------------
    # UUIDv4, matching User.id and RefreshToken.id: sequential integers would
    # let anyone holding one transaction id guess the ids - and by extension
    # the approximate count - of everyone else's.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Unguessable public identifier, safe to expose in URLs.",
    )

    # --- Ownership ----------------------------------------------------------
    # ondelete="CASCADE": deleting a user removes their transactions at the
    # database level, even from a hand-run psql DELETE. Orphaned rows here
    # would be a slow leak in a table that only ever grows.
    #
    # index=True is load-bearing on its own (not only via the composite index
    # above): a foreign key does NOT create an index on the referencing
    # column in PostgreSQL, and every query in this feature filters by
    # user_id.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="The account this transaction belongs to.",
    )

    # --- The money ------------------------------------------------------------
    # NUMERIC, never float - binary floats cannot represent 0.10 exactly, and
    # that error compounds across a ledger. Signed: negative is money out
    # (an expense), positive is money in (income or a refund). A signed
    # amount makes a balance a single SUM(amount) with no CASE on a separate
    # type column to keep in sync with the sign.
    #
    # precision=12, scale=2 allows up to 9,999,999,999.99 - far beyond any
    # personal-finance transaction, with room to spare rather than tuned to
    # the edge.
    amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
        doc="Signed amount. Negative is money out, positive is money in.",
    )

    # --- Description ------------------------------------------------------
    description: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="What the transaction was, e.g. 'Grocery store'.",
    )

    # Milestone 5 cut this over from a free-text string to a real FK - see
    # app/models/category.py. ondelete="RESTRICT" is a database-level
    # backstop behind the application's own in-use check in
    # services/category.py's delete_category: a category still referenced
    # here should never actually reach a DELETE, but if application logic
    # ever had a bug that tried, the constraint refuses it loudly instead of
    # silently orphaning this column. Nullable, same as the free-text column
    # was - an uncategorized transaction stays valid.
    #
    # No relationship() to Category, for the same MissingGreenlet reason
    # user_id has none - see the class docstring. Reads that need the
    # category's name join explicitly; see services/category.py's
    # get_category_names.
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        doc="The category this transaction belongs to, if any.",
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Optional freeform notes.",
    )

    # --- When it happened, vs. when we recorded it -------------------------
    # Deliberately separate from created_at: a user enters a transaction
    # today for a purchase they made last week, and reports need the date it
    # actually happened, not the date it was typed in. Supplied by the
    # client, not defaulted server-side, because only the client knows it.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="UTC time the transaction happened.",
    )

    # --- Timestamps ---------------------------------------------------------
    # Same reasoning as User.created_at / updated_at: server_default so
    # PostgreSQL - one shared clock - supplies the value, not drifting
    # application clocks.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="UTC time this row was created, assigned by the database.",
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        doc="UTC time of the last ORM-issued update.",
    )

    def __repr__(self) -> str:
        return f"<Transaction id={self.id} user_id={self.user_id} amount={self.amount}>"
