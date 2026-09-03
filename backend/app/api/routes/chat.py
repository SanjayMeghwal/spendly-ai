"""Chat endpoint - natural-language Q&A over a user's own transactions.

HTTP only - routing and status codes. No business logic lives here; see
app/services/chat.py for answer generation and app/services/embedding.py /
app/services/transaction.py for retrieval, both already built for
GET /transactions/search (M10) and reused here unchanged.
"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.api.routes.transactions import to_read_model
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.category import get_category_names
from app.services.chat import ChatError, generate_answer
from app.services.embedding import EmbeddingError, embed_text
from app.services.transaction import search_transactions

router = APIRouter(prefix="/chat", tags=["chat"])

# How many transactions ground each answer. A fixed, small constant rather
# than a client-supplied limit (unlike /transactions/search's `limit` query
# param): more context does not reliably make an LLM answer better, and a
# caller has no principled basis to pick a different number for a question
# it hasn't asked yet.
_RETRIEVAL_TOP_K = 10


def _chat_unavailable() -> HTTPException:
    """503 for 'no answer could be produced'.

    Covers both retrieval (the question itself could not be embedded, same
    as /transactions/search) and generation (Groq is unreachable). Either
    way there is nothing to return - not a bug in the request, a dependency
    temporarily down, matching /health/ready's and /transactions/search's
    convention.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Chat is temporarily unavailable.",
    )


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    response_model=ChatResponse,
    summary="Ask a natural-language question about the caller's own transactions",
    description=(
        "Embeds the question, retrieves the most semantically similar "
        f"transactions (top {_RETRIEVAL_TOP_K}), and asks an LLM to answer "
        "using only those as context. Stateless - each call is independent, "
        "there is no conversation history."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The question could not be embedded, or Groq is unreachable."
        },
    },
)
async def chat(request: ChatRequest, current_user: CurrentUser, db: DbSession) -> ChatResponse:
    try:
        query_embedding = await embed_text(request.question)
    except EmbeddingError:
        raise _chat_unavailable() from None

    transactions = await search_transactions(
        db, user_id=current_user.id, query_embedding=query_embedding, limit=_RETRIEVAL_TOP_K
    )
    category_ids = {t.category_id for t in transactions if t.category_id is not None}
    category_names = await get_category_names(
        db, user_id=current_user.id, category_ids=category_ids
    )

    try:
        answer = await generate_answer(
            question=request.question, transactions=transactions, category_names=category_names
        )
    except ChatError:
        raise _chat_unavailable() from None

    sources = [to_read_model(t, category_names) for t in transactions]
    return ChatResponse(answer=answer, sources=sources)
