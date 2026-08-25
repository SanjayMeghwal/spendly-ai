"""Request and response schemas for transactions.

Same reason models/ and schemas/ are separate everywhere else in this
project: `app.models.Transaction` has a `user_id` column, and none of the
schemas below do. The owner is always the caller's `CurrentUser`, never a
value accepted from the request - see app/api/routes/transactions.py.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


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
