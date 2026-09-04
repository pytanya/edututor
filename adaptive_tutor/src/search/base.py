from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config import Region, settings


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    score: float = 0.0


class SearchTimeoutError(Exception):
    """Таймаут запроса к поисковому движку."""


class SearchRateLimitError(Exception):
    """Превышен лимит запросов (HTTP 429)."""


class SearchEngine(ABC):
    """Абстрактный базовый класс для поисковых движков."""
    
    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 5,
        language: str = "ru",
    ) -> list[SearchResult]:
        pass


class SearchRouter:
    """Роутер поисковых запросов с учетом региона и fallback."""
    
    _engines: dict[Region, list[SearchEngine]] = {}
    
    @classmethod
    def get_engines(cls, region: Region | None = None) -> list[SearchEngine]:
        if region is None:
            region = settings.region
        
        if region not in cls._engines:
            cls._engines[region] = cls._create_engines(region)
        
        return cls._engines[region]
    
    @classmethod
    def _create_engines(cls, region: Region) -> list[SearchEngine]:
        engines = []
        
        if region == Region.RU:
            # Yandex Cloud Search API v2 как основной (нужны key + folderId)
            if settings.yandex_api_key and settings.yandex_folder_id:
                from .yandex import YandexSearch
                engines.append(
                    YandexSearch(
                        api_key=settings.yandex_api_key,
                        folder_id=settings.yandex_folder_id,
                        base_url=settings.yandex_search_url,
                    )
                )
        else:
            # Tavily как основной
            if settings.tavily_api_key:
                from .tavily import TavilySearch
                engines.append(TavilySearch(api_key=settings.tavily_api_key))
        
        # DuckDuckGo как fallback
        if settings.ddgs_enabled:
            from .duckduckgo import DuckDuckGoSearch
            engines.append(DuckDuckGoSearch())
        
        return engines
    
    @classmethod
    async def search(
        cls,
        query: str,
        max_results: int = 5,
        region: Region | None = None,
    ) -> list[SearchResult]:
        engines = cls.get_engines(region)
        
        for engine in engines:
            try:
                results = await engine.search(query, max_results)
                if results:
                    return results
            except SearchTimeoutError as e:
                print(f"[fallback] {engine.__class__.__name__} таймаут: {e}")
                continue
            except SearchRateLimitError as e:
                print(f"[fallback] {engine.__class__.__name__} rate limit: {e}")
                continue
            except Exception as e:
                print(f"[fallback] {engine.__class__.__name__} ошибка: {e}")
                continue
        
        return []
