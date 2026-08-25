"""Transaction business logic.

LAYERING - this module must never import FastAPI, matching services/user.py.

EVERY QUERY HERE FILTERS BY user_id. That is not a style preference: a query
against this table that omits it is a data leak, one user's financial history
served to another. Nothing here ever takes a transaction id alone - it is
always (user_id, transaction_id) together, so "not mine" and "does not exist"
produce the exact same result. The API layer turns that result into a single
404, the same answer for both cases, on purpose: confirming that someone
ELSE's transaction id is merely a valid id would tell an attacker probing ids
more than the response should.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transaction


async def create_transaction(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    amount: Decimal,
    description: str,
    occurred_at: datetime,
    category: str | None = None,
    notes: str | None = None,
) -> Transaction:
    """Create a transaction owned by user_id and return the persisted row."""
    transaction = Transaction(
        user_id=user_id,
        amount=amount,
        description=description,
        occurred_at=occurred_at,
        category=category,
        notes=notes,
    )
    session.add(transaction)
    await session.commit()
    # Load created_at / updated_at, which PostgreSQL filled in during the INSERT.
    await session.refresh(transaction)
    return transaction
