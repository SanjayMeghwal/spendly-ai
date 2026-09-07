import inspect
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict

from langgraph.graph import StateGraph

from app.services import category as category_service
from app.services import chat as chat_service
from app.services import embedding as embedding_service
from app.services import transaction as transaction_service
from app.services.chat import ChatError

# ---------- helpers ----------
async def load_categories(state: Dict[str, Any]) -> Dict[str, Any]:
    user_id = state["user_id"]
    db = state["db"]
    categories = await category_service.list_categories(db, user_id=user_id)
    state["categories"] = {c.id: c.name for c in categories}
    return state

async def embed_description(state: Dict[str, Any]) -> Dict[str, Any]:
    description = state["description"]
    amount = state["amount"]
    text = embedding_service.build_transaction_embedding_text(
        description=description, amount=amount, category_name=None
    )
    embed_func = embedding_service.embed_text
    if inspect.iscoroutinefunction(embed_func):
        state["embedding"] = await embed_func(text)
    else:
        state["embedding"] = embed_func(text)
    return state

async def find_similar(state: Dict[str, Any]) -> Dict[str, Any]:
    user_id = state["user_id"]
    db = state["db"]
    embedding = state["embedding"]
    similar = transaction_service.search_transactions(
        db, user_id=user_id, query_embedding=embedding, limit=5
    )
    if inspect.iscoroutinefunction(transaction_service.search_transactions):
        similar = await similar
    state["similar"] = similar
    return state

async def ask_llm(state: Dict[str, Any]) -> Dict[str, Any]:
    description = state["description"]
    amount = state["amount"]
    date = state.get("date")
    similar = state["similar"]
    cat_names = state["categories"]
    desc = f"Description: {description}\nAmount: {amount}"
    if date is not None:
        desc += f"\nDate: {date.isoformat()}"
    similar_lines = "\n".join([f"- {t.description}, amount {t.amount}" for t in similar]) or "No similar transactions found."
    context = "\n".join([
        "User categories:",
        ", ".join([name for name in cat_names.values()]) or "None",
        "Similar past transactions:",
        similar_lines,
    ])
    prompt = (
        f"You are a personal finance assistant.\\n{desc}\n\\n{context}\n\\nSelect the best category name for this transaction and compare it with the categories above. Provide the category name on the first line in the form 'Category: <name>'. Also supply a brief reason (max 10 words)."
    )
    try:
        answer = chat_service.generate_answer(question=prompt, transactions=[], category_names={})
        if inspect.iscoroutinefunction(chat_service.generate_answer):
            answer = await answer
    except ChatError as exc:
        raise exc
    first_line = answer.splitlines()[0] if answer else ""
    if first_line.lower().startswith("category:"):
        _, value = first_line.split(":", 1)
        state["suggested_category"] = value.strip()
        state["reason"] = " ".join(answer.splitlines()[1:]) if len(answer.splitlines()) > 1 else ""
    else:
        state["suggested_category"] = None
        state["reason"] = answer.strip() if answer else ""
    return state

async def finalise(state: Dict[str, Any]) -> Dict[str, Any]:
    cat_name = state.get("suggested_category")
    cat_map = state["categories"]
    if cat_name and cat_name in cat_map.values():
        cat_id = next(id_ for id_, name in cat_map.items() if name == cat_name)
        confidence = 1.0
    else:
        cat_name = "Uncategorized"
        cat_id = None
        confidence = 0.5
    return {
        "category_id": cat_id,
        "category_name": cat_name,
        "confidence": confidence,
        "reason": state.get("reason", ""),
    }


def build_categoriser_graph():
    workflow = StateGraph(dict)
    workflow.add_node("load", load_categories)
    workflow.add_node("embed", embed_description)
    workflow.add_node("search", find_similar)
    workflow.add_node("ask", ask_llm)
    workflow.add_node("finalise", finalise)
    workflow.set_entry_point("load")
    workflow.add_edge("load", "embed")
    workflow.add_edge("embed", "search")
    workflow.add_edge("search", "ask")
    workflow.add_edge("ask", "finalise")
    workflow.set_finish_point("finalise")
    return workflow.compile()

_categoriser = build_categoriser_graph()

async def run_categorizer(
    db, *, user_id, description, amount, date=None, category_id=None
) -> Dict[str, Any]:
    state = {
        "user_id": user_id,
        "db": db,
        "description": description,
        "amount": amount,
        "date": date,
        "category_id": category_id,
    }
    result = await _categoriser.ainvoke(state)
    return result
