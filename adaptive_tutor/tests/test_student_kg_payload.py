"""Чистые payload-хелперы Слоя 2 (E2 Task 9): точная форма §5 без sqlite."""

from src.student.student_kg import (
    accuracy,
    knowledge_graph_payload,
    row_payload,
    stats_of,
)


def _row(topic, status, mastery, attempts, correct, last_seen=1.0):
    return {
        "topic": topic,
        "subject": "ф",
        "status": status,
        "mastery": mastery,
        "attempts": attempts,
        "correct": correct,
        "weak_areas": [],
        "relations": {"prerequisite": [], "related": []},
        "last_seen": last_seen,
    }


def test_accuracy():
    assert accuracy(3, 3) == 1.0
    assert accuracy(2, 1) == 0.5
    assert accuracy(0, 0) == 0.0


def test_payload_stats():
    rows = [
        _row("a", "mastered", 0.9, 3, 3),
        _row("b", "in_progress", 0.5, 1, 1),
    ]
    out = knowledge_graph_payload("stu", "ф", rows)
    assert out["topics"]["a"]["accuracy"] == 1.0
    assert out["stats"] == {"mastered": 1, "in_progress": 1, "not_studied": 0, "total": 2}


def test_payload_mastered_by_attempts_mastery_even_when_status_in_progress():
    """stats.mastered считается по is_mastered, а не только по status-строке."""
    rows = [
        _row("a", "in_progress", 0.8285, 3, 3),
        _row("b", "not_studied", 0.0, 0, 0),
    ]
    out = knowledge_graph_payload("stu", "ф", rows)
    assert out["stats"] == {"mastered": 1, "in_progress": 1, "not_studied": 1, "total": 2}


def test_payload_empty_stats_zero():
    out = knowledge_graph_payload("stu", "", [])
    assert out["topics"] == {}
    assert out["stats"] == {"mastered": 0, "in_progress": 0, "not_studied": 0, "total": 0}


def test_row_payload_exact_keys_and_defaults():
    p = row_payload({"topic": "x", "subject": "ф", "attempts": 0, "correct": 0})
    assert set(p) == {
        "topic", "subject", "status", "mastery", "attempts", "correct",
        "accuracy", "weak_areas", "last_seen", "relations",
    }
    assert p["status"] == "not_studied"
    assert p["accuracy"] == 0.0
    assert p["weak_areas"] == []
    assert p["relations"] == {"prerequisite": [], "related": []}
    assert p["last_seen"] is None


def test_stats_of_raw_rows():
    rows = [
        {"status": "mastered", "attempts": 3, "mastery": 0.9},
        {"status": "not_studied", "attempts": 0, "mastery": 0.0},
    ]
    assert stats_of(rows) == {"mastered": 1, "in_progress": 0, "not_studied": 1, "total": 2}
