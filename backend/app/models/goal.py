"""The Goal model - a savings target for one category.

Milestone 6's whole point: compare what a user wants to have saved against
what `transactions` says they've actually put toward it. A goal targets
exactly one category and is evaluated cumulatively - unlike Budget, there is
no monthly reset; "progress so far" is computed on read in
app/services/goal.py as the running total since the category's first
transaction, not a per-period figure.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Numeric, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Goal(Base):
    """A savings target for one category, belonging to one user.

    NOTE ON WHAT IS DELIBERATELY ABSENT: no `relationship()` to User or
    Category, for the same reason Budget has none - this is async
    SQLAlchemy, where touching an unloaded relationship raises
    `MissingGreenlet` instead of lazily querying. The service layer filters
    by `user_id` explicitly, and matches transactions to a goal by
    category_id - see app/services/goal.py.
    """

    __tablename__ = "goals"

    __table_args__ = (
        # A target of zero or less isn't a goal - and the column doesn't
        # share Transaction.amount's signed convention, so nothing else
        # stops a negative value from reaching the database.
        CheckConstraint("target_amount > 0", name="target_amount_positive"),
        # "One goal per category per user" - identical reasoning to
        # Budget's uq_budgets_user_id_category_id: two goals on the same
        # category would both claim credit for the same inflow.
        Index("uq_goals_user_id_category_id", "user_id", "category_id", unique=True),
    )

    # --- Identity ---------------------------------------------------------
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Unguessable public identifier, safe to expose in URLs.",
    )

    # --- Ownership ----------------------------------------------------------
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="The account this goal belongs to.",
    )

    # --- What it's saving toward ---------------------------------------------
    # ondelete="RESTRICT" is a database-level backstop behind
    # delete_category's own in-use check - see Budget.category_id's
    # identical reasoning. index=True is NOT redundant with the composite
    # unique index above: it can't efficiently serve a lookup by
    # category_id alone, which PostgreSQL needs for the RESTRICT check.
    category_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        doc="The category this goal tracks.",
    )

    # Not signed, like Budget.limit_amount - a target is inherently a
    # positive amount, enforced by the check constraint above.
    target_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
        doc="How much this goal is saving toward.",
    )

    # A calendar date, not a DateTime - a deadline has no meaningful
    # time-of-day or timezone component, unlike Transaction.occurred_at.
    # Nullable: a goal can be open-ended, with no deadline at all.
    target_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        doc="Calendar date this goal is targeting, if any.",
    )

    # --- Timestamps ---------------------------------------------------------
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
        return f"<Goal id={self.id} user_id={self.user_id} category_id={self.category_id}>"
