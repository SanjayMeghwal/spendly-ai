"""Request and response schemas for budgets.

Same reason models/ and schemas/ are separate everywhere else in this
project: `app.models.Budget` has a `user_id` column, and none of the schemas
below do. The owner is always the caller's `CurrentUser`, never a value
accepted from the request - see app/api/routes/budgets.py.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BudgetCreate(BaseModel):
    """Request body for POST /budgets."""

    category_id: UUID = Field(description="A category this user owns.")

    # gt=0 mirrors the column's CheckConstraint("limit_amount > 0") - an
    # invalid limit is rejected here with a 422 instead of reaching the
    # database and failing there with a less helpful error. max_digits and
    # decimal_places mirror the column's NUMERIC(12, 2) exactly, same as
    # TransactionCreate.amount.
    limit_amount: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
        description="Maximum spend allowed per calendar month for this category.",
        examples=["500.00"],
    )


class BudgetUpdate(BaseModel):
    """Request body for PATCH /budgets/{id}.

    Every field is optional, and that is the whole contract of a PATCH: the
    caller sends only what changes, and anything omitted is left alone. Both
    fields back NOT NULL columns, so - unlike TransactionUpdate's category_id
    and notes - neither can ever be cleared to null.
    """

    category_id: UUID | None = None
    limit_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)

    # See TransactionUpdate.reject_explicit_null for the full reasoning:
    # `UUID | None`/`Decimal | None` exist only so the field can be OMITTED,
    # but that same type would also accept an explicit `null`, which would
    # reach the database as a NOT NULL violation - an unhandled 500 - rather
    # than a clean validation error.
    @field_validator("category_id", "limit_amount", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("must not be null - omit the field instead to leave it unchanged")
        return value


class BudgetRead(BaseModel):
    """A budget, as returned to its owner, including this month's status.

    No `user_id` field, for the same reason TransactionRead has none.
    `spent`, `remaining`, and `category_name` are NOT columns on the Budget
    model - `spent`/`remaining` are computed per request from `transactions`
    by app/services/budget.py, for whichever month was asked for (the
    current UTC month, by default); `category_name` is looked up from
    `categories`, the same denormalization TransactionRead uses. Because
    none of the three are ORM attributes, routes build this model
    explicitly rather than via `model_validate(budget)` alone - see
    app/api/routes/budgets.py.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category_id: UUID
    category_name: str
    limit_amount: Decimal
    spent: Decimal
    remaining: Decimal
    created_at: datetime
    updated_at: datetime
