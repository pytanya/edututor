"""Модели Knowledge Wiki: заметка об ошибке и статья (OKF v0.2)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

import yaml


def now_iso() -> str:
    """Текущее время ISO (секунды, naive)."""
    return datetime.datetime.now().isoformat(timespec="seconds")


def _dump_yaml(data: dict[str, Any]) -> str:
    """Локальный дамп frontmatter (allow_unicode, порядок ключей сохраняется)."""
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


@dataclass
class WikiNote:
    """Заметка об ошибке: фидбек + контекст ответа (дата из last_studied)."""

    date: str
    feedback: str
    question: str | None = None
    student_answer: str | None = None
    correct_answer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Словарь для frontmatter (порядок: date, feedback, ..., None опускаются)."""
        out: dict[str, Any] = {"date": self.date, "feedback": self.feedback}
        if self.question:
            out["question"] = self.question
        if self.student_answer:
            out["student_answer"] = self.student_answer
        if self.correct_answer:
            out["correct_answer"] = self.correct_answer
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WikiNote:
        return cls(
            date=str(data.get("date") or ""),
            feedback=str(data.get("feedback") or ""),
            question=data.get("question"),
            student_answer=data.get("student_answer"),
            correct_answer=data.get("correct_answer"),
        )


def _as_note(n: WikiNote | dict[str, Any] | str) -> WikiNote:
    """Нормализация заметки: WikiNote/dict/строка "{дата}: {фидбек}" -> WikiNote."""
    if isinstance(n, WikiNote):
        return n
    if isinstance(n, dict):
        return WikiNote.from_dict(n)
    date, _, fb = n.partition(": ")
    return WikiNote(date=date, feedback=fb or n)


@dataclass
class WikiArticle:
    """Статья по теме: frontmatter OKF v0.2 + markdown-тело «конспект».

    accuracy — производное (correct/attempts), в yaml пишется, но как вход
    не принимается (пересчитывается при чтении).
    """

    subject: str
    topic: str
    title: str | None = None
    grade: str = ""
    curriculum: str = ""
    mastery: float = 0.5
    attempts: int = 0
    correct: int = 0
    last_studied: str = ""
    body: str = ""
    section_number: str | None = None
    weak_areas: list[str] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    notes: list[WikiNote] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    source: str = ""
    okf_version: str = "0.2"

    MAX_NOTES = 10

    def __post_init__(self) -> None:
        if not self.title:
            self.title = self.topic
        if not self.last_studied:
            self.last_studied = now_iso()
        self.notes = [_as_note(n) for n in self.notes]

    @property
    def accuracy(self) -> float:
        return round(self.correct / self.attempts, 4) if self.attempts else 0.0

    def frontmatter(self) -> dict[str, Any]:
        """Ключи в порядке спеки §2.2; accuracy — производная, пишется в файл."""
        data: dict[str, Any] = {
            "okf_version": self.okf_version,
            "type": "Topic",
            "title": self.title,
            "topic": self.topic,
            "subject": self.subject,
            "grade": self.grade or "",
            "curriculum": self.curriculum or "",
            "mastery": round(self.mastery, 4),
            "accuracy": self.accuracy,
            "attempts": self.attempts,
            "correct": self.correct,
            "last_studied": self.last_studied,
        }
        if self.section_number:
            data["section_number"] = self.section_number
        if self.weak_areas:
            data["weak_areas"] = self.weak_areas
        if self.relations:
            data["relations"] = self.relations
        if self.notes:
            data["notes"] = [n.to_dict() for n in self.notes]
        if self.concepts:
            data["concepts"] = self.concepts
        if self.source:
            data["source"] = self.source
        return data

    def to_dict(self) -> dict[str, Any]:
        """article_dict API: frontmatter + body (и accuracy внутри)."""
        out = self.frontmatter()
        out["body"] = self.body or ""
        return out

    def to_markdown(self) -> str:
        """OKF-файл: frontmatter + заголовок # title + тело."""
        front = "---\n" + _dump_yaml(self.frontmatter()) + "---\n"
        body = self.body.strip()
        if not body:
            body = f"Материал по теме «{self.title}» накапливается по мере прохождения квизов."
        return f"{front}# {self.title}\n\n{body}\n"

    @classmethod
    def from_dict(cls, subject: str, topic: str, data: dict[str, Any]) -> WikiArticle:
        """Восстановление из frontmatter (+body). human subject — из данных."""
        raw_notes = data.get("notes") or []
        notes = []
        for n in raw_notes:
            if isinstance(n, dict):
                notes.append(WikiNote.from_dict(n))
            elif isinstance(n, WikiNote):
                notes.append(n)
            elif isinstance(n, str):
                # legacy "{дата}: {фидбек}" — на практике не пишем, но читаем терпимо
                date, _, fb = n.partition(": ")
                notes.append(WikiNote(date=date, feedback=fb or n))
        return cls(
            subject=str(data.get("subject") or subject),
            topic=topic,
            title=data.get("title"),
            grade=data.get("grade") or "",
            curriculum=data.get("curriculum") or "",
            mastery=float(data.get("mastery", 0.5)),
            attempts=int(data.get("attempts", 0)),
            correct=int(data.get("correct", 0)),
            last_studied=str(data.get("last_studied") or now_iso()),
            weak_areas=list(data.get("weak_areas") or []),
            relations=list(data.get("relations") or []),
            notes=notes,
            concepts=list(data.get("concepts") or []),
            source=str(data.get("source") or ""),
            section_number=data.get("section_number"),
            body=str(data.get("body") or ""),
        )

    def apply_result(
        self,
        score01: float,
        correct: bool,
        feedback: str = "",
        question: str | None = None,
        student_answer: str | None = None,
        correct_answer: str | None = None,
    ) -> None:
        """Применить один оценённый ответ: EMA 0.7/0.3, attempts++, заметка при ошибке."""
        self.mastery = round(0.7 * self.mastery + 0.3 * float(score01), 4)
        self.attempts += 1
        if correct:
            self.correct += 1
        self.last_studied = now_iso()
        if not correct and feedback:
            self.add_note(
                feedback=feedback,
                question=question,
                student_answer=student_answer,
                correct_answer=correct_answer,
            )

    def add_note(
        self,
        feedback: str,
        question: str | None = None,
        student_answer: str | None = None,
        correct_answer: str | None = None,
    ) -> None:
        """Заметка об ошибке: дедуп по feedback[:180]; при дубле — свежая дата
        и добор отсутствующих student_answer/correct_answer (question не мержится)."""
        if not feedback:
            return
        key = feedback[:180]
        date = self.last_studied[:10] if self.last_studied else now_iso()[:10]
        for note in self.notes:
            if note.feedback == key:
                note.date = date
                if not note.student_answer and student_answer:
                    note.student_answer = student_answer
                if not note.correct_answer and correct_answer:
                    note.correct_answer = correct_answer
                return
        self.notes.append(
            WikiNote(
                date=date,
                feedback=key,
                question=question,
                student_answer=student_answer,
                correct_answer=correct_answer,
            )
        )
        if len(self.notes) > self.MAX_NOTES:
            self.notes = self.notes[-self.MAX_NOTES:]
