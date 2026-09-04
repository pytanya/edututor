"""E2 server-хуки: apply_result на evaluation/review, mastery-гейт, adaptive-поля."""

from types import SimpleNamespace

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


class _NoAgent:
    """Runtime, который падает при вызове — агент не должен вызываться в review."""

    def __call__(self):
        raise AssertionError("агент вызван в review-режиме")


def _rec(correct: bool, feedback: str = ""):
    return SimpleNamespace(correct=correct, feedback=feedback)


def _app(tmp_path, payloads, store):
    def fake_factory():
        return AgentRuntime(
            llm=SequencePlanner(payloads),
            models={"planner": "p", "fast": "f", "judge": "j"},
            tool_context=ToolContext(region="GLOBAL"),
            critic=Critic(llm=ApproveJudge(), model="j"),
        )

    return create_app(runtime_factory=fake_factory, student_store=store)


_QUIZ = (
    '{"type":"quiz","text":"Сколько будет 2+2?",'
    '"payload":{"answer_type":"single","options":["3","4","5"],"_correct_answer":"4"}}'
)
_EVAL_OK = (
    '{"type":"evaluation","text":"Верно!","payload":{"correct":true,'
    '"feedback":"Верно!","knowledge_delta":0.3}}'
)


def test_evaluation_applies_mastery(tmp_path):
    """Два верных evaluation-хода по ОДНОЙ сессии: mastery == 0.755, level == mastery."""
    store = StudentStore(str(tmp_path / "s.db"))
    app = _app(tmp_path, [_QUIZ, _EVAL_OK, _QUIZ, _EVAL_OK], store)
    client = TestClient(app)
    base = {"session_id": "ses_m", "student_id": "stu_m",
            "topic": "интегралы", "subject": "математика", "grade": "11"}
    client.post("/chat", json={**base, "message": "Изучаем интегралы"})   # -> quiz
    client.post("/chat", json={**base, "message": "Ответ: 4"})            # -> evaluation: верно
    client.post("/chat", json={**base, "message": "Изучаем дальше"})      # -> quiz
    client.post("/chat", json={**base, "message": "Ответ: 4"})            # -> evaluation: верно
    t = store.get_topic("stu_m", "интегралы")
    assert t is not None
    assert t["mastery"] == pytest.approx(0.755)
    assert t["level"] == t["mastery"]          # level поддерживается = mastery
    assert t["status"] == "in_progress"        # 2 попытки: ещё не mastered
    store.close()


def test_mastery_gate_emitted_once(tmp_path):
    """topic B имеет незакрытый пререквизит A -> system-событие mastery.gate один раз."""
    store = StudentStore(str(tmp_path / "s.db"))
    store.apply_result("stu_g", "B", "м", _rec(True, ""))
    store.set_relations("stu_g", "B", {"prerequisite": ["A"], "related": []})
    app = _app(tmp_path, ['{"type":"theory","text":"Изучаем B."}'], store)
    client = TestClient(app)
    base = {"message": "изучаем B", "session_id": "ses_g",
            "student_id": "stu_g", "topic": "B", "subject": "м"}

    first = client.post("/chat/stream", json=base)
    assert first.status_code == 200
    assert "mastery.gate" in first.text
    assert "Совет: прежде чем «B»" in first.text
    assert "A" in first.text

    second = client.post("/chat/stream", json=base)
    assert second.status_code == 200
    assert "mastery.gate" not in second.text
    store.close()


def test_adaptive_has_mastery_fields(tmp_path):
    """POST /chat (теория): adaptive содержит topic_status/topic_mastery/topic_accuracy."""
    store = StudentStore(str(tmp_path / "s.db"))
    store.apply_result("stu_a", "интегралы", "математика", _rec(True, ""))
    app = _app(tmp_path, ['{"type":"theory","text":"Объяснение темы."}'], store)
    client = TestClient(app)
    resp = client.post("/chat", json={
        "message": "расскажи про интегралы", "session_id": "ses_a",
        "student_id": "stu_a", "topic": "интегралы", "subject": "математика",
    })
    assert resp.status_code == 200
    adaptive = resp.json()["adaptive"]
    assert adaptive["topic_status"] == "in_progress"
    assert adaptive["topic_mastery"] == pytest.approx(0.65)
    assert adaptive["topic_accuracy"] == pytest.approx(1.0)
    assert adaptive["recommended_next"] == "интегралы"
    store.close()


def test_review_answer_updates_mastery(tmp_path):
    """Ответ на review-карточку обновляет mastery темы карточки (карточку НЕ создаёт)."""
    store = StudentStore(str(tmp_path / "s.db"))
    store.add_review_card("stu_r", {"question": "2+2?", "topic": "арифметика",
                                    "subject": "математика", "options": ["3", "4", "5"],
                                    "answer_type": "single", "correct_answer": "4"})
    app = create_app(runtime_factory=_NoAgent(), student_store=store)
    client = TestClient(app)
    start = {"message": "", "kind": "review_request", "session_id": "ses_r",
             "student_id": "stu_r", "topic": "арифметика", "subject": "математика"}
    answer = {"message": "Ответ: 4", "kind": "message", "session_id": "ses_r",
              "student_id": "stu_r", "topic": "арифметика", "subject": "математика"}

    r = client.post("/chat/stream", json=start)
    assert r.status_code == 200
    assert "review" in r.text
    client.post("/chat/stream", json=answer)

    t = store.get_topic("stu_r", "арифметика")
    assert t is not None
    assert t["mastery"] == pytest.approx(0.65)
    assert t["attempts"] == 1
    assert t["status"] == "in_progress"
    store.close()
