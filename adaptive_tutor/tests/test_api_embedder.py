"""Тесты API-эмбеддера (OpenAI-совместимый /embeddings RouterAI)."""

from types import SimpleNamespace

import src.rag as rag_module
from src.rag import ApiEmbedder


class _FakeEmbedding:
    def __init__(self, index: int, embedding: list[float]):
        self.index = index
        self.embedding = embedding


class _FakeResponse:
    def __init__(self, data: list[_FakeEmbedding]):
        self.data = data


class _FakeEmbeds:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, model: str, **kwargs) -> _FakeResponse:
        texts: list[str] = kwargs["input"]
        self.calls.append({"model": model, "input": list(texts)})
        data = [_FakeEmbedding(i, [float(i), 0.0]) for i in range(len(texts))]
        return _FakeResponse(list(reversed(data)))


class _FakeClient:
    def __init__(self) -> None:
        self.embeddings = _FakeEmbeds()


def test_api_embedder_preserves_input_order_despite_response_order():
    fake = _FakeClient()
    embedder = ApiEmbedder(model_name="acme/embed", client=fake)

    vectors = embedder.embed(["first", "second", "third"])

    assert vectors == [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]
    assert fake.embeddings.calls == [
        {"model": "acme/embed", "input": ["first", "second", "third"]}
    ]


def test_api_embedder_empty_input_skips_network_call():
    fake = _FakeClient()
    embedder = ApiEmbedder(model_name="acme/embed", client=fake)

    assert embedder.embed([]) == []
    assert fake.embeddings.calls == []


def test_api_embedder_works_inside_rag_engine():
    fake = _FakeClient()
    engine = rag_module.RAGEngine(embedder=ApiEmbedder(model_name="acme/embed", client=fake))

    engine.ingest(["apple fruit", "planet mars"], metadatas=[{"topic": "a"}, {"topic": "b"}])

    store = engine.store
    assert len(store._chunks) == 2
    assert store._vectors == [[0.0, 0.0], [1.0, 0.0]]


def test_make_embedder_api_provider_returns_api_embedder(monkeypatch):
    stub = SimpleNamespace(
        embedding_provider="api",
        embedding_model="intfloat/multilingual-e5-large",
        routerai_api_key="k",
        routerai_base_url="https://routerai.ru/api/v1",
    )
    monkeypatch.setattr(rag_module, "settings", stub)

    assert isinstance(rag_module.make_embedder(), ApiEmbedder)


def test_make_embedder_local_provider_default(monkeypatch):
    stub = SimpleNamespace(
        embedding_provider="local",
        embedding_model="intfloat/multilingual-e5-small",
        routerai_api_key="k",
        routerai_base_url="https://routerai.ru/api/v1",
    )
    monkeypatch.setattr(rag_module, "settings", stub)

    assert isinstance(rag_module.make_embedder(), rag_module.LocalEmbedder)
