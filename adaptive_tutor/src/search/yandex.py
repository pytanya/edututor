"""Поиск через Yandex Cloud Search API v2 (регион РФ).

Как в референсе project_work (`source_finder._search_yandex`): требуются и
API-ключ, и ``folderId`` каталога (TUTOR_YANDEX_API_KEY + TUTOR_YANDEX_FOLDER_ID).
Ответ приходит JSON с base64-XML в ``rawData`` — разбираем ``<doc>`` элементы.
"""

import base64
import re

import httpx

from .base import SearchEngine, SearchRateLimitError, SearchResult, SearchTimeoutError

_DEFAULT_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"
_HLWORD_RE = re.compile(r"</?hlword[^>]*>")


def _strip_hlword(text: str) -> str:
    """Убирает подсветку <hlword>…</hlword> из XML-фрагмента результата."""
    return _HLWORD_RE.sub("", text).strip()


def parse_raw_results(xml_text: str, max_results: int = 5) -> list[SearchResult]:
    """Разбирает XML (из base64 rawData) в список SearchResult."""
    results: list[SearchResult] = []
    for doc in re.findall(r"<doc[^>]*>(.*?)</doc>", xml_text, flags=re.S):
        m_url = re.search(r"<url>(.*?)</url>", doc, flags=re.S)
        m_title = re.search(r"<title>(.*?)</title>", doc, flags=re.S)
        m_snippet = re.search(r"<passage>(.*?)</passage>", doc, flags=re.S)
        url = _strip_hlword(m_url.group(1)) if m_url else ""
        title = _strip_hlword(m_title.group(1)) if m_title else ""
        snippet = _strip_hlword(m_snippet.group(1)) if m_snippet else ""
        if url:
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= max_results:
            break
    return results


class YandexSearch(SearchEngine):
    """Поиск через Yandex Cloud Search API v2 (регион РФ)."""

    def __init__(self, api_key: str, folder_id: str = "", base_url: str = ""):
        self.api_key = api_key
        self.folder_id = folder_id
        self.base_url = base_url or _DEFAULT_URL

    async def search(
        self,
        query: str,
        max_results: int = 5,
        language: str = "ru",
    ) -> list[SearchResult]:
        if not self.api_key or not self.folder_id:
            raise RuntimeError(
                "Yandex Search: не настроены TUTOR_YANDEX_API_KEY / TUTOR_YANDEX_FOLDER_ID"
            )
        payload = {
            "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query},
            "folderId": self.folder_id,
            "responseFormat": "FORMAT_XML",
        }
        headers = {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.base_url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            raise SearchTimeoutError(f"Yandex timeout: {e}") from e

        if response.status_code == 429:
            raise SearchRateLimitError("Yandex rate limit (429)")

        response.raise_for_status()
        data = response.json()
        raw_b64 = data.get("rawData", "")
        if not raw_b64:
            raise RuntimeError("Yandex Search вернул пустой rawData")
        try:
            xml_text = base64.b64decode(raw_b64).decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Yandex Search: не удалось декодировать rawData: {e}") from e

        results = parse_raw_results(xml_text, max_results)
        if not results:
            raise RuntimeError("Yandex Search не вернул результатов")
        return results
