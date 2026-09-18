"""Канонизация предмета и выведение предмета по теме (спека §3.1)."""

from src.config import settings
from src.wiki.models import WikiArticle
from src.wiki.store import KnowledgeWiki, canonicalize


def test_canonicalize_trims_and_collapses_spaces():
    assert canonicalize("  Математика   ") == "Математика"
    assert canonicalize("Математика.") == "Математика"
    assert canonicalize("") == ""
    assert canonicalize(None) == ""


def test_canonicalize_default_obshchaya_tema_is_empty():
    assert canonicalize("общая тема") == ""
    assert canonicalize("Общая Тема") == ""


def test_canonicalize_uses_alias_map(monkeypatch):
    monkeypatch.setattr(
        settings,
        "subject_aliases",
        {
            "общая тема": "",
            "вероятность и математическая статистика": "вероятность и статистика",
            "Русский Язык": "Русский язык",
        },
    )
    assert canonicalize("Общая Тема") == ""
    assert canonicalize("  вероятность и математическая статистика. ") == (
        "вероятность и статистика"
    )
    assert canonicalize("русский язык") == "Русский язык"


def test_resolve_subject_infer_and_fallback(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    wiki.upsert(WikiArticle(subject="История", topic="Крымская война"))
    assert wiki.resolve_subject("", "Крымская война") == "История"
    assert wiki.resolve_subject("", "Неизвестная тема") == "общая тема"
    assert wiki.resolve_subject("История", "Крымская война") == "История"


def test_apply_record_infers_subject_from_existing_topic(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    wiki.upsert(WikiArticle(subject="История", topic="Крымская война"))
    art = wiki.apply_record(
        {"topic": "Крымская война", "score01": 1.0, "correct": True, "feedback": "Верно"},
        subject="",
    )
    assert art is not None
    assert art.subject == "История"


def test_apply_record_rejects_obshchaya_tema_when_match_exists(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    wiki.upsert(WikiArticle(subject="История", topic="Крымская война"))
    art = wiki.apply_record(
        {"topic": "Крымская война", "score01": 0.0, "correct": False, "feedback": "Ошибка"},
        subject="общая тема",
    )
    assert art is not None
    assert art.subject == "История"