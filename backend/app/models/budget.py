"""The Budget model - a per-category spending limit.

Milestone 4's whole point: compare what a user said they'd spend against
what `transactions` says they actually spent. A budget targets exactly one
category and is evaluated live against the current calendar month - there
are no period rows to create or roll over each month; "spent so far" is
computed on read in app/services/budget.py.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Budget(Base):
    """A spending limit for one category, belonging to one user.

    NOTE ON WHAT IS DELIBERATELY ABSENT: no `relationship()` back to User or
    forward to Transaction, for the same reason Transaction has none - this
    is async SQLAlchemy, where touching an unloaded relationship raises
    `MissingGreenlet` instead of lazily querying. The service layer filters
    by `user_id` explicitly, and matches transactions to a budget by
    category string, not a foreign key - see app/services/budget.py.
    """

    __tablename__ = "budgets"

    __table_args__ = (
        # A limit of zero or less isn't a budget, it's a ban - and the
        # column doesn't share Transaction.amount's signed convention, so
        # nothing else stops a negative value from reaching the database.
        CheckConstraint("limit_amount > 0", name="limit_amount_positive"),
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
        doc="The account this budget belongs to.",
    )

    # --- What it limits -----------------------------------------------------
    # Not nullable, unlike Transaction.category: a budget with no category
    # would have nothing to compare against. There is no "overall" budget in
    # this milestone.
    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Free-text category this budget limits, e.g. 'Groceries'.",
    )

    # Unlike Transaction.amount, this is not signed - a limit is inherently
    # a positive cap, enforced by the check constraint above.
    limit_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
        doc="Maximum spend allowed per calendar month for this category.",
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
        return f"<Budget id={self.id} user_id={self.user_id} category={self.category!r}>"


# Expression-based index, so it must be declared after the class rather than
# inside __table_args__: it needs the fully-instrumented Budget.category
# attribute (to build the lower() expression), which doesn't exist yet while
# the class body is still executing.
#
# Enforces "one budget per category per user", matching case-insensitively -
# the same rule app/services/budget.py uses to match transactions against a
# budget. Without this, "Groceries" and "groceries" could both exist as
# separate budgets that silently split one category's spend between them.
Index(
    "uq_budgets_user_id_category_lower",
    Budget.user_id,
    func.lower(Budget.category),
    unique=True,
)
