"""Тесты answer record и санитайзера."""

from src.agent.envelope import sanitize_envelope
from src.models.schemas import ContentEnvelope
from src.student.answer_record import build_answer_record


def test_record_without_quiz():
    r = build_answer_record(
        session_id="ses_1", student_id="stu_1", topic="t", subject="s",
        student_answer="ответ", correct=False, feedback="нет", last_quiz=None,
    )
    assert r.correct is False
    assert r.question == ""
    assert r.correct_answer == ""
    assert r.record_id.startswith("rec_")
    assert r.ts > 0


def test_record_with_quiz_secret():
    quiz = {"question": "Q?", "options": ["a", "b"], "answer_type": "single",
            "difficulty": "hard", "correct_answer": "a"}
    r = build_answer_record(
        session_id="ses_1", student_id="stu_1", topic="t", subject="s",
        student_answer="Ответ: b", correct=False, feedback="нет", last_quiz=quiz,
    )
    assert r.question == "Q?"
    assert r.options == ["a", "b"]
    assert r.answer_type == "single"
    assert r.correct_answer == "a"


def test_sanitize_removes_secret_keys():
    env = ContentEnvelope(
        type="quiz", text="Q?", payload={"answer_type": "single", "options": ["a", "b"],
                                         "_correct_answer": "a", "correct_answer": "a"},
    )
    out = sanitize_envelope(env)
    assert "_correct_answer" not in out.payload
    assert "correct_answer" not in out.payload
    assert out.payload["options"] == ["a", "b"]
    assert out.text == "Q?"


def test_sanitize_keeps_other_keys():
    env = ContentEnvelope(type="theory", text="hi", payload={"topic": "t"})
    assert sanitize_envelope(env).payload == {"topic": "t"}
