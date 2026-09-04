"""Тесты Yandex Cloud Search API v2: парсинг rawData (base64-XML) и движок."""

import base64
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.search.base import SearchRateLimitError, SearchTimeoutError
from src.search.yandex import YandexSearch, parse_raw_results

_XML = """<?xml version="1.0"?>
<yandexsearch><response><results><grouping>
  <group>
    <doc>
      <url>https://example.com/a</url>
      <title>Броуновское <hlword>движение</hlword></title>
      <passage>хаотическое <hlword>движение</hlword> частиц</passage>
    </doc>
  </group>
  <group>
    <doc>
      <url>https://example.com/b</url>
      <title>Пример</title>
      <passage>текст без подсветки</passage>
    </doc>
  </group>
</grouping></results></response></yandexsearch>"""


def test_parse_raw_results_extracts_docs_and_strips_hlword():
    """Парсинг XML: извлекаются doc, убирается разметка <hlword>."""
    results = parse_raw_results(_XML, max_results=5)
    assert len(results) == 2
    assert results[0].url == "https://example.com/a"
    assert results[0].title == "Броуновское движение"
    assert results[0].snippet == "хаотическое движение частиц"
    assert results[1].title == "Пример"


def test_parse_raw_results_respects_limit():
    assert len(parse_raw_results(_XML, max_results=1)) == 1


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, *args, **kwargs):
        return self._response


async def test_yandex_search_v2_success():
    engine = YandexSearch(api_key="k", folder_id="folder-1")
    xml_b64 = base64.b64encode(_XML.encode("utf-8")).decode("ascii")
    fake = _FakeAsyncClient(_FakeResponse(200, {"rawData": xml_b64}))

    with patch("httpx.AsyncClient", return_value=fake):
        results = await engine.search("броуновское движение", max_results=2)

    assert len(results) == 2
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "хаотическое движение частиц"


async def test_yandex_search_requires_folder_id():
    engine = YandexSearch(api_key="k", folder_id="")
    with pytest.raises(RuntimeError, match="FOLDER_ID"):
        await engine.search("тест")


async def test_yandex_search_rate_limit_raises_specific_error():
    engine = YandexSearch(api_key="k", folder_id="folder-1")
    fake = _FakeAsyncClient(_FakeResponse(429, {}))
    with patch("httpx.AsyncClient", return_value=fake), pytest.raises(SearchRateLimitError):
        await engine.search("тест")


async def test_yandex_search_timeout_raises_specific_error():
    engine = YandexSearch(api_key="k", folder_id="folder-1")
    fake = _FakeAsyncClient(_FakeResponse(200, {}))
    fake.post = AsyncMock(side_effect=httpx.TimeoutException("slow"))
    with patch("httpx.AsyncClient", return_value=fake), pytest.raises(SearchTimeoutError):
        await engine.search("тест")
