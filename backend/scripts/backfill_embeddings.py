"""One-off maintenance script: embed every transaction that doesn't have one.

Two reasons a transaction can reach this state: it predates Milestone 10
(created before the embedding column existed), or embed_transaction_or_none
already tried and failed at write time because Ollama was down (see
services/embedding.py) - creation is deliberately best-effort, not blocking,
so a transaction is never rejected just because a local AI server had a bad
moment. This script is the sweep that catches both.

NOT an API endpoint. It walks every user's transactions in one pass, which
the API layer must never do - every route handler filters by user_id (see
services/transaction.py's module docstring). That is correct there, because
a route serves one authenticated caller. This is an operator running a
maintenance pass over the whole database, the same trust boundary as running
psql directly - there is no "caller" to scope it to.

Usage (from backend/):
    uv run python -m scripts.backfill_embeddings

Must run as a module (-m), not by path. Running it by path
(`python scripts/backfill_embeddings.py`) puts scripts/ itself on
sys.path[0] instead of backend/, so `from app...` below fails with
ModuleNotFoundError - confirmed by actually hitting that error, not
assumed. -m puts the current working directory (backend/) on sys.path
instead, which is what every other `app.*` import in this project already
relies on.
"""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import Category, Transaction
from app.services.embedding import embed_transaction_or_none

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill_embeddings")

# Committed every N rows rather than once at the end, so a script killed
# partway through (or a very large table) doesn't lose all progress made so
# far - and so operators watching the log see it moving.
_COMMIT_EVERY = 50


async def backfill_embeddings(session: AsyncSession) -> tuple[int, int]:
    """Embed every transaction with embedding IS NULL.

    Returns (embedded, still_missing). still_missing is not an error - it
    means Ollama was unreachable for those rows; run the script again once
    it's back up.
    """
    result = await session.execute(select(Transaction).where(Transaction.embedding.is_(None)))
    transactions = list(result.scalars().all())
    if not transactions:
        return 0, 0

    category_ids = {t.category_id for t in transactions if t.category_id is not None}
    category_names: dict[uuid.UUID, str] = {}
    if category_ids:
        rows = await session.execute(
            select(Category.id, Category.name).where(Category.id.in_(category_ids))
        )
        # Not dict(rows): Result has its own .keys() (the column names), so
        # dict()'s mapping-protocol check picks THAT up instead of treating
        # rows as an iterable of (id, name) pairs. Same pattern as
        # services/category.py's get_category_names.
        category_names = {category_id: name for category_id, name in rows}  # noqa: C416

    embedded = 0
    still_missing = 0
    for i, transaction in enumerate(transactions, start=1):
        category_name = (
            category_names.get(transaction.category_id)
            if transaction.category_id is not None
            else None
        )
        transaction.embedding = await embed_transaction_or_none(
            description=transaction.description,
            amount=transaction.amount,
            category_name=category_name,
        )
        if transaction.embedding is not None:
            embedded += 1
        else:
            still_missing += 1

        if i % _COMMIT_EVERY == 0:
            await session.commit()
            logger.info("Progress: %d/%d", i, len(transactions))

    await session.commit()
    return embedded, still_missing


async def main() -> None:
    async with AsyncSessionLocal() as session:
        embedded, still_missing = await backfill_embeddings(session)
    logger.info(
        "Done: %d embedded, %d still missing (Ollama unreachable - rerun once it's up)",
        embedded,
        still_missing,
    )


if __name__ == "__main__":
    asyncio.run(main())
