"""Миграция wiki: merge-дедуп «общая тема»+предмет и идемпотентность (спека §3.3)."""

from src.config import settings
from src.tools.wiki_normalize import normalize_student
from src.wiki.models import WikiArticle
from src.wiki.store import KnowledgeWiki


def _write(tmp_path, student, subject, topic, attempts=1, correct=1, mastery=0.5, body=""):
    wiki = KnowledgeWiki(tmp_path, student_id=student)
    wiki.upsert(
        WikiArticle(
            subject=subject,
            topic=topic,
            attempts=attempts,
            correct=correct,
            mastery=mastery,
            body=body,
        )
    )


def test_normalize_merges_common_subject_duplicate(tmp_path):
    stu = tmp_path / "stu_m"
    stu.mkdir(parents=True)
    _write(
        tmp_path, "stu_m", "общая тема", "Синтаксис", attempts=2, correct=1, mastery=0.6
    )
    _write(
        tmp_path,
        "stu_m",
        "Русский язык",
        "Синтаксис",
        attempts=3,
        correct=2,
        mastery=0.7,
        body="Конспект по синтаксису.",
    )
    stats: dict[str, int] = {}
    normalize_student(stu, stats)
    wiki = KnowledgeWiki(tmp_path, student_id="stu_m")
    art = wiki.get("Русский язык", "Синтаксис")
    assert art is not None
    assert art.attempts == 5
    assert art.correct == 3
    assert art.mastery == 0.7
    assert "Конспект по синтаксису." in art.body
    assert wiki.get("общая тема", "Синтаксис") is None
    assert not (tmp_path / "общая-тема").exists()


def test_normalize_keeps_same_topic_in_different_subjects(tmp_path):
    stu = tmp_path / "stu_x"
    stu.mkdir(parents=True)
    _write(tmp_path, "stu_x", "Алгебра", "Системы уравнений")
    _write(tmp_path, "stu_x", "Физика", "Системы уравнений")
    stats: dict[str, int] = {}
    normalize_student(stu, stats)
    wiki = KnowledgeWiki(tmp_path, student_id="stu_x")
    assert wiki.get("Алгебра", "Системы уравнений") is not None
    assert wiki.get("Физика", "Системы уравнений") is not None


def test_normalize_moves_article_to_canonical_alias_subject(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings,
        "subject_aliases",
        {"вероятность и математическая статистика": "вероятность и статистика"},
    )
    stu = tmp_path / "stu_a"
    stu.mkdir(parents=True)
    _write(tmp_path, "stu_a", "вероятность и математическая статистика", "Диаграммы")
    stats: dict[str, int] = {}
    normalize_student(stu, stats)
    wiki = KnowledgeWiki(tmp_path, student_id="stu_a")
    assert wiki.get("вероятность и статистика", "Диаграммы") is not None
    assert not (tmp_path / "вероятность-и-математическая-статистика").exists()


def test_normalize_is_idempotent(tmp_path):
    stu = tmp_path / "stu_i"
    stu.mkdir(parents=True)
    _write(tmp_path, "stu_i", "общая тема", "Тема", attempts=2, correct=1, mastery=0.6)
    _write(tmp_path, "stu_i", "История", "Тема", attempts=1, correct=1, mastery=0.8)
    first: dict[str, int] = {}
    normalize_student(stu, first)
    second: dict[str, int] = {}
    normalize_student(stu, second)
    assert second["deleted"] == 0
    wiki = KnowledgeWiki(tmp_path, student_id="stu_i")
    art = wiki.get("История", "Тема")
    assert art is not None
    assert art.attempts == 3
    assert art.correct == 2