"""Request and response schemas for transactions.

Same reason models/ and schemas/ are separate everywhere else in this
project: `app.models.Transaction` has a `user_id` column, and none of the
schemas below do. The owner is always the caller's `CurrentUser`, never a
value accepted from the request - see app/api/routes/transactions.py.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TransactionCreate(BaseModel):
    """Request body for POST /transactions."""

    # max_digits/decimal_places mirror the column's NUMERIC(12, 2) exactly,
    # so an out-of-range amount is rejected here with a 422 instead of
    # reaching the database and failing there with a less helpful error.
    amount: Decimal = Field(
        max_digits=12,
        decimal_places=2,
        description="Signed amount. Negative is money out, positive is money in.",
        examples=["-12.50"],
    )

    description: str = Field(
        min_length=1,
        max_length=255,
        description="What the transaction was.",
        examples=["Grocery store"],
    )

    category: str | None = Field(
        default=None,
        max_length=100,
        description="Free-text category. Optional.",
        examples=["Groceries"],
    )

    occurred_at: datetime = Field(description="When the transaction happened, in UTC.")

    # 10,000 characters is generous for a freeform note while still being a
    # bound - the column itself (Text) has none, and an API with no limit at
    # all invites a client to post something unreasonable and find out only
    # when the database complains.
    notes: str | None = Field(default=None, max_length=10_000, description="Optional notes.")


class TransactionUpdate(BaseModel):
    """Request body for PATCH /transactions/{id}.

    Every field is optional, and that is the whole contract of a PATCH: the
    caller sends only what changes, and anything omitted is left alone. A
    field explicitly sent as `null` - where the type allows it - clears it.
    """

    amount: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    description: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, max_length=100)
    occurred_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=10_000)

    # amount, description, and occurred_at map to NOT NULL columns.
    # `Decimal | None` above exists only so the field can be OMITTED -
    # Pydantic has no "optional but not nullable" annotation - but that same
    # type would also accept an explicit `"amount": null`, which would reach
    # the database as a NOT NULL violation (an unhandled 500) rather than a
    # clean validation error.
    #
    # `mode="before"` and `validate_default=False` (pydantic's default)
    # together mean this runs ONLY when the client actually sends the key -
    # an omitted field never reaches this validator, so "don't mention it"
    # still means "no change".
    @field_validator("amount", "description", "occurred_at", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("must not be null - omit the field instead to leave it unchanged")
        return value


class TransactionRead(BaseModel):
    """A transaction, as returned to its owner.

    No `user_id` field. The caller already knows it is their own data - every
    route in this feature scopes its query by the authenticated user - so
    echoing the id back would be redundant, not a security boundary.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    amount: Decimal
    description: str
    category: str | None
    notes: str | None
    occurred_at: datetime
    created_at: datetime
    updated_at: datetime
