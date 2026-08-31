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

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession
from app.models import Transaction
from app.schemas.transaction import TransactionCreate, TransactionRead, TransactionUpdate
from app.services.category import CategoryNotFound, get_category_names
from app.services.transaction import (
    create_transaction,
    delete_transaction,
    get_transaction,
    list_transactions,
    update_transaction,
)

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


def _to_read_model(
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
    return _to_read_model(transaction, category_names)


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
    return [_to_read_model(t, category_names) for t in transactions]


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
    return _to_read_model(transaction, category_names)


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
    return _to_read_model(transaction, category_names)


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
