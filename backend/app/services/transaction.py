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
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transaction
from app.services.category import CategoryNotFound, get_category
from app.services.embedding import embed_transaction_or_none

# Sentinel distinguishing "the caller did not mention this field" from "the
# caller sent it as null". PATCH needs both: category_id and notes are
# nullable columns, so `None` is a meaningful value (clear it), not merely
# "no change". A plain `None` default could not tell those apart.
_UNSET: Any = object()


async def _check_category_id(
    session: AsyncSession, *, user_id: uuid.UUID, category_id: uuid.UUID | None
) -> None:
    """Raise CategoryNotFound if category_id doesn't resolve to one of this
    user's categories. A no-op for None - "no category" is always valid."""
    if category_id is None:
        return
    if await get_category(session, user_id=user_id, category_id=category_id) is None:
        raise CategoryNotFound(category_id)


async def _resolve_category_name(
    session: AsyncSession, *, user_id: uuid.UUID, category_id: uuid.UUID | None
) -> str | None:
    """Validate category_id the same way _check_category_id does, but also
    hand back the category's name - embedding text wants it, and this way
    the category table is only queried once instead of twice.

    Raises:
        CategoryNotFound: category_id doesn't belong to user_id.
    """
    if category_id is None:
        return None
    category = await get_category(session, user_id=user_id, category_id=category_id)
    if category is None:
        raise CategoryNotFound(category_id)
    return category.name


async def create_transaction(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    amount: Decimal,
    description: str,
    occurred_at: datetime,
    category_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> Transaction:
    """Create a transaction owned by user_id and return the persisted row.

    Raises:
        CategoryNotFound: category_id doesn't belong to user_id.
    """
    category_name = await _resolve_category_name(session, user_id=user_id, category_id=category_id)
    embedding = await embed_transaction_or_none(
        description=description, amount=amount, category_name=category_name
    )
    transaction = Transaction(
        user_id=user_id,
        amount=amount,
        description=description,
        occurred_at=occurred_at,
        category_id=category_id,
        notes=notes,
        embedding=embedding,
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


async def search_transactions(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query_embedding: list[float],
    limit: int,
) -> list[Transaction]:
    """Return this user's transactions most semantically similar to
    query_embedding, closest match first.

    Cosine distance (pgvector's `<=>` operator, via .cosine_distance() -
    see pgvector.sqlalchemy.vector.VECTOR.Comparator) rather than L2:
    cosine cares about the DIRECTION two vectors point, not their
    magnitude, which is the right notion of "similar meaning" for text
    embeddings - two descriptions of the same kind of purchase should
    match regardless of how strongly-worded either one is.

    Rows with no embedding yet - not backfilled, or Ollama was down when
    they were created/imported, see embed_transaction_or_none - are
    excluded rather than ranked last. There is no distance to compute
    against a NULL vector, and asking PostgreSQL to order by one would
    error, not merely rank it poorly.
    """
    result = await session.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id, Transaction.embedding.is_not(None))
        .order_by(Transaction.embedding.cosine_distance(query_embedding))
        .limit(limit)
    )
    return list(result.scalars().all())


async def update_transaction(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID,
    amount: Decimal | None = _UNSET,
    description: str | None = _UNSET,
    category_id: uuid.UUID | None = _UNSET,
    occurred_at: datetime | None = _UNSET,
    notes: str | None = _UNSET,
) -> Transaction | None:
    """Apply a partial update and return the updated row, or None if not found.

    Callers pass `**payload.model_dump(exclude_unset=True)` from
    TransactionUpdate, so a field absent from the request body never reaches
    this function at all and keeps its `_UNSET` default - untouched. A field
    the caller did send, even as `null`, arrives as that real value - except
    for amount, description, and occurred_at, which TransactionUpdate's own
    validator already refuses to accept as null, since they back NOT NULL
    columns. The asserts below are that guarantee, made visible to mypy: a
    Mapped[Decimal] column cannot be assigned `Decimal | None` without one.

    Raises:
        CategoryNotFound: category_id was sent and doesn't belong to user_id.
    """
    transaction = await get_transaction(session, user_id=user_id, transaction_id=transaction_id)
    if transaction is None:
        return None

    if amount is not _UNSET:
        assert amount is not None
        transaction.amount = amount
    if description is not _UNSET:
        assert description is not None
        transaction.description = description
    if category_id is not _UNSET:
        await _check_category_id(session, user_id=user_id, category_id=category_id)
        transaction.category_id = category_id
    if occurred_at is not _UNSET:
        assert occurred_at is not None
        transaction.occurred_at = occurred_at
    if notes is not _UNSET:
        transaction.notes = notes

    await session.commit()
    await session.refresh(transaction)
    return transaction


async def delete_transaction(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID,
) -> bool:
    """Delete a transaction. Returns whether a row was actually deleted.

    Hard delete: the row is gone, not marked. A transaction is deleted only
    when its owner asks to remove a mistaken entry, and there is no audit or
    undelete feature yet that would need the row to survive.
    """
    transaction = await get_transaction(session, user_id=user_id, transaction_id=transaction_id)
    if transaction is None:
        return False

    await session.delete(transaction)
    await session.commit()
    return True
