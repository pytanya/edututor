import httpx
from tavily import AsyncTavilyClient

from .base import SearchEngine, SearchRateLimitError, SearchResult, SearchTimeoutError


class TavilySearch(SearchEngine):
    """Поиск через Tavily Search API (регион Global)."""
    
    def __init__(self, api_key: str):
        self.client = AsyncTavilyClient(api_key=api_key)
    
    async def search(
        self,
        query: str,
        max_results: int = 5,
        language: str = "en",
    ) -> list[SearchResult]:
        try:
            response = await self.client.search(
                query=query,
                max_results=max_results,
                search_depth="basic",
                include_answer=False,
            )
        except httpx.TimeoutException as e:
            raise SearchTimeoutError(f"Tavily timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise SearchRateLimitError(f"Tavily rate limit: {e}") from e
            raise
        
        results = []
        for item in response.get("results", []):
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                score=item.get("score", 0.0),
            ))
        
        return results
