"""Тесты: модель «вызывает» инструмент текстом, а не нативными tool_calls.

Некоторые провайдеры/модели (например, RU-planner) не умеют нативные
tool_calls и пишут «Функция rag_search json {...}» прямо в text. Такой текст
не должен попадать ученику как финальный ответ.
"""

from src.agent.loop import AgentRuntime, _textual_tool_calls, run_agent
from src.agent.tools import ToolContext
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.models.schemas import AgentGraphState, ContentType

OBSERVED_JUNK = (
    "Я подготовлю объяснение темы и задание. Сначала уточню ключевые факты из "
    "учебных материалов.Функцияrag_search json "
    '{"query": "броуновское движение объяснение и примеры задач"}'
)


def test_textual_tool_calls_parses_observed_pattern():
    calls = _textual_tool_calls(OBSERVED_JUNK)
    assert len(calls) == 1
    assert calls[0].name == "rag_search"
    assert calls[0].arguments["query"] == "броуновское движение объяснение и примеры задач"


def test_textual_tool_calls_web_search_and_nested_quotes():
    text = 'Хм. Функция web_search json {"query": "теорема \\"Виета\\"", "max_results": 3}'
    calls = _textual_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "web_search"
    assert calls[0].arguments["max_results"] == 3
    assert calls[0].arguments["query"] == 'теорема "Виета"'


def test_textual_tool_calls_ignores_prose_and_bad_args():
    assert _textual_tool_calls("В физике упоминается термин rag_search без вызова") == []
    assert _textual_tool_calls('rag_search {"top_k": 5}') == []  # нет обязательного query
    assert _textual_tool_calls('{"type": "theory", "text": "ответ"}') == []


class TextualToolLLM(LLMClient):
    """Сначала «вызывает» rag_search текстом, потом даёт JSON-конверт."""

    def __init__(self):
        self.calls = 0
        self.recorded: list[list[dict]] = []

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        self.calls += 1
        self.recorded.append(list(messages))
        if self.calls == 1:
            return LLMResponse(
                content=OBSERVED_JUNK,
                model=model,
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
                finish_reason="stop",
            )
        return LLMResponse(
            content=(
                '{"type": "theory", "text": "Объяснение броуновского движения '
                '$$x^2$$", "payload": {}}'
            ),
            model=model,
            usage=TokenUsage(prompt_tokens=12, completion_tokens=8),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""

    count_tokens = lambda self, text: len(text) // 4  # noqa: E731


_RAG_OK = (
    '{"status": "ok", "data": [{"text": "Броуновское движение — хаотичное движение '
    'частиц в жидкости", "score": 0.9, "metadata": {"topic": "физика"}}]}'
)


async def test_plan_converts_textual_call_to_tool_call():
    llm = TextualToolLLM()
    rt = AgentRuntime(
        llm=llm,
        models={"planner": "$test", "fast": "$test"},
        tool_context=ToolContext(rag=None, region="GLOBAL"),
    )
    state = AgentGraphState(messages=[{"role": "user", "content": "hello"}])
    out = await rt.plan(state)
    assert out["tool_calls"], "текстовый вызов должен стать tool_calls"
    assert out["tool_calls"][0].name == "rag_search"
    assert "броуновское движение" in out["tool_calls"][0].arguments["query"]
    assert "final_answer" not in out, "текстовый вызов не должен быть финалом"


async def test_run_agent_with_textual_tool_call_executes_and_answers(monkeypatch):
    llm = TextualToolLLM()
    rt = AgentRuntime(
        llm=llm,
        models={"planner": "$test", "fast": "$test"},
        tool_context=ToolContext(rag=None, region="GLOBAL"),
    )

    async def fake_execute_tool(name, args, ctx, tracker):
        assert name == "rag_search"
        return _RAG_OK

    monkeypatch.setattr("src.agent.loop.execute_tool", fake_execute_tool)
    result = await run_agent(rt, [{"role": "user", "content": "расскажи про броуновское"}])

    assert llm.calls == 2
    second = "\n".join(m.get("content", "") for m in llm.recorded[1])
    assert "[Результат инструмента rag_search]" in second
    assert result.content_envelope is not None
    assert result.content_envelope.type == ContentType.THEORY
    assert "Объяснение броуновского движения" in result.final_answer
    assert "Функцияrag_search" not in result.final_answer, "служебный мусор не должен утечь"


async def test_finalize_replaces_textual_tool_junk_with_clean_answer():
    llm = TextualToolLLM()
    rt = AgentRuntime(
        llm=llm,
        models={"planner": "$test", "fast": "$test"},
        tool_context=ToolContext(rag=None, region="GLOBAL"),
    )
    state = AgentGraphState(messages=[], final_answer=OBSERVED_JUNK)
    out = await rt.finalize(state)
    assert out["error"] == "не сформирован финальный ответ"
    assert "Функцияrag_search" not in out["final_answer"]
    assert out["content_envelope"]["type"] == "theory"
