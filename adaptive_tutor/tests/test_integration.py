"""Интеграционные тесты: полный агентный цикл с адаптацией и критиком."""


from src.agent.critic import Critic
from src.agent.loop import AgentRuntime, run_agent
from src.agent.tools import ToolContext
from src.llm.base import LLMClient, LLMResponse, TokenUsage


class FakePlannerLLM(LLMClient):
    """Возвращает tool_call, потом финальный ответ с формулами."""

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
                tool_calls=[{
                    "id": "call_1",
                    "function": {
                        "name": "web_search",
                        "arguments": '{"query": "квадратное уравнение"}',
                    },
                }],
            )
        return LLMResponse(
            content=(
                "Квадратное уравнение $ax^2 + bx + c = 0$ решается формулой "
                "$$x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$"
            ),
            model=model,
            usage=TokenUsage(prompt_tokens=20, completion_tokens=15),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


class FakeJudgeLLM(LLMClient):
    """Критик: всегда одобряет."""

    def __init__(self):
        self.calls = 0

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024,
        tools=None, tool_choice=None,
    ):
        return LLMResponse(
            content='{"passed": true, "issues": []}',
            model=model,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


async def test_full_cycle_with_adaptation_and_critic():
    planner = FakePlannerLLM()
    judge = FakeJudgeLLM()

    runtime = AgentRuntime(
        llm=planner,
        models={"planner": "test", "judge": "judge"},
        tool_context=ToolContext(region="GLOBAL"),
        critic=Critic(llm=judge, model="judge"),
    )

    result = await run_agent(
        runtime,
        messages=[{"role": "user", "content": "Объясни квадратное уравнение"}],
        session_id="integration-test",
        student_profile={
            "current_knowledge_level": 0.7,
            "learning_style": "visual",
            "fatigue_level": 0.2,
        },
    )

    assert result.final_answer
    assert "ax^2" in result.final_answer or "frac" in result.final_answer
    assert not result.error
