"""Одноразовая нормализация wiki: канонизация предмета и merge-дедуп дублей.

Запуск: python -m adaptive_tutor.tools.wiki_normalize
Идемпотентно: повторный прогон не меняет уже нормализованные данные.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import settings
from ..wiki.enrich import is_stub_body
from ..wiki.models import WikiArticle
from ..wiki.store import _INDEX_NAME, KnowledgeWiki


def _dedup_str(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        s = str(it or "").strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def _merge_notes(articles: list[WikiArticle]) -> list:
    merged: list = []
    seen: set[str] = set()
    for art in articles:
        for note in art.notes:
            key = note.feedback[:180]
            if key in seen:
                continue
            seen.add(key)
            merged.append(note)
    return merged[-WikiArticle.MAX_NOTES :]


def _merge_group(key: tuple[str, str], articles: list[WikiArticle]) -> WikiArticle:
    subject, topic = key
    base = max(articles, key=lambda a: (a.attempts, len(a.body or "")))
    bodies = [a.body or "" for a in articles if a.body and not is_stub_body(a.body)]
    body = max(bodies, key=len, default="")
    return WikiArticle(
        subject=subject,
        topic=topic,
        title=base.title or topic,
        grade=max((a.grade for a in articles if a.grade), key=len, default=""),
        curriculum=max(
            (a.curriculum for a in articles if a.curriculum), key=len, default=""
        ),
        mastery=max(a.mastery for a in articles),
        attempts=sum(a.attempts for a in articles),
        correct=sum(a.correct for a in articles),
        last_studied=max((a.last_studied for a in articles), default=""),
        body=body,
        weak_areas=_dedup_str([w for a in articles for w in a.weak_areas]),
        notes=_merge_notes(articles),
        concepts=_dedup_str([c for a in articles for c in a.concepts]),
        source=next((a.source for a in articles if a.source), ""),
        section_number=next(
            (a.section_number for a in articles if a.section_number), None
        ),
    )


def normalize_student(student_dir: Path, stats: dict[str, int]) -> None:
    """Нормализует статьи одного ученика (merge-дедуп + перемещение в канон)."""
    for k in ("articles", "groups", "deleted", "subjects_removed"):
        stats.setdefault(k, 0)
    wiki = KnowledgeWiki(student_dir.parent, student_id=student_dir.name)
    groups: dict[tuple[str, str], list[tuple[Path, WikiArticle]]] = {}
    for subj_dir in sorted(p for p in student_dir.iterdir() if p.is_dir()):
        for f in sorted(subj_dir.glob("*.md")):
            if f.name == _INDEX_NAME:
                continue
            art = wiki._read_file(f, subj_dir.name, f.stem)
            if art is None:
                continue
            stats["articles"] += 1
            subject = wiki.resolve_subject(art.subject, art.topic)
            groups.setdefault((subject, art.topic), []).append((f, art))
    kept_subjects: set[str] = set()
    for key, items in groups.items():
        subject, topic = key
        target = wiki.article_path(subject, topic)
        for f, _ in items:
            if f.resolve() == target.resolve():
                continue
            f.unlink(missing_ok=True)
            stats["deleted"] += 1
        merged = _merge_group(key, [a for _, a in items])
        wiki.upsert(merged)
        kept_subjects.add(subject)
        stats["groups"] += 1
    for subj_dir in sorted(p for p in student_dir.iterdir() if p.is_dir()):
        articles = [
            x for x in subj_dir.iterdir()
            if x.suffix == ".md" and x.name != _INDEX_NAME
        ]
        if articles:
            continue
        (subj_dir / _INDEX_NAME).unlink(missing_ok=True)
        subj_dir.rmdir()
        stats["subjects_removed"] += 1
    for subject in kept_subjects:
        wiki._write_index(subject)


def normalize_root(root_dir: str | os.PathLike) -> dict[str, int]:
    root = Path(root_dir)
    stats = {
        "articles": 0,
        "groups": 0,
        "deleted": 0,
        "subjects_removed": 0,
    }
    for student_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        normalize_student(student_dir, stats)
    return stats


def main() -> None:
    root = Path(settings.resolved_knowledge_wiki_dir)
    if not root.exists():
        print("Нет каталога wiki, выходим.")
        return
    print(f"Готово: {normalize_root(root)}")


if __name__ == "__main__":
    main()