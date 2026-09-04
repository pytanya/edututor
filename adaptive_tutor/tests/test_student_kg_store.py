"""Слой 2: миграция колонок и методы StudentStore (EMA/статусы/relations/рекомендации)."""

from types import SimpleNamespace

import pytest

from src.student.store import StudentStore


@pytest.fixture
def store(tmp_path):
    s = StudentStore(str(tmp_path / "students.db"))
    yield s
    s.close()


def _rec(correct: bool, feedback: str = ""):
    return SimpleNamespace(correct=correct, feedback=feedback)


def test_migration_on_old_db(tmp_path):
    """Старая БД без новых колонок: миграция добавляет их и не теряет данные."""
    import sqlite3

    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE topics (student_id TEXT, topic TEXT, level REAL DEFAULT 0.5, "
        "attempts INT DEFAULT 0, correct INT DEFAULT 0, last_seen REAL, "
        "PRIMARY KEY (student_id, topic));"
        "CREATE TABLE sessions (student_id TEXT, session_id TEXT, topic TEXT, "
        "started_at REAL, ended_at REAL, UNIQUE (student_id, session_id));"
    )
    conn.execute("INSERT INTO topics VALUES ('stu_1', 'старая', 0.5, 1, 1, 1.0)")
    conn.commit()
    conn.close()
    s = StudentStore(db)
    topic_cols = {r["name"] for r in s._rows("PRAGMA table_info(topics)")}
    assert {"subject", "mastery", "status", "weak_areas", "relations"} <= topic_cols
    session_cols = {r["name"] for r in s._rows("PRAGMA table_info(sessions)")}
    assert {"subject", "grade"} <= session_cols
    old = s.get_topic("stu_1", "старая")
    assert old["level"] == 0.5 and old["attempts"] == 1
    s.close()


def test_apply_result_ema_and_status(store):
    store.apply_result("stu_1", "тема", "физика", _rec(True, ""))
    store.apply_result("stu_1", "тема", "физика", _rec(True, ""))
    t = store.apply_result("stu_1", "тема", "физика", _rec(True, ""))
    assert t["mastery"] == pytest.approx(0.8285)
    assert t["status"] == "mastered"
    assert t["level"] == t["mastery"]          # key присутствует (level=mastery)


def test_apply_result_weak_areas_cap_and_dedupe(store):
    for fb in ["п1", "п2", "п3", "п1", "п4"]:
        store.apply_result("stu_1", "т", "физика", _rec(False, fb))
    t = store.get_topic("stu_1", "т")
    assert t["weak_areas"] == ["п2", "п3", "п4"]   # без дублей, не более 3, свежие


def test_set_relations_merge_and_gaps(store):
    store.apply_result("stu_1", "B", "м", _rec(True, ""))          # in_progress (1 попытка)
    store.set_relations("stu_1", "B", {"prerequisite": ["A"], "related": ["C"]})
    store.set_relations("stu_1", "B", {"prerequisite": ["A"], "related": ["D"]})
    t = store.get_topic("stu_1", "B")
    assert t["relations"]["prerequisite"] == ["A"]
    assert sorted(t["relations"]["related"]) == ["C", "D"]
    assert store.get_prerequisite_gaps("stu_1", "B") == ["A"]       # A не изучена
    for _ in range(3):
        store.apply_result("stu_1", "A", "м", _rec(True, ""))
    assert store.get_prerequisite_gaps("stu_1", "B") == []          # A освоена


def test_recommend_order_weak_first_then_in_progress_then_not_studied(store):
    store.apply_result("stu_1", "слабая", "физика", _rec(False, "x"))
    store.apply_result("stu_1", "слабая", "физика", _rec(False, "x"))   # acc 0.0/2 -> weak
    store.apply_result("stu_1", "процесс", "физика", _rec(True, ""))
    rec = store.recommend_topics("stu_1", subject="физика", limit=10)
    names = [r["topic"] for r in rec]
    assert names[0] == "слабая"
    assert names.index("слабая") < names.index("процесс")


def test_recommend_includes_missing_prerequisite_gap(store):
    store.apply_result("stu_1", "текущая", "физика", _rec(True, ""))
    store.set_relations("stu_1", "текущая", {"prerequisite": ["новая-тема"]})
    names = [
        r["topic"]
        for r in store.recommend_topics("stu_1", subject="физика", current_topic="текущая")
    ]
    assert "новая-тема" in names


def test_get_mastered_and_weak_filters(store):
    for _ in range(3):
        store.apply_result("stu_1", "освоена", "физика", _rec(True, ""))
    assert [t["topic"] for t in store.get_mastered_topics("stu_1", "физика")] == ["освоена"]
    assert store.get_weak_topics("stu_1", "физика") == []


def test_register_session_with_subject_grade_and_last_session(store):
    store.register_session("stu_1", "ses_1", "тема", subject="физика", grade="7")
    last = store.get_last_session("stu_1")
    assert last is not None
    assert last["subject"] == "физика" and last["grade"] == "7"
    assert store.list_sessions("stu_1")[0]["session_id"] == "ses_1"
    assert store.get_last_session("stu_unknown") is None
