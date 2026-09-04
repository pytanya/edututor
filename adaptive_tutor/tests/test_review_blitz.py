"""Блиц повторений: запуск через kind=review_request, ответы грейдятся без агента."""

import json

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.student.store import StudentStore


class _NoAgent:
    """Runtime, который падает при вызове — агент не должен вызываться в review."""

    def __call__(self):
        raise AssertionError("агент вызван в review-режиме")


@pytest.fixture
def client_with_cards(tmp_path):
    store = StudentStore(str(tmp_path / "s.db"))
    store.add_review_card("stu_1", {"question": "2+2?", "topic": "арифметика",
                                    "subject": "математика", "options": ["3", "4", "5"],
                                    "answer_type": "single", "correct_answer": "4"})
    app = create_app(runtime_factory=_NoAgent(), student_store=store)
    yield TestClient(app), store
    store.close()


def _sse_events(text: str) -> list[tuple[str, dict]]:
    """Разбирает SSE-текст на (event, data)-фреймы."""
    events = []
    for block in text.split("\n\n"):
        event = None
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if data_lines:
            events.append((event or "message", json.loads("\n".join(data_lines))))
    return events


def test_review_request_serves_card_and_grade(client_with_cards):
    client, store = client_with_cards
    # Оба хода идут в ОДНУ сессию: review-состояние (очередь карточек) живёт на
    # сессии, как в обычном чате.
    start = {"message": "", "kind": "review_request", "session_id": "ses_blitz",
             "student_id": "stu_1", "topic": "арифметика", "subject": "математика"}
    answer = {"message": "Ответ: 4", "kind": "message", "session_id": "ses_blitz",
              "student_id": "stu_1", "topic": "арифметика", "subject": "математика"}

    # старт блица (первый ход): отдаётся 1 карточка quiz без вызова агента
    r = client.post("/chat/stream", json=start)
    msg_events = [ev for ev in _sse_events(r.text) if ev[0] == "message"]
    assert msg_events, "ожидался минимум один message-фрейм"
    first = msg_events[0][1]
    assert first["envelope"]["type"] == "quiz"
    assert "2+2?" in first["content"]
    assert first["envelope"]["payload"]["review"] is True
    assert store.review_stats("stu_1")["due"] == 1

    # ответ верный (детерминированно) -> карточка уходит из due
    client.post("/chat/stream", json=answer)
    assert store.review_stats("stu_1")["due"] == 0


def test_review_answer_is_journaled(client_with_cards):
    """Каждый оценённый review-ответ пишется в session_records (review:<card_id>)."""
    client, store = client_with_cards
    start = {"message": "", "kind": "review_request", "session_id": "ses_log",
             "student_id": "stu_1", "topic": "арифметика", "subject": "математика"}
    answer = {"message": "Ответ: 4", "kind": "message", "session_id": "ses_log",
              "student_id": "stu_1", "topic": "арифметика", "subject": "математика"}
    client.post("/chat/stream", json=start)
    client.post("/chat/stream", json=answer)

    rows = store.list_records("stu_1", session_id="ses_log")
    assert len(rows) == 1
    row = rows[0]
    assert row["record_id"].startswith("rec_")
    assert row["question_id"].startswith("review:")
    assert row["question"] == "2+2?"
    assert row["topic"] == "арифметика"
    assert row["options"] == ["3", "4", "5"]
    assert row["correct"] == 1


def test_review_wrong_answer_updates_wiki_article(client_with_cards, tmp_path):
    """E4 §2.4: неверный review-ответ создаёт/обновляет статью ученика (attempts, note)."""
    from src.wiki.store import KnowledgeWiki

    client, store = client_with_cards
    start = {"message": "", "kind": "review_request", "session_id": "ses_wiki",
             "student_id": "stu_1", "topic": "арифметика", "subject": "математика"}
    answer = {"message": "Ответ: 3", "kind": "message", "session_id": "ses_wiki",
              "student_id": "stu_1", "topic": "арифметика", "subject": "математика"}
    client.post("/chat/stream", json=start)
    client.post("/chat/stream", json=answer)

    art = KnowledgeWiki(str(tmp_path / "wiki"), student_id="stu_1").get(
        "математика", "арифметика"
    )
    assert art is not None
    assert art.attempts == 1
    assert art.correct == 0
    assert art.mastery == pytest.approx(0.5 * 0.7)
    assert len(art.notes) == 1
    assert art.notes[0].question == "2+2?"
    assert art.notes[0].student_answer == "Ответ: 3"
