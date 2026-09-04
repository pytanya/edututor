"""Эндпоинт GET /student/{id}/review: статистика + due-карточки."""

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.student.store import StudentStore


class _NoAgent:
    """Runtime, который падает при вызове — агент не должен вызываться."""

    def __call__(self):
        raise AssertionError("агент вызван в review-режиме")


@pytest.fixture
def client_empty_bank(tmp_path):
    store = StudentStore(str(tmp_path / "s.db"))
    app = create_app(runtime_factory=_NoAgent(), student_store=store)
    yield TestClient(app), store
    store.close()


def _seed_one_due_card(store: StudentStore) -> None:
    store.add_review_card("stu_1", {"question": "2+2?", "topic": "арифметика",
                                    "subject": "математика", "options": ["3", "4", "5"],
                                    "answer_type": "single", "correct_answer": "4"})


def test_review_endpoint_empty_bank(client_empty_bank):
    """Пустой банк: fallback-форма (200), total == 0, due == []."""
    client, _store = client_empty_bank
    r = client.get("/student/stu_1/review")
    assert r.status_code == 200
    body = r.json()
    assert body["stats"] == {"total": 0, "due": 0, "lapses": 0, "by_topic": {}}
    assert body["due"] == []


def test_review_endpoint_filled(client_empty_bank):
    """После сидинга одной due-карточки — stats.due == 1 и карточка в due."""
    client, store = client_empty_bank
    _seed_one_due_card(store)
    r = client.get("/student/stu_1/review")
    assert r.status_code == 200
    body = r.json()
    assert body["stats"]["total"] == 1
    assert body["stats"]["due"] == 1
    assert len(body["due"]) == 1
    card = body["due"][0]
    assert card["card_id"]
    assert card["question"] == "2+2?"
