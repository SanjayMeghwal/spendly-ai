"""Tests for GET /api/v1/transactions/search.

Ordering-by-similarity tests set transaction.embedding directly to
hand-picked orthonormal-ish vectors rather than relying on the suite-wide
Ollama stub (tests/conftest.py's stub_ollama_embeddings, which returns the
same constant vector for every call) - real relevance ranking needs vectors
that actually differ, and controlling them directly is the simplest way to
get a deterministic ordering to assert on.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import Transaction, User
from app.services.embedding import EmbeddingError
from app.services.user import create_user

SEARCH_URL = "/api/v1/transactions/search"
PASSWORD = "correct-horse-battery-staple"
EMBEDDING_DIM = 768


async def register(session: AsyncSession, *, email: str = "ada@example.com") -> User:
    return await create_user(session, email=email, password=PASSWORD)


def auth(user: User) -> dict[str, str]:
    token = create_access_token(user.id, token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


def unit_vector(hot_index: int) -> list[float]:
    """A 768-dim vector that is 1.0 at hot_index and 0.0 everywhere else.

    Two different hot_index vectors are orthogonal (cosine distance ~1);
    identical ones have cosine distance 0 - exactly the separation needed to
    make relevance ordering assertable instead of coincidental.
    """
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
    """Insert a Transaction with a specific (or absent) embedding directly,
    bypassing create_transaction - these tests need exact control over the
    vector, not whatever the stubbed embed_text call would produce.
    """
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
class TestSuccessfulSearch:
    async def test_ranks_the_closest_match_first(
        self,
        db_client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await register(db_session)
        closest = await add_transaction_with_embedding(
            db_session, user_id=user.id, embedding=unit_vector(0), description="closest match"
        )
        await add_transaction_with_embedding(
            db_session, user_id=user.id, embedding=unit_vector(1), description="unrelated one"
        )
        await add_transaction_with_embedding(
            db_session, user_id=user.id, embedding=unit_vector(2), description="unrelated two"
        )

        # The suite-wide Ollama stub returns a constant [0.1, ..., 0.1]
        # vector for the query itself, which is not close to any hand-set
        # unit vector - so this test overrides just the query embedding to
        # exactly match `closest`'s.
        async def query_embeds_as_closest(text: str) -> list[float]:
            return unit_vector(0)

        monkeypatch.setattr("app.api.routes.transactions.embed_text", query_embeds_as_closest)

        response = await db_client.get(SEARCH_URL, params={"q": "anything"}, headers=auth(user))

        assert response.status_code == 200
        results = response.json()
        assert results[0]["id"] == str(closest.id)

    async def test_excludes_transactions_with_no_embedding(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        embedded = await add_transaction_with_embedding(
            db_session, user_id=user.id, embedding=unit_vector(0), description="has embedding"
        )
        await add_transaction_with_embedding(
            db_session, user_id=user.id, embedding=None, description="no embedding yet"
        )

        response = await db_client.get(SEARCH_URL, params={"q": "anything"}, headers=auth(user))

        assert response.status_code == 200
        ids = [t["id"] for t in response.json()]
        assert ids == [str(embedded.id)]

    async def test_never_returns_another_users_transaction(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        await add_transaction_with_embedding(
            db_session, user_id=grace.id, embedding=unit_vector(0), description="Grace's secret"
        )

        response = await db_client.get(SEARCH_URL, params={"q": "anything"}, headers=auth(ada))

        assert response.status_code == 200
        assert response.json() == []

    async def test_limit_caps_the_number_of_results(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        for i in range(5):
            await add_transaction_with_embedding(
                db_session, user_id=user.id, embedding=unit_vector(i), description=f"item {i}"
            )

        response = await db_client.get(
            SEARCH_URL, params={"q": "anything", "limit": 2}, headers=auth(user)
        )

        assert len(response.json()) == 2

    async def test_returns_empty_list_when_the_user_has_no_transactions(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(SEARCH_URL, params={"q": "anything"}, headers=auth(user))

        assert response.status_code == 200
        assert response.json() == []


@pytest.mark.integration
class TestValidation:
    async def test_rejects_an_empty_query(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(SEARCH_URL, params={"q": ""}, headers=auth(user))

        assert response.status_code == 422

    async def test_missing_query_is_rejected(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(SEARCH_URL, headers=auth(user))

        assert response.status_code == 422

    async def test_limit_above_the_maximum_is_rejected(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(
            SEARCH_URL, params={"q": "anything", "limit": 51}, headers=auth(user)
        )

        assert response.status_code == 422


@pytest.mark.integration
class TestOllamaUnavailable:
    async def test_returns_503_when_the_query_cannot_be_embedded(
        self,
        db_client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await register(db_session)

        async def always_fails(text: str) -> list[float]:
            raise EmbeddingError("Ollama is unreachable")

        monkeypatch.setattr("app.api.routes.transactions.embed_text", always_fails)

        response = await db_client.get(SEARCH_URL, params={"q": "anything"}, headers=auth(user))

        assert response.status_code == 503


@pytest.mark.integration
class TestAuthentication:
    async def test_requires_authentication(self, db_client: AsyncClient) -> None:
        response = await db_client.get(SEARCH_URL, params={"q": "anything"})

        assert response.status_code == 401
