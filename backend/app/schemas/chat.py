"""Request and response schemas for RAG chat."""

from pydantic import BaseModel, Field

from app.schemas.transaction import TransactionRead


class ChatRequest(BaseModel):
    """Request body for POST /chat."""

    question: str = Field(
        min_length=1,
        max_length=500,
        description="A natural-language question about the caller's own transactions.",
        examples=["How much did I spend on groceries last month?"],
    )


class ChatResponse(BaseModel):
    """Response body for POST /chat.

    `sources` are the transactions retrieval grounded the answer in, closest
    match first - the same rows /transactions/search would return for
    `question`. Returning them lets the caller verify the answer rather than
    trust it outright, matching CLAUDE.md's "treat all LLM output as
    untrusted" stance: nothing here should be acted on (e.g. used to make a
    financial decision) without the user being able to check it against the
    real transactions.
    """

    answer: str
    sources: list[TransactionRead]
