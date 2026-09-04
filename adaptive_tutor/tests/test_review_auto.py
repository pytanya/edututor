"""Авто-добавление карточки при неверном ответе квиза."""

import pytest
from fastapi.testclient import TestClient

from src.agent.critic import Critic
from src.agent.loop import AgentRuntime
from src.agent.tools import ToolContext
from src.api.server import create_app
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.student.store import StudentStore


class _TC:
    def parse_usage(self, raw):
        return TokenUsage()

    def estimate(self, text):
        return 0


class SequencePlanner(LLMClient):
    """Возвращает payload по очереди (по одному на вызов planner)."""

    def __init__(self, payloads):
        super().__init__(token_counter=_TC())
        # Общая очередь через замыкание: НЕ копируем список, иначе каждый
        # рантайм на запрос стартует с полного набора payloads заново.
        self._payloads = payloads

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        payload = self._payloads.pop(0) if self._payloads else '{"type":"theory","text":"…"}'
        return LLMResponse(content=payload, model=model,
                           usage=TokenUsage(1, 1), finish_reason="stop")

    async def chat_stream(self, *a, **k):
        yield ""


class ApproveJudge(LLMClient):
    def __init__(self):
        super().__init__(token_counter=_TC())

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        return LLMResponse(content='{"passed": true, "issues": []}', model=model,
                           usage=TokenUsage(1, 1), finish_reason="stop")

    async def chat_stream(self, *a, **k):
        yield ""


def _app(tmp_path, first_payload, second_payload, student_store):
    payloads = [first_payload, second_payload]

    def fake_factory():
        return AgentRuntime(
            llm=SequencePlanner(payloads),
            models={"planner": "p", "fast": "f", "judge": "j"},
            tool_context=ToolContext(region="GLOBAL"),
            critic=Critic(llm=ApproveJudge(), model="j"),
        )

    return create_app(runtime_factory=fake_factory, student_store=student_store)


@pytest.mark.asyncio
async def test_wrong_quiz_auto_adds_card(tmp_path):
    store = StudentStore(str(tmp_path / "s.db"))
    quiz = (
        '{"type":"quiz","text":"Сколько будет 2+2?",'
        '"payload":{"answer_type":"single","options":["3","4","5"],'
        '"_correct_answer":"4"}}'
    )
    ev = (
        '{"type":"evaluation","text":"Неверно. Правильный ответ: 4",'
        '"payload":{"correct":false,"knowledge_delta":-0.1}}'
    )
    app = _app(tmp_path, quiz, ev, store)
    client = TestClient(app)
    base = {"session_id": "ses_test", "student_id": "stu_1",
            "topic": "тема", "subject": "математика", "grade": "5"}
    client.post("/chat", json={**base, "message": "Изучаем тему X"})       # -> quiz
    client.post("/chat", json={**base, "message": "Ответ: 3"})             # -> evaluation: неверно
    stats = store.review_stats("stu_1")
    assert stats["total"] == 1
    cards = store.list_review_cards("stu_1", limit=5)
    assert cards[0]["correct_answer"] == "4"
    store.close()
