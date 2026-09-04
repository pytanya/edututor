from collections.abc import AsyncIterator, Callable
from typing import Any

from openai import AsyncOpenAI

from .base import LLMClient, LLMResponse, TokenCounter, TokenUsage


class OpenRouterTokenCounter(TokenCounter):
    """Подсчет токенов для OpenRouter.
    
    OpenRouter возвращает usage в теле ответа; стоимость приходит в
    ``usage.cost`` (в credits, ≈USD). Токены нормализуются через
    безопасные get-цепочки из-за неполного ``usage`` на некоторых моделях.
    """

    currency = "usd"

    def parse_usage(self, raw_response: Any) -> TokenUsage:
        if isinstance(raw_response, dict):
            usage = raw_response.get("usage", {}) or {}
            prompt = usage.get("prompt_tokens", 0)
            completion = usage.get("completion_tokens", 0)
            total = usage.get("total_tokens", prompt + completion)
            cost_credits = float(usage.get("cost", 0.0) or 0.0)
        else:
            # OpenAI SDK object
            prompt = getattr(raw_response.usage, "prompt_tokens", 0)
            completion = getattr(raw_response.usage, "completion_tokens", 0)
            total = getattr(raw_response.usage, "total_tokens", prompt + completion)
            cost_credits = float(getattr(raw_response.usage, "cost", 0.0) or 0.0)

        return TokenUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cost_local=cost_credits,
            cost_usd=cost_credits,  # credits ≈ USD
        )

    def estimate(self, text: str) -> int:
        # OpenRouter использует нативный токенизатор модели;
        # здесь — приблизительная оценка для лимитов.
        return len(text) // 4


class OpenRouterClient(LLMClient):
    """Клиент для OpenRouter (регион Global)."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        token_counter: OpenRouterTokenCounter | None = None,
        timeout: float = 120.0,
    ):
        super().__init__(token_counter or OpenRouterTokenCounter())
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            default_headers={
                "HTTP-Referer": "https://adaptive-tutor.app",
                "X-Title": "Adaptive Tutor",
            },
        )

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