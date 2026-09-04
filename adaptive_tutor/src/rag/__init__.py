"""RAG: эмбеддинги, векторное хранилище, retrieval."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from ..config import settings


@dataclass
class DocChunk:
    id: str
    text: str
    metadata: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class RetrievalResult:
    chunk: DocChunk
    score: float


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbedder:
    """Локальные эмбеддинги (sentence-transformers, e5-multilingual)."""
    _model = None

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or settings.embedding_model

    @classmethod
    def _get_model(cls):
        if cls._model is None:
            from sentence_transformers import SentenceTransformer
            cls._model = SentenceTransformer(settings.embedding_model)
        return cls._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        vecs = model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vecs]


class VectorStore(ABC):
    """Абстракция векторного хранилища."""

    @abstractmethod
    def upsert(
        self,
        chunks: list[DocChunk],
        vectors: list[list[float]] | None = None,
    ) -> None: ...

    @abstractmethod
    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievalResult]: ...


class InMemoryVectorStore(VectorStore):
    """In-memory fallback: для тестов и малых объёмов."""

    def __init__(self):
        self._chunks: list[DocChunk] = []
        self._vectors: list[list[float] | None] = []

    def upsert(
        self,
        chunks: list[DocChunk],
        vectors: list[list[float]] | None = None,
    ) -> None:
        """Добавляет чанки и их векторы.

        vectors опционален (для forward-compatibility): чанк без вектора
        сохраняется, но не участвует в семантическом ранжировании.
        """
        vectors = vectors or []
        for i, chunk in enumerate(chunks):
            self._chunks.append(chunk)
            self._vectors.append(vectors[i] if i < len(vectors) else None)

    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievalResult]:
        import numpy as np

        if not self._chunks:
            return []
        scored = []
        for chunk, vec in zip(self._chunks, self._vectors, strict=False):
            if vec is None:
                continue
            if filters and not self._match(chunk.metadata, filters):
                continue
            scored.append((float(np.dot(vector, vec)), chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            RetrievalResult(chunk=chunk, score=score)
            for score, chunk in scored[:top_k]
        ]

    def list_chunks(self, filters: dict[str, Any] | None = None) -> list[DocChunk]:
        """Все чанки хранилища, отфильтрованные по равенству метаданных.

        Фильтр — точное совпадение ``metadata[k] == v`` (как ``_match`` в
        ``search``); порядок вставки — свежие чанки последними.
        """
        if not filters:
            return list(self._chunks)
        return [chunk for chunk in self._chunks if self._match(chunk.metadata, filters)]

    @staticmethod
    def _match(meta: dict, filters: dict) -> bool:
        return all(meta.get(k) == v for k, v in filters.items())


def make_embedder() -> Embedder:
    if settings.embedding_provider == "api":
        # API-эмбеддинги (RouterAI/OpenRouter) — заглушка-протокол
        raise NotImplementedError("API-embedder ещё не подключён")
    return LocalEmbedder()


class RAGEngine:
    """Высокоуровневый движок: семантический запрос -> документы."""

    def __init__(
        self,
        store: VectorStore | None = None,
        embedder: Embedder | None = None,
    ):
        self.store = store or InMemoryVectorStore()
        self.embedder = embedder or make_embedder()
        self._next_id = 0

    def ingest(self, texts: list[str], metadatas: list[dict] | None = None) -> None:
        """Эмбеддит тексты и сохраняет чанки вместе с векторами в store."""
        metadatas = metadatas or [{} for _ in texts]
        vectors = self.embedder.embed(texts)
        chunks = [
            DocChunk(
                id=f"c{self._next_id + i}",
                text=text,
                metadata=metadata,
            )
            for i, (text, metadata) in enumerate(zip(texts, metadatas, strict=False))
        ]
        self._next_id += len(chunks)
        self.store.upsert(chunks, vectors)

    def search(
        self,
        query: str,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> list[RetrievalResult]:
        vec = self.embedder.embed([query])[0]
        return self.store.search(vec, top_k, filters)

    def list_chunks(self, filters: dict | None = None) -> list[DocChunk]:
        """Прокси на ``store.list_chunks``: все чанки с фильтром по метаданным."""
        return self.store.list_chunks(filters)