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

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.transaction import TransactionCreate, TransactionRead
from app.services.transaction import create_transaction

router = APIRouter(prefix="/transactions", tags=["transactions"])


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
