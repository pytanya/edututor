"""Тесты критика (judge model) и интеграции с агентным циклом."""

import json

import pytest

from src.agent.critic import Critic, _check_latex, _parse_critic_response
from src.agent.loop import AgentRuntime
from src.agent.tools import ToolContext
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.models.schemas import AgentGraphState


class FakeCriticLLM(LLMClient):
    """LLM, возвращающий заданный JSON-ответ критика."""

    def __init__(self, response_json: dict):
        self._response = response_json
        self.calls = []

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024,
        tools=None, tool_choice=None,
    ):
        self.calls.append({"messages": messages, "model": model})
        return LLMResponse(
            content=json.dumps(self._response, ensure_ascii=False),
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


class FakeCriticLLMError(LLMClient):
    """LLM, бросающий исключение при вызове."""

    def __init__(self):
        from src.llm.base import TokenCounter
        class _DummyCounter(TokenCounter):
            def parse_usage(self, raw): return TokenUsage()
            def estimate(self, text): return 0
        super().__init__(token_counter=_DummyCounter())

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024,
        tools=None, tool_choice=None,
    ):
        raise RuntimeError("LLM недоступен")

    async def chat_stream(self, *a, **k):
        yield ""


class FakeCriticLLMWrapped(LLMClient):
    """LLM, возвращающий JSON в markdown-обёртке."""

    def __init__(self, response_json: dict):
        self._response = response_json
        self.calls = []

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024,
        tools=None, tool_choice=None,
    ):
        self.calls.append(True)
        wrapped = "```json\n" + json.dumps(self._response, ensure_ascii=False) + "\n```"
        return LLMResponse(
            content=wrapped,
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


class FakePlannerLLM(LLMClient):
    """LLM планировщика: сразу выдаёт финальный ответ."""

    def __init__(self, answer: str):
        self._answer = answer
        self.calls = 0

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024,
        tools=None, tool_choice=None,
    ):
        self.calls += 1
        return LLMResponse(
            content=self._answer,
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


# ── TestCheckLatex ──

class TestCheckLatex:
    def test_balanced_passes(self):
        text = "Формула $$E=mc^2$$ и текст $x$ здесь"
        assert _check_latex(text) == []

    def test_unbalanced_dbl_fails(self):
        text = "Формула $$E=mc^2$$ и $$конец"
        issues = _check_latex(text)
        assert len(issues) == 1
        assert "$$" in issues[0]

    def test_unbalanced_single_fails(self):
        text = "Формула $x + y$ и $открытая"
        issues = _check_latex(text)
        assert len(issues) == 1
        assert "$" in issues[0]

    def test_no_latex_passes(self):
        assert _check_latex("Просто текст без формул") == []

    def test_multiple_dd_balanced(self):
        text = "$$a$$ $$b$$"
        assert _check_latex(text) == []


# ── TestParseCriticResponse ──

class TestParseCriticResponse:
    def test_valid_json_passed(self):
        data = {"passed": True, "issues": [], "corrected_answer": None}
        result = _parse_critic_response(json.dumps(data))
        assert result.passed is True
        assert result.issues == []
        assert result.corrected_answer is None

    def test_valid_json_failed_with_correction(self):
        data = {
            "passed": False,
            "issues": ["факт не подтверждён"],
            "corrected_answer": "Исправленный ответ",
        }
        result = _parse_critic_response(json.dumps(data))
        assert result.passed is False
        assert result.issues == ["факт не подтверждён"]
        assert result.corrected_answer == "Исправленный ответ"

    def test_wrapped_in_codeblock(self):
        data = {"passed": True, "issues": [], "corrected_answer": None}
        wrapped = "```json\n" + json.dumps(data) + "\n```"
        result = _parse_critic_response(wrapped)
        assert result.passed is True

    def test_invalid_json_returns_passed(self):
        result = _parse_critic_response("не json ответ")
        assert result.passed is True
        assert result.issues == []


# ── TestCriticValidate ──

class TestCriticValidate:
    @pytest.mark.asyncio
    async def test_empty_answer_fails(self):
        llm = FakeCriticLLM({"passed": True})
        critic = Critic(llm, model="test")
        result = await critic.validate("")
        assert result.passed is False
        assert "пустой ответ" in result.issues

    @pytest.mark.asyncio
    async def test_whitespace_only_answer_fails(self):
        llm = FakeCriticLLM({"passed": True})
        critic = Critic(llm, model="test")
        result = await critic.validate("   \n  ")
        assert result.passed is False
        assert "пустой ответ" in result.issues

    @pytest.mark.asyncio
    async def test_latex_issue_fails_without_llm(self):
        llm = FakeCriticLLM({"passed": True})
        critic = Critic(llm, model="test")
        result = await critic.validate("Ответ с $$несбалансированной формулой")
        assert result.passed is False
        assert any("$" in i for i in result.issues)
        assert len(llm.calls) == 0

    @pytest.mark.asyncio
    async def test_valid_answer_calls_llm(self):
        llm = FakeCriticLLM({"passed": True, "issues": [], "corrected_answer": None})
        critic = Critic(llm, model="test")
        result = await critic.validate("Всё хорошо $$E=mc^2$$")
        assert result.passed is True
        assert len(llm.calls) == 1

    @pytest.mark.asyncio
    async def test_llm_failure_returns_passed(self):
        llm = FakeCriticLLMError()
        critic = Critic(llm, model="test")
        result = await critic.validate("Нормальный ответ")
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_rag_context_passed_to_llm(self):
        llm = FakeCriticLLM({"passed": True, "issues": [], "corrected_answer": None})
        critic = Critic(llm, model="test")
        ctx = [{"text": "фрагмент 1"}, {"text": "фрагмент 2"}]
        await critic.validate("Ответ $$x^2$$", rag_context=ctx)
        assert len(llm.calls) == 1
        messages = llm.calls[0]["messages"]
        user_msg = messages[-1]["content"]
        assert "Kонтекст из базы знаний" in user_msg
        assert "фрагмент 1" in user_msg

    @pytest.mark.asyncio
    async def test_llm_returns_correction(self):
        llm = FakeCriticLLM({
            "passed": False,
            "issues": ["галлюцинация"],
            "corrected_answer": "Исправленный ответ",
        })
        critic = Critic(llm, model="test")
        result = await critic.validate("Ответ $$x^2$$")
        assert result.passed is False
        assert result.corrected_answer == "Исправленный ответ"

    @pytest.mark.asyncio
    async def test_wrapped_json_response(self):
        llm = FakeCriticLLMWrapped({"passed": True, "issues": [], "corrected_answer": None})
        critic = Critic(llm, model="test")
        result = await critic.validate("Текст $$a=b$$")
        assert result.passed is True


# ── TestCriticIntegration ──

def _make_runtime(planner_llm, critic=None):
    return AgentRuntime(
        llm=planner_llm,
        models={"planner": "test", "fast": "test"},
        tool_context=ToolContext(rag=None, region="GLOBAL"),
        critic=critic,
    )


class TestCriticIntegration:
    @pytest.mark.asyncio
    async def test_finalize_with_critic_passes(self):
        planner = FakePlannerLLM("Ответ $$E=mc^2$$")
        critic_llm = FakeCriticLLM({"passed": True, "issues": [], "corrected_answer": None})
        rt = _make_runtime(planner, critic=Critic(critic_llm, "test"))

        state = AgentGraphState(
            messages=[{"role": "user", "content": "test"}],
            final_answer="Ответ $$E=mc^2$$",
        )
        result = await rt.finalize(state)
        assert result["final_answer"] == "Ответ $$E=mc^2$$"
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_finalize_with_critic_rejects(self):
        planner = FakePlannerLLM("Ответ")
        critic_llm = FakeCriticLLM({
            "passed": False,
            "issues": ["галлюцинация"],
            "corrected_answer": "Исправленный ответ $$x$$",
        })
        rt = _make_runtime(planner, critic=Critic(critic_llm, "test"))

        state = AgentGraphState(
            messages=[{"role": "user", "content": "test"}],
            final_answer="Ответ",
        )
        result = await rt.finalize(state)
        assert result["final_answer"] == "Исправленный ответ $$x$$"

    @pytest.mark.asyncio
    async def test_finalize_without_critic_skips(self):
        planner = FakePlannerLLM("Ответ")
        rt = _make_runtime(planner, critic=None)

        state = AgentGraphState(
            messages=[{"role": "user", "content": "test"}],
            final_answer="Ответ",
        )
        result = await rt.finalize(state)
        assert result["final_answer"] == "Ответ"

    @pytest.mark.asyncio
    async def test_finalize_validator_fails_skips_critic(self):
        """Если OutputValidator провалился, критик не вызывается."""
        planner = FakePlannerLLM("Ответ")
        critic_llm = FakeCriticLLM({"passed": False, "issues": ["не должен быть вызван"]})
        rt = _make_runtime(planner, critic=Critic(critic_llm, "test"))

        state = AgentGraphState(
            messages=[{"role": "user", "content": "test"}],
            final_answer="Ответ с $несбалансированной формулой",
        )
        result = await rt.finalize(state)
        assert "требуется переформулировка" in result["final_answer"]
        assert "error" in result
        assert len(critic_llm.calls) == 0

    @pytest.mark.asyncio
    async def test_finalize_critic_no_correction_keeps_answer(self):
        """Без исправления от критика ответ не портится внутренними маркерами."""
        planner = FakePlannerLLM("Ответ")
        critic_llm = FakeCriticLLM({
            "passed": False,
            "issues": ["проблема"],
            "corrected_answer": None,
        })
        rt = _make_runtime(planner, critic=Critic(critic_llm, "test"))

        state = AgentGraphState(
            messages=[{"role": "user", "content": "test"}],
            final_answer="Ответ",
        )
        result = await rt.finalize(state)
        assert result["final_answer"] == "Ответ"

    @pytest.mark.asyncio
    async def test_finalize_with_rag_context(self):
        planner = FakePlannerLLM("Ответ")
        critic_llm = FakeCriticLLM({"passed": True, "issues": [], "corrected_answer": None})
        rt = _make_runtime(planner, critic=Critic(critic_llm, "test"))

        state = AgentGraphState(
            messages=[{"role": "user", "content": "test"}],
            final_answer="Ответ $$x$$",
            rag_context=[{"text": "контекст"}],
        )
        result = await rt.finalize(state)
        assert result["final_answer"] == "Ответ $$x$$"
        messages = critic_llm.calls[0]["messages"]
        user_msg = messages[-1]["content"]
        assert "контекст" in user_msg
