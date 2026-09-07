'''Agent endpoints – categorization & budget advisor.

This module defines the public API surface for the LangGraph agents added in M12.
It mirrors the style of other route modules: thin HTTP layer, delegating all business
logic to `app.services.agents` functions. The endpoints are protected by the
standard FastAPI dependencies (`CurrentUser`, `DbSession`, and the shared
`AiRateLimit` dependency which enforces the per‑user sliding‑window rate limit
defined in `app.core.rate_limit`).

The request/response models live in `app.schemas.agents` and are Pydantic
`BaseModel`s, ensuring OpenAPI documentation (when enabled) and runtime validation.
'''

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AiRateLimit, CurrentUser, DbSession
from app.schemas.agents import (
    CategorizationRequest,
    CategorizationResponse,
    AdvisorRequest,
    AdvisorResponse,
)
from app.services.agents import run_categorizer, run_budget_advisor
from app.services.embedding import EmbeddingError
from app.services.chat import ChatError

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post(
    "/categorize",
    status_code=status.HTTP_200_OK,
    response_model=CategorizationResponse,
    summary="Auto‑categorize a transaction using the LangGraph categorizer",
    description=(
        "Accepts a transaction description, amount, optional date and optional pre‑"
        "selected category. Returns the suggested category, confidence, and a "
        "human‑readable reason. The call is rate‑limited per user (see `AI_RATE_"\
        "LIMIT_REQUESTS`)."
    ),
)
async def categorize(
    payload: CategorizationRequest,
    current_user: CurrentUser,
    db: DbSession,
    _ai_rate_limit: AiRateLimit,
) -> CategorizationResponse:
    """Run the categorizer agent.

    Errors are mapped to HTTP status codes:
    * `EmbeddingError` → 503 Service Unavailable (embedding backend down)
    * `ChatError`      → 503 Service Unavailable (LLM backend down)
    """
    try:
        result = await run_categorizer(
            db,
            user_id=current_user.id,
            description=payload.description,
            amount=payload.amount,
            date=payload.date,
            category_id=payload.category_id,
        )
    except EmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Embedding service unavailable",
        ) from exc
    except ChatError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM service unavailable",
        ) from exc

    return CategorizationResponse(
        category_id=result.get("category_id"),
        category_name=result.get("category_name"),
        confidence=result.get("confidence"),
        reason=result.get("reason"),
    )


@router.post(
    "/advice",
    status_code=status.HTTP_200_OK,
    response_model=AdvisorResponse,
    summary="Budget‑advisor agent – give financial advice",
    description=(
        "Provides AI‑generated advice based on the user's budgets, goals, and "
        "recent transaction summary. The optional `question` field can be used "
        "to ask a specific query; an empty string yields a generic overview."
    ),
)
async def advice(
    payload: AdvisorRequest,
    current_user: CurrentUser,
    db: DbSession,
    _ai_rate_limit: AiRateLimit,
) -> AdvisorResponse:
    """Run the budget‑advisor agent.

    The agent returns a JSON blob; we validate it and surface any parsing error
    as a 400 Bad Request. LLM‑service failures become 503.
    """
    try:
        result = await run_budget_advisor(
            db, user_id=current_user.id, question=payload.question
        )
    except ChatError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM service unavailable",
        ) from exc
    except ValueError as exc:
        # Raised by `parse_json` when the LLM output is not valid JSON or missing
        # required keys.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return AdvisorResponse(**result)
