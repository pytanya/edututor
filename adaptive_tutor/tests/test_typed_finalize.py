"""Тесты: finalize парсит конверт, run_agent шлёт envelope в message."""

import json

import pytest

from src.agent.critic import Critic
from src.agent.loop import AgentRuntime, run_agent
from src.agent.tools import ToolContext
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.models.schemas import AgentGraphState, ContentType
from src.observability.logger import JsonlLogger


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


class QueuedLLM(LLMClient):
    """Очередь ответов planner: каждый вызов ``llm.chat`` берёт следующий.

    Один вызов происходит на каждый plan/finalize плюс по одному на retry-попытку,
    поэтому очередью удобно воспроизводить сценарии «обрыв → восстановление».
    Элемент — строка (finish_reason="stop") либо кортеж ``(content, finish_reason)``.
    """

    def __init__(self, *responses):
        super().__init__(token_counter=_TC())
        self._responses = list(responses)
        self.calls = 0

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024,
                   tools=None, tool_choice=None):
        self.calls += 1
        if self.calls > len(self._responses):
            raise AssertionError("llm.chat вызван чаще, чем заготовлено ответов")
        item = self._responses[self.calls - 1]
        content, finish_reason = item if isinstance(item, tuple) else (item, "stop")
        return LLMResponse(
            content=content,
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            finish_reason=finish_reason,
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
    """Обрыв на quiz/practice без сформированного text → нейтральный отказ.

    Раньше здесь был вежливый отказ «ответ получился слишком длинным» — теперь
    после неудачного retry ученик видит нейтральную формулировку, никогда — JSON.
    """
    raw = '{"type": "quiz", "payload": {"answer_type": "single", "options": ["a", "b"]'
    planner = QueuedLLM((raw, "length"), (raw, "length"))
    rt = _runtime(planner)
    result = await run_agent(rt, [{"role": "user", "content": "да"}])
    assert planner.calls == 2  # план + одна retry-попытка
    assert result.content_envelope is not None
    assert result.content_envelope.type == ContentType.THEORY
    assert "Не получилось сформировать ответ" in result.final_answer
    assert "слишком длинным" not in result.final_answer
    assert '{"type": "quiz"' not in result.final_answer


@pytest.mark.asyncio
async def test_finalize_retry_rescues_truncated_envelope():
    """Обрыв quiz → один retry с валидным конвертом: финал — из ответа retry."""
    truncated = '{"type": "practice", "text": "Реши пример: $5 + (-3)$", "payload": {'
    valid = (
        '{"type": "practice", "text": "Реши пример: $5 + (-3) = ?$", '
        '"payload": {"task_ref": "x"}, "difficulty": "medium"}'
    )
    planner = QueuedLLM((truncated, "length"), (valid, "stop"))
    rt = _runtime(planner)
    result = await run_agent(rt, [{"role": "user", "content": "давай ещё примеры"}])
    assert planner.calls == 2, "должны были сходить в LLM дважды: план + retry"
    assert result.content_envelope is not None
    assert result.content_envelope.type == ContentType.PRACTICE
    assert result.final_answer == "Реши пример: $5 + (-3) = ?$"


@pytest.mark.asyncio
async def test_finalize_double_truncation_gives_neutral_refusal():
    """Два обрыва без частичного text → нейтральный отказ, не сырой JSON."""
    truncated = '{"type": "practice", "payload": {"task_ref": "x"}, "difficulty": "med'
    planner = QueuedLLM((truncated, "length"), (truncated, "length"))
    rt = _runtime(planner)
    result = await run_agent(rt, [{"role": "user", "content": "да"}])
    assert planner.calls == 2
    assert result.content_envelope is not None
    assert result.content_envelope.type == ContentType.THEORY
    assert "Не получилось сформировать ответ" in result.final_answer
    assert "слишком длинным" not in result.final_answer
    assert '{"type"' not in result.final_answer


@pytest.mark.asyncio
async def test_finalize_partial_question_used_as_theory_hint():
    """Двойной обрыв quiz, но text успел сформироваться → theory-подсказка, не отказ."""
    truncated = '{"type": "quiz", "text": "Сколько будет $5 + (-3)$?", "payload": {"answ'
    planner = QueuedLLM((truncated, "length"), (truncated, "length"))
    rt = _runtime(planner)
    result = await run_agent(rt, [{"role": "user", "content": "да"}])
    assert planner.calls == 2
    assert result.content_envelope is not None
    assert result.content_envelope.type == ContentType.THEORY
    assert result.final_answer == "Сколько будет $5 + (-3)$?"
    assert "Не получилось сформировать ответ" not in result.final_answer


@pytest.mark.asyncio
async def test_finalize_logs_final_truncated_event(tmp_path):
    """Невосстановимый обрыв пишет final.truncated с raw_len/attempt в JSONL."""
    log_path = tmp_path / "agent.jsonl"
    raw = '{"type": "practice", "payload": {"task_ref": "x"}'
    planner = QueuedLLM((raw, "length"), (raw, "length"))
    rt = _runtime(planner)
    rt.logger = JsonlLogger(str(log_path))
    result = await run_agent(
        rt,
        [{"role": "user", "content": "да"}],
        session_id="sess-1",
        trace_id="trace-truncated",
    )
    assert "Не получилось сформировать ответ" in result.final_answer
    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events = [rec for rec in records if rec.get("event") == "final.truncated"]
    assert len(events) == 2
    assert events[0]["attempt"] == 1
    assert events[0]["finish_reason"] == "length"
    assert events[1]["attempt"] == 2
    assert events[1]["outcome"] == "refusal"
    assert events[1]["raw_len"] == len(raw)
    assert events[1]["trace_id"] == "trace-truncated"
    assert events[1]["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_finalize_plain_text_does_not_log_truncated(tmp_path):
    """Обычный текст не похож на оборванный JSON — события final.truncated нет."""
    log_path = tmp_path / "agent.jsonl"
    planner = QueuedLLM("Обычный текст без JSON")
    rt = _runtime(planner)
    rt.logger = JsonlLogger(str(log_path))
    result = await run_agent(rt, [{"role": "user", "content": "hi"}])
    assert result.final_answer == "Обычный текст без JSON"
    records = []
    if log_path.exists():
        records = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    assert not any(rec.get("event") == "final.truncated" for rec in records)
