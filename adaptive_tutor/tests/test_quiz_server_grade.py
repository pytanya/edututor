"""Серверный грейд ответа на активный in-chat квиз (детерминированный closed / LLM open).

Сценарий бага: модель выдаёт quiz (session.last_quiz), ученик отвечает «Ответ: …»,
а вердикт раньше выносил planner вслепую (не видел ни options, ни _correct_answer).
Теперь такой ход перехватывается ДО run_agent:
  - закрытый вопрос — сравнение с эталоном на сервере;
  - открытый — короткий LLM-грейдер (fast), как review-карточки.
"""

from fastapi.testclient import TestClient

from src.agent.critic import Critic
from src.agent.loop import AgentRuntime
from src.agent.tools import ToolContext
from src.api.server import create_app
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.student.store import StudentStore

_CLOSED_QUIZ = (
    '{"type": "quiz", "text": "Сколько будет 2+2?", '
    '"payload": {"answer_type": "single", "options": ["3", "4", "5"], '
    '"_correct_answer": "4"}, "difficulty": "easy"}'
)
_OPEN_QUIZ = (
    '{"type": "quiz", "text": "Что такое инерция?", '
    '"payload": {"answer_type": "open", "_correct_answer": "свойство тела '
    'сохранять скорость"}, "difficulty": "medium"}'
)
_THEORY = '{"type": "theory", "text": "Разберём подробнее.", "difficulty": "medium"}'


class _TC:
    def parse_usage(self, raw):
        return TokenUsage()

    def estimate(self, text):
        return 0


class QuizThenFail(LLMClient):
    """Ход 1: quiz; любой повторный вызов planner — ошибка (агент не должен зваться)."""

    def __init__(self, quiz: str):
        super().__init__(token_counter=_TC())
        self.quiz = quiz
        self.calls = 0

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024,
                   tools=None, tool_choice=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(content=self.quiz, model=model,
                               usage=TokenUsage(1, 1), finish_reason="stop")
        raise AssertionError("planner вызван повторно: ответ на квиз должен грейдиться сервером")

    async def chat_stream(self, *a, **k):
        yield ""


class QuizThenTheory(LLMClient):
    """Ход 1: quiz; ход 2: theory (сообщение не-ответ уходит в агента)."""

    def __init__(self):
        super().__init__(token_counter=_TC())
        self.calls = 0

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024,
                   tools=None, tool_choice=None):
        self.calls += 1
        payload = _CLOSED_QUIZ if self.calls == 1 else _THEORY
        return LLMResponse(content=payload, model=model,
                           usage=TokenUsage(1, 1), finish_reason="stop")

    async def chat_stream(self, *a, **k):
        yield ""


class QuizThenOpenGrader(LLMClient):
    """Planner возвращает open-quiz; затем серверный LLM-грейдер (fast)."""

    def __init__(self):
        super().__init__(token_counter=_TC())
        self.calls = 0

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024,
                   tools=None, tool_choice=None):
        self.calls += 1
        joined = " ".join(m.get("content", "") for m in messages if m.get("content"))
        if self.calls == 1:
            return LLMResponse(content=_OPEN_QUIZ, model=model,
                               usage=TokenUsage(1, 1), finish_reason="stop")
        if "Эталонный ответ" in joined:
            return LLMResponse(
                content='{"correct": true, "feedback": "Да, инерция — свойство '
                        'сохранять скорость движения."}',
                model=model, usage=TokenUsage(1, 1), finish_reason="stop",
            )
        return LLMResponse(content=_THEORY, model=model,
                           usage=TokenUsage(1, 1), finish_reason="stop")

    async def chat_stream(self, *a, **k):
        yield ""


class QuizThenPlannerTheory(LLMClient):
    """Planner возвращает open-quiz, затем theory (сообщение ушло агенту)."""

    def __init__(self):
        super().__init__(token_counter=_TC())
        self.calls = 0

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024,
                   tools=None, tool_choice=None):
        self.calls += 1
        payload = _OPEN_QUIZ if self.calls == 1 else _THEORY
        return LLMResponse(content=payload, model=model,
                           usage=TokenUsage(1, 1), finish_reason="stop")

    async def chat_stream(self, *a, **k):
        yield ""


class ApproveJudge(LLMClient):
    def __init__(self):
        super().__init__(token_counter=_TC())

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024,
                   tools=None, tool_choice=None):
        return LLMResponse(content='{"passed": true, "issues": []}', model=model,
                           usage=TokenUsage(1, 1), finish_reason="stop")

    async def chat_stream(self, *a, **k):
        yield ""


def _app(tmp_path, llm):
    def factory():
        return AgentRuntime(
            llm=llm,
            models={"planner": "p", "fast": "f", "judge": "j"},
            tool_context=ToolContext(region="GLOBAL"),
            critic=Critic(llm=ApproveJudge(), model="j"),
        )

    app = create_app(runtime_factory=factory, student_store=StudentStore(str(tmp_path / "s.db")))
    app.state.rag_engine = None
    app.state.provisioner = None
    return app


def _post(client, message, session, student="stu_q", topic="арифметика"):
    return client.post("/chat", json={
        "message": message, "session_id": session, "student_id": student,
        "topic": topic, "subject": "математика",
    })


def test_closed_quiz_answer_graded_server_side(tmp_path):
    """Верный ответ на закрытый квиз: evaluation correct=true, агент не вызывается."""
    fake = QuizThenFail(_CLOSED_QUIZ)
    app = _app(tmp_path, fake)
    with TestClient(app) as c:
        r1 = _post(c, "дай задание", "s1")
        assert r1.json()["envelope"]["type"] == "quiz"
        assert fake.calls == 1

        r2 = _post(c, "Ответ: 4", "s1")
        assert r2.status_code == 200
        env = r2.json()["envelope"]
        assert env["type"] == "evaluation"
        assert env["payload"]["correct"] is True
        assert fake.calls == 1  # planner не звали: грейд серверный


def test_closed_quiz_wrong_answer_feedback(tmp_path):
    """Неверный ответ: correct=false, в фидбеке назван правильный вариант."""
    fake = QuizThenFail(_CLOSED_QUIZ)
    app = _app(tmp_path, fake)
    with TestClient(app) as c:
        _post(c, "дай задание", "s2")
        r2 = _post(c, "Ответ: 3", "s2")
        env = r2.json()["envelope"]
        assert env["type"] == "evaluation"
        assert env["payload"]["correct"] is False
        assert "4" in r2.json()["reply"]


def test_non_answer_message_still_goes_to_agent(tmp_path):
    """Не-ответ («объясни подробнее») не перехватывается — агент отвечает theory."""
    fake = QuizThenTheory()
    app = _app(tmp_path, fake)
    with TestClient(app) as c:
        r1 = _post(c, "дай задание", "s3")
        assert r1.json()["envelope"]["type"] == "quiz"
        r2 = _post(c, "расскажи подробнее", "s3")
        assert r2.json()["envelope"]["type"] == "theory"
        assert fake.calls == 2


def test_open_quiz_answer_graded_by_llm_grader(tmp_path):
    """Открытый вопрос: серверный LLM-грейдер (fast) с эталоном в промпте."""
    fake = QuizThenOpenGrader()
    app = _app(tmp_path, fake)
    with TestClient(app) as c:
        r1 = _post(c, "дай задание", "s4")
        assert r1.json()["envelope"]["type"] == "quiz"

        r2 = _post(c, "Ответ: это свойство сохранять скорость", "s4")
        assert r2.status_code == 200
        env = r2.json()["envelope"]
        assert env["type"] == "evaluation"
        assert env["payload"]["correct"] is True
        assert fake.calls == 2  # 1-й planner (quiz) + 1 LLM-грейдер


def test_open_quiz_free_form_answer_without_prefix_is_graded(tmp_path):
    """Свободный ответ без «Ответ: » (набран в чате) тоже перехватывается."""
    fake = QuizThenOpenGrader()
    app = _app(tmp_path, fake)
    with TestClient(app) as c:
        r1 = _post(c, "дай задание", "s6")
        assert r1.json()["envelope"]["type"] == "quiz"

        r2 = _post(c, "инерция — это свойство тела сохранять скорость", "s6")
        env = r2.json()["envelope"]
        assert env["type"] == "evaluation"
        assert env["payload"]["correct"] is True
        assert fake.calls == 2


def test_open_quiz_question_to_tutor_goes_to_agent(tmp_path):
    """Встречный вопрос «расскажи подробнее?» не грейдится как ответ — уходит агенту."""
    fake = QuizThenPlannerTheory()
    app = _app(tmp_path, fake)
    with TestClient(app) as c:
        r1 = _post(c, "дай задание", "s7")
        assert r1.json()["envelope"]["type"] == "quiz"

        r2 = _post(c, "а расскажи подробнее, что такое инерция?", "s7")
        assert r2.json()["envelope"]["type"] == "theory"
        assert fake.calls == 2  # quiz -> theory (агент отвечал на вопрос, а не грейдил)


def test_open_quiz_directive_without_question_mark_goes_to_agent(tmp_path):
    """Директива без «?» в конце («Объясни, что такое инерция») тоже уходит агенту."""
    fake = QuizThenPlannerTheory()
    app = _app(tmp_path, fake)
    with TestClient(app) as c:
        r1 = _post(c, "дай задание", "s8")
        assert r1.json()["envelope"]["type"] == "quiz"

        r2 = _post(c, "Объясни, что такое инерция", "s8")
        assert r2.json()["envelope"]["type"] == "theory"
        assert fake.calls == 2


def test_grade_writes_journal_and_clears_last_quiz(tmp_path):
    """Серверный грейд пишет запись журнала и снимает session.last_quiz."""
    fake = QuizThenFail(_CLOSED_QUIZ)
    app = _app(tmp_path, fake)
    with TestClient(app) as c:
        _post(c, "дай задание", "s5")
        _post(c, "Ответ: 4", "s5")
        session = c.app.state.sessions.get("s5")
        assert session.last_quiz is None
    rows = app.state.student_store.list_records("stu_q")
    assert len(rows) == 1
    assert rows[0]["question"] == "Сколько будет 2+2?"
    assert rows[0]["correct"] == 1
    assert rows[0]["student_answer"] == "Ответ: 4"
