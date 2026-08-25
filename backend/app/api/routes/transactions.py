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
from app.schemas.transaction import TransactionCreate, TransactionRead
from app.services.transaction import create_transaction, get_transaction, list_transactions

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


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=TransactionRead,
    summary="Record a transaction",
)
async def create(
    payload: TransactionCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> TransactionRead:
    transaction = await create_transaction(
        db,
        user_id=current_user.id,
        amount=payload.amount,
        description=payload.description,
        category=payload.category,
        occurred_at=payload.occurred_at,
        notes=payload.notes,
    )
    return TransactionRead.model_validate(transaction)


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
    return [TransactionRead.model_validate(t) for t in transactions]


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
    return TransactionRead.model_validate(transaction)
