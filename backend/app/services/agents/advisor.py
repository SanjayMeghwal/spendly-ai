import inspect
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List

from langgraph.graph import StateGraph

from app.services import budget as budget_service
from app.services import category as category_service
from app.services import chat as chat_service
from app.services import goal as goal_service
from app.services import report as report_service
from app.services.chat import ChatError

# ---------- helpers ----------
async def load_context(state: Dict[str, Any]) -> Dict[str, Any]:
    """Pull budgets, goals, and a 6‑month summary for the authenticated user."""
    user_id = state["user_id"]
    db = state["db"]
    budgets = await budget_service.list_budgets(db, user_id=user_id)
    goals = await goal_service.list_goals(db, user_id=user_id)
    summary = await report_service.monthly_summary(db, user_id=user_id, months=6)
    state["budgets"] = budgets
    state["goals"] = goals
    state["summary"] = summary
    # Also load category names so we can render budgets/goals nicely.
    cat_ids = {b.category_id for b in budgets if b.category_id}
    goal_ids = {g.category_id for g in goals if g.category_id}
    cat_ids.update(goal_ids)
    cat_map = {}
    if cat_ids:
        cats = await category_service.list_categories(db, user_id=user_id)
        cat_map = {c.id: c.name for c in cats if c.id in cat_ids}
    state["category_map"] = cat_map
    return state

async def ask_llm(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a prompt containing the user's financial context and the optional question.

    The prompt is intentionally short; all heavy lifting is in the LLM. The
    response is expected to be a JSON snippet with the required shape.
    """
    budgets = state["budgets"]
    goals = state["goals"]
    summary = state["summary"]
    cat_map = state["category_map"]
    # Render simple human‑readable lists.
    bud_lines = [
        f"- {cat_map.get(b.category_id, 'Uncategorized')}: limit {b.limit_amount}"
        for b in budgets
    ]
    goal_lines = [
        f"- {cat_map.get(g.category_id, 'Uncategorized')}: target {g.target_amount}"
        for g in goals
    ]
    summary_lines = [
        f"{row.month}: income {row.income} expenses {row.expenses}"
        for row in summary
    ]
    context = "\n".join([
        "Budgets:",
        "\n".join(bud_lines) or "None",
        "Goals:",
        "\n".join(goal_lines) or "None",
        "Monthly Summary (last 6 months):",
        "\n".join(summary_lines) or "None",
    ])
    question = state.get("question", "")
    prompt = (
        f"You are a personal finance advisor.\n{context}\n\nQuestion: {question}\n\n"
        'Respond with JSON containing the following keys: "advice", '
        '"recommended_actions", "warnings". Do NOT include any other keys. '
        "Each value should be a string or a list of strings."
    )
    try:
        answer = chat_service.generate_answer(question=prompt, transactions=[], category_names={})
        if inspect.iscoroutinefunction(chat_service.generate_answer):
            answer = await answer
    except ChatError as exc:
        raise exc
    state["raw_llm_output"] = answer
    return state

async def parse_json(state: Dict[str, Any]) -> Dict[str, Any]:
    """Attempt to parse the LLM output as JSON and validate the keys."""
    import json
    raw = state.get("raw_llm_output", "")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("Malformed LLM response: not valid JSON")
    # Validate required keys.
    advice = data.get("advice", "")
    actions = data.get("recommended_actions", [])
    warnings = data.get("warnings", [])
    if not isinstance(advice, str) or not isinstance(actions, list) or not isinstance(warnings, list):
        raise ValueError("Malformed LLM response: wrong types for keys")
    return {
        "advice": advice,
        "recommended_actions": [str(a) for a in actions],
        "warnings": [str(w) for w in warnings],
    }


def build_budget_advisor_graph():
    workflow = StateGraph(dict)
    workflow.add_node("load", load_context)
    workflow.add_node("ask", ask_llm)
    workflow.add_node("parse", parse_json)
    workflow.set_entry_point("load")
    workflow.add_edge("load", "ask")
    workflow.add_edge("ask", "parse")
    workflow.set_finish_point("parse")
    return workflow.compile()

_budget_advisor = build_budget_advisor_graph()

async def run_budget_advisor(
    db, *, user_id, question: str | None = None
) -> Dict[str, Any]:
    state = {
        "user_id": user_id,
        "db": db,
        "question": question or "",
    }
    result = await _budget_advisor.ainvoke(state)
    return result

