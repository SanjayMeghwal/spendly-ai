"""Request and response schemas for categories.

Same reason models/ and schemas/ are separate everywhere else in this
project: `app.models.Category` has a `user_id` column, and none of the
schemas below do. The owner is always the caller's `CurrentUser`, never a
value accepted from the request - see app/api/routes/categories.py.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CategoryCreate(BaseModel):
    """Request body for POST /categories."""

    name: str = Field(
        min_length=1,
        max_length=100,
        description="Display name for this category.",
        examples=["Groceries"],
    )


class CategoryUpdate(BaseModel):
    """Request body for PATCH /categories/{id}.

    The only mutation this endpoint supports is a rename - the whole point
    of a real Category resource over free text, per app/models/category.py.
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)

    # See TransactionUpdate.reject_explicit_null / BudgetUpdate's identical
    # validator for the full reasoning: `str | None` exists only so the
    # field can be OMITTED, but that same type would also accept an
    # explicit `null`, which would reach the database as a NOT NULL
    # violation - an unhandled 500 - rather than a clean validation error.
    @field_validator("name", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("must not be null - omit the field instead to leave it unchanged")
        return value


class CategoryRead(BaseModel):
    """A category, as returned to its owner.

    No `user_id` field, for the same reason TransactionRead/BudgetRead have
    none.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
