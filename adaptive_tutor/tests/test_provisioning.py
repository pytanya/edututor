"""Тесты провижининга базы знаний: build_queries и provision_topic."""

import math
import re

import pytest

from src.rag import RAGEngine
from src.rag.provisioning import build_queries, provision_topic
from src.search.base import SearchResult


class StubEmbedder:
    """Детерминированный фейк-эмбеддер: без сети и модели.

    Сходство текстов определяется пересечением токенов из словаря.
    """

    def __init__(self, vocab: list[str] | None = None):
        default_vocab = [
            "apple",
            "fruit",
            "juice",
            "planet",
            "mars",
            "space",
            "solar",
            "system",
            "earth",
            "formula",
            "roots",
            "algebra",
            "example",
            "red",
            "sky",
        ]
        self.vocab = vocab or default_vocab
        self._index = {w: i for i, w in enumerate(self.vocab)}

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for text in texts:
            tokens = set(re.findall(r"[a-zа-яё]+", text.lower()))
            vec = [0.0] * len(self.vocab)
            for token in tokens:
                idx = self._index.get(token)
                if idx is not None:
                    vec[idx] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            result.append([v / norm for v in vec])
        return result


class FakeSearch:
    """Асинхронная заглушка поиска с очередью ответов (списки или исключения)."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def __call__(self, query: str, max_results: int = 5, region=None):
        self.calls.append({"query": query, "max_results": max_results, "region": region})
        if not self._responses:
            return []
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _url(name: str) -> str:
    return f"https://example.com/{name}"


def _result(name: str, url: str = "") -> SearchResult:
    return SearchResult(title=name, url=url or _url(name), snippet=f"{name} planet mars")


def test_build_queries_order_and_dedupe():
    queries = build_queries("квадратные уравнения", subject="алгебра", grade="8 класс")

    assert queries == [
        "квадратные уравнения алгебра 8 класс",
        "квадратные уравнения алгебра",
        "квадратные уравнения",
    ]
    assert len(queries) == len(set(queries))


def test_build_queries_without_grade_keeps_two():
    assert build_queries("квадратные уравнения", subject="алгебра") == [
        "квадратные уравнения алгебра",
        "квадратные уравнения",
    ]


def test_build_queries_topic_only_is_single():
    assert build_queries("квадратные уравнения") == ["квадратные уравнения"]


@pytest.mark.asyncio
async def test_provision_ingests_results_with_metadata():
    engine = RAGEngine(embedder=StubEmbedder())
    results = [
        _result("Квадратные уравнения"),
        SearchResult(
            title="Примеры решения",
            url=_url("b"),
            snippet="apple fruit juice",
            score=0.5,
        ),
    ]
    fake = FakeSearch([results])
    count = await provision_topic(
        engine,
        "квадратные уравнения",
        subject="алгебра",
        grade="8 класс",
        search=fake,
    )

    assert count == 2
    assert len(engine.store._chunks) == 2
    chunk = engine.store._chunks[0]
    assert chunk.metadata["subject"] == "алгебра"
    assert chunk.metadata["grade"] == "8 класс"
    assert chunk.metadata["topic"] == "квадратные уравнения"
    assert chunk.metadata["source"] == _url("Квадратные уравнения")
    assert chunk.metadata["kind"] == "web"
    assert chunk.metadata["title"] == "Квадратные уравнения"
    assert "Квадратные уравнения" in chunk.text
    assert "planet mars" in chunk.text

    found = engine.search("planet mars", top_k=5, filters={"subject": "алгебра"})
    assert len(found) == 2

    assert fake.calls[0]["query"] == "квадратные уравнения алгебра 8 класс"
    assert fake.calls[0]["region"] is None


@pytest.mark.asyncio
async def test_provision_dedupes_by_url_across_queries():
    engine = RAGEngine(embedder=StubEmbedder())
    result = _result("Дубликат", url=_url("dup"))
    fake = FakeSearch([[result], [result]])

    count = await provision_topic(
        engine,
        "квадратные уравнения",
        subject="алгебра",
        grade="8 класс",
        search=fake,
    )

    assert count == 1
    assert len(engine.store._chunks) == 1
    assert len(fake.calls) >= 2


@pytest.mark.asyncio
async def test_provision_no_results_returns_zero_without_ingest():
    engine = RAGEngine(embedder=StubEmbedder())
    count = await provision_topic(engine, "квадратные уравнения", search=FakeSearch([]))

    assert count == 0
    assert engine.store._chunks == []

    empty = SearchResult(title="   ", url="", snippet="")
    engine2 = RAGEngine(embedder=StubEmbedder())
    count2 = await provision_topic(engine2, "тема", search=FakeSearch([[empty]]))

    assert count2 == 0
    assert engine2.store._chunks == []


@pytest.mark.asyncio
async def test_provision_continues_after_query_exception():
    engine = RAGEngine(embedder=StubEmbedder())
    result = _result("Выживший")
    fake = FakeSearch([RuntimeError("поиск упал"), [result]])

    count = await provision_topic(
        engine,
        "квадратные уравнения",
        subject="алгебра",
        grade="8 класс",
        search=fake,
    )

    assert count == 1
    assert len(engine.store._chunks) == 1


@pytest.mark.asyncio
async def test_provision_respects_max_results():
    engine = RAGEngine(embedder=StubEmbedder())
    many = [_result(f"Результат {i}") for i in range(10)]
    fake = FakeSearch([many])

    count = await provision_topic(engine, "тема", max_results=3, search=fake)

    assert count == 3
    assert len(engine.store._chunks) == 3
    assert fake.calls[0]["max_results"] == 3
