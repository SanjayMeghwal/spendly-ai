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
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Budget(Base):
    """A spending limit for one category, belonging to one user.

    NOTE ON WHAT IS DELIBERATELY ABSENT: no `relationship()` to User or
    Category, for the same reason Transaction has none - this is async
    SQLAlchemy, where touching an unloaded relationship raises
    `MissingGreenlet` instead of lazily querying. The service layer filters
    by `user_id` explicitly, and matches transactions to a budget by
    category_id - see app/services/budget.py.
    """

    __tablename__ = "budgets"

    __table_args__ = (
        # A limit of zero or less isn't a budget, it's a ban - and the
        # column doesn't share Transaction.amount's signed convention, so
        # nothing else stops a negative value from reaching the database.
        CheckConstraint("limit_amount > 0", name="limit_amount_positive"),
        # "One budget per category per user." Simpler than the free-text
        # version this replaces (see git history for
        # uq_budgets_user_id_category_lower): category_id already refers to
        # one canonical Category row, so this needs no func.lower() - name
        # uniqueness is Category's own job now, not Budget's.
        Index("uq_budgets_user_id_category_id", "user_id", "category_id", unique=True),
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
    # Milestone 5 cut this over from a free-text string to a real FK - see
    # app/models/category.py. Not nullable, unlike Transaction.category_id:
    # a budget with no category would have nothing to compare against.
    # There is no "overall" budget in this milestone. ondelete="RESTRICT"
    # is a database-level backstop behind delete_category's own in-use
    # check - see Transaction.category_id's identical reasoning.
    #
    # index=True is NOT redundant with __table_args__'s composite unique
    # index below: (user_id, category_id) can't efficiently serve a lookup
    # by category_id alone, which is exactly what PostgreSQL needs to check
    # this FK's ondelete="RESTRICT" when a category is deleted - a B-tree
    # index only helps a query bound on its LEADING column(s).
    category_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        doc="The category this budget limits.",
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
        return f"<Budget id={self.id} user_id={self.user_id} category_id={self.category_id}>"
