"""Тесты каскадного LLM-клиента: retry, фолбек по моделям и провайдерам."""

from collections import deque

import httpx
import openai
import pytest

from src.llm.base import LLMResponse, TokenUsage
from src.llm.cascade import ResilientLLMClient


def _response(content: str, model: str, tool_calls: list | None = None) -> LLMResponse:
    return LLMResponse(
        content=content,
        model=model,
        usage=TokenUsage(),
        finish_reason="stop",
        tool_calls=tool_calls,
    )


class FakeExecutor:
    """Заглушка LLMClient: выдаёт предзаданные исходы по очереди."""

    def __init__(self, name: str, outcomes: list):
        self.name = name
        self.outcomes = deque(outcomes)
        self.calls: list[str] = []

    async def chat(
        self,
        messages,
        model,
        temperature=0.7,
        max_tokens=1024,
        tools=None,
        tool_choice=None,
    ):
        self.calls.append(model)
        outcome = (
            self.outcomes.popleft()
            if self.outcomes
            else RuntimeError(f"{self.name}: больше исходов нет")
        )
        if isinstance(outcome, Exception):
            raise outcome
        if outcome == "empty":
            return _response("", model)
        return _response(f"{self.name}:{model}", model)


def _not_found() -> openai.APIStatusError:
    request = httpx.Request("POST", "https://routerai.test/v1/chat/completions")
    response = httpx.Response(404, request=request)
    return openai.APIStatusError("model not found", response=response, body=None)


@pytest.mark.asyncio
async def test_retries_transient_then_success():
    """Временная ошибка пережидается retry на той же паре провайдер/модель."""
    exec1 = FakeExecutor("p1", [TimeoutError("boom"), TimeoutError("boom"), "ok"])
    client = ResilientLLMClient(providers=[("p1", exec1)], retries=2, backoff_base=0.0)
    resp = await client.chat(messages=[{"role": "user", "content": "hi"}], model="m1")
    assert resp.content == "p1:m1"
    assert exec1.calls == ["m1", "m1", "m1"]


@pytest.mark.asyncio
async def test_empty_content_moves_to_fallback_model():
    """Пустой ответ основной модели уводит на фолбек-модель."""
    exec1 = FakeExecutor("p1", ["empty", "ok"])
    client = ResilientLLMClient(
        providers=[("p1", exec1)],
        model_fallbacks={"deepseek-r1": ["deepseek-chat"]},
        retries=0,
        backoff_base=0.0,
    )
    resp = await client.chat(messages=[{"role": "user", "content": "hi"}], model="deepseek-r1")
    assert resp.content == "p1:deepseek-chat"
    assert exec1.calls == ["deepseek-r1", "deepseek-chat"]


@pytest.mark.asyncio
async def test_fallback_provider_used_when_primary_missing_model():
    """Ошибка модели (404) на первом провайдере — сразу на второй шлюз."""
    exec1 = FakeExecutor("routerai", [_not_found()])
    exec2 = FakeExecutor("openrouter", ["ok"])
    client = ResilientLLMClient(providers=[("routerai", exec1), ("openrouter", exec2)], retries=0)
    resp = await client.chat(messages=[{"role": "user", "content": "hi"}], model="m1")
    assert resp.content == "openrouter:m1"
    assert exec1.calls == ["m1"]
    assert exec2.calls == ["m1"]


@pytest.mark.asyncio
async def test_tool_call_response_with_empty_content_is_kept():
    """Пустой content при наличии tool_calls — это НЕ ошибка, ответ принимается."""
    tool_calls = [{"function": {"name": "rag_search", "arguments": '{"query": "x"}'}}]

    class ToolExecutor(FakeExecutor):
        async def chat(
            self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
        ):
            self.calls.append(model)
            return _response("", model, tool_calls=tool_calls)

    exec1 = ToolExecutor("p1", [])
    client = ResilientLLMClient(providers=[("p1", exec1)], retries=0)
    resp = await client.chat(messages=[{"role": "user", "content": "hi"}], model="m1")
    assert resp.tool_calls == tool_calls


@pytest.mark.asyncio
async def test_all_candidates_empty_raises():
    """Если каждый кандидат вернул пусто — поднимается RuntimeError с деталями."""
    exec1 = FakeExecutor("p1", ["empty", "empty"])
    client = ResilientLLMClient(
        providers=[("p1", exec1)],
        model_fallbacks={"m1": ["m2"]},
        retries=0,
    )
    with pytest.raises(RuntimeError, match="пустой ответ"):
        await client.chat(messages=[{"role": "user", "content": "hi"}], model="m1")


@pytest.mark.asyncio
async def test_all_providers_fail_raises_runtime_error():
    """Исчерпание всех провайдеров/моделей -> RuntimeError (без проброса внутрянки)."""
    exec1 = FakeExecutor("p1", [TimeoutError("boom"), TimeoutError("boom")])
    client = ResilientLLMClient(providers=[("p1", exec1)], retries=1, backoff_base=0.0)
    with pytest.raises(RuntimeError, match="недоступны"):
        await client.chat(messages=[{"role": "user", "content": "hi"}], model="m1")


def test_candidate_models_dedupe():
    """Порядок кандидатов: основная модель + фолбек без дублей."""
    client = ResilientLLMClient(
        providers=[("p1", FakeExecutor("p1", []))],
        model_fallbacks={"m1": ["m2", "m1", "m3"]},
    )
    assert client._candidate_models("m1") == ["m1", "m2", "m3"]
    assert client._candidate_models("unknown") == ["unknown"]


@pytest.mark.asyncio
async def test_retry_empty_disabled_keeps_empty_answer():
    """При retry_empty=False пустой ответ не считается ошибкой и возвращается."""
    exec1 = FakeExecutor("p1", ["empty"])
    client = ResilientLLMClient(providers=[("p1", exec1)], retries=0, retry_empty=False)
    resp = await client.chat(messages=[{"role": "user", "content": "hi"}], model="m1")
    assert resp.content == ""


@pytest.mark.asyncio
async def test_no_providers_raises_at_construction():
    with pytest.raises(RuntimeError, match="провайдера"):
        ResilientLLMClient(providers=[], retries=0)


async def _drain(agen):
    return [chunk async for chunk in agen]


@pytest.mark.asyncio
async def test_chat_stream_skips_empty_candidate():
    """Стрим: пустой кандидат пропускается, контент берётся у следующего."""

    class StreamFake:
        def __init__(self):
            self.calls = []

        async def chat_stream(
            self, messages, model, temperature=0.7, max_tokens=1024, on_chunk=None
        ):
            self.calls.append(model)
            if model == "m1":
                return
            for ch in ("при", "вет"):
                yield ch

    exec1 = StreamFake()
    client = ResilientLLMClient(
        providers=[("p1", exec1)],
        model_fallbacks={"m1": ["m2"]},
        retries=0,
    )
    chunks = await _drain(client.chat_stream(messages=[], model="m1"))
    assert "".join(chunks) == "привет"
    assert exec1.calls == ["m1", "m2"]
