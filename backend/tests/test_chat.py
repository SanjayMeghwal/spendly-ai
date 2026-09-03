"""Tests for POST /api/v1/chat.

Retrieval (embed_text, search_transactions) is exercised for real against
the test database, exactly as test_transactions_search.py does - only the
external calls (embedding, Groq generation) are mocked, per CLAUDE.md's
testing rules.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Transaction, User
from app.services.chat import ChatError
from app.services.embedding import EmbeddingError
from app.services.user import create_user

CHAT_URL = "/api/v1/chat"
PASSWORD = "correct-horse-battery-staple"
EMBEDDING_DIM = 768


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


def unit_vector(hot_index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[hot_index] = 1.0
    return vector


async def add_transaction_with_embedding(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    embedding: list[float] | None,
    description: str = "Grocery store",
) -> Transaction:
    transaction = Transaction(
        user_id=user_id,
        amount=Decimal("-10.00"),
        description=description,
        occurred_at=datetime(2026, 1, 15, tzinfo=UTC),
        embedding=embedding,
    )
    session.add(transaction)
    await session.commit()
    await session.refresh(transaction)
    return transaction


@pytest.mark.integration
class TestSuccessfulChat:
    async def test_returns_an_answer_grounded_in_the_users_own_transactions(
        self,
        db_client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await register(db_session)
        relevant = await add_transaction_with_embedding(
            db_session, user_id=user.id, embedding=unit_vector(0), description="Whole Foods"
        )

        async def query_embeds_as_relevant(text: str) -> list[float]:
            return unit_vector(0)

        async def fake_answer(
            *, question: str, transactions: list[Transaction], category_names: dict[uuid.UUID, str]
        ) -> str:
            assert question == "How much on groceries?"
            assert [t.id for t in transactions] == [relevant.id]
            return "You spent $10.00 on groceries."

        monkeypatch.setattr("app.api.routes.chat.embed_text", query_embeds_as_relevant)
        monkeypatch.setattr("app.api.routes.chat.generate_answer", fake_answer)

        response = await db_client.post(
            CHAT_URL, json={"question": "How much on groceries?"}, headers=auth(user)
        )

        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == "You spent $10.00 on groceries."
        assert [s["id"] for s in body["sources"]] == [str(relevant.id)]

    async def test_never_grounds_the_answer_in_another_users_transaction(
        self,
        db_client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        await add_transaction_with_embedding(
            db_session, user_id=grace.id, embedding=unit_vector(0), description="Grace's secret"
        )

        async def fake_answer(
            *, question: str, transactions: list[Transaction], category_names: dict[uuid.UUID, str]
        ) -> str:
            assert transactions == []
            return "I found no relevant transactions."

        monkeypatch.setattr("app.api.routes.chat.generate_answer", fake_answer)

        response = await db_client.post(CHAT_URL, json={"question": "anything"}, headers=auth(ada))

        assert response.status_code == 200
        assert response.json()["sources"] == []


@pytest.mark.integration
class TestValidation:
    async def test_rejects_an_empty_question(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(CHAT_URL, json={"question": ""}, headers=auth(user))

        assert response.status_code == 422

    async def test_missing_question_is_rejected(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(CHAT_URL, json={}, headers=auth(user))

        assert response.status_code == 422

    async def test_question_over_the_length_limit_is_rejected(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.post(CHAT_URL, json={"question": "x" * 501}, headers=auth(user))

        assert response.status_code == 422


@pytest.mark.integration
class TestUnavailable:
    async def test_returns_503_when_the_question_cannot_be_embedded(
        self,
        db_client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await register(db_session)

        async def always_fails(text: str) -> list[float]:
            raise EmbeddingError("Ollama is unreachable")

        monkeypatch.setattr("app.api.routes.chat.embed_text", always_fails)

        response = await db_client.post(CHAT_URL, json={"question": "anything"}, headers=auth(user))

        assert response.status_code == 503

    async def test_returns_503_when_groq_cannot_produce_an_answer(
        self,
        db_client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await register(db_session)

        async def always_fails(
            *, question: str, transactions: list[Transaction], category_names: dict[uuid.UUID, str]
        ) -> str:
            raise ChatError("Groq is unreachable")

        monkeypatch.setattr("app.api.routes.chat.generate_answer", always_fails)

        response = await db_client.post(CHAT_URL, json={"question": "anything"}, headers=auth(user))

        assert response.status_code == 503


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.post(CHAT_URL, json={"question": "anything"})

        assert response.status_code == 401
