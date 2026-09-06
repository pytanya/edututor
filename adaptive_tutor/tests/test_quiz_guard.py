"""Контроль качества quiz: структурная валидность и «утечка» ответа в вопрос.

Юнит-тесты — на чистых dict-конвертах (без langgraph); серверная интеграция —
через TestClient с фейковым рантаймом (quiz.reject заменяет квиз на theory).
"""

from fastapi.testclient import TestClient

from src.agent.loop import AgentRuntime
from src.agent.quiz_guard import (
    QUIZ_BLOCKED_TEXT,
    QUIZ_REJECT_RETRY_TEXT,
    leak_reasons,
    quiz_problems,
    structural_issues,
)
from src.agent.tools import ToolContext
from src.api.server import create_app
from src.config import settings
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.student.store import StudentStore


def _quiz(**kw):
    payload = {
        "answer_type": "single",
        "options": ["Сила", "Масса", "Энергия", "Импульс"],
        "_correct_answer": "Масса",
    }
    payload.update(kw.get("payload") or {})
    return {
        "type": "quiz",
        "text": kw.get("text", "Что является мерой инертности тела?"),
        "payload": payload,
    }


def test_valid_single_quiz_passes():
    assert quiz_problems(_quiz()) == []


def test_open_quiz_without_options_passes():
    assert quiz_problems(
        {"type": "quiz", "text": "Опишите своими словами, что такое инерция?",
         "payload": {"answer_type": "open"}}
    ) == []


def test_missing_text_rejected():
    assert quiz_problems(_quiz(text="  ")) != []


def test_unknown_answer_type_rejected():
    assert quiz_problems({"type": "quiz", "text": "Вопрос?",
                          "payload": {"answer_type": "checkbox"}}) != []


def test_too_few_options_rejected():
    problems = quiz_problems(_quiz(payload={"options": ["Масса"]}))
    assert any("мало вариантов" in p for p in problems)


def test_duplicate_options_rejected():
    problems = quiz_problems(_quiz(payload={"options": ["Масса", "Масса", "Сила"]}))
    assert any("дублируются" in p for p in problems)


def test_correct_answer_not_among_options_rejected():
    problems = quiz_problems(
        _quiz(payload={"options": ["Сила", "Энергия", "Импульс", "Скорость"],
                       "_correct_answer": "Масса"})
    )
    assert any("эталонный" in p for p in problems)


def test_missing_correct_answer_rejected():
    problems = quiz_problems(_quiz(payload={"_correct_answer": ""}))
    assert any("эталонного" in p for p in problems)


def test_leak_statement_question_flagged():
    """Боевой пример: утверждение «Мерой инертности тела является его масса.»."""
    problems = quiz_problems(
        _quiz(text="Мерой инертности тела является его масса.")
    )
    assert any("раскрыт" in p for p in problems)


def test_leak_latex_formula_flagged():
    """Боевой пример: пример-формула в вопросе == правильный вариант."""
    problems = quiz_problems(
        {
            "type": "quiz",
            "text": "Квадратным уравнением называется уравнение вида "
                    "$ax^2 + bx + c = 0$. Примером является $x^2 - 5x + 6 = 0$.",
            "payload": {
                "answer_type": "single",
                "options": ["$2x + 3 = 0$", "$x^2 - 5x + 6 = 0$",
                            "$3x^3 + 2x^2 - x = 0$", "\\frac{1}{x} + x = 2"],
                "_correct_answer": "$x^2 - 5x + 6 = 0$",
            },
        }
    )
    assert any("раскрыт" in p for p in problems)


def test_question_like_stem_with_term_not_flagged():
    """Стем-вопрос, упоминающий термин варианта, не считается утечкой."""
    assert quiz_problems(
        _quiz(text="Какая физическая величина из перечисленных является мерой инертности?")
    ) == []


def test_question_with_quoted_candidates_not_flagged():
    """«Что верно: A или B?» — дословные варианты в вопросе легитимны."""
    assert quiz_problems(
        {
            "type": "quiz",
            "text": "Какая формула площади прямоугольника верна: "
                    "$S = a \\cdot b$ или $S = 2(a+b)$?",
            "payload": {
                "answer_type": "single",
                "options": ["$S = a \\cdot b$", "$S = 2(a+b)$", "$S = a + b$", "$S = a^b$"],
                "_correct_answer": "$S = a \\cdot b$",
            },
        }
    ) == []


def test_leak_reasons_empty_for_no_options():
    assert leak_reasons("Вопрос без вариантов?", []) == []


# --- Серверная интеграция: quiz.reject заменяет некачественный квиз на theory ---


class _FixedQuizLLM(LLMClient):
    """Планировщик, возвращающий заранее заданный quiz-конверт."""

    def __init__(self, text: str):
        self.text = text

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024,
                   tools=None, tool_choice=None):
        return LLMResponse(
            content=(
                '{"type": "quiz", "text": ' + __import__("json").dumps(self.text) + ', '
                '"payload": {"answer_type": "single", '
                '"options": ["Сила", "Масса", "Энергия", "Импульс"], '
                '"_correct_answer": "Масса"}, "difficulty": "easy"}'
            ),
            model=model,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


def _runtime(leaky: bool):
    text = (
        "Мерой инертности тела является его масса."
        if leaky
        else "Что является мерой инертности тела?"
    )
    return AgentRuntime(
        llm=_FixedQuizLLM(text),
        models={"planner": "test", "fast": "test", "judge": "test"},
        tool_context=ToolContext(region="GLOBAL"),
        critic=None,
    )


def _post(tmp_path, runtime_factory, monkeypatch):
    monkeypatch.setattr(settings, "log_file", str(tmp_path / "tutor.jsonl"))
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(runtime_factory=runtime_factory, student_store=store)
    app.state.rag_engine = None
    app.state.provisioner = None
    with TestClient(app) as c:
        resp = c.post(
            "/chat",
            json={"message": "Задай вопрос", "session_id": "ses_q",
                  "student_id": "stu_q", "topic": "инерция", "subject": "физика"},
        )
        body = resp.json()
        log = (tmp_path / "tutor.jsonl").read_text(encoding="utf-8")
    store.close()
    return body, log


def test_server_rejects_leaky_quiz(monkeypatch, tmp_path):
    body, log = _post(tmp_path, lambda: _runtime(leaky=True), monkeypatch)
    assert body["envelope"]["type"] == "theory"
    assert "контроль качества" in body["reply"]
    assert "quiz.reject" in log
    assert "_correct_answer" not in body["reply"]


def test_server_keeps_valid_quiz(monkeypatch, tmp_path):
    body, log = _post(tmp_path, lambda: _runtime(leaky=False), monkeypatch)
    assert body["envelope"]["type"] == "quiz"
    assert "quiz.reject" not in log


# --- Новые тесты: structural_issues блокирует не-вопрос (баг «LLM генерирует решение») ---


def _struct(text: str, **kw):
    """Помощник: dict-конверт с заданным текстом и стандартным single-payload."""
    payload = {
        "answer_type": "single",
        "options": ["A", "B", "C", "D"],
        "_correct_answer": "B",
    }
    payload.update(kw.get("payload") or {})
    return {"type": "quiz", "text": text, "payload": payload}


def test_structural_issues_rejects_statement_without_question_mark():
    """Текст без «?» и без вопросительного слова — отклоняется."""
    problems = structural_issues(
        "Квадратное уравнение x² − 5x + 6 = 0 имеет два действительных корня.",
        _struct("Квадратное уравнение x² − 5x + 6 = 0 имеет два действительных корня.")["payload"],
    )
    assert any("не является вопросом" in p for p in problems)


def test_structural_issues_allows_text_with_question_mark():
    """Текст с «?» — пропускается."""
    problems = structural_issues(
        "Сколько корней у уравнения $x^2 - 5x + 6 = 0$?",
        _struct("Сколько корней у уравнения $x^2 - 5x + 6 = 0$?")["payload"],
    )
    assert not any("не является вопросом" in p for p in problems)


def test_structural_issues_allows_question_word_starts():
    """Текст с вопросительного слова — пропускается даже без «?»."""
    base_payload = _struct("")["payload"]
    for starter in ("Что", "Какой", "Какая", "Почему", "Сколько", "Укажите", "Назови"):
        problems = structural_issues(starter + " такое уравнение?", base_payload)
        assert not any("не является вопросом" in p for p in problems), \
            f"Ожидалось пропустить начало '{starter}'"


# --- Quiz.reject: streak и нейтральные тексты на сервере (2026-09-06) --------


def _post_msg(client, message, session="ses_q"):
    resp = client.post(
        "/chat",
        json={
            "message": message,
            "session_id": session,
            "student_id": "stu_q",
            "topic": "инерция",
            "subject": "физика",
        },
    )
    assert resp.status_code == 200
    return resp.json()


def test_reject_streak_increments_and_text_is_neutral(monkeypatch, tmp_path):
    """Отклонённый quiz: streak растёт, тексты нейтральные, без loop-фразы."""
    monkeypatch.setattr(settings, "log_file", str(tmp_path / "tutor.jsonl"))
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(runtime_factory=lambda: _runtime(leaky=True), student_store=store)
    app.state.rag_engine = None
    app.state.provisioner = None
    with TestClient(app) as c:
        body1 = _post_msg(c, "давай квиз")
        session = c.app.state.sessions.get("ses_q")
        assert session.quiz_reject_streak == 1
        assert session.quiz_blocked is False
        assert body1["reply"] == QUIZ_REJECT_RETRY_TEXT

        body2 = _post_msg(c, "другой вопрос")
        session = c.app.state.sessions.get("ses_q")
        assert session.quiz_reject_streak == 2
        assert session.quiz_blocked is True
        assert body2["reply"] == QUIZ_REJECT_RETRY_TEXT

        body3 = _post_msg(c, "другой вопрос")
        session = c.app.state.sessions.get("ses_q")
        assert session.quiz_reject_streak == 3
        assert session.quiz_blocked is True
        assert body3["reply"] == QUIZ_BLOCKED_TEXT
        assert "другой вопрос" not in body3["reply"]
        assert "сформулирую его заново" not in body3["reply"]
    store.close()
