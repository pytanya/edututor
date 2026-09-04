"""Тесты review-методов StudentStore (SQLite)."""

import pytest

from src.student.store import StudentStore


@pytest.fixture
def store(tmp_path):
    s = StudentStore(str(tmp_path / "students.db"))
    yield s
    s.close()


def _rec(question="Q?", **kw):
    data = {"question": question, "topic": "t", "subject": "s",
            "answer_type": "single", "options": ["a", "b"], "difficulty": "medium"}
    data.update(kw)
    return data


def test_add_and_dedupe(store):
    assert store.add_review_card("stu_1", _rec(question="Вопрос один?")) is True
    assert store.add_review_card("stu_1", _rec(question="Вопрос один?")) is False
    stats = store.review_stats("stu_1")
    assert stats["total"] == 1
    assert stats["due"] == 1


def test_add_empty_question(store):
    assert store.add_review_card("stu_1", _rec(question="   ")) is False
    assert store.review_stats("stu_1")["total"] == 0


def test_due_filter_by_subject_and_limit(store):
    store.add_review_card("stu_1", _rec(question="A?", subject="физика"))
    store.add_review_card("stu_1", _rec(question="B?", subject="математика"))
    due = store.get_due_review("stu_1", subject="физика")
    assert [c["question"] for c in due] == ["A?"]
    assert store.get_due_review("stu_1", limit=1)  # лимит по всем предметам


def test_review_card_sm2_advances(store):
    store.add_review_card("stu_1", _rec(question="C?"))
    card = store.list_review_cards("stu_1", limit=1)[0]
    updated = store.review_card("stu_1", card["card_id"], True)
    assert updated is not None
    assert updated["reps"] == 1
    assert store.review_stats("stu_1")["due"] == 0


def test_review_unknown_card(store):
    assert store.review_card("stu_1", "nope", True) is None
