"""Transaction endpoints.

HTTP only - routing, status codes, and translating domain results into
responses. No business logic lives here; see app/services/transaction.py.

EVERY HANDLER TAKES `current_user: CurrentUser` AND NOTHING ELSE NAMES THE
OWNER. There is no `user_id` in any URL or request body in this file. The
owner of a transaction is always the identity in the caller's signed access
token, exactly as /auth/me works - see app/api/deps.py. A URL like
`GET /transactions/{id}` invites the classic bug of trusting an id from the
request; scoping every service call by `current_user.id` instead means there
is no id to confuse and no ownership check to forget.
"""

import csv
import io
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from pydantic import ValidationError

from app.api.deps import CurrentUser, DbSession
from app.models import Transaction
from app.schemas.transaction import TransactionCreate, TransactionRead, TransactionUpdate
from app.schemas.transaction_import import TransactionImportError, TransactionImportResult
from app.services.category import CategoryNotFound, get_category_names
from app.services.embedding import EmbeddingError, embed_text
from app.services.transaction import (
    create_transaction,
    delete_transaction,
    get_transaction,
    list_transactions,
    search_transactions,
    update_transaction,
)
from app.services.transaction_import import ImportRow, import_transactions

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _not_found() -> HTTPException:
    """The single 404 for 'no such transaction of yours'.

    Used both when the id does not exist at all and when it belongs to
    another user - see app/services/transaction.py for why those two cases
    must look identical to the caller.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No transaction with that id.",
    )


def _search_unavailable() -> HTTPException:
    """503 for 'the query itself could not be embedded'.

    Unlike create/import, which write happily without an embedding (see
    embed_transaction_or_none), a search has no fallback: with no vector for
    the query text, there is nothing to rank by. 503, matching
    /health/ready's convention - this is "a dependency is temporarily down",
    not a bug in the request.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Search is temporarily unavailable.",
    )


def _invalid_category() -> HTTPException:
    """The single 422 for 'category_id isn't one of your categories'.

    422, not 404: the URL's own resource (the transaction, or nothing yet
    on create) is fine - it's the request BODY that names a related entity
    that doesn't exist, which is a validation failure, not a routing one.
    """
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="category_id does not refer to one of your categories.",
    )


def to_read_model(
    transaction: Transaction, category_names: dict[uuid.UUID, str]
) -> TransactionRead:
    """Attach the denormalized category name to a transaction row.

    `category_names` is a bulk lookup the caller already did - see
    app/services/category.py's get_category_names - so building a page of
    results never queries once per row.
    """
    return TransactionRead(
        id=transaction.id,
        amount=transaction.amount,
        description=transaction.description,
        category_id=transaction.category_id,
        category_name=category_names.get(transaction.category_id)
        if transaction.category_id
        else None,
        notes=transaction.notes,
        occurred_at=transaction.occurred_at,
        created_at=transaction.created_at,
        updated_at=transaction.updated_at,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=TransactionRead,
    summary="Record a transaction",
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "category_id does not refer to one of your categories."
        },
    },
)
async def create(
    payload: TransactionCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> TransactionRead:
    try:
        transaction = await create_transaction(
            db,
            user_id=current_user.id,
            amount=payload.amount,
            description=payload.description,
            category_id=payload.category_id,
            occurred_at=payload.occurred_at,
            notes=payload.notes,
        )
    except CategoryNotFound:
        raise _invalid_category() from None
    category_ids = {transaction.category_id} if transaction.category_id else set()
    category_names = await get_category_names(
        db, user_id=current_user.id, category_ids=category_ids
    )
    return to_read_model(transaction, category_names)


_REQUIRED_CSV_COLUMNS = {"date", "amount", "description"}
_MAX_CSV_ROWS = 2000


class _CsvStructureError(Exception):
    """A problem with the whole file - bad encoding, missing required
    header columns, or too many rows - rather than one bad row.

    Raised before any row is processed and answered with a single 422 for
    the whole request, unlike a per-row problem, which becomes a
    TransactionImportError entry inside an otherwise-200 response.
    """


def _parse_csv_date(value: str | None) -> datetime:
    if not value or not value.strip():
        raise ValueError("date is required")
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    # Midnight UTC: a CSV row has no time-of-day to offer, and occurred_at is
    # always stored TIMESTAMPTZ/UTC - see app/models/transaction.py.
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def _validation_error_reason(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


def _parse_csv_row(raw_row: dict[str, str | None]) -> ImportRow:
    """Parse and validate one CSV row.

    amount/description are validated through TransactionCreate itself -
    same max_digits/decimal_places/length bounds a normal POST enforces,
    reused rather than duplicated by hand so the two paths can never
    silently drift apart. occurred_at is parsed separately first, since it
    needs its own YYYY-MM-DD-only rule that TransactionCreate's plain
    `datetime` field doesn't express.

    Raises:
        ValueError: the row fails validation; the message becomes the
            TransactionImportError's reason.
    """
    occurred_at = _parse_csv_date(raw_row.get("date"))
    try:
        validated = TransactionCreate(
            amount=raw_row.get("amount") or "",
            description=(raw_row.get("description") or "").strip(),
            occurred_at=occurred_at,
            category_id=None,
        )
    except ValidationError as exc:
        raise ValueError(_validation_error_reason(exc)) from exc

    category_name = (raw_row.get("category") or "").strip() or None
    return ImportRow(
        occurred_at=validated.occurred_at,
        amount=validated.amount,
        description=validated.description,
        category_name=category_name,
    )


def _parse_csv(raw: bytes) -> tuple[list[ImportRow], list[TransactionImportError]]:
    """Turn uploaded CSV bytes into validated rows plus per-row errors.

    utf-8-sig, not utf-8: tolerates the byte-order-mark Excel prepends when
    it saves a CSV, which plain utf-8 decoding would otherwise leave as a
    stray character glued to the first header name.

    Raises:
        _CsvStructureError: the file itself is malformed (bad encoding,
            missing required columns, too many rows) - a whole-request
            problem, not a per-row one.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _CsvStructureError("File is not valid UTF-8 text.") from exc

    reader = csv.DictReader(io.StringIO(text))
    missing = _REQUIRED_CSV_COLUMNS - set(reader.fieldnames or [])
    if missing:
        raise _CsvStructureError(f"Missing required column(s): {', '.join(sorted(missing))}.")

    raw_rows = list(reader)
    if len(raw_rows) > _MAX_CSV_ROWS:
        raise _CsvStructureError(f"File has {len(raw_rows)} rows; the limit is {_MAX_CSV_ROWS}.")

    rows: list[ImportRow] = []
    errors: list[TransactionImportError] = []
    for line_number, raw_row in enumerate(raw_rows, start=1):
        try:
            rows.append(_parse_csv_row(raw_row))
        except ValueError as exc:
            errors.append(TransactionImportError(row=line_number, reason=str(exc)))
    return rows, errors


@router.post(
    "/import",
    status_code=status.HTTP_200_OK,
    response_model=TransactionImportResult,
    summary="Bulk-create transactions from an uploaded CSV",
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The file itself is malformed - bad encoding, "
            "missing required columns, or too many rows."
        },
    },
)
async def import_csv(
    current_user: CurrentUser,
    db: DbSession,
    file: UploadFile,
) -> TransactionImportResult:
    raw = await file.read()
    try:
        rows, errors = _parse_csv(raw)
    except _CsvStructureError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from None

    outcome = await import_transactions(db, user_id=current_user.id, rows=rows)

    return TransactionImportResult(
        imported=outcome.imported,
        skipped_duplicates=outcome.skipped_duplicates,
        errors=errors,
    )


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    response_model=list[TransactionRead],
    summary="List the authenticated user's transactions",
)
async def list_mine(
    current_user: CurrentUser,
    db: DbSession,
    # le=100 caps a single response's size regardless of what a client asks
    # for - an unbounded limit would let one request pull a user's entire
    # transaction history in one query.
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[TransactionRead]:
    transactions = await list_transactions(db, user_id=current_user.id, limit=limit, offset=offset)
    category_ids = {t.category_id for t in transactions if t.category_id is not None}
    category_names = await get_category_names(
        db, user_id=current_user.id, category_ids=category_ids
    )
    return [to_read_model(t, category_names) for t in transactions]


@router.get(
    "/search",
    status_code=status.HTTP_200_OK,
    response_model=list[TransactionRead],
    summary="Semantic search over the authenticated user's transactions",
    description=(
        "Embeds the query and ranks this user's transactions by cosine "
        "similarity, closest first. Transactions with no embedding yet "
        "(not backfilled, or Ollama was unreachable when they were "
        "written) are excluded, not ranked last."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The query could not be embedded - Ollama is unreachable."
        },
    },
)
async def search(
    current_user: CurrentUser,
    db: DbSession,
    q: str = Query(min_length=1, max_length=500, description="Natural-language search text."),
    # Same upper bound reasoning as list_mine's limit - a small, fixed cap
    # regardless of what a client asks for.
    limit: int = Query(default=10, ge=1, le=50),
) -> list[TransactionRead]:
    # Deliberately NOT embed_transaction_or_none: that function's whole
    # point is "a missing embedding is fine, the write still succeeds
    # without one" - true for a transaction row, false for a search query.
    # A search with no query vector has nothing to rank by, so this must
    # raise, not silently return no results.
    try:
        query_embedding = await embed_text(q)
    except EmbeddingError:
        raise _search_unavailable() from None

    transactions = await search_transactions(
        db, user_id=current_user.id, query_embedding=query_embedding, limit=limit
    )
    category_ids = {t.category_id for t in transactions if t.category_id is not None}
    category_names = await get_category_names(
        db, user_id=current_user.id, category_ids=category_ids
    )
    return [to_read_model(t, category_names) for t in transactions]


@router.get(
    "/{transaction_id}",
    status_code=status.HTTP_200_OK,
    response_model=TransactionRead,
    summary="Get one of the authenticated user's transactions",
    responses={status.HTTP_404_NOT_FOUND: {"description": "No transaction with that id."}},
)
async def get_one(
    transaction_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> TransactionRead:
    transaction = await get_transaction(db, user_id=current_user.id, transaction_id=transaction_id)
    if transaction is None:
        raise _not_found()
    category_ids = {transaction.category_id} if transaction.category_id else set()
    category_names = await get_category_names(
        db, user_id=current_user.id, category_ids=category_ids
    )
    return to_read_model(transaction, category_names)


@router.patch(
    "/{transaction_id}",
    status_code=status.HTTP_200_OK,
    response_model=TransactionRead,
    summary="Update one of the authenticated user's transactions",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No transaction with that id."},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "category_id does not refer to one of your categories."
        },
    },
)
async def update(
    transaction_id: uuid.UUID,
    payload: TransactionUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> TransactionRead:
    # exclude_unset, not exclude_none: a field the client omitted must be left
    # alone, while a field sent explicitly as null (category_id, notes) must
    # clear it. Only exclude_unset tells those two apart - see
    # services/transaction.py's _UNSET sentinel, which is what actually reads
    # this distinction.
    try:
        transaction = await update_transaction(
            db,
            user_id=current_user.id,
            transaction_id=transaction_id,
            **payload.model_dump(exclude_unset=True),
        )
    except CategoryNotFound:
        raise _invalid_category() from None
    if transaction is None:
        raise _not_found()
    category_ids = {transaction.category_id} if transaction.category_id else set()
    category_names = await get_category_names(
        db, user_id=current_user.id, category_ids=category_ids
    )
    return to_read_model(transaction, category_names)


@router.delete(
    "/{transaction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one of the authenticated user's transactions",
    responses={status.HTTP_404_NOT_FOUND: {"description": "No transaction with that id."}},
)
async def delete(
    transaction_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    deleted = await delete_transaction(db, user_id=current_user.id, transaction_id=transaction_id)
    if not deleted:
        raise _not_found()
