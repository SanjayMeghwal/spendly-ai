"""
Auto‑categorise a new transaction via LangGraph.

This agent demonstrates a minimal LINAGraph workflow that
* embeds a transaction’s text,
* retrieves similar past transactions, and
* asks GPT to suggest the best category name.

The graph is intentionally tiny – the goal is to show how to
chain our existing services (`embed_text`, `search_transactions`,
`generate_answer`) without adding new HTTP endpoints or middleware.
"""

import uuid
from decimal import Decimal

from langgraph.graph import StateGraph, CompiledGraph

from app.services.embedding import embed_text
from app.services.transaction import search_transactions, _resolve_category_name  # used only for possible future extensions
from app.services.chat import generate_answer

# ---------- helpers ----------

async def embed_description(state: dict) -> dict:
    """Embed the description and amount into a vector.

    The text format mirrors the one used when creating a transaction
    so that the search results are semantically coherent.
    """
    description = state["description"]
    amount = state["amount"]
    kind = "expense" if amount < 0 else "income"
    text = f"{description}, {kind} of {abs(amount)}"
    state["embedding"] = await embed_text(text)
    return state


async def find_similar(state: dict) -> dict:
    """Retrieve up to five of the most similar past transactions.

    Only the transaction rows themselves are returned – the agent later
    forwards them to the LLM.
    """
    user_id: uuid.UUID = state["user_id"]
    embedding = state["embedding"]
    hits = await search_transactions(
        user_id=user_id,
        query_embedding=embedding,
        limit=5,
    )
    state["similar"] = hits
    return state


async def ask_gpt(state: dict) -> dict:
    """Ask GPT to suggest a category.

    The prompt contains the description, amount, and a short list of
    similar past transactions.  The response is expected to start with
    something like "Category: <name>" – we parse that into
    ``state['suggested_category']``.
    """
    # Build a concise prompt.
    prompt = (
        "You are a finance‑assistant. A user wants a category for a new transaction.\n\n"
        f"NEW TRANSACTION:\nDescription: {state['description']}\n"
        f"Amount: {state['amount']}\n\n"
        "Similar past transactions:\n"
    )
    for t in state["similar"]:
        prompt += f"- {t.description}, amount {t.amount}\n"
    prompt += (
        "\nBased on the description and past purchases, suggest the best "
        "category name for this transaction, and a short justification (≤ 10 words)."
    )
    # The chat helper accepts a structured call; we can pass an empty list/dict
    # because the prompt already contains the context.
    answer = await generate_answer(question=prompt, transactions=[], category_names={})
    first_line = answer.splitlines()[0]
    if ":" in first_line:
        _, val = first_line.split(":", 1)
        state["suggested_category"] = val.strip()
    else:
        state["suggested_category"] = None
    return state


def build_categoriser_graph() -> CompiledGraph:
    workflow = StateGraph(dict)

    workflow.add_node("embed", embed_description)
    workflow.add_node("search", find_similar)
    workflow.add_node("ask", ask_gpt)

    workflow.set_entry_point("embed")
    workflow.add_edge("embed", "search")
    workflow.add_edge("search", "ask")
    workflow.set_finish_point("ask")

    return workflow.compile()

# Expose a ready‑to‑invoke graph instance.
# Importing this module automatically compiles the graph which is
# cheap, thread‑safe, and ready for use by the API layer.

categoriser = build_categoriser_graph()
