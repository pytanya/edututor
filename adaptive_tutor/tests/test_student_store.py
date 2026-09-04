"""Тесты SQLite-хранилища профилей учеников."""

import time

import pytest

from src.student.store import StudentStore


@pytest.fixture
def store(tmp_path):
    s = StudentStore(str(tmp_path / "students.db"))
    yield s
    s.close()


def test_upsert_and_get_student(store):
    store.upsert_student("stu_1")
    row = store.get_student("stu_1")
    assert row is not None and row["student_id"] == "stu_1"
    assert store.get_student("stu_x") is None


def test_touch_topic_updates_level_and_attempts(store):
    store.touch_topic("stu_1", "интегралы", level_delta=0.3, correct=True)
    store.touch_topic("stu_1", "интегралы", level_delta=-0.1, correct=False)
    topics = store.list_topics("stu_1")
    assert len(topics) == 1
    assert topics[0]["attempts"] == 2
    assert topics[0]["correct"] == 1
    assert topics[0]["level"] == pytest.approx(0.7, abs=1e-6)


def test_topic_level_default_and_after_update(store):
    assert store.get_topic_level("stu_1", "нет темы") == 0.5
    store.touch_topic("stu_1", "производные", level_delta=0.5, correct=True)
    assert store.get_topic_level("stu_1", "производные") == pytest.approx(1.0)


def test_register_session(store):
    store.upsert_student("stu_1")
    store.register_session("stu_1", "ses_1", "тема")
    store.register_session("stu_1", "ses_1", "тема")  # idempotent
    rows = store.list_topics("stu_1")
    assert rows == []


def test_register_session_upsert_updates_topic_and_keeps_started_at(store):
    """Повторный register_session обновляет topic/ended_at, но не started_at."""
    store.upsert_student("stu_1")
    store.register_session("stu_1", "ses_1", "тема первая")
    first = store.list_sessions("stu_1")[0]
    time.sleep(0.02)
    store.register_session("stu_1", "ses_1", "тема вторая")
    rows = store.list_sessions("stu_1")
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "ses_1"
    assert row["topic"] == "тема вторая"
    assert row["started_at"] == first["started_at"]
    assert row["ended_at"] >= first["ended_at"]
    assert row["ended_at"] >= row["started_at"]


def test_list_sessions_orders_desc_and_limits(store):
    """list_sessions сортирует по started_at DESC и уважает limit."""
    store.upsert_student("stu_1")
    store.register_session("stu_1", "ses_1", "тема 1")
    time.sleep(0.02)
    store.register_session("stu_1", "ses_2", "тема 2")
    time.sleep(0.02)
    store.register_session("stu_1", "ses_3", "тема 3")
    rows = store.list_sessions("stu_1")
    assert [r["session_id"] for r in rows] == ["ses_3", "ses_2", "ses_1"]
    assert set(rows[0]) == {"session_id", "topic", "started_at", "ended_at"}
    limited = store.list_sessions("stu_1", limit=2)
    assert [r["session_id"] for r in limited] == ["ses_3", "ses_2"]
    assert store.list_sessions("stu_unknown") == []
    assert store.list_sessions("") == []


def test_migration_adds_profile_columns_and_is_idempotent(tmp_path):
    """Миграция добавляет learner_type/grade и не падает на повторном открытии."""
    import sqlite3

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE students (student_id TEXT PRIMARY KEY, "
        "name TEXT DEFAULT '', created_at REAL, updated_at REAL)"
    )
    conn.execute(
        "INSERT INTO students (student_id, name, created_at, updated_at) "
        "VALUES ('stu_old', '', 1, 1)"
    )
    conn.commit()
    conn.close()

    s1 = StudentStore(path)
    columns = {r["name"] for r in s1._rows("PRAGMA table_info(students)")}
    assert {"learner_type", "grade"} <= columns
    row = s1.get_student("stu_old")
    assert row["learner_type"] == "" and row["grade"] == ""
    s1.close()

    s2 = StudentStore(path)  # повторное открытие — миграция идемпотентна
    s2.close()


def test_set_profile_upserts_and_preserves_on_none(store):
    profile = store.set_profile(
        "stu_p1", name="Иван Иванов", learner_type="schoolchild", grade="8 класс"
    )
    assert profile == {
        "student_id": "stu_p1",
        "name": "Иван Иванов",
        "learner_type": "schoolchild",
        "grade": "8 класс",
    }
    # None-аргумент не затирает сохранённое
    after = store.set_profile("stu_p1", grade="9 класс")
    assert after["name"] == "Иван Иванов"
    assert after["learner_type"] == "schoolchild"
    assert after["grade"] == "9 класс"
    row = store.get_student("stu_p1")
    assert row["name"] == "Иван Иванов"
    assert row["learner_type"] == "schoolchild"
    assert row["grade"] == "9 класс"


def test_set_profile_new_id_is_upsert_and_get_student_returns_profile(store):
    store.set_profile("stu_new", name="Петя Петров", learner_type="student", grade="")
    row = store.get_student("stu_new")
    assert row is not None
    assert row["learner_type"] == "student"
    assert store.get_student("stu_missing") is None
