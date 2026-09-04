from collections.abc import AsyncIterator, Callable
from typing import Any

from openai import AsyncOpenAI

from .base import LLMClient, LLMResponse, TokenCounter, TokenUsage


class RouterAITokenCounter(TokenCounter):
    """Подсчет токенов для RouterAI.
    
    RouterAI возвращает usage в теле ответа; стоимость приходит в
    ``usage.cost`` (в рублях). Поля токенов могут находиться в
    под-объекте, поэтому извлекаются через безопасные get-цепочки.
    """

    currency = "rub"

    def parse_usage(self, raw_response: Any) -> TokenUsage:
        # raw_response может быть dict (json()) или объектом OpenAI SDK.
        if isinstance(raw_response, dict):
            usage = raw_response.get("usage", {}) or {}
            prompt = usage.get("prompt_tokens", 0)
            completion = usage.get("completion_tokens", 0)
            total = usage.get("total_tokens", prompt + completion)
            cost_local = float(usage.get("cost", 0.0) or 0.0)
        else:
            # OpenAI SDK object
            prompt = getattr(raw_response.usage, "prompt_tokens", 0)
            completion = getattr(raw_response.usage, "completion_tokens", 0)
            total = getattr(raw_response.usage, "total_tokens", prompt + completion)
            cost_local = float(getattr(raw_response.usage, "cost", 0.0) or 0.0)

        exchange_rate = 90.0  # RUB -> USD (примерный курс)
        return TokenUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cost_local=cost_local,
            cost_usd=round(cost_local / exchange_rate, 6),
        )

    def estimate(self, text: str) -> int:
        # Приблизительный подсчет: 1 токен ≈ 4 символа для русского текста
        return len(text) // 4


class RouterAIClient(LLMClient):
    """Клиент для RouterAI (регион РФ)."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        token_counter: RouterAITokenCounter | None = None,
        timeout: float = 120.0,
    ):
        super().__init__(token_counter or RouterAITokenCounter())
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def _extract_content_and_calls(self, choice: Any):
        message = choice.message
        content = message.content or ""
        tool_calls = []
        if message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        return content, tool_calls

    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        response = await self.client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        content, tool_calls = self._extract_content_and_calls(choice)
        usage = self.token_counter.parse_usage(response)

        return LLMResponse(
            content=content,
            model=response.model,
            usage=usage,
            finish_reason=choice.finish_reason,
            tool_calls=tool_calls,
        )

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        on_chunk: Callable | None = None,
    ) -> AsyncIterator[str]:
        stream = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                if on_chunk:
                    on_chunk(content)
                yield content