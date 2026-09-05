"""Тесты персистентности LinUCB-бандита (per student+topic)."""

from src.student.store import StudentStore


def test_topic_bandit_default_and_round_trip(tmp_path):
    store = StudentStore(str(tmp_path / "students.db"))
    store.upsert_student("stu_1")
    try:
        fresh = store.get_topic_bandit("stu_1", "квадратные уравнения")
        assert fresh["d"] == 4
        assert fresh["alpha"] == 0.6
        assert len(fresh["arms"]) == 3
        assert fresh["arms"][0]["n"] == 0

        fresh["arms"][1]["n"] = 7
        store.set_topic_bandit("stu_1", "квадратные уравнения", fresh)

        loaded = store.get_topic_bandit("stu_1", "квадратные уравнения")
        assert loaded["arms"][1]["n"] == 7
        # независимо от параметров d/alpha, если состояние уже сохранено
        loaded2 = store.get_topic_bandit("stu_1", "квадратные уравнения", d=2, alpha=9.0)
        assert loaded2["d"] == 4
    finally:
        store.close()


def test_topic_bandit_ignores_corrupt_json(tmp_path):
    store = StudentStore(str(tmp_path / "students.db"))
    store.upsert_student("stu_1")
    try:
        fresh = store.get_topic_bandit("stu_1", "квадратные уравнения")
        store.set_topic_bandit("stu_1", "квадратные уравнения", fresh)
        store._exec(
            "UPDATE topics SET bandit = 'not-json' WHERE student_id = 'stu_1' AND topic = ?",
            ("квадратные уравнения",),
        )
        bandit = store.get_topic_bandit("stu_1", "квадратные уравнения")
        assert bandit["arms"][0]["n"] == 0
    finally:
        store.close()
