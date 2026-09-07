"""Тесты агентного цикла и инструментов с фейковым LLM."""

import json

from src.agent.critic import Critic
from src.agent.loop import (
    NODE_TOOLS,
    STATUS_FINAL,
    STATUS_STOP,
    AgentRuntime,
    run_agent,
    should_continue,
)
from src.agent.prompts import build_messages
from src.agent.tools import ToolContext, ToolFailureTracker, execute_tool
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.models.schemas import AgentGraphState, LearningStyle


class FakeLLM(LLMClient):
    """Возвращает либо tool_call, либо (на следующем вызове) финальный ответ."""

    def __init__(self):
        self.calls = 0

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024,
        tools=None, tool_choice=None,
    ):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                model=model,
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": "call_1",
                        "function": {
                            "name": "rag_search",
                            "arguments": '{"query": "тест", "top_k": 2}',
                        },
                    }
                ],
            )
        return LLMResponse(
            content="Финальный ответ с формулой $$E=mc^2$$",
            model=model,
            usage=TokenUsage(prompt_tokens=12, completion_tokens=8),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""

    count_tokens = lambda self, text: len(text) // 4  # noqa: E731


class RecordingLLM(LLMClient):
    """Возвращает tool_call, затем финал; записывает messages каждого вызова."""

    def __init__(self, tool_calls_on: int = 1):
        self.calls = 0
        self.tool_calls_on = tool_calls_on
        self.recorded: list[list[dict]] = []

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        self.calls += 1
        self.recorded.append(list(messages))
        if self.calls <= self.tool_calls_on:
            return LLMResponse(
                content="",
                model=model,
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "id": f"call_{self.calls}",
                        "function": {
                            "name": "rag_search",
                            "arguments": '{"query": "Пифагор", "top_k": 2}',
                        },
                    }
                ],
            )
        return LLMResponse(
            content="Финальный ответ с формулой $$a^2+b^2=c^2$$",
            model=model,
            usage=TokenUsage(prompt_tokens=12, completion_tokens=8),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""

    count_tokens = lambda self, text: len(text) // 4  # noqa: E731


def _runtime(model="$test"):
    return AgentRuntime(
        llm=FakeLLM(),
        models={"planner": model, "fast": model},
        tool_context=ToolContext(rag=None, region="GLOBAL"),
    )


async def test_plan_triggers_tool_then_final():
    rt = _runtime()
    state = AgentGraphState(messages=[{"role": "user", "content": "hello"}])
    out = await rt.plan(state)
    assert out["tool_calls"], "первый вызов должен запросить инструмент"
    assert out["tool_calls"][0].name == "rag_search"

    state2 = AgentGraphState(
        messages=[{"role": "user", "content": "hello"}], tool_calls=out["tool_calls"]
    )
    out2 = await rt.run_tools(state2)
    assert out2["tools_result"]["rag_search"].startswith('{"status": "error"')  # RAG не подключён

    state3 = AgentGraphState(messages=[{"role": "user", "content": "hello"}])
    out3 = await rt.plan(state3)
    assert out3["final_answer"]


async def test_should_continue_routing():
    st = AgentGraphState(messages=[])
    assert should_continue(st) == STATUS_FINAL
    st.tool_calls = [{"name": "x", "arguments": {}}]
    assert should_continue(st) == NODE_TOOLS
    st2 = AgentGraphState(messages=[])
    st2.terminated = True
    assert should_continue(st2) == STATUS_STOP


async def test_exceeded_steps_force_final():
    state = AgentGraphState(messages=[])
    for _ in range(6):
        state.steps.append({})  # type: ignore[arg-type]
    assert should_continue(state) == STATUS_FINAL


async def test_execute_tool_unknown_error():
    ctx = ToolContext(region="GLOBAL")
    out = await execute_tool("unknown_tool", {}, ctx, ToolFailureTracker())
    assert '"status": "error"' in out


async def test_run_agent_end_to_end():
    rt = _runtime()
    result = await run_agent(rt, [{"role": "user", "content": "объясни тему"}])
    assert result.final_answer
    assert "E=mc^2" in result.final_answer


_RAG_TOOL_OUTPUT = (
    '{"status": "ok", "data": [{"text": "Теорема Пифагора: a^2 + b^2 = c^2", '
    '"score": 0.93, "metadata": {"topic": "geometry"}}]}'
)


async def test_run_tools_appends_observation_and_preserves_history(monkeypatch):
    rt = _runtime()

    async def fake_execute_tool(name, args, ctx, tracker):
        assert name == "rag_search"
        return _RAG_TOOL_OUTPUT

    monkeypatch.setattr("src.agent.loop.execute_tool", fake_execute_tool)
    state = AgentGraphState(
        messages=[{"role": "user", "content": "hello"}],
        tool_calls=[{"name": "rag_search", "arguments": {"query": "пифагор", "top_k": 2}}],
    )
    out = await rt.run_tools(state)

    assert out["tool_calls"] == []
    assert len(out["messages"]) == 2, "история не должна теряться"
    assert out["messages"][0] == {"role": "user", "content": "hello"}
    obs = out["messages"][1]
    assert obs["role"] == "user"
    assert "[Результат инструмента rag_search]" in obs["content"]
    assert "Теорема Пифагора" in obs["content"]
    assert out["rag_context"]
    assert out["rag_context"][0]["text"].startswith("Теорема Пифагора")


async def test_planner_second_call_receives_tool_result(monkeypatch):
    fake = RecordingLLM()
    rt = AgentRuntime(
        llm=fake,
        models={"planner": "$test", "fast": "$test"},
        tool_context=ToolContext(rag=None, region="GLOBAL"),
    )

    async def fake_execute_tool(name, args, ctx, tracker):
        assert name == "rag_search"
        return _RAG_TOOL_OUTPUT

    monkeypatch.setattr("src.agent.loop.execute_tool", fake_execute_tool)
    result = await run_agent(rt, [{"role": "user", "content": "расскажи про Пифагора"}])

    assert fake.calls == 2
    second = fake.recorded[1]
    text = "\n".join(m.get("content", "") for m in second)
    assert "[Результат инструмента rag_search]" in text
    assert "Теорема Пифагора" in text
    assert "[Контекст из базы знаний]" in text
    assert result.rag_context and result.rag_context[0]["text"].startswith("Теорема Пифагора")
    assert result.final_answer


async def test_student_profile_mapped_to_adaptive_fields():
    fake = RecordingLLM(tool_calls_on=0)
    rt = AgentRuntime(
        llm=fake,
        models={"planner": "$test", "fast": "$test"},
        tool_context=ToolContext(rag=None, region="GLOBAL"),
    )
    result = await run_agent(
        rt,
        [{"role": "user", "content": "объясни тему"}],
        student_profile={
            "current_knowledge_level": 0.2,
            "learning_style": "visual",
            "fatigue_level": 0.8,
        },
    )

    assert result.current_knowledge_level == 0.2
    assert result.learning_style == LearningStyle.VISUAL
    assert result.fatigue_level == 0.8
    msgs = build_messages(result)
    content = "\n".join(m.get("content", "") for m in msgs)
    assert "схемы" in content or "ASCII" in content
    assert "НИЗКИЙ" in content
    assert "УСТАЛ" in content


async def test_student_profile_clamp_and_fallback():
    from src.agent.loop import _adaptive_fields

    fields = _adaptive_fields(
        {"current_knowledge_level": 2.5, "fatigue_level": -1, "learning_style": "auditory"}
    )
    assert fields["current_knowledge_level"] == 1.0
    assert fields["fatigue_level"] == 0.0
    assert fields["learning_style"] == LearningStyle.AUDITORY

    fields = _adaptive_fields(
        {"current_knowledge_level": "not-a-number", "learning_style": "unknown-style"}
    )
    assert fields["current_knowledge_level"] == 0.5
    assert fields["fatigue_level"] == 0.0
    assert fields["learning_style"] == LearningStyle.READING


class _ApproveJudgeLLM(LLMClient):
    """Критик-LLM: всегда одобряет ответ."""

    def __init__(self):
        self.calls = 0

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        self.calls += 1
        return LLMResponse(
            content='{"passed": true, "issues": []}',
            model=model,
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


def _make_events_runtime(llm, on_event):
    return AgentRuntime(
        llm=llm,
        models={"planner": "$test", "fast": "$test"},
        tool_context=ToolContext(rag=None, region="GLOBAL"),
        on_event=on_event,
    )


async def _run_collector_agent(monkeypatch, events, session_id="sess-events"):
    async def fake_execute_tool(name, args, ctx, tracker):
        assert name == "rag_search"
        return _RAG_TOOL_OUTPUT

    monkeypatch.setattr("src.agent.loop.execute_tool", fake_execute_tool)
    rt = _make_events_runtime(
        RecordingLLM(), lambda ev, data: events.append((ev, data))
    )
    return await run_agent(
        rt, [{"role": "user", "content": "расскажи про Пифагора"}], session_id=session_id
    )


async def test_agent_emits_step_tool_and_tool_result_events(monkeypatch):
    events: list[tuple[str, dict]] = []
    result = await _run_collector_agent(monkeypatch, events)

    step_tool = next(
        (ev, data) for ev, data in events if ev == "agent.step" and data["action"] == "tool"
    )
    assert step_tool[1]["tool"] == "rag_search"
    assert step_tool[1]["model"] == "$test"
    assert step_tool[1]["status"] == "ok"

    tool_result = next((ev, data) for ev, data in events if ev == "agent.tool")
    assert tool_result == ("agent.tool", {"name": "rag_search", "status": "ok"})

    step_final = next(
        (ev, data) for ev, data in events if ev == "agent.step" and data["action"] == "final"
    )
    assert step_final[1]["status"] == "ok"
    assert step_final[1]["tool"] is None

    assert events.index(step_tool) < events.index(tool_result) < events.index(step_final)

    assert events[-2][0] == "message"
    assert events[-2][1]["content"] == result.final_answer
    assert events[-2][1]["session_id"] == "sess-events"
    assert events[-1][0] == "done"
    assert events[-1][1]["session_id"] == "sess-events"
    assert events[-1][1]["steps"] == len(result.steps)
    assert events[-1][1]["trace_id"]


async def test_agent_emits_finalize_event():
    events: list[tuple[str, dict]] = []
    rt = AgentRuntime(
        llm=FakeLLM(),
        models={"planner": "$test", "fast": "$test"},
        tool_context=ToolContext(rag=None, region="GLOBAL"),
        critic=Critic(llm=_ApproveJudgeLLM(), model="judge"),
        on_event=lambda ev, data: events.append((ev, data)),
    )
    state = AgentGraphState(messages=[{"role": "user", "content": "hello"}])
    state.final_answer = "Ответ с формулой $$a^2+b^2=c^2$$"
    await rt.finalize(state)

    assert ("agent.finalize", {"status": "ok"}) in events


async def test_on_event_raising_observer_does_not_break_run(monkeypatch):
    events: list[tuple[str, dict]] = []

    def raising_collector(ev, data):
        events.append((ev, data))
        raise RuntimeError("наблюдатель упал")

    rt = _make_events_runtime(RecordingLLM(), raising_collector)

    async def fake_execute_tool(name, args, ctx, tracker):
        return _RAG_TOOL_OUTPUT

    monkeypatch.setattr("src.agent.loop.execute_tool", fake_execute_tool)
    result = await run_agent(rt, [{"role": "user", "content": "расскажи про Пифагора"}])

    assert events, "события должны собираться до исключения наблюдателя"
    assert result.final_answer


async def test_generate_quiz_records_budget_and_log(tmp_path):
    """Вызов generate_quiz учитывается в бюджете сессии и пишется в JSONL."""
    from src.observability.logger import JsonlLogger
    from src.safety import BudgetGuard

    log_file = tmp_path / "agent.jsonl"
    logger = JsonlLogger(str(log_file))
    budget = BudgetGuard()

    class QuizLLM(LLMClient):
        def __init__(self, *args, **kwargs):
            pass

        async def chat(self, messages, model, temperature=0.7, max_tokens=1024,
                       tools=None, tool_choice=None):
            self.seen_messages = list(messages)
            return LLMResponse(
                content=json.dumps(
                    {
                        "question": "Как направлена сила тяжести?",
                        "answer_type": "single",
                        "options": ["вертикально вниз", "вверх", "горизонтально", "по кругу"],
                        "_correct_answer": "вертикально вниз",
                    },
                    ensure_ascii=False,
                ),
                model=model,
                usage=TokenUsage(prompt_tokens=80, completion_tokens=40, cost_usd=0.001),
                finish_reason="stop",
            )

        async def chat_stream(self, *a, **k):
            yield ""

        count_tokens = lambda self, text: len(text) // 4  # noqa: E731

    llm = QuizLLM()
    ctx = ToolContext(
        rag=None,
        region="GLOBAL",
        llm=llm,
        model="fast",
        budget=budget,
        logger=logger,
        trace_id="trc_gq",
    )
    out = await execute_tool(
        "generate_quiz",
        {"topic": "Сила тяжести", "difficulty": "easy"},
        ctx,
        ToolFailureTracker(),
    )
    parsed = json.loads(out)
    assert parsed["status"] == "ok"
    assert parsed["data"]["_correct_answer"] == "вертикально вниз"
    assert parsed["data"]["difficulty"] == "easy"
    assert budget.calls == 1
    assert budget.spent == 0.001

    content = log_file.read_text(encoding="utf-8")
    assert '"event": "quiz.gen"' in content
    assert '"trace_id": "trc_gq"' in content
    assert '"name": "generate_quiz"' in content
    assert '"cost_usd": 0.001' in content
    assert llm.seen_messages[0]["content"].startswith("Ты — составитель")


async def test_generate_quiz_without_llm_fails_closed():
    """Без подключённого LLM generate_quiz возвращает ошибку (fail-soft)."""
    ctx = ToolContext(region="GLOBAL")
    out = await execute_tool("generate_quiz", {"topic": "x"}, ctx, ToolFailureTracker())
    parsed = json.loads(out)
    assert parsed["status"] == "error"
    assert "LLM не подключён" in parsed["error"]