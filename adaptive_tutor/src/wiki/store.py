"""Knowledge Wiki: персистентные OKF-статьи (файлы markdown + YAML-frontmatter)."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any

import yaml

from ..config import settings
from .models import WikiArticle, now_iso

_INDEX_NAME = "_index.md"
_DEFAULT_BODY = "Материал по теме «{title}» накапливается по мере прохождения квизов."


def _dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def slug(text: str) -> str:
    """Имя файла/каталога из названия (спека §2.1): unicode-safe slug."""
    out: list[str] = []
    for ch in (text or "").lower():
        if ch.isalnum() or ch in "-_.":
            out.append(ch)
        elif ch in " /\\":
            out.append("-")
    s = "".join(out).strip("-")
    return s or "topic"


class KnowledgeWiki:
    """Персональные статьи ученика: <root>/<student_id>/<slug(subject)>/<slug(topic)>.md.

    Плюс индекс предмета _index.md. Файлы — источник истины (не SQLite),
    читаемые/экспортируемые как OKF. Битый файл -> get() None (не удаляем).
    """

    def __init__(self, root_dir: str | os.PathLike | None = None, student_id: str = "") -> None:
        self.base = Path(root_dir or settings.resolved_knowledge_wiki_dir)
        self.student_id = (student_id or "").strip()
        self.root = self.base / self.student_id if self.student_id else self.base
        self.root.mkdir(parents=True, exist_ok=True)

    def for_student(self, student_id: str) -> KnowledgeWiki:
        return KnowledgeWiki(self.base, student_id=student_id)

    def subject_dir(self, subject: str) -> Path:
        d = self.root / slug(subject)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def article_path(self, subject: str, topic: str) -> Path:
        return self.subject_dir(subject) / f"{slug(topic)}.md"

    def get(self, subject: str, topic: str) -> WikiArticle | None:
        p = self.article_path(subject, topic)
        if not p.exists():
            return None
        return self._read_file(p, subject, topic)

    def _read_file(self, path: Path, subject: str, topic: str) -> WikiArticle | None:
        try:
            text = path.read_text(encoding="utf-8")
            parts = text.split("---", 2)
            if len(parts) < 3:
                return None
            data = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()
            lines = body.splitlines()
            if lines and lines[0].startswith("#"):
                body = "\n".join(lines[1:]).strip()
            real_topic = str(data.get("topic") or topic)
            return WikiArticle.from_dict(subject, real_topic, {**data, "body": body})
        except Exception:
            return None  # битый файл: молча, файл не удаляем

    def list_subjects(self) -> list[str]:
        """Slug-и каталогов предметов, где есть статьи (не только _index)."""
        out: list[str] = []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir():
                continue
            if any(f.suffix == ".md" and f.name != _INDEX_NAME for f in d.iterdir()):
                out.append(d.name)
        return out

    def list_articles(self, subject: str | None = None) -> list[WikiArticle]:
        articles: list[WikiArticle] = []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir():
                continue
            if subject is not None and d.name != slug(subject):
                continue
            for f in sorted(d.glob("*.md")):
                if f.name == _INDEX_NAME:
                    continue
                art = self._read_file(f, d.name, f.stem)
                if art is not None:
                    articles.append(art)
        return articles

    def article_by_slug(self, subject_slug: str, topic_slug: str) -> WikiArticle | None:
        """Поиск статьи по slug-сегментам URL (human-имя — из frontmatter)."""
        for art in self.list_articles():
            if slug(art.subject) == subject_slug and slug(art.topic) == topic_slug:
                return art
        return None

    def upsert(self, article: WikiArticle) -> Path:
        p = self.article_path(article.subject, article.topic)
        p.write_text(article.to_markdown(), encoding="utf-8")
        self._write_index(article.subject)
        return p

    def _write_index(self, subject: str) -> None:
        articles = [a for a in self.list_articles(subject)]
        index = self.subject_dir(subject) / _INDEX_NAME
        if not articles:
            with contextlib.suppress(Exception):
                index.unlink(missing_ok=True)
            return
        lines = [f"# Предмет «{subject}»\n", "Темы и текущее мастерство:\n"]
        for a in sorted(articles, key=lambda x: -x.mastery):
            pct = int(round(a.mastery * 100))
            lines.append(
                f"- [{a.title}]({slug(a.topic)}.md) — мастерство {pct}% (попыток: {a.attempts})"
            )
        meta = {
            "okf_version": "0.2",
            "type": "Index",
            "title": f"Предмет «{subject}»",
            "subject": subject,
            "last_studied": now_iso(),
        }
        index.write_text(
            "---\n" + _dump_yaml(meta) + "---\n" + "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    def delete(self, subject: str, topic: str) -> bool:
        """Удаляет статью ученика; True если файл был. Пересоздаёт индекс."""
        p = self.article_path(subject, topic)
        if not p.exists():
            return False
        try:
            p.unlink()
        except Exception:
            return False
        with contextlib.suppress(Exception):
            self._write_index(subject)
        return True

    def apply_record(
        self,
        record: Any,
        subject: str = "",
        grade: str = "",
        curriculum: str = "",
    ) -> WikiArticle | None:
        """Применить один оценённый ответ (answer record) к статье.

        record — E1 AnswerRecord или dict с ключами topic/score01/correct/
        feedback/question/student_answer/correct_answer. Нет topic или score01/
        correct == None -> None (статья не создаётся без ответа).
        subject/grade/curriculum — контекст запроса (заполняется при создании).
        """
        if record is None:
            return None
        r = record.to_dict() if hasattr(record, "to_dict") else dict(record)
        topic = r.get("topic") or ""
        if not topic:
            return None
        score = r.get("score01")
        correct = r.get("correct")
        if score is None or correct is None:
            return None
        human_subject = subject or r.get("subject") or "общая тема"
        art = self.get(human_subject, topic)
        if art is None:
            art = WikiArticle(
                subject=human_subject,
                topic=topic,
                title=topic,
                grade=grade or "",
                curriculum=curriculum or "",
            )
        art.apply_result(
            float(score),
            bool(correct),
            r.get("feedback") or "",
            question=r.get("question"),
            student_answer=r.get("student_answer"),
            correct_answer=r.get("correct_answer"),
        )
        self.upsert(art)
        return art

    def sync_mastery(self, subject: str, topic_updates: dict[str, float]) -> list[WikiArticle]:
        """Идемпотентная синхронизация mastery по topic_updates {topic: mastery}
        (без attempts/correct). Статья-оболочка создаётся при отсутствии."""
        updated: list[WikiArticle] = []
        for topic, mastery in (topic_updates or {}).items():
            if not topic:
                continue
            art = self.get(subject, topic)
            if art is None:
                art = WikiArticle(subject=subject, topic=topic, title=topic)
            art.mastery = round(float(mastery), 4)
            art.last_studied = now_iso()
            self.upsert(art)
            updated.append(art)
        return updated

    def to_summary_dict(self) -> list[dict[str, Any]]:
        """[{subject: <human>, articles: [article_dict...]}] для GET /wiki."""
        groups: dict[str, list[dict[str, Any]]] = {}
        for art in self.list_articles():
            groups.setdefault(art.subject, []).append(art.to_dict())
        return [
            {"subject": subject, "articles": articles}
            for subject, articles in sorted(groups.items())
        ]
