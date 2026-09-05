import pytest

from src.config import Region, Settings, settings
from src.llm.base import LLMClientFactory
from src.search.base import SearchRouter


def test_settings_defaults():
    # Без .env (реальный env может задавать RU) проверяем дефолты.
    s = Settings(_env_file=None)
    assert s.region == Region.GLOBAL
    assert s.max_agent_steps == 6


@pytest.fixture
def fake_keys(monkeypatch):
    """Заменяем API-ключи заглушками, чтобы фабрика могла конструировать клиентов."""
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-test-openrouter")
    monkeypatch.setattr(settings, "routerai_api_key", "sk-test-routerai")
    monkeypatch.setattr(settings, "yandex_api_key", "sk-test-yandex")
    monkeypatch.setattr(settings, "tavily_api_key", "tvly-test-tavily")
    yield


def test_factory_creates_openrouter_primary_for_global(fake_keys):
    client = LLMClientFactory.get_client(Region.GLOBAL)
    assert client.__class__.__name__ == "ResilientLLMClient"
    names = [name for name, _ in client.providers]
    # GLOBAL: первичный провайдер — OpenRouter, плюс фолбек RouterAI
    assert names[0] == "openrouter"
    assert "routerai" in names
    executor = dict(client.providers)["openrouter"]
    # у Global-клиента правильный токен-счётчик (USD/credits)
    assert executor.token_counter.currency == "usd"


def test_factory_creates_routerai_primary_for_ru(fake_keys):
    client = LLMClientFactory.get_client(Region.RU)
    assert client.__class__.__name__ == "ResilientLLMClient"
    names = [name for name, _ in client.providers]
    # RU: первичный провайдер — RouterAI, плюс фолбек OpenRouter
    assert names[0] == "routerai"
    assert "openrouter" in names
    executor = dict(client.providers)["routerai"]
    # у RouterAI токен-счётчик работает в рублях
    assert executor.token_counter.currency == "rub"


def test_runtime_models_region_defaults_use_instruct_planner(fake_keys):
    """Основная модель RU — qwen3.7-flash (как в референсе, instruct)."""
    ru = LLMClientFactory.get_models_for_region(Region.RU)
    assert ru["planner"] == "qwen/qwen3.7-flash"
    assert ru["judge"] == "google/gemini-2.5-flash"
    # deepseek-chat остаётся доступен как фолбек-кандидат (instruct)
    ru_fb = LLMClientFactory.get_fallback_models_for_region(Region.RU)["planner"]
    assert "deepseek/deepseek-chat" in ru_fb


def test_search_router_has_fallback(fake_keys):
    engines = SearchRouter.get_engines(Region.GLOBAL)
    engine_names = [e.__class__.__name__ for e in engines]
    assert "DuckDuckGoSearch" in engine_names


def test_search_router_has_tavily_primary(fake_keys):
    engines = SearchRouter.get_engines(Region.GLOBAL)
    engine_names = [e.__class__.__name__ for e in engines]
    assert engine_names[0] in ("TavilySearch", "DuckDuckGoSearch")