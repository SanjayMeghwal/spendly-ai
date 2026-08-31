"""CSV import business logic.

LAYERING - this module must never import FastAPI, matching every other
service module. It also does not import app/schemas/transaction_import.py:
services return plain data, and routes assemble the response schema - the
same division of labour app/services/report.py already uses.

CSV PARSING ITSELF DOES NOT LIVE HERE. Turning uploaded bytes into rows is
HTTP-adjacent framing, not business logic - it happens in
app/api/routes/transactions.py, which hands this module only
already-parsed, already-type-checked rows. What lives here is the part
that actually depends on the database: resolving a category NAME to this
user's category_id, and deciding which rows are duplicates of something
that already exists.

EVERY QUERY HERE FILTERS BY user_id. Same reasoning as every other service
module: a query against this table that omits it is a data leak.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transaction
from app.services.category import list_categories


class ImportRow(NamedTuple):
    """One CSV row that has already passed structural/type validation.

    `category_name` is the raw text from the CSV's `category` column - not
    yet resolved to a category_id, since that requires a database lookup
    this module owns. None means the cell was blank.
    """

    occurred_at: datetime
    amount: Decimal
    description: str
    category_name: str | None


class ImportOutcome(NamedTuple):
    """How many rows were actually inserted vs. recognized as duplicates.

    Carries no per-row errors - a row that failed to PARSE never reaches
    this module at all (see the module docstring), so there is nothing
    about parsing for this type to report.
    """

    imported: int
    skipped_duplicates: int


async def import_transactions(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    rows: list[ImportRow],
) -> ImportOutcome:
    """Insert the given rows, resolving categories by name and skipping duplicates.

    A row is a DUPLICATE - skipped, not an error - if a transaction with the
    same (occurred_at, amount, description) already exists for this user,
    OR if an earlier row in this same list was already accepted with that
    same key. The latter is why accepted keys are added to `seen` inside the
    loop rather than computed once up front: two identical rows in one file
    must produce one imported transaction, not two.

    A `category_name` that doesn't match any of this user's categories
    case-insensitively - including a blank one - resolves to category_id
    None (Uncategorized). Nothing is auto-created; see services/category.py
    for why categories stay something a user deliberately names.
    """
    categories = await list_categories(session, user_id=user_id)
    category_ids_by_lower_name = {category.name.lower(): category.id for category in categories}

    dates = {row.occurred_at for row in rows}
    seen: set[tuple[datetime, Decimal, str]] = set()
    if dates:
        result = await session.execute(
            select(Transaction.occurred_at, Transaction.amount, Transaction.description).where(
                Transaction.user_id == user_id, Transaction.occurred_at.in_(dates)
            )
        )
        seen = {(occurred_at, amount, description) for occurred_at, amount, description in result}

    to_insert: list[Transaction] = []
    skipped_duplicates = 0
    for row in rows:
        key = (row.occurred_at, row.amount, row.description)
        if key in seen:
            skipped_duplicates += 1
            continue
        seen.add(key)

        category_id = (
            category_ids_by_lower_name.get(row.category_name.lower()) if row.category_name else None
        )
        to_insert.append(
            Transaction(
                user_id=user_id,
                amount=row.amount,
                description=row.description,
                occurred_at=row.occurred_at,
                category_id=category_id,
            )
        )

    if to_insert:
        session.add_all(to_insert)
        await session.commit()

    return ImportOutcome(imported=len(to_insert), skipped_duplicates=skipped_duplicates)
