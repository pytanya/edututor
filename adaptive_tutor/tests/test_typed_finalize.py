"""Тесты: finalize парсит конверт, run_agent шлёт envelope в message."""

import pytest

from src.agent.critic import Critic
from src.agent.loop import AgentRuntime, run_agent
from src.agent.tools import ToolContext
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.models.schemas import AgentGraphState, ContentType


class _TC:
    def parse_usage(self, raw):
        return TokenUsage()

    def estimate(self, text):
        return 0


class EnvelopePlanner(LLMClient):
    """Возвращает JSON-конверт сразу (без инструментов)."""

    def __init__(self, content: str):
        super().__init__(token_counter=_TC())
        self._content = content

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024,
                   tools=None, tool_choice=None):
        return LLMResponse(
            content=self._content,
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


class ApproveJudge(LLMClient):
    def __init__(self):
        super().__init__(token_counter=_TC())

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024,
                   tools=None, tool_choice=None):
        return LLMResponse(
            content='{"passed": true, "issues": []}',
            model=model,
            usage=TokenUsage(1, 1),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


def _runtime(llm, events=None):
    return AgentRuntime(
        llm=llm,
        models={"planner": "p", "fast": "f", "judge": "j"},
        tool_context=ToolContext(region="GLOBAL"),
        critic=Critic(llm=ApproveJudge(), model="j"),
        on_event=(lambda ev, data: events.append((ev, data)) if events is not None else None),
    )


@pytest.mark.asyncio
async def test_finalize_parses_envelope():
    raw = (
        '{"type": "quiz", "text": "Вопрос?", '
        '"payload": {"answer_type": "single", "options": ["a"]}}'
    )
    planner = EnvelopePlanner(raw)
    rt = _runtime(planner)
    state = AgentGraphState(messages=[], final_answer=raw)
    out = await rt.finalize(state)
    assert out["content_envelope"]["type"] == "quiz"
    assert out["final_answer"] == "Вопрос?"


@pytest.mark.asyncio
async def test_run_agent_message_event_has_envelope():
    events = []
    planner = EnvelopePlanner('{"type": "theory", "text": "Объяснение $$x$$", "payload": {}}')
    rt = _runtime(planner, events)
    result = await run_agent(rt, [{"role": "user", "content": "hi"}])
    assert result.content_envelope is not None
    assert result.content_envelope.type == ContentType.THEORY
    msg_events = [data for ev, data in events if ev == "message"]
    assert msg_events, "ожидали событие message"
    assert msg_events[-1]["envelope"]["type"] == "theory"


@pytest.mark.asyncio
async def test_plain_text_falls_back_to_theory_in_finalize():
    planner = EnvelopePlanner("Обычный текст без JSON")
    rt = _runtime(planner)
    result = await run_agent(rt, [{"role": "user", "content": "hi"}])
    assert result.content_envelope is not None
    assert result.content_envelope.type == ContentType.THEORY
    assert result.content_envelope.text == "Обычный текст без JSON"


@pytest.mark.asyncio
async def test_finalize_salvages_truncated_theory_envelope():
    """Обрыв на max_tokens посреди JSON: сырой «словарь» не показываем."""
    raw = (
        '{"type": "theory", "text": "Рациональные числа — это числа. '
        'Формула: $$F = m \\cdot g$$, а дальше текст обрывается на '
    )
    planner = EnvelopePlanner(raw)
    rt = _runtime(planner)
    state = AgentGraphState(messages=[], final_answer=raw)
    out = await rt.finalize(state)
    assert out["content_envelope"]["type"] == "theory"
    answer = out["final_answer"]
    assert answer.startswith("Рациональные числа")
    assert "type" not in answer and '{"' not in answer


@pytest.mark.asyncio
async def test_finalize_hides_truncated_quiz_raw():
    """Обрыв на quiz/practice: вместо сырого JSON — вежливый отказ."""
    raw = '{"type": "quiz", "text": "Вопрос для ученика, который оборвался'
    planner = EnvelopePlanner(raw)
    rt = _runtime(planner)
    state = AgentGraphState(messages=[], final_answer=raw)
    out = await rt.finalize(state)
    assert out["content_envelope"]["type"] == "theory"
    assert "оборвался" in out["final_answer"]
    assert '{"type": "quiz"' not in out["final_answer"]
