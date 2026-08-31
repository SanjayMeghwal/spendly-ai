"""Request and response schemas for goals.

Same reason models/ and schemas/ are separate everywhere else in this
project: `app.models.Goal` has a `user_id` column, and none of the schemas
below do. The owner is always the caller's `CurrentUser`, never a value
accepted from the request - see app/api/routes/goals.py.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GoalCreate(BaseModel):
    """Request body for POST /goals."""

    category_id: UUID = Field(description="A category this user owns.")

    # gt=0 mirrors the column's CheckConstraint("target_amount > 0") - an
    # invalid target is rejected here with a 422 instead of reaching the
    # database and failing there with a less helpful error.
    target_amount: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
        description="How much this goal is saving toward.",
        examples=["5000.00"],
    )

    target_date: date | None = Field(
        default=None, description="Calendar date this goal is targeting. Optional."
    )


class GoalUpdate(BaseModel):
    """Request body for PATCH /goals/{id}.

    Every field is optional, and that is the whole contract of a PATCH: the
    caller sends only what changes, and anything omitted is left alone.
    category_id and target_amount back NOT NULL columns and can never be
    cleared to null - same reasoning as BudgetUpdate's fields. target_date
    is genuinely nullable, though: it can be sent explicitly as null to
    remove a goal's deadline, the same way TransactionUpdate's category_id
    and notes can be cleared.
    """

    category_id: UUID | None = None
    target_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    target_date: date | None = None

    # See TransactionUpdate.reject_explicit_null for the full reasoning.
    # Deliberately NOT applied to target_date - unlike category_id and
    # target_amount, null is a meaningful value there (clear the deadline),
    # not merely "no change".
    @field_validator("category_id", "target_amount", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("must not be null - omit the field instead to leave it unchanged")
        return value


class GoalRead(BaseModel):
    """A goal, as returned to its owner, including current progress.

    No `user_id` field, for the same reason TransactionRead/BudgetRead have
    none. `category_name`, `progress`, and `remaining` are NOT columns on
    the Goal model - `category_name` is looked up from `categories`,
    `progress` is computed per request from `transactions` by
    app/services/goal.py, and `remaining` = `target_amount - progress`,
    shown uncapped: overshooting a goal is a fact worth showing, not
    hiding. Because none of the three are ORM attributes, routes build this
    model explicitly rather than via `model_validate(goal)` alone - see
    app/api/routes/goals.py.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category_id: UUID
    category_name: str
    target_amount: Decimal
    target_date: date | None
    progress: Decimal
    remaining: Decimal
    created_at: datetime
    updated_at: datetime
