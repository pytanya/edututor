from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from ..config import Region, settings

# Фолбек-модели по умолчанию на регион: роль -> упорядоченный список моделей,
# которые пробуются, если основная модель роли вернула пусто/недоступна.
_REGION_FALLBACKS: dict[Region, dict[str, list[str]]] = {
    Region.RU: {
        # Основной RU planner — qwen3.7-flash (как в референсе; instruct, без
        # reasoning). Если он недоступен/пуст — deepseek-chat, последней — qwen-2.5.
        "planner": ["deepseek/deepseek-chat", "qwen/qwen-2.5-7b-instruct"],
        "judge": ["deepseek/deepseek-chat"],
    },
    Region.GLOBAL: {
        "planner": ["google/gemini-2.5-flash"],
        "judge": ["google/gemini-2.5-flash"],
    },
}


@dataclass
class TokenUsage:
    """Нормализованные сведения о подсчете токенов.
    
    Провайдеры (OpenRouter, RouterAI) возвращают токены и стоимость
    в различающихся полях JSON-ответа. Каждый TokenCounter отвечает
    за извлечение и нормализацию этих полей для своего провайдера.
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    cost_local: float = 0.0  # RUB для RouterAI, credits для OpenRouter

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "cost_local": self.cost_local,
        }


class TokenCounter(ABC):
    """Стратегия подсчета токенов, специфичная для провайдера.
    
    Извлекает usage-поля из raw-ответа провайдера, т.к. их расположение
    в JSON отличается у OpenRouter и RouterAI.
    """

    currency: str = "usd"

    @abstractmethod
    def parse_usage(self, raw_response: Any) -> TokenUsage:
        """Нормализует usage из raw-ответа провайдера в TokenUsage."""
        pass

    @abstractmethod
    def estimate(self, text: str) -> int:
        """Приблизительный подсчет токенов для текста (для лимитов/заглушек)."""
        pass


def _split_csv(value: str) -> list[str]:
    """Разбивает строку «м1, м2» на список; пустая строка -> []."""
    return [item.strip() for item in value.split(",") if item.strip()]


class LLMResponse:
    """Нормализованный ответ LLM-провайдера."""

    def __init__(
        self,
        content: str,
        model: str,
        usage: TokenUsage,
        finish_reason: str,
        tool_calls: list | None = None,
    ):
        self.content = content
        self.model = model
        self.usage = usage
        self.finish_reason = finish_reason
        self.tool_calls = tool_calls or []

    @property
    def prompt_tokens(self) -> int:
        return self.usage.prompt_tokens

    @property
    def completion_tokens(self) -> int:
        return self.usage.completion_tokens

    @property
    def total_tokens(self) -> int:
        return self.usage.total_tokens

    @property
    def cost_usd(self) -> float:
        return self.usage.cost_usd


class LLMClient(ABC):
    """Абстрактный базовый класс для LLM-клиентов."""

    def __init__(self, token_counter: TokenCounter):
        self.token_counter = token_counter

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        pass

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        on_chunk: Callable | None = None,
    ) -> AsyncIterator[str]:
        pass

    def count_tokens(self, text: str) -> int:
        """Подсчет токенов делегируется провайдер-специфичному TokenCounter."""
        return self.token_counter.estimate(text)


class LLMClientFactory:
    """Фабрика LLM-клиентов с каскадом провайдеров/моделей.

    ``get_client`` возвращает ``ResilientLLMClient``: первичный провайдер региона
    (RU -> RouterAI, GLOBAL -> OpenRouter), а при его недоступности — второй
    шлюз; внутри каждой пары провайдер/модель — retry с backoff и фолбек на
    запасные модели (см. ``cascade.ResilientLLMClient``). Экземпляры безопасно
    кешируются и переиспользуются между сессиями.
    """

    _clients: dict[Region, LLMClient] = {}
    _executors: dict[str, LLMClient] = {}

    @classmethod
    def get_client(cls, region: Region | None = None) -> LLMClient:
        if region is None:
            region = settings.region

        if region not in cls._clients:
            cls._clients[region] = cls._build_cascade(region)
        return cls._clients[region]

    @classmethod
    def _executor(cls, name: str) -> LLMClient:
        """Кешированный провайдер-клиент (RouterAI или OpenRouter)."""
        cached = cls._executors.get(name)
        if cached is not None:
            return cached
        if name == "routerai":
            from .router_ai import RouterAIClient, RouterAITokenCounter

            instance = RouterAIClient(
                api_key=settings.routerai_api_key,
                base_url=settings.routerai_base_url,
                token_counter=RouterAITokenCounter(),
                timeout=settings.llm_timeout_sec,
            )
        elif name == "openrouter":
            from .openrouter import OpenRouterClient, OpenRouterTokenCounter

            instance = OpenRouterClient(
                api_key=settings.openrouter_api_key,
                base_url=settings.openrouter_base_url,
                token_counter=OpenRouterTokenCounter(),
                timeout=settings.llm_timeout_sec,
            )
        else:
            raise ValueError(f"Неизвестный провайдер: {name!r}")
        cls._executors[name] = instance
        return instance

    @classmethod
    def _provider_chain(cls, region: Region) -> list[str]:
        """Порядок провайдеров: первичный по региону -> второй шлюз (если ключ есть)."""
        default_primary = "routerai" if region == Region.RU else "openrouter"
        primary = (settings.llm_primary_provider or "").strip().lower() or default_primary
        secondary = "openrouter" if primary == "routerai" else "routerai"
        chain: list[str] = []
        for name in (primary, secondary):
            has_key = (
                bool(settings.routerai_api_key)
                if name == "routerai"
                else bool(settings.openrouter_api_key)
            )
            if has_key:
                chain.append(name)
        return chain

    @classmethod
    def _build_cascade(cls, region: Region) -> LLMClient:
        from .cascade import ResilientLLMClient

        providers = [(name, cls._executor(name)) for name in cls._provider_chain(region)]
        role_models = cls.get_models_for_region(region)
        fallbacks = cls.get_fallback_models_for_region(region)
        model_fallbacks: dict[str, list[str]] = {}
        for role, primary in role_models.items():
            candidates = fallbacks.get(role)
            if candidates and primary:
                model_fallbacks[primary] = candidates
        return ResilientLLMClient(
            providers=providers,
            model_fallbacks=model_fallbacks,
            retries=settings.llm_retries,
            retry_empty=settings.llm_retry_empty,
        )

    @classmethod
    def get_models_for_region(cls, region: Region) -> dict[str, str]:
        """Возвращает доступные модели для региона.

        Основная «разговорная» модель (planner) для RU — qwen3.7-flash (как в
        референсе project_work: instruct, без reasoning-пустых ответов); для
        GLOBAL — claude-sonnet-4. deepseek-v4-flash-0731 можно включить через
        TUTOR_LLM_PLANNER_MODEL, но это reasoning-модель: при тесном max_tokens
        съедает бюджет на «размышления» и возвращает пустой content.
        Переопределяется через TUTOR_LLM_PLANNER_MODEL/_FAST/_JUDGE.
        """
        if region == Region.RU:
            models = {
                "planner": "qwen/qwen3.7-flash",
                "fast": "qwen/qwen-2.5-7b-instruct",
                "judge": "google/gemini-2.5-flash",
            }
        else:
            models = {
                "planner": "anthropic/claude-sonnet-4",
                "fast": "google/gemini-2.5-flash",
                "judge": "openai/gpt-4.1-mini",
            }
        for role, override in (
            ("planner", settings.llm_planner_model),
            ("fast", settings.llm_fast_model),
            ("judge", settings.llm_judge_model),
        ):
            value = (override or "").strip()
            if value:
                models[role] = value
        return models

    @classmethod
    def get_fallback_models_for_region(cls, region: Region) -> dict[str, list[str]]:
        """Фолбек-модели для ролей региона (env TUTOR_LLM_*_FALLBACK_MODELS имеют приоритет)."""
        defaults = _REGION_FALLBACKS[region]
        planner_override = _split_csv(settings.llm_fallback_models)
        judge_override = _split_csv(settings.llm_judge_fallback_models)
        return {
            "planner": planner_override or defaults.get("planner", []),
            "judge": judge_override or defaults.get("judge", []),
        }
