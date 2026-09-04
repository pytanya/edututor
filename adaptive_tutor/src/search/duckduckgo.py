from ddgs import DDGS

from .base import SearchEngine, SearchResult


class DuckDuckGoSearch(SearchEngine):
    """Поиск через DuckDuckGo (fallback)."""
    
    async def search(
        self,
        query: str,
        max_results: int = 5,
        language: str = "ru",
    ) -> list[SearchResult]:
        # DDGS не поддерживает async, используем sync в отдельном потоке
        import asyncio
        
        def _sync_search():
            ddgs = DDGS()
            results = ddgs.text(query, max_results=max_results)
            return results
        
        loop = asyncio.get_event_loop()
        raw_results = await loop.run_in_executor(None, _sync_search)
        
        results = []
        for item in raw_results:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("href", ""),
                snippet=item.get("body", ""),
            ))
        
        return results
