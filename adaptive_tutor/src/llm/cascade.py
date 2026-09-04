"""Каскадный LLM-клиент: провайдеры -> фолбек-модели -> retry.

Повторяет схему из референса project_work (`src/llm_client.py`):
- каскад по ПРОВАЙДЕРАМ (RouterAI -> OpenRouter и обратно) в порядке приоритета;
- внутри каждого провайдера — по МОДЕЛЯМ: запрошенная модель + фолбек-модели;
- retry с экспоненциальным backoff для временных ошибок (429/5xx/сеть/таймаут);
- пустой ответ (content == "" и нет tool_calls) считается ошибкой хода и
  переводит на следующего кандидата (reasoning-модели иногда возвращают пусто).

`chat()` пробует кандидатов по очереди и возвращает первый успешный ответ
(LLMResponse). Если все провайдеры и модели недоступны — поднимает RuntimeError,
который перехватывается вызывающим слоем (план/критик).
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import openai

from .base import LLMClient, LLMResponse, TokenCounter, TokenUsage

logger = logging.getLogger("adaptive_tutor.llm.cascade")

# Статусы, на которых имеет смысл retry с backoff (всё остальное — сразу на
# следующего кандидата: модель не найдена, нет прав, невалидный запрос и т.п.).
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
BACKOFF_BASE_SEC = 1.0


def _is_empty_response(resp: LLMResponse) -> bool:
    """Пустой ответ без вызова инструмента — кандидат не отработал."""
    return not (resp.content or "").strip() and not resp.tool_calls


class ResilientLLMClient(LLMClient):
    """LLMClient с провайдерским каскадом, фолбек-моделями и retry.

    providers — упорядоченный список кортежей ``(имя, LLMClient)`` (первичный
    провайдер первым). model_fallbacks — карта ``{модель: [фолбек-модели]}``,
    задающая порядок моделей для каждой основной модели роли.
    """

    def __init__(
        self,
        providers: list[tuple[str, LLMClient]],
        model_fallbacks: dict[str, list[str]] | None = None,
        token_counter: TokenCounter | None = None,
        retries: int = 2,
        backoff_base: float = BACKOFF_BASE_SEC,
        retry_empty: bool = True,
    ):
        self.providers: list[tuple[str, LLMClient]] = list(providers)
        if not self.providers:
            raise RuntimeError(
                "Нет ни одного настроенного LLM-провайдера. "
                "Укажите TUTOR_ROUTERAI_API_KEY и/или TUTOR_OPENROUTER_API_KEY в .env"
            )
        self.model_fallbacks = model_fallbacks or {}
        self.retries = max(0, int(retries))
        self.backoff_base = float(backoff_base)
        self.retry_empty = bool(retry_empty)
        # count_tokens() используется только как приближение; основная часть —
        # на стороне конкретного провайдера (executor).
        super().__init__(token_counter or _NullTokenCounter())

    # --- построение кандидатов -------------------------------------------

    def _candidate_models(self, model: str) -> list[str]:
        """Запрошенная модель + фолбек-модели (без дублей, порядок сохранён)."""
        fallbacks = self.model_fallbacks.get(model, [])
        seen: set[str] = set()
        ordered: list[str] = []
        for m in [model, *fallbacks]:
            if m and m not in seen:
                seen.add(m)
                ordered.append(m)
        return ordered

    # --- один вызов с retry ----------------------------------------------

    async def _call_with_retry(
        self,
        executor: LLMClient,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        tools: list[dict] | None,
        tool_choice: str | None,
    ) -> LLMResponse:
        """Запрос к одному провайдеру/модели с retry на временные ошибки."""
        last_error: BaseException | None = None
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return await executor.chat(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                )
            except openai.APIStatusError as exc:  # включает RateLimitError (429)
                if exc.status_code not in RETRYABLE_STATUS_CODES:
                    raise  # переходим к следующему кандидату без retry
                last_error = exc
            except (
                openai.APIConnectionError,
                openai.APITimeoutError,
                TimeoutError,
                ConnectionError,
            ) as exc:
                last_error = exc
            if attempt < attempts:
                delay = self.backoff_base * (2 ** (attempt - 1))
                logger.warning(
                    "LLM-каскад: попытка %d/%d для модели %s не удалась (%s); backoff %.1fс",
                    attempt,
                    attempts,
                    model,
                    _describe(last_error),
                    delay,
                )
                await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error

    # --- публичный API ----------------------------------------------------

    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        errors: list[str] = []
        for provider_name, executor in self.providers:
            for candidate in self._candidate_models(model):
                started = time.time()
                try:
                    resp = await self._call_with_retry(
                        executor, candidate, messages, temperature, max_tokens, tools, tool_choice
                    )
                except openai.APIStatusError as exc:
                    errors.append(f"{provider_name}/{candidate}: {_describe(exc)}")
                    logger.warning(
                        "LLM-каскад: %s недоступен (%s), пробую следующего кандидата.",
                        candidate,
                        _describe(exc),
                    )
                    continue
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{provider_name}/{candidate}: {_describe(exc)}")
                    logger.warning(
                        "LLM-каскад: %s не отработал (%s), пробую следующего кандидата.",
                        candidate,
                        _describe(exc),
                    )
                    continue
                if self.retry_empty and _is_empty_response(resp):
                    errors.append(f"{provider_name}/{candidate}: пустой ответ")
                    logger.warning(
                        "LLM-каскад: %s вернул пустой ответ, пробую следующего кандидата.",
                        candidate,
                    )
                    continue
                logger.info(
                    "LLM-каскад: ответ от %s/%s (%.1fс)",
                    provider_name,
                    candidate,
                    time.time() - started,
                )
                return resp
        raise RuntimeError("Все LLM-провайдеры и модели недоступны: " + "; ".join(errors))

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        on_chunk: Callable | None = None,
    ) -> AsyncIterator[str]:
        """Стриминг с каскадом: кандидат, первым отдавший контент, выигрывает."""
        for provider_name, executor in self.providers:
            for candidate in self._candidate_models(model):
                parts: list[str] = []
                try:
                    async for chunk in executor.chat_stream(
                        messages=messages,
                        model=candidate,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        on_chunk=on_chunk,
                    ):
                        parts.append(chunk)
                        yield chunk
                    if not parts:
                        continue
                    return
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "LLM-каскад: стрим %s/%s не удался (%s)",
                        provider_name,
                        candidate,
                        _describe(exc),
                    )
                    continue
        # Стрим полностью недоступен — просто завершаем генератор.
        return

    async def close(self) -> None:
        for _, executor in self.providers:
            closer = getattr(executor, "close", None)
            if closer is not None:
                await closer()


def _describe(error: BaseException | None) -> str:
    if error is None:
        return "неизвестная ошибка"
    status = getattr(getattr(error, "response", None), "status_code", None)
    if status is not None:
        return f"HTTP {status}: {error}"
    return str(error)


class _NullTokenCounter(TokenCounter):
    """Заглушка для count_tokens(): реальный подсчёт ведёт провайдер-executor."""

    currency = "usd"

    def parse_usage(self, raw_response: Any) -> TokenUsage:
        return TokenUsage()

    def estimate(self, text: str) -> int:
        return len(text) // 4
