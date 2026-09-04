"""Провижининг базы знаний: веб-поиск по теме и индексация сниппетов в RAG.

По запросу студента (тема/предмет/класс) модуль строит поисковые запросы,
выполняет их через SearchRouter (или переданную заглушку) и сохраняет
результаты в векторное хранилище с метаданными subject/grade/topic/source,
чтобы последующие rag_search-запросы возвращали реальный контент.
"""

from collections.abc import Awaitable, Callable

from ..config import Region
from ..rag import RAGEngine
from ..search.base import SearchResult

SearchFn = Callable[..., Awaitable[list[SearchResult]]]

_MAX_TEXT_LEN = 800


def build_queries(topic: str, subject: str = "", grade: str = "") -> list[str]:
    """Собирает 1-3 поисковых запроса: от полного сочетания к самой теме.

    Первый запрос — тема вместе с предметом и классом, затем менее
    специфичные варианты. Дубликаты отбрасываются с сохранением порядка.
    """
    topic = topic.strip()
    q1 = " ".join(part for part in (topic, subject, grade) if part.strip())
    queries: list[str] = []
    if q1:
        queries.append(q1)
    if grade.strip():
        q2 = " ".join(part for part in (topic, subject) if part.strip())
        if q2 and q2 not in queries:
            queries.append(q2)
    if topic and topic not in queries:
        queries.append(topic)
    return queries


async def provision_topic(
    engine: RAGEngine,
    topic: str,
    subject: str = "",
    grade: str = "",
    region: Region | None = None,
    max_results: int = 8,
    search: SearchFn | None = None,
) -> int:
    """Ищет материалы по теме и индексирует их в engine.

    Возвращает число сохранённых чанков. Сбой одного поискового запроса
    не прерывает процесс (переход к следующему), полный провал даёт 0.
    """
    if search is None:
        from ..search.base import SearchRouter

        search = SearchRouter.search

    docs: dict[str, tuple[str, dict]] = {}
    for query in build_queries(topic, subject, grade):
        if len(docs) >= max_results:
            break
        try:
            remaining = max_results - len(docs)
            results = await search(query, max_results=remaining, region=region)
        except Exception:
            continue
        for result in results or []:
            if len(docs) >= max_results:
                break
            title = (result.title or "").strip()
            snippet = (result.snippet or "").strip()
            url = (result.url or "").strip()
            if not (title or snippet):
                continue
            key = url or f"__no_url_{len(docs)}__"
            if key in docs:
                continue
            text = (title + "\n" + snippet).strip()[:_MAX_TEXT_LEN]
            docs[key] = (
                text,
                {
                    "subject": subject,
                    "grade": grade,
                    "topic": topic,
                    "source": url,
                    "kind": "web",
                    "title": title,
                },
            )

    if not docs:
        return 0

    texts = [pair[0] for pair in docs.values()]
    metadatas = [pair[1] for pair in docs.values()]
    engine.ingest(texts=texts, metadatas=metadatas)
    return len(docs)
