from unittest.mock import patch

from src.search.base import (
    SearchEngine,
    SearchRateLimitError,
    SearchResult,
    SearchRouter,
    SearchTimeoutError,
)


class FakeSearchEngine(SearchEngine):
    """Заглушка поискового движка с предустановленными результатами или ошибками."""

    def __init__(
        self,
        results: list[SearchResult] | None = None,
        exc: Exception | None = None,
    ):
        self._results = results or []
        self._exc = exc
        self.called = False

    async def search(
        self,
        query: str,
        max_results: int = 5,
        language: str = "ru",
    ) -> list[SearchResult]:
        self.called = True
        if self._exc:
            raise self._exc
        return self._results


async def test_timeout_triggers_fallback():
    primary = FakeSearchEngine(exc=SearchTimeoutError("timeout"))
    fallback = FakeSearchEngine(results=[SearchResult(title="ok", url="u", snippet="s")])

    with patch.object(SearchRouter, "get_engines", return_value=[primary, fallback]):
        results = await SearchRouter.search("test")

    assert len(results) == 1
    assert results[0].title == "ok"
    assert primary.called
    assert fallback.called


async def test_rate_limit_triggers_fallback():
    primary = FakeSearchEngine(exc=SearchRateLimitError("429"))
    fallback = FakeSearchEngine(results=[SearchResult(title="ok", url="u", snippet="s")])

    with patch.object(SearchRouter, "get_engines", return_value=[primary, fallback]):
        results = await SearchRouter.search("test")

    assert len(results) == 1
    assert primary.called
    assert fallback.called


async def test_generic_exception_triggers_fallback():
    primary = FakeSearchEngine(exc=RuntimeError("boom"))
    fallback = FakeSearchEngine(results=[SearchResult(title="ok", url="u", snippet="s")])

    with patch.object(SearchRouter, "get_engines", return_value=[primary, fallback]):
        results = await SearchRouter.search("test")

    assert len(results) == 1
    assert primary.called
    assert fallback.called


async def test_all_engines_fail_returns_empty():
    e1 = FakeSearchEngine(exc=SearchTimeoutError("t"))
    e2 = FakeSearchEngine(exc=SearchRateLimitError("429"))

    with patch.object(SearchRouter, "get_engines", return_value=[e1, e2]):
        results = await SearchRouter.search("test")

    assert results == []


async def test_primary_succeeds_no_fallback():
    primary = FakeSearchEngine(results=[SearchResult(title="ok", url="u", snippet="s")])
    fallback = FakeSearchEngine(results=[SearchResult(title="unused", url="u", snippet="s")])

    with patch.object(SearchRouter, "get_engines", return_value=[primary, fallback]):
        results = await SearchRouter.search("test")

    assert len(results) == 1
    assert results[0].title == "ok"
    assert primary.called
    assert not fallback.called
