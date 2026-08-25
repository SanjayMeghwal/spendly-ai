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

from sqlalchemy import select
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


async def list_transactions(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int,
    offset: int,
) -> list[Transaction]:
    """Return one user's transactions, most recently occurred first.

    Ordered by (occurred_at, id) rather than occurred_at alone. Two
    transactions can share an occurred_at value (e.g. two backfilled entries
    for the same day), and without a tiebreaker their relative order between
    calls is merely whatever PostgreSQL feels like that time - which would
    make pagination silently skip or repeat a row across pages. Ties break on
    id, which is unique, so the order is always deterministic.
    """
    result = await session.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def get_transaction(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID,
) -> Transaction | None:
    """Look up one transaction by id, scoped to its owner.

    Returns None both when the id does not exist at all and when it belongs
    to someone else - the two cases are indistinguishable on purpose. See the
    module docstring.
    """
    result = await session.execute(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()
