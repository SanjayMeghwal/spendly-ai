"""Business logic for LangGraph agents.

This module provides thin wrappers used by the HTTP layer (``backend/app/api/routes/agents.py``).
It deliberately stays out of FastAPI – only async DB sessions and the existing
service functions are used.
"""

import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Transaction, Budget, Goal
from app.services.embedding import embed_text
from app.services.transaction import search_transactions
from app.services.chat import generate_answer, ChatError

# ---------------------------------------------------------------------------
# Categorizer agent helper
# ---------------------------------------------------------------------------

async def run_categorizer(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    description: str,
    amount: Decimal,
    date: Optional[datetime] = None,
    category_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Run the auto‑categorisation LangGraph agent.

    The function mirrors the behaviour expected by ``tests/test_agents.py``:

    * Embed the transaction description/amount.
    * Retrieve up‑to‑five similar past transactions for grounding.
    * Prompt the LLM and parse ``"Category: <name>"`` plus an optional
      ``"Reason: …"`` line.
    * Look up the suggested category in the caller's own categories.
    * Return a dict matching ``CategorizationResponse``.

    ``date`` and ``category_id`` are accepted for API compatibility but are not
    used by the current agent implementation.
    """

    # 1️⃣ Build the embedding text – identical to ``app.services.langgraph.categorizer``.
    kind = "expense" if amount < 0 else "income"
    embed_input = f"{description}, {kind} of {abs(amount)}"
    embedding = await embed_text(embed_input)

    # 2️⃣ Find similar transactions for the LLM context.
    similar_txns = await search_transactions(
        db,
        user_id=user_id,
        query_embedding=embedding,
        limit=5,
    )

    # 3️⃣ Construct the prompt – same wording as ``ask_gpt`` in the graph.
    prompt = (
        "You are a finance‑assistant. A user wants a category for a new transaction.\n\n"
        f"NEW TRANSACTION:\nDescription: {description}\nAmount: {amount}\n\n"
        "Similar past transactions:\n"
    )
    for t in similar_txns:
        # ``t`` is a ``Transaction`` ORM instance; we only need a short description.
        prompt += f"- {t.description}, amount {t.amount}\n"
    prompt += (
        "\nBased on the description and past purchases, suggest the best category name "
        "for this transaction, and a short justification (≤ 10 words)."
    )

    # 4️⃣ Call the LLM.
    answer = await generate_answer(question=prompt, transactions=[], category_names={})

    # 5️⃣ Parse the answer.
    lines = [ln.strip() for ln in answer.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("LLM returned empty answer")
    # Expected first line: "Category: <name>"
    first = lines[0]
    if ":" not in first:
        raise ValueError(f"Unexpected LLM answer format: {first}")
    _, cat_name = first.split(":", 1)
    cat_name = cat_name.strip()
    # Optional reason – everything after the first line joined back together.
    reason = "\n".join(lines[1:]) if len(lines) > 1 else ""
    # Preserve the raw "Reason: …" prefix if present; tests only check it starts with that.

    # 6️⃣ Resolve the category name against the user's categories (case‑insensitive).
    cat_row = await db.execute(
        select(Category).where(
            Category.user_id == user_id,
            func.lower(Category.name) == cat_name.lower(),
        )
    )
    category_obj = cat_row.scalar_one_or_none()

    if category_obj:
        result = {
            "category_id": str(category_obj.id),
            "category_name": category_obj.name,
            "confidence": 1.0,
            "reason": reason,
        }
    else:
        # Fallback – uncategorised.
        result = {
            "category_id": None,
            "category_name": "Uncategorized",
            "confidence": 0.5,
            "reason": reason,
        }

    return result

# ---------------------------------------------------------------------------
# Budget‑advisor agent helper
# ---------------------------------------------------------------------------

async def run_budget_advisor(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    question: Optional[str] = None,
) -> dict[str, Any]:
    """Run the budget‑advisor LangGraph agent.

    The current implementation is intentionally lightweight – it gathers the
    user's budgets and goals, builds a short grounding prompt, asks the LLM, and
    expects the LLM to return a JSON object matching ``AdvisorResponse``.  The
    tests monkey‑patch ``generate_answer`` to return a JSON string, so we simply
    parse that string and return the resulting dict.

    * If the LLM output is not valid JSON, a ``ValueError`` is raised – the
      route translates this to a ``400 Bad Request``.
    * ``ChatError`` propagates unchanged, yielding a ``503 Service Unavailable``.
    """

    # Minimal grounding – list budgets and goals (ids and limits) so the model
    # has some context.  The exact formatting is not important for the tests.
    budgets = await db.execute(select(Budget).where(Budget.user_id == user_id))
    budget_list = [b for b in budgets.scalars().all()]
    goals = await db.execute(select(Goal).where(Goal.user_id == user_id))
    goal_list = [g for g in goals.scalars().all()]

    # Build a prompt – the model is instructed to return JSON.
    prompt_parts = ["You are a personal‑finance advisor."]
    if question:
        prompt_parts.append(f"Question: {question}")
    else:
        prompt_parts.append("Give a brief financial overview.")
    prompt_parts.append("User budgets (category_id, limit):")
    for b in budget_list:
        prompt_parts.append(f"- {b.category_id}: {b.limit_amount}")
    prompt_parts.append("User goals (category_id, target):")
    for g in goal_list:
        target = g.target_amount
        prompt_parts.append(f"- {g.category_id}: {target}")
    prompt_parts.append(
        "Return a JSON object with keys: advice (string), recommended_actions (list of strings), warnings (list of strings)."
    )
    prompt = "\n".join(prompt_parts)

    # Call LLM.
    answer = await generate_answer(question=prompt, transactions=[], category_names={})

    # The answer should be a JSON string.
    try:
        parsed = json.loads(answer)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM did not return valid JSON: {exc}") from exc

    # Ensure expected keys exist – missing keys are treated as empty/defaults.
    advice = parsed.get("advice", "")
    recommended_actions = parsed.get("recommended_actions", [])
    warnings = parsed.get("warnings", [])

    return {
        "advice": advice,
        "recommended_actions": recommended_actions,
        "warnings": warnings,
    }

# ---------------------------------------------------------------------------
# __all__ for explicit export
# ---------------------------------------------------------------------------

__all__ = ["run_categorizer", "run_budget_advisor"]
