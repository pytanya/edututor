"""Тесты RAG: семантическое ранжирование, фильтры, выравнивание векторов."""

import math
import re
import sys

from src.rag import DocChunk, InMemoryVectorStore, LocalEmbedder, RAGEngine


class StubEmbedder:
    """Детерминированный фейк-эмбеддер: мешок слов -> нормированный вектор.

    Не грузит sentence-transformers и не ходит в сеть. Сходство текстов
    определяется пересечением токенов, поэтому ранжирование осмысленно.
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
            "red",
            "sky",
            "something",
            "about",
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


def test_rag_relevance_ranking_not_insertion_order():
    engine = RAGEngine(embedder=StubEmbedder())
    engine.ingest(
        ["apple fruit", "planet mars"],
        metadatas=[{"topic": "food"}, {"topic": "space"}],
    )
    results = engine.search("something about mars", top_k=5)

    assert len(results) == 2
    assert results[0].chunk.text == "planet mars"
    assert results[0].score > results[1].score


def test_rag_filters_exclude_nonmatching_chunks():
    engine = RAGEngine(embedder=StubEmbedder())
    engine.ingest(
        ["apple fruit", "planet mars"],
        metadatas=[{"topic": "food"}, {"topic": "space"}],
    )
    results = engine.search("something about mars", top_k=5, filters={"topic": "food"})

    assert [r.chunk.text for r in results] == ["apple fruit"]


def test_rag_alignment_after_multiple_ingests():
    engine = RAGEngine(embedder=StubEmbedder())
    engine.ingest(["apple fruit"], metadatas=[{"topic": "food"}])
    engine.ingest(["planet mars"], metadatas=[{"topic": "space"}])

    store = engine.store
    assert len(store._chunks) == 2
    assert len(store._vectors) == 2

    assert [c.id for c in store._chunks] == ["c0", "c1"]
    for _chunk, vec in zip(store._chunks, store._vectors, strict=True):
        assert vec is not None
        assert len(vec) > 0

    mars = engine.search("planet mars", top_k=1)
    assert mars[0].chunk.text == "planet mars"
    apple = engine.search("apple fruit", top_k=1)
    assert apple[0].chunk.text == "apple fruit"


def test_rag_score_direction_is_sensible():
    engine = RAGEngine(embedder=StubEmbedder())
    engine.ingest(["mars planet", "mars"], metadatas=[{}, {}])
    results = engine.search("mars", top_k=2)

    assert len(results) == 2
    assert results[0].chunk.text == "mars"
    assert results[1].chunk.text == "mars planet"
    assert results[0].score >= results[1].score


def test_rag_chunk_without_vector_is_skipped_not_first():
    store = InMemoryVectorStore()
    store.upsert([DocChunk(id="c0", text="apple fruit", metadata={})])
    engine = RAGEngine(store=store, embedder=StubEmbedder())

    assert engine.search("apple fruit", top_k=5) == []


def test_local_embedder_stays_lazy():
    before = "sentence_transformers" in sys.modules
    embedder = LocalEmbedder(model_name="unused/test-model")
    assert ("sentence_transformers" in sys.modules) == before
    RAGEngine(embedder=embedder)
    assert ("sentence_transformers" in sys.modules) == before
