"""Tests for services/embedding.py.

Ollama is an external service - slow, and not something we want the test
suite to depend on being up - so per CLAUDE.md's testing rules it is
mocked here, the same tier as a third-party API or an LLM call. Nothing
else in this module touches the database, so none of these use
`db_session` or `@pytest.mark.integration`.
"""

import json
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from app.core.config import get_settings
from app.services.embedding import (
    EmbeddingError,
    build_transaction_embedding_text,
    embed_text,
    embed_transaction_or_none,
)

# Captured before any test monkeypatches httpx.AsyncClient. Patching
# app.services.embedding.httpx.AsyncClient patches the SAME class object
# httpx.AsyncClient refers to everywhere - embedding.py's `httpx` is the
# real module, not a copy - so the factory below must build clients from
# this saved reference, not from httpx.AsyncClient, or it recurses into
# itself.
_RealAsyncClient = httpx.AsyncClient


def _mock_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[..., httpx.AsyncClient]:
    """Build a factory that stands in for httpx.AsyncClient.

    embed_text constructs its own AsyncClient(base_url=..., timeout=...)
    internally, so there is no client instance a test can hand it directly.
    Monkeypatching httpx.AsyncClient itself to this factory lets the real
    base_url/timeout arguments through while swapping in a MockTransport
    that never touches the network.
    """

    def factory(*, base_url: str = "", **kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(base_url=base_url, transport=httpx.MockTransport(handler))

    return factory


class TestEmbedText:
    async def test_returns_the_embedding_vector(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/embeddings"
            body = json.loads(request.content)
            assert body["model"] == get_settings().OLLAMA_EMBEDDING_MODEL
            assert body["prompt"] == "Grocery store, -42.50"
            return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3]})

        monkeypatch.setattr("app.services.embedding.httpx.AsyncClient", _mock_client(handler))

        result = await embed_text("Grocery store, -42.50")

        assert result == [0.1, 0.2, 0.3]

    async def test_raises_embedding_error_on_http_error_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "model not found"})

        monkeypatch.setattr("app.services.embedding.httpx.AsyncClient", _mock_client(handler))

        with pytest.raises(EmbeddingError):
            await embed_text("anything")

    async def test_raises_embedding_error_when_ollama_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        monkeypatch.setattr("app.services.embedding.httpx.AsyncClient", _mock_client(handler))

        with pytest.raises(EmbeddingError):
            await embed_text("anything")

    async def test_raises_embedding_error_when_response_has_no_embedding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": "shape"})

        monkeypatch.setattr("app.services.embedding.httpx.AsyncClient", _mock_client(handler))

        with pytest.raises(EmbeddingError):
            await embed_text("anything")


class TestBuildTransactionEmbeddingText:
    def test_negative_amount_reads_as_an_expense(self) -> None:
        text = build_transaction_embedding_text(
            description="Grocery store", amount=Decimal("-42.50"), category_name="Groceries"
        )

        assert text == "Grocery store, expense of 42.50, category Groceries"

    def test_positive_amount_reads_as_income(self) -> None:
        text = build_transaction_embedding_text(
            description="Paycheck", amount=Decimal("2000.00"), category_name=None
        )

        assert text == "Paycheck, income of 2000.00"


class TestEmbedTransactionOrNone:
    async def test_returns_the_embedding_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["prompt"] == "Grocery store, expense of 42.50, category Groceries"
            return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3]})

        monkeypatch.setattr("app.services.embedding.httpx.AsyncClient", _mock_client(handler))

        result = await embed_transaction_or_none(
            description="Grocery store", amount=Decimal("-42.50"), category_name="Groceries"
        )

        assert result == [0.1, 0.2, 0.3]

    async def test_returns_none_instead_of_raising_when_ollama_is_down(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        monkeypatch.setattr("app.services.embedding.httpx.AsyncClient", _mock_client(handler))

        result = await embed_transaction_or_none(
            description="Grocery store", amount=Decimal("-42.50"), category_name="Groceries"
        )

        assert result is None
        assert "Failed to embed transaction" in caplog.text
