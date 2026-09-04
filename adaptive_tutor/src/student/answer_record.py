"""Answer record — единый «результат ответа» (серверная склейка квиза и оценки).

Источник правды для review-карточек (E1), mastery (E2) и wiki/CSV (E4).
"""

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class AnswerRecord:
    record_id: str
    session_id: str
    student_id: str
    topic: str
    subject: str
    correct: bool
    feedback: str
    question: str = ""
    options: list | None = None
    answer_type: str = "open"
    difficulty: str = "medium"
    correct_answer: str = ""
    student_answer: str = ""
    ts: float = field(default_factory=time.time)


def build_answer_record(
    *,
    session_id: str,
    student_id: str,
    topic: str,
    subject: str,
    student_answer: str,
    correct: bool,
    feedback: str,
    last_quiz: dict | None,
) -> AnswerRecord:
    """Собирает AnswerRecord из секрета последнего квиза и фидбека evaluation."""
    q = last_quiz or {}
    return AnswerRecord(
        record_id=f"rec_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        student_id=student_id,
        topic=topic,
        subject=subject,
        correct=bool(correct),
        feedback=feedback or "",
        question=str(q.get("question") or ""),
        options=q.get("options"),
        answer_type=str(q.get("answer_type") or "open"),
        difficulty=str(q.get("difficulty") or "medium"),
        correct_answer=str(q.get("correct_answer") or ""),
        student_answer=student_answer or "",
    )
