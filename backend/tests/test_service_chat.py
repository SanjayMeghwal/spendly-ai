"""Tests for services/chat.py.

Groq is an external service - slow, and not something we want the test suite
to depend on being up - so per CLAUDE.md's testing rules it is mocked here,
the same tier as Ollama in test_service_embedding.py. Nothing here touches
the database.
"""

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.core.config import get_settings
from app.models import Transaction
from app.services.chat import ChatError, generate_answer

# Same reasoning as test_service_embedding.py's _RealAsyncClient: patching
# app.services.chat.httpx.AsyncClient patches the SAME class object
# httpx.AsyncClient refers to everywhere, so the factory below must build
# clients from this saved reference or it recurses into itself.
_RealAsyncClient = httpx.AsyncClient


def _mock_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[..., httpx.AsyncClient]:
    def factory(*, base_url: str = "", **kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(base_url=base_url, transport=httpx.MockTransport(handler))

    return factory


def _transaction(
    *,
    description: str = "Grocery store",
    amount: Decimal = Decimal("-42.50"),
    category_id: uuid.UUID | None = None,
) -> Transaction:
    return Transaction(
        user_id=uuid.uuid4(),
        amount=amount,
        description=description,
        occurred_at=datetime(2026, 1, 15, tzinfo=UTC),
        category_id=category_id,
    )


class TestGenerateAnswer:
    async def test_returns_the_answer_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["model"] == get_settings().GROQ_MODEL
            assert body["messages"][0]["role"] == "system"
            assert "Grocery store, expense of 42.50" in body["messages"][1]["content"]
            assert "How much on groceries?" in body["messages"][1]["content"]
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "You spent $42.50."}}]}
            )

        monkeypatch.setattr("app.services.chat.httpx.AsyncClient", _mock_client(handler))

        result = await generate_answer(
            question="How much on groceries?",
            transactions=[_transaction()],
            category_names={},
        )

        assert result == "You spent $42.50."

    async def test_includes_the_category_name_when_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        category_id = uuid.uuid4()

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert "category Groceries" in body["messages"][1]["content"]
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        monkeypatch.setattr("app.services.chat.httpx.AsyncClient", _mock_client(handler))

        await generate_answer(
            question="anything",
            transactions=[_transaction(category_id=category_id)],
            category_names={category_id: "Groceries"},
        )

    async def test_placeholder_context_when_no_transactions_were_retrieved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert "No transactions were found" in body["messages"][1]["content"]
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        monkeypatch.setattr("app.services.chat.httpx.AsyncClient", _mock_client(handler))

        await generate_answer(question="anything", transactions=[], category_names={})

    async def test_raises_chat_error_on_http_error_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "rate limited"})

        monkeypatch.setattr("app.services.chat.httpx.AsyncClient", _mock_client(handler))

        with pytest.raises(ChatError):
            await generate_answer(question="anything", transactions=[], category_names={})

    async def test_raises_chat_error_when_groq_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        monkeypatch.setattr("app.services.chat.httpx.AsyncClient", _mock_client(handler))

        with pytest.raises(ChatError):
            await generate_answer(question="anything", transactions=[], category_names={})

    async def test_raises_chat_error_when_response_has_no_choices(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "shape"})

        monkeypatch.setattr("app.services.chat.httpx.AsyncClient", _mock_client(handler))

        with pytest.raises(ChatError):
            await generate_answer(question="anything", transactions=[], category_names={})

    async def test_raises_chat_error_when_content_is_not_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

        monkeypatch.setattr("app.services.chat.httpx.AsyncClient", _mock_client(handler))

        with pytest.raises(ChatError):
            await generate_answer(question="anything", transactions=[], category_names={})
