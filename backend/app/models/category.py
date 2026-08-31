"""The Category model - a user-owned, renameable label for spending.

Milestone 5's whole point: `Transaction.category` and `Budget.category`
used to be free-text strings, matched case-insensitively wherever they were
compared. That made a rename expensive (every row using the old string had
to be found and rewritten) and comparison fragile (a typo silently created
a new "category" instead of matching the existing one). A real, owned
Category row fixes both: transactions and budgets reference it by id, so
renaming is a single-row update, and matching is an id comparison, not a
string comparison.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Category(Base):
    """A named label for spending, belonging to one user.

    NOTE ON WHAT IS DELIBERATELY ABSENT: no `relationship()` back to User or
    forward to Transaction/Budget, for the same reason those models have
    none - this is async SQLAlchemy, where touching an unloaded relationship
    raises `MissingGreenlet` instead of lazily querying. The service layer
    filters by `user_id` explicitly, and Transaction/Budget reference a
    category by `category_id`, not a relationship - see
    app/services/category.py.
    """

    __tablename__ = "categories"

    # --- Identity ---------------------------------------------------------
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Unguessable public identifier, safe to expose in URLs. What "
        "Transaction.category_id and Budget.category_id reference.",
    )

    # --- Ownership ----------------------------------------------------------
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="The account this category belongs to.",
    )

    # --- The name -------------------------------------------------------------
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Display name, e.g. 'Groceries'.",
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
        return f"<Category id={self.id} user_id={self.user_id} name={self.name!r}>"


# Expression-based index, so it must be declared after the class rather than
# inside __table_args__: it needs the fully-instrumented Category.name
# attribute (to build the lower() expression), which doesn't exist yet while
# the class body is still executing. See Budget's identical
# uq_budgets_user_id_category_lower for the same pattern.
#
# Enforces "one category name per user", matching case-insensitively -
# "Groceries" and "groceries" must not both exist, or nothing would tell a
# user which one a transaction meant.
Index(
    "uq_categories_user_id_name_lower",
    Category.user_id,
    func.lower(Category.name),
    unique=True,
)
