import json
import uuid
from datetime import datetime, UTC
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.tokens import create_access_token
from app.models import Category, Transaction, User, Budget, Goal, Goal, Category
from app.services.user import create_user
from app.services.category import create_category
from app.services.budget import create_budget
from app.services.goal import create_goal

# Helpers – reuse pattern from other tests
PASSWORD = "correct-horse-battery-seed"

async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)

def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, token_version=user.token_version)}"}

async def add_category(session: AsyncSession, user: User, *, name: str = "Groceries") -> Category:
    return await create_category(session, user_id=user.id, name=name)

# ---------- Categorization tests ----------
@pytest.mark.integration
class TestCategorizationAgent:
    async def test_successful_categorization(self, db_client: AsyncClient, db_session: AsyncSession, monkeypatch):
        user = await register(db_session)
        category = await add_category(db_session, user, name="Groceries")
        # Mock embedding
        monkeypatch.setattr("app.services.embedding.embed_text", lambda text: [0.0] * 768)
        # Mock search to return a transaction using the category
        transaction = Transaction(
            user_id=user.id,
            amount=Decimal("-50.00"),
            description="Shop groceries shop",
            occurred_at=datetime(2026, 9, 4, tzinfo=UTC),
            category_id=category.id,
        )
        monkeypatch.setattr(
            "app.services.transaction.search_transactions",
            lambda *args, **kwargs: [transaction],
        )
        # Mock LLM
        monkeypatch.setattr(
            "app.services.chat.generate_answer",
            lambda *args, **kwargs: "Category: Groceries\nReason: similar purchases",
        )
        payload = {
            "description": "Shop groceries shop",
            "amount": "-50.00",
            "date": datetime(2026, 9, 4, tzinfo=UTC).isoformat(),
        }
        resp = await db_client.post("/api/v1/agents/categorize", json=payload, headers=auth(user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["category_name"] == "Groceries"
        assert body["confidence"] == 1.0
        assert body["reason"].startswith("Reason: ")
        assert body["category_id"] == str(category.id)

    async def test_uncategorized_fallback_on_unknown_category(self, db_client, db_session, monkeypatch):
        user = await register(db_session)
        await add_category(db_session, user, name="Groceries")
        monkeypatch.setattr("app.services.embedding.embed_text", lambda text: [0.0] * 768)
        transaction = Transaction(
            user_id=user.id,
            amount=Decimal("-50.00"),
            description="Some item",
            occurred_at=datetime(2026, 9, 4, tzinfo=UTC),
        )
        monkeypatch.setattr(
            "app.services.transaction.search_transactions",
            lambda *args, **kwargs: [transaction],
        )
        # LLM returns a name not in user categories
        monkeypatch.setattr(
            "app.services.chat.generate_answer",
            lambda *args, **kwargs: "Category: UnknownCat\nReason: not found",
        )
        payload = {"description": "Some item", "amount": "-20.00"}
        resp = await db_client.post("/api/v1/agents/categorize", json=payload, headers=auth(user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["category_name"] == "Uncategorized"
        assert body["category_id"] is None
        assert body["confidence"] == 0.5

    async def test_unauthenticated(self, db_client):
        payload = {"description": "x", "amount": "-1.00"}
        resp = await db_client.post("/api/v1/agents/categorize", json=payload)
        assert resp.status_code == 401

    async def test_user_isolation(self, db_client: AsyncClient, db_session: AsyncSession, monkeypatch):
        user_a = await register(db_session, email="a@example.com")
        user_b = await register(db_session, email="b@example.com")
        cat_a = await add_category(db_session, user_a, name="Groceries")
        cat_b = await add_category(db_session, user_b, name="Clothing")
        # Mock embedding
        monkeypatch.setattr("app.services.embedding.embed_text", lambda text: [0.0] * 768)
        # For user_a we return transaction with category Groceries
        transaction_a = Transaction(
            user_id=user_a.id,
            amount=Decimal("-30.00"),
            description="Shop groceries",
            occurred_at=datetime(2026, 9, 4, tzinfo=UTC),
            category_id=cat_a.id,
        )
        # For user_b search returns same description but no category
        transaction_b = Transaction(
            user_id=user_b.id,
            amount=Decimal("-15.00"),
            description="Shop groceries",
            occurred_at=datetime(2026, 9, 4, tzinfo=UTC),
        )
        monkeypatch.setattr(
            "app.services.transaction.search_transactions",
            lambda *args, **kwargs: [transaction_a] if kwargs["user_id"] == user_a.id else [transaction_b],
        )
        # LLM returns Category: Groceries
        monkeypatch.setattr(
            "app.services.chat.generate_answer",
            lambda *args, **kwargs: "Category: Groceries\nReason: matches",
        )
        payload = {"description": "Shop groceries", "amount": "-30.00"}
        # User A gets Groceries
        resp_a = await db_client.post("/api/v1/agents/categorize", json=payload, headers=auth(user_a))
        assert resp_a.status_code == 200
        body_a = resp_a.json()
        assert body_a["category_name"] == "Groceries"
        # User B falls back
        resp_b = await db_client.post("/api/v1/agents/categorize", json=payload, headers=auth(user_b))
        assert resp_b.status_code == 200
        body_b = resp_b.json()
        assert body_b["category_name"] == "Uncategorized"
        assert body_b["category_id"] is None

# ---------- Budget Advisor tests ----------
@pytest.mark.integration
class TestBudgetAdvisorAgent:
    async def test_successful_advice(self, db_client: AsyncClient, db_session: AsyncSession, monkeypatch):
        user = await register(db_session)
        # add a budget and a goal
        cat = await add_category(db_session, user, name="Groceries")
        await create_budget(db_session, user_id=user.id, category_id=cat.id, limit_amount=Decimal("200.00"))
        await create_goal(db_session, user_id=user.id, category_id=cat.id, target_amount=Decimal("500.00"), target_date=None)
        # Mock LLM
        monkeypatch.setattr(
            "app.services.chat.generate_answer",
            lambda *args, **kwargs: json.dumps({"advice": "Increase budget", "recommended_actions": ["Add $50"], "warnings": []}),
        )
        payload = {"question": "How should I adjust my budget?"}
        resp = await db_client.post("/api/v1/agents/advice", json=payload, headers=auth(user))
        assert resp.status_code == 200
        body = resp.json()
        assert body["advice"] == "Increase budget"
        assert body["recommended_actions"] == ["Add $50"]
        assert body["warnings"] == []

    async def test_malformed_json(self, db_client: AsyncClient, db_session: AsyncSession, monkeypatch):
        user = await register(db_session)
        monkeypatch.setattr(
            "app.services.chat.generate_answer",
            lambda *args, **kwargs: "Not JSON",
        )
        resp = await db_client.post("/api/v1/agents/advice", json={"question": "test"}, headers=auth(user))
        assert resp.status_code == 400

    async def test_llm_error_returns_503(self, db_client: AsyncClient, db_session: AsyncSession, monkeypatch):
        user = await register(db_session)
        from app.services.chat import ChatError
        monkeypatch.setattr(
            "app.services.chat.generate_answer",
            lambda *args, **kwargs: (_ for _ in ()).throw(ChatError("boom")),
        )
        resp = await db_client.post("/api/v1/agents/advice", json={"question": "test"}, headers=auth(user))
        assert resp.status_code == 503

    async def test_unauthenticated(self, db_client: AsyncClient):
        resp = await db_client.post("/api/v1/agents/advice", json={"question": "test"})
        assert resp.status_code == 401
