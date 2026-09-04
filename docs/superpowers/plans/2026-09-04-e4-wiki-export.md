# E4 — Knowledge Wiki и экспорт: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить персональную Knowledge Wiki (markdown+OKF-статьи с мастерством и заметками об ошибках, ленивое LLM-обогащение, UI-панель и тепловую карту) и учительский экспорт (CSV журнала `session_records` и OKF-бандл графа источника) с download/манифест-эндпоинтами.

**Architecture:** Wiki — чистые markdown-файлы с YAML-frontmatter на диске (`<knowledge_wiki>/<student_id>/<slug(subject)>/<slug(topic)>.md`, индекс `_index.md`); мастерство-EMA и заметки накапливаются из «answer record» в хуке `_run_chat` при каждом evaluation. Журнал ответов — новая SQLite-таблица `session_records` в том же `students.db` (источник CSV). Экспорт — чистые функции сборки CSV (`src/export/csv_exporter.py`) и OKF-бандла (`src/export/okf.py`); HTTP-эндпоинты под `/student/{id}`. LLM-обогащение тела — асинхронная роль `fast` (temp 0.3, max_tokens 400), ошибки глотаются.

**Tech Stack:** Python 3.11+ (в `.venv` — 3.12.7), FastAPI, Pydantic v2, sqlite3 stdlib + `threading.Lock`, `PyYAML>=6.0` (уже в env — 6.0.3, фиксируем в deps), pytest, ruff; React 19 + Vite (vitest, oxlint).

**Spec:** `docs/superpowers/specs/2026-09-04-e4-wiki-export-design.md`

## Global Constraints

- **Порядок этапов:** E4 выполняется поверх E1 (answer-record, `ChatSession.subject/grade/last_quiz`, review-блиц, sanitize) и E2 (`src/student/mastery.py`, `store.apply_result`, `src/kg/*` source-graph, mastery-оверлей). Если в рабочем дереве их ещё нет — сначала применить планы E1/E2; ниже код привязан к именам их дизайнов (хуки в `_run_chat`, переменная `record` в evaluation-блоке). E4 не пере-планирует E1–E3.
- Python >= 3.11. Все существующие тесты остаются зелёными.
- `ruff check src/ tests/` — чисто (line-length 100, правила из `pyproject.toml`).
- Репозиторий **без коммитов**: каждый Task заканчивается «Validation» (pytest + ruff), а НЕ git commit. Коммит — только по явному запросу пользователя.
- Русские docstring/комментарии. Pydantic v2. Один SQLite-connection + `threading.Lock` (паттерн `StudentStore`).
- Backend run из cwd `adaptive_tutor/`: `.venv/Scripts/python.exe -m pytest tests/<file> -q`, lint — `.venv/Scripts/ruff.exe check <files>`. Frontend из cwd `frontend/`: `npm test`, `npm run lint`.
- Агент (LLM-цикл) не должен знать про wiki/экспорт: хуки и эндпоинты только в `src/api/server.py`.
- Wiki/заметки/обогащение/БД-ошибки — fail-soft: чат не падает (паттерн try/except существующих хуков).
- `.env` не менять; новые переменные — только `.env.example`.
- Любой конверт/текст, уходящий фронтенду, не должен содержать секретов квиза (E1-санитайзер уже применяется; wiki использует только поля answer record).
- **Известная неопределённость зависимостей:** E1/E2 на момент написания ещё не влиты в `adaptive_tutor/src` (в дереве их модулей нет). План фиксирует «интерфейсный контракт», который дают E1/E2, и разрешает исполнителю адаптировать имена в хуке к фактическому коду, не меняя поведение. Единый источник ответа — дизайн-спеки E1 (§3) и E2 (§3), на которые опирается спека E4.
- Файловые операции wiki не защищены общим локом (отдельный `KnowledgeWiki` на ученика; в один файл пишет один процесс-поток). При параллельных ходах одного ученика допускается «последний пишущий побеждает» — как в референсе.
- `session_records` пишется на КАЖДЫЙ evaluation (обычный и review); `record_id = rec_<uuid12>`; ISO-метки CSV считаются на лету из `ts` (`time.time()`).
- Пути subject/topic в URL — человеческие имена (фронтенд отдаёт то, что показал); сервер slug-ирует их перед поиском файла и возвращает человеческое имя из frontmatter.

---

## File Structure

```
adaptive_tutor/src/wiki/__init__.py           # create (пустой/реэкспорт)
adaptive_tutor/src/wiki/models.py             # create: WikiNote, WikiArticle
adaptive_tutor/src/wiki/store.py              # create: slug(), KnowledgeWiki
adaptive_tutor/src/wiki/enrich.py             # create: async enrich_body(...)
adaptive_tutor/src/export/__init__.py         # create
adaptive_tutor/src/export/csv_exporter.py     # create
adaptive_tutor/src/export/okf.py              # create
adaptive_tutor/src/student/store.py           # modify: session_records
adaptive_tutor/src/config.py                  # modify: dirs + flags
adaptive_tutor/.env.example                   # modify
adaptive_tutor/pyproject.toml                 # modify: PyYAML>=6.0
adaptive_tutor/requirements.txt               # modify: PyYAML>=6.0
adaptive_tutor/src/api/server.py              # modify: хуки + эндпоинты
adaptive_tutor/tests/test_wiki.py             # create
adaptive_tutor/tests/test_export.py           # create
adaptive_tutor/docs/api.md                    # modify: wiki/export API
adaptive_tutor/README.md                      # modify (кратко)
frontend/src/api.js                           # modify
frontend/src/App.jsx                          # modify
frontend/src/components/KnowledgeWikiPanel.jsx# create
frontend/src/components/MasteryWall.jsx       # create
frontend/src/components/TopicArticle.jsx      # create
frontend/src/index.css                        # modify
frontend/src/components/KnowledgeWikiPanel.test.jsx  # create (или *.test.jsx рядом)
root README.md                                # modify (кратко, Features)
```

---

### Task 1: Конфиг Wiki/экспорта + фиксация PyYAML

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Test: инлайн-проверка (без нового тест-файла)

**Interfaces:**
- Consumes: `pydantic_settings.BaseSettings`, паттерн `resolved_student_db_path` (`src/config.py:89`).
- Produces:
  - `settings.knowledge_wiki_dir: str = "data/knowledge_wiki"`,
  - `settings.okf_dir: str = "data/okf"`,
  - `settings.wiki_enabled: bool = True`,
  - `settings.wiki_enrich_enabled: bool = True`,
  - `settings.resolved_knowledge_wiki_dir -> str`,
  - `settings.resolved_okf_dir -> str`.

- [ ] **Step 1: `src/config.py` — добавить поля после секции «Профиль ученика» (`resolved_student_db_path`)**

```python
    knowledge_wiki_dir: str = Field(
        default="data/knowledge_wiki", description="Корень wiki-статей (относительно adaptive_tutor/)"
    )
    okf_dir: str = Field(
        default="data/okf", description="Корень OKF-бандлов экспорта (относительно adaptive_tutor/)"
    )
    wiki_enabled: bool = Field(default=True, description="Включить Knowledge Wiki")
    wiki_enrich_enabled: bool = Field(
        default=True, description="Включить ленивое LLM-обогащение тела статьи"
    )

    @property
    def resolved_knowledge_wiki_dir(self) -> str:
        """Абсолютный путь к корню wiki: как student_db_path (от adaptive_tutor/)."""
        return _resolve_data_path(self.knowledge_wiki_dir)

    @property
    def resolved_okf_dir(self) -> str:
        """Абсолютный путь к корню OKF-бандлов."""
        return _resolve_data_path(self.okf_dir)
```

Вынести общий резолвер (из `resolved_student_db_path`) в модульную функцию рядом с классом:

```python
def _resolve_data_path(path: str) -> str:
    import os
    if os.path.isabs(path):
        return path
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, path)
```

`resolved_student_db_path` переписать на `return _resolve_data_path(self.student_db_path)` (поведение не меняется).

- [ ] **Step 2: `.env.example` — добавить блок**

```text
# Knowledge Wiki и экспорт (E4)
TUTOR_KNOWLEDGE_WIKI_DIR=data/knowledge_wiki
TUTOR_OKF_DIR=data/okf
TUTOR_WIKI_ENABLED=true
TUTOR_WIKI_ENRICH_ENABLED=true
```

- [ ] **Step 3: `pyproject.toml` и `requirements.txt` — `PyYAML>=6.0` в `[project]dependencies` / `requirements.txt`** (уже установлен 6.0.3 — фиксируем явно, т.к. wiki/OKF пишут YAML-frontmatter напрямую).

- [ ] **Step 4: Проверка импорта PyYAML в окружении**

Run (cwd `adaptive_tutor`): `.venv/Scripts/python.exe -c "import yaml; print(yaml.__version__)"`
Expected: `6.0.3`

- [ ] **Step 5: Validation**
Run: `.venv/Scripts/python.exe -c "from src.config import settings; print(settings.knowledge_wiki_dir, settings.okf_dir, settings.wiki_enabled, settings.wiki_enrich_enabled, settings.resolved_knowledge_wiki_dir)"`
Expected: `data/knowledge_wiki data/okf True True <...>/adaptive_tutor/data/knowledge_wiki`
Run: `.venv/Scripts/ruff.exe check src/config.py`
Expected: All checks passed.

---

### Task 2: Модели Wiki — `src/wiki/models.py` (`WikiNote`, `WikiArticle`)

**Files:**
- Create: `src/wiki/__init__.py` (пустой или реэкспорт моделей)
- Create: `src/wiki/models.py`
- Test: `tests/test_wiki.py` (часть 1: slug/статья/EMA/заметки — единый файл наполняется во всех wiki-задачах)

**Interfaces:**
- `WikiNote` — dataclass: `date, question=None, student_answer=None, feedback, correct_answer=None`; `to_dict()` (без None), `from_dict`.
- `WikiArticle` — dataclass: поля из спеки §2.2, `MAX_NOTES = 10`, методы `frontmatter()`, `to_markdown()`, `to_dict()`, `apply_result(...)`, `add_note(...)`, `from_dict(subject, topic, data)`; property `accuracy`.
- Формат файла — OKF v0.2 (спека §2.2); заголовок `# title` всегда перегенерируется.

- [ ] **Step 1: `WikiNote`**

```python
"""Модели Knowledge Wiki: заметка об ошибке и статья (OKF v0.2)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any


def now_iso() -> str:
    """Текущее время ISO (секунды, naive)."""
    return datetime.datetime.now().isoformat(timespec="seconds")


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
    def from_dict(cls, data: dict[str, Any]) -> "WikiNote":
        return cls(
            date=str(data.get("date") or ""),
            feedback=str(data.get("feedback") or ""),
            question=data.get("question"),
            student_answer=data.get("student_answer"),
            correct_answer=data.get("correct_answer"),
        )
```

- [ ] **Step 2: `WikiArticle` — поля и базовая сериализация**

```python
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
```

- [ ] **Step 3: `WikiArticle.to_markdown()` + `from_dict()`**

```python
    def to_markdown(self) -> str:
        """OKF-файл: frontmatter + заголовок # title + тело."""
        front = "---\n" + _dump_yaml(self.frontmatter()) + "---\n"
        body = self.body.strip()
        if not body:
            body = f"Материал по теме «{self.title}» накапливается по мере прохождения квизов."
        return f"{front}# {self.title}\n\n{body}\n"
```

Импорт `_dump_yaml` из `src/wiki/store.py` (см. Task 3) — туда выносится общий `yaml.safe_dump(allow_unicode=True, sort_keys=False)`; в `models.py` допустим локальный хелпер, чтобы `models` не зависел от `store` (рекомендуется локальный `_dump_yaml` + общий используется в `store`, чтобы не создавать цикл импортов).

```python
    @classmethod
    def from_dict(cls, subject: str, topic: str, data: dict[str, Any]) -> "WikiArticle":
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
```

- [ ] **Step 4: EMA + заметки (`apply_result`, `add_note`) — точно по спеке §2.3**

```python
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
```

`add_note` (дедуп по `feedback[:180]`, слияние, хвост 10):

```python
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
```

`_as_note(n)` — нормализация `WikiNote | dict | str` в `WikiNote` (используется в `__post_init__`).

- [ ] **Step 5: Validation**

Run (cwd `adaptive_tutor`): `.venv/Scripts/python.exe -m pytest tests/test_wiki.py -q` — до наполнения тестов в Task 3 добавить туда минимум: «3 верных = 0.8285», «дедуп заметок», «кап 10», «round-trip». Ниже полный список тестов; файл можно наполнить сразу здесь, тогда Task 3/4 лишь добавляют store/enrich-кейсы. Порядок не важен — **в конце каждого Task** прогон всего `tests/test_wiki.py`.

Expected: `N passed`.

---

### Task 3: Хранилище Wiki — `src/wiki/store.py` (`slug`, `KnowledgeWiki`)

**Files:**
- Create: `src/wiki/store.py`
- Test: `tests/test_wiki.py`

**Interfaces:**
- `slug(text: str) -> str` — 1:1 со спекой §2.1/референсом.
- `class KnowledgeWiki`: `__init__(root_dir, student_id)`, `for_student`, `subject_dir`, `article_path`, `get(subject, topic)`, `list_subjects()`, `list_articles(subject=None)`, `upsert(article)`, `delete(subject, topic)`, `_write_index(subject)`, `apply_record(record, subject="", grade="", curriculum="")`, `sync_mastery(subject, topic_updates)`, `to_summary_dict()`, `article_by_slug(subject_slug, topic_slug)`.
- Чтение битого/незакрытого frontmatter → `None` (молча, файл не удаляем).
- Markdown-запись/чтение с PyYAML: `safe_dump(..., allow_unicode=True, sort_keys=False)`.

- [ ] **Step 1: `slug()` и пути**

```python
"""Knowledge Wiki: персистентные OKF-статьи (файлы markdown + YAML-frontmatter)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

import yaml

from ..config import settings
from .models import WikiArticle

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
```

- [ ] **Step 2: класс `KnowledgeWiki` — чтение**

```python
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

    def for_student(self, student_id: str) -> "KnowledgeWiki":
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
```

`_read_file` — терпимый парсер:

```python
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
```

- [ ] **Step 3: листинги (`list_subjects`, `list_articles`, `article_by_slug`)**

```python
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
```

- [ ] **Step 4: запись (`upsert` + `_write_index`), `delete`**

```python
    def upsert(self, article: WikiArticle) -> Path:
        p = self.article_path(article.subject, article.topic)
        p.write_text(article.to_markdown(), encoding="utf-8")
        self._write_index(article.subject)
        return p

    def _write_index(self, subject: str) -> None:
        articles = [a for a in self.list_articles(subject)]
        if not articles:
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
        (self.subject_dir(subject) / _INDEX_NAME).write_text(
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
        try:
            self._write_index(subject)
        except Exception:
            pass
        return True
```

В импортах `store.py` использовать `from .models import WikiArticle, now_iso` (вместо фрагмента `from .models import WikiArticle`).

- [ ] **Step 5: `apply_record` / `sync_mastery` / `to_summary_dict`**

```python
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
```

> **NOTE для исполнителя:** в E4 суммарного события нет (спека §2.4), поэтому `sync_mastery` вызывается только юнит-тестами; сигнатура зафиксирована как `sync_mastery(subject, topic_updates)` — subject обязателен.

- [ ] **Step 6: полный `tests/test_wiki.py`** — тесты:

1. `slug`: `slug("Тема/Раздел") == "тема-раздел"`, `slug("  ") == "topic"`, кириллица + регистр, точки/дефисы сохраняются.
2. round-trip: `WikiArticle` → `to_markdown` → `KnowledgeWiki._read_file`/`get` → равные поля; body с `# title` срезается; `to_dict` содержит `accuracy`.
3. default-body: `to_markdown()` при пустом body содержит «Материал по теме…».
4. битый файл (не-`---`, битый YAML, незакрытый frontmatter) → `get()` = None.
5. EMA: 3 верных подряд: mastery 0.5 → 0.65 → 0.755 → **0.8285**, attempts=3, correct=3.
6. заметки: неверный ответ с feedback добавляет note; повторный неверный с тем же feedback[:180] → 1 note (дата обновлена, `correct_answer` дозаполнен); 11 разных feedback → 10 заметок (хвост).
7. `apply_record`: создание статьи на первом ответе (grade/curriculum), idempotent — attempts растёт только на фактический вызов; отсутствие `topic`/`score01` → None, файл не создан.
8. индекс: после 2 upsert одной темы `_index.md` содержит обе ссылки, сортировка по mastery desc; после delete последней статьи индекса нет (или не содержит удалённую).
9. `article_by_slug` находит по slug-сегментам.
10. `sync_mastery` создаёт статью с пресет-мастерством без attempts++.

- [ ] **Step 7: Validation**
Run: `.venv/Scripts/python.exe -m pytest tests/test_wiki.py -q` — all passed.
Run: `.venv/Scripts/ruff.exe check src/wiki/ tests/test_wiki.py` — clean.

---

### Task 4: LLM-обогащение тела — `src/wiki/enrich.py`

**Files:**
- Create: `src/wiki/enrich.py`
- Test: `tests/test_wiki.py`

**Interfaces:**
- `async def enrich_body(wiki, subject: str, topic: str, context: list[str], llm, model: str = "") -> dict | None`
  - статья не существует **или** `len(body.strip()) > 20` → `None`;
  - контекст (сниппеты) схлопнут в `"\n---\n".join(context)[:4000]`;
  - системный промпт из референса («Wiki-LLM EduTutor… 3-6 предложений… без заголовков и списков»);
  - `llm.chat(messages, model=..., temperature=0.3, max_tokens=400)`;
  - принять только `len(body.strip(' \n"\'`')) > 20`; записать в `art.body`, `last_studied=now`, `upsert`; вернуть `to_dict()`;
  - любое исключение/пустой ответ → вернуть `None` (заглушка остаётся), не ронять.

- [ ] **Step 1: реализация `enrich.py`**

```python
"""Wiki-LLM: ленивое обогащение тела статьи «конспектом» из RAG-фрагментов.

Роль fast, temperature 0.3, max_tokens 400. Ошибки глотаются: при недоступном
LLM или пустом результате статья остаётся заглушкой (чат не падает).
"""

from __future__ import annotations

from typing import Any

from .models import now_iso

_SYSTEM = (
    "Ты — Wiki-LLM EduTutor. По фрагментам учебных материалов напиши краткий "
    "конспект темы (3-6 предложений): ключевые факты, термины, определения. "
    "Верни ТОЛЬКО текст конспекта, без заголовков и списков."
)

_MAX_CONTEXT_CHARS = 4000


def build_messages(topic: str, context: list[str]) -> list[dict[str, str]]:
    """Сообщения для LLM; контекст обрезается до _MAX_CONTEXT_CHARS."""
    chunks = [c for c in (context or []) if c and c.strip()]
    ctx = "\n---\n".join(chunks)[:_MAX_CONTEXT_CHARS]
    user = f"Тема: {topic}\nФрагменты материалов:\n{ctx}"
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]


async def enrich_body(
    wiki: Any,
    subject: str,
    topic: str,
    context: list[str],
    llm: Any,
    model: str = "",
) -> dict[str, Any] | None:
    """Генерирует/обновляет тело статьи. None — статья-заглушка или сбой."""
    art = wiki.get(subject, topic)
    if art is None or len((art.body or "").strip()) > 20:
        return None
    chunks = [c for c in (context or []) if c and c.strip()]
    if not chunks or llm is None:
        return None
    try:
        resp = await llm.chat(
            messages=build_messages(topic, chunks),
            model=model or "fast",
            temperature=0.3,
            max_tokens=400,
        )
        content = resp.content if resp is not None else None
        body = (content or "").strip().strip(" \n\"'`")
        if len(body) <= 20:
            return None
        art.body = body
        art.last_studied = now_iso()
        wiki.upsert(art)
        return art.to_dict()
    except Exception:
        return None  # best-effort: ошибки обогащения не роняют поток
```

- [ ] **Step 2: тесты (в `tests/test_wiki.py`)**

Fake LLM (async):

```python
class _FakeLLM:
    def __init__(self, text="", error=None):
        self.text = text
        self.error = error
        self.calls = 0

    async def chat(self, messages, model="", temperature=0.7, max_tokens=1024, **kw):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return _Resp(self.text)
```

Тест-кейсы:
1. пустое тело → `llm.calls == 1`; результат записан в файл; `to_dict()["body"]` содержит конспект.
2. контекст пуст/`llm=None` → `None`, файл не тронут.
3. тело уже >20 символов → вызова нет (`calls == 0`).
4. короткий ответ (`"коротко"`) → `None`, тело остаётся заглушкой.
5. `llm.chat` бросает исключение → `None`, без исключения наружу.
6. контекст длиннее 4000 символов обрезается (проверка `len(user)` ≤ 4000 + префикс темы) — тест на `build_messages`.

- [ ] **Step 3: Validation**
Run: `.venv/Scripts/python.exe -m pytest tests/test_wiki.py -q`
Run: `.venv/Scripts/ruff.exe check src/wiki/enrich.py tests/test_wiki.py`

---

### Task 5: Журнал `session_records` + хуки записи ответов

**Files:**
- Modify: `src/student/store.py`
- Modify: `src/api/server.py` (хуки; эндпоинты — в Task 7/8)
- Test: `tests/test_export.py` (часть про store) + интеграционный хук-тест в `tests/test_wiki.py` или `tests/test_api.py`

**Interfaces:**
- Схема — точно спека §3 (PK `(student_id, record_id)`, индекс `(student_id, ts)`).
- `StudentStore.append_record(student_id, session_id, record: dict) -> None`
- `StudentStore.list_records(student_id, subject=None, session_id=None, limit=500) -> list[dict]` — окно «последних N» по `ts`, возвращается в хронологическом порядке (ASC).
- В `_run_chat` evaluation-блоке (после E1/E2 кода построения answer record и `store.apply_result`): `store.append_record(student_id, session_id, record)`; дальше wiki-хук (Task 7 ставит рядом, но запись в `session_records` живёт здесь).

- [ ] **Step 1: схема в `_SCHEMA` (`src/student/store.py`)**

```python
CREATE TABLE IF NOT EXISTS session_records (
  student_id TEXT NOT NULL,
  record_id  TEXT NOT NULL,
  session_id TEXT NOT NULL,
  ts         REAL NOT NULL,
  subject    TEXT DEFAULT '',
  topic      TEXT DEFAULT '',
  question_id TEXT DEFAULT '',
  question   TEXT DEFAULT '',
  options    TEXT DEFAULT NULL,
  answer_type TEXT DEFAULT 'open',
  difficulty TEXT DEFAULT 'medium',
  student_answer TEXT DEFAULT '',
  correct    INTEGER DEFAULT 0,
  feedback   TEXT DEFAULT '',
  score01    REAL DEFAULT 0.0,
  PRIMARY KEY (student_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_records_student ON session_records (student_id, ts);
```

- [ ] **Step 2: методы**

```python
    def append_record(self, student_id: str, session_id: str, record: dict) -> None:
        """Пишет строку журнала ответов (из answer record). record_id = rec_<uuid12>."""
        import json as _json
        import uuid as _uuid
        options = record.get("options")
        options_json = None
        if isinstance(options, list):
            options_json = _json.dumps(options, ensure_ascii=False)
        self._exec(
            "INSERT OR REPLACE INTO session_records "
            "(student_id, record_id, session_id, ts, subject, topic, question_id, "
            " question, options, answer_type, difficulty, student_answer, correct, "
            " feedback, score01) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                student_id,
                record.get("record_id") or f"rec_{_uuid.uuid4().hex[:12]}",
                session_id,
                float(record.get("ts") or time.time()),
                str(record.get("subject") or ""),
                str(record.get("topic") or ""),
                str(record.get("question_id") or ""),
                str(record.get("question") or ""),
                options_json,
                str(record.get("answer_type") or "open"),
                str(record.get("difficulty") or "medium"),
                str(record.get("student_answer") or ""),
                int(1 if record.get("correct") else 0),
                str(record.get("feedback") or ""),
                float(record.get("score01", 1.0 if record.get("correct") else 0.0)),
            ),
        )

    def list_records(
        self,
        student_id: str,
        subject: str | None = None,
        session_id: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Записи журнала: окно из limit ПОСЛЕДНИХ (по ts), отдано ASC."""
        if not student_id or limit <= 0:
            return []
        sql = "SELECT * FROM session_records WHERE student_id = ?"
        params: list = [student_id]
        if subject:
            sql += " AND subject = ?"
            params.append(subject)
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        sql += " ORDER BY ts DESC LIMIT ?"
        rows = self._rows(sql, (*params, limit))
        return list(reversed(rows))
```

`score01` в записи: если в record нет `score01` — производное `1.0`/`0.0` от `correct` (единый источник как в E2: 1.0 при correct).

- [ ] **Step 3: хук в `_run_chat` (запись на каждый evaluation)**

В `_run_chat` в существующем блоке `if store is not None and envelope.type.value == "evaluation":` (рядом с кодом E1 — построение answer record, и E2 — `store.apply_result`) **после** построения `record` добавить:

```python
    if store is not None and envelope.type.value == "evaluation":
        payload = envelope.payload or {}
        topic = body.topic
        # ... существующий код E1/E2: сборка answer record в переменной `record`
        #     и store.apply_result(student_id, topic, subject, record) ...
        if record is not None:
            try:
                store.append_record(student_id, session_id, record)
            except Exception as exc:  # noqa: BLE001
                print(f"[student] не удалось сохранить запись журнала: {exc}")
```

> Адаптация: имя переменной answer record — по фактической реализации E1 в этом же блоке (дизайн E1 §5.1). Если в сборке имя иное — использовать его; поведение не менять. Если E1 не возвращает record для evaluation без последнего квиза — запись журнала пропустить (fail-soft), wiki/мастерство не страдают.

- [ ] **Step 4: review-ответы тоже логируются**

В review-ветке E1 (`_review_turn`) в точке, где грейдится ответ на карточку, собрать answer record с `question_id = f"review:{card_id}"`, `question` = текст карточки, `options/answer_type/difficulty/correct_answer` = поля карточки, и вызвать `store.append_record(...)` (+ `store.apply_result`/wiki по спеке E2/E4 §2.4). Сбой — try/except + лог, блиц не прерывается.

- [ ] **Step 5: тесты**

В `tests/test_export.py`:
1. `append_record` создаёт строку; повторный вызов с тем же `record_id` — upsert (1 строка).
2. `list_records` возвращает ASC-окно последних N (`limit`), фильтры `subject`/`session_id`.
3. `options` сериализуется в JSON и читается обратно списком; `score01` производное при отсутствии.
4. пустой `student_id`/`limit<=0` → `[]`.

В `tests/test_api.py` (или `test_wiki.py`): сквозной хук — POST /chat с фейковым рантаймом, возвращающим `evaluation`-конверт (`payload={"correct": True, "feedback": "ок", "knowledge_delta": 0.2}`, `topic` в запросе), затем `client.get`/`store.list_records` показывает 1 запись с `record_id ~ rec_`. (См. паттерн фейк-рантайма `test_typed_finalize`/`test_api.py`.)

- [ ] **Step 6: Validation**
Run: `.venv/Scripts/python.exe -m pytest tests/test_export.py tests/test_api.py -q`
Run: `.venv/Scripts/ruff.exe check src/student/store.py src/api/server.py tests/test_export.py`

---

### Task 6: Чистые сборщики экспорта — `src/export/csv_exporter.py` и `src/export/okf.py`

**Files:**
- Create: `src/export/__init__.py`
- Create: `src/export/csv_exporter.py`
- Create: `src/export/okf.py`
- Test: `tests/test_export.py`

**Interfaces (csv_exporter):**
- `QUESTION_COLUMNS`, `SUMMARY_COLUMNS` — точно спека §4.1.
- `iso_ts(ts) -> str` — ISO из unix-`ts`.
- `questions_csv(rows: list[dict]) -> str` — строка CSV (без BOM; BOM добавляет эндпоинт при `encode("utf-8-sig")`); `options` из списка → `" | "`.
- `summary_csv(rows: list[dict]) -> str`.
- Рендер: `None` → `""`; булево `correct` → `1/0`; заголовки всегда есть (пустой экспорт = CSV с заголовками).

**Interfaces (okf):**
- `emit_okf_bundle(out_dir, subject, grade="", curriculum="", graph=None, mastery=None) -> Path`
  - `graph: {"nodes": [{id,title,type,color?,section_number?,parent_id?}], "edges": [{source,target,relation}]}` — формат E2 (§4.1); `None` → пустой граф.
  - `mastery: dict[title -> float]` — оверлей мастерства (по title узла).
  - Пишет `index.md`, `topics/<slug(title)>.md` для каждого не-`book` узла, `log.md`.
- `validate_bundle(bundle_dir) -> {"conformant": bool, "errors": [str], "files": [relpath...]}`.

- [ ] **Step 1: `csv_exporter.py`**

```python
"""Экспорт для учителя: CSV журнала вопросов и сводки сессий.

UTF-8 с BOM добавляет HTTP-эндпоинт (encode("utf-8-sig")) — Excel открывает
корректно. Сборка — чистые функции; rows — списки dict-строк.
"""

from __future__ import annotations

import csv
import datetime
import io
from typing import Any, Iterable

QUESTION_COLUMNS = [
    "timestamp", "session_id", "subject", "topic", "question_id", "question",
    "options", "answer_type", "difficulty", "student_answer", "score01",
    "correct", "feedback",
]

SUMMARY_COLUMNS = [
    "session_id", "subject", "topic", "started_at", "ended_at",
    "questions", "correct", "accuracy", "mastered_topics",
]


def iso_ts(ts: Any) -> str:
    """ISO-метка (секунды) из unix-ts; нечисловое -> ''."""
    try:
        return datetime.datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
    except Exception:
        return ""


def _text(value: Any) -> Any:
    return "" if value is None else value


def _row_texts(row: dict[str, Any], columns: list[str]) -> list[Any]:
    out: list[Any] = []
    for col in columns:
        value = row.get(col)
        if col == "timestamp":
            value = iso_ts(value)
        elif col in ("started_at", "ended_at"):
            value = iso_ts(value)
        elif isinstance(value, list):
            value = " | ".join(str(v) for v in value)
        elif isinstance(value, bool):
            value = int(value)
        out.append(_text(value))
    return out


def _csv_text(columns: list[str], rows: Iterable[dict[str, Any]]) -> str:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(_row_texts(row, columns))
    return buf.getvalue()


def questions_csv(rows: Iterable[dict[str, Any]]) -> str:
    """CSV по строкам журнала (порядок колонок — QUESTION_COLUMNS)."""
    return _csv_text(QUESTION_COLUMNS, rows)


def summary_csv(rows: Iterable[dict[str, Any]]) -> str:
    """CSV сводки по сессиям (SUMMARY_COLUMNS)."""
    return _csv_text(SUMMARY_COLUMNS, rows)
```

- [ ] **Step 2: `okf.py`**

```python
"""OKF-бандл: граф источника subject|grade как каталог markdown (спека §4.2).

Файлы OKF v0.2: index.md, topics/<slug(title)>.md (не-book узлы), log.md.
Мастерство (по title) и relations добавляются в frontmatter при наличии.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import yaml

from ..wiki.store import slug

OKF_VERSION = "0.2"
GENERATOR = "edututor/0.1"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _frontmatter(data: dict[str, Any]) -> str:
    return "---\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False) + "---\n"


def _node_relations(node_id: str, edges: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"target": e["target"], "relation": e["relation"]}
            for e in edges if e.get("source") == node_id]


def emit_okf_bundle(
    out_dir: Path,
    subject: str,
    grade: str = "",
    curriculum: str = "",
    graph: dict[str, Any] | None = None,
    mastery: dict[str, float] | None = None,
) -> Path:
    """Эмитит OKF-бандл в out_dir (создаётся). Возвращает out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes = list((graph or {}).get("nodes", []))
    edges = list((graph or {}).get("edges", []))
    mastery = mastery or {}
    generated = _now()
    source_name = subject

    index_meta = {
        "okf_version": OKF_VERSION,
        "type": "Index",
        "title": f"Учебник «{source_name}»",
        "subject": subject or "",
        "grade": grade or "",
        "curriculum": curriculum or "",
        "status": "stable",
        "generated": {"by": GENERATOR, "at": generated},
    }
    body = (f"# Учебник «{source_name}»\n\n"
            f"Граф знаний: {len(nodes)} узлов, {len(edges)} рёбер.\n\nТемы:\n")
    for n in nodes:
        if n.get("type") == "book":
            continue
        title = n.get("title", "")
        body += f"- [«{title}»](topics/{slug(title)}.md)\n"
    body += "\nПроисхождение: граф источника (E2).\n"
    (out_dir / "index.md").write_text(_frontmatter(index_meta) + body, encoding="utf-8")

    log_meta = {"type": "ChangeLog", "title": "История изменений"}
    log = f"## {generated}\n- Сформирован OKF-бандл «{source_name}» ({GENERATOR}).\n"
    (out_dir / "log.md").write_text(_frontmatter(log_meta) + log, encoding="utf-8")

    topics_dir = out_dir / "topics"
    topics_dir.mkdir(exist_ok=True)
    for n in nodes:
        if n.get("type") == "book":
            continue
        title = n.get("title", "")
        meta: dict[str, Any] = {
            "type": "Topic" if n.get("type") == "topic" else "Section",
            "title": title,
            "subject": subject or "",
            "grade": grade or "",
            "status": "stable",
            "generated": {"by": GENERATOR, "at": generated},
        }
        if n.get("section_number"):
            meta["section_number"] = n["section_number"]
        if curriculum:
            meta["curriculum"] = curriculum
        rels = _node_relations(n.get("id", ""), edges)
        if rels:
            meta["relations"] = rels
        if title in mastery:
            meta["mastery"] = round(float(mastery[title]), 4)
        desc = f"{title}\n\nРаздел учебника «{source_name}»"
        if n.get("section_number"):
            desc += f", №{n['section_number']}"
        desc += ". Открыть и готовиться по теме: выбор узла в графе знаний.\n"
        (topics_dir / f"{slug(title)}.md").write_text(_frontmatter(meta) + desc, encoding="utf-8")
    return out_dir


def validate_bundle(bundle_dir: Path) -> dict[str, Any]:
    """Проверка конформизма: каждый *.md начинается с '---', валидный YAML, type."""
    bundle_dir = Path(bundle_dir)
    errors: list[str] = []
    files: list[str] = []
    for p in sorted(bundle_dir.rglob("*.md")):
        rel = str(p.relative_to(bundle_dir)).replace("\\", "/")
        files.append(rel)
        text = p.read_text(encoding="utf-8")
        if not text.startswith("---"):
            errors.append(f"{rel}: нет YAML-frontmatter")
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            errors.append(f"{rel}: frontmatter не закрыт")
            continue
        try:
            data = yaml.safe_load(parts[1])
        except Exception as e:
            errors.append(f"{rel}: YAML невалиден: {e}")
            continue
        if not isinstance(data, dict) or not str(data.get("type", "")).strip():
            errors.append(f"{rel}: поле type пустое")
    return {"conformant": not errors, "errors": errors, "files": files}
```

- [ ] **Step 3: тесты `tests/test_export.py`** (объединить с store-тестами Task 5):

CSV:
1. `questions_csv` на 2 строках: заголовок = `QUESTION_COLUMNS`, `options` [a,b] → `"a | b"`, `timestamp` из unix в ISO, `correct` bool → `1/0`.
2. пустой список → только заголовки.
3. `summary_csv` колонки = `SUMMARY_COLUMNS`; `mastered_topics` список → join.
4. текст не содержит BOM (BOM добавит эндпоинт) — `not text.startswith("\ufeff")`.

OKF:
5. `emit_okf_bundle` с графом (book root + topic + section + concept) → `index.md` не ссылается на book-узел; `topics/` есть для topic/section; `validate_bundle` → `conformant=True`; каждый topic-файл имеет `type`, мастерство по title попало в frontmatter, `relations` из edges.
6. `validate_bundle` на битом бандле (нет `---`; пустой type) → `conformant=False` + errors.
7. пустой граф → index/log существуют, `files` ≥ 2, conformant.
8. `_node_relations` возвращает только исходящие рёбра узла.

- [ ] **Step 4: Validation**
Run: `.venv/Scripts/python.exe -m pytest tests/test_export.py -q`
Run: `.venv/Scripts/ruff.exe check src/export/ tests/test_export.py`

---

### Task 7: Эндпоинты Wiki + хук wiki в `_run_chat`

**Files:**
- Modify: `src/api/server.py`
- Test: `tests/test_wiki.py` (API-блок) / `tests/test_api.py`

**Interfaces:**
- `GET /student/{student_id}/wiki?subject=` → без subject: `{"subjects":[{subject, articles}]}`; с subject: `{"subject": human, "articles":[...]}`; пусто — `{"subjects": []}` (200).
- `GET /student/{student_id}/wiki/{subject}/{topic}` → article_dict; 404 `"Тема не найдена в базе знаний"`.
- `POST /student/{student_id}/wiki/enrich` body `{subject, topic}` → `{"article": dict|null, "note": str}`.
- `DELETE /student/{student_id}/wiki/{subject}/{topic}` → `{"deleted": true, "subject", "topic"}`; 404.
- Хук в `_run_chat`: на каждый evaluation — `wiki.apply_record(...)`; при пустом теле — фоновое ленивое обогащение.
- Observability: JSONL-события `wiki.updated`, `wiki.note`.

- [ ] **Step 1: импорты и хелперы в `server.py`**

```python
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from ..config import settings
from ..wiki.store import KnowledgeWiki, slug as _slug
from ..wiki import enrich as wiki_enrich
from ..export import csv_exporter
from ..export import okf as okf_export
```

Хелперы:

```python
def _wiki_for(student_id: str) -> KnowledgeWiki | None:
    """Wiki ученика (None если выключено)."""
    if not settings.wiki_enabled:
        return None
    return KnowledgeWiki(settings.resolved_knowledge_wiki_dir, student_id=student_id)
```

- [ ] **Step 2: ленивое обогащение (фоновое) в `_run_chat`**

После `store.append_record` (Task 5) и применения wiki (см. ниже) при `settings.wiki_enrich_enabled` и коротком теле — постановка фоновой задачи. В `create_app` добавить `app.state.wiki_enriching: set = set()`.

Код в `_run_chat` (внутри того же evaluation-блока, после `wiki.apply_record`):

```python
    if store is not None and envelope.type.value == "evaluation":
        # ... (E1/E2 хуки, record) ...
        wiki = _wiki_for(student_id)
        if wiki is not None and record is not None:
            try:
                art = wiki.apply_record(
                    record,
                    subject=body.subject,
                    grade=body.grade,
                    curriculum="",
                )
                correct = (record.get("correct") if isinstance(record, dict)
                           else bool(getattr(record, "correct", True)))
                if art is not None:
                    _log_wiki(app, "wiki.updated", student_id=student_id,
                              subject=art.subject, topic=art.topic, mastery=art.mastery)
                    if not correct and art.notes:
                        _log_wiki(app, "wiki.note", student_id=student_id,
                                  subject=art.subject, topic=art.topic, notes=len(art.notes))
                    await _schedule_enrich(app, student_id, body, art, wiki)
            except Exception as exc:  # noqa: BLE001
                print(f"[wiki] не удалось применить ответ: {exc}")
```

Вспомогательные функции (module-level в `server.py`):

```python
def _log_wiki(app: FastAPI, event: str, **fields: Any) -> None:
    """JSONL-события наблюдаемости (спека §8)."""
    try:
        app.state.jsonl_logger.log("", "INFO", event, **fields)
    except Exception:
        pass


async def _schedule_enrich(app: FastAPI, student_id: str, body: "ChatRequest",
                           art: Any, wiki: KnowledgeWiki) -> None:
    """Ленивое обогащение тела: fire-and-forget, только при провижиненных
    материалах темы и наличии RAG. Ошибки глотаются."""
    if not settings.wiki_enrich_enabled or not body.topic:
        return
    if (art.body or "").strip():
        return
    rag = app.state.rag_engine
    key = "|".join((body.subject.strip(), body.grade.strip(), body.topic.strip()))
    if rag is None or key not in app.state.provisioned:
        return
    if key in app.state.wiki_enriching:
        return
    app.state.wiki_enriching.add(key)

    async def _run() -> None:
        try:
            from ..llm.base import LLMClientFactory
            llm = LLMClientFactory.get_client(settings.region)
            models = LLMClientFactory.get_models_for_region(settings.region)
            context = _rag_context(app, body.topic, body.subject, body.grade)
            res = await wiki_enrich.enrich_body(
                wiki, art.subject, body.topic, context, llm,
                model=models.get("fast", "fast"),
            )
            if res is not None:
                _log_wiki(app, "wiki.updated", student_id=student_id,
                          subject=art.subject, topic=body.topic, mastery=res.get("mastery"))
        except Exception:  # noqa: BLE001
            pass
        finally:
            app.state.wiki_enriching.discard(key)

    task = asyncio.create_task(_run())
    tasks: set = app.state._enrich_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)
```

> Вызывается только из async `_run_chat`; `asyncio.create_task` там безопасен. Ссылки на задачи держатся в `app.state._enrich_tasks` (set), чтобы не собрались GC.

```python
def _rag_context(app: FastAPI, topic: str, subject: str = "", grade: str = "", k: int = 3) -> list[str]:
    """Фрагменты по теме из RAG (fail-soft)."""
    rag = app.state.rag_engine
    if rag is None:
        return []
    filters: dict[str, str] = {}
    if subject:
        filters["subject"] = subject
    if grade:
        filters["grade"] = grade
    try:
        results = rag.search(query=topic, top_k=k, filters=filters or None)
        return [r.chunk.text for r in results]
    except Exception:
        return []
```

- [ ] **Step 3: Wiki-эндпоинты**

```python
    @app.get("/student/{student_id}/wiki")
    def student_wiki(student_id: str, subject: str = Query(default="")) -> dict[str, Any]:
        """Список статей: все предметы или один subject (человеческое имя)."""
        wiki = _wiki_for(student_id)
        articles = wiki.list_articles() if wiki is not None else []
        if subject:
            picked = [a for a in articles if a.subject == subject or _slug(a.subject) == _slug(subject)]
            return {"subject": picked[0].subject if picked else subject,
                    "articles": [a.to_dict() for a in picked]}
        groups: dict[str, list] = {}
        for a in articles:
            groups.setdefault(a.subject, []).append(a.to_dict())
        return {"subjects": [{"subject": s, "articles": arts}
                             for s, arts in sorted(groups.items())]}

    @app.get("/student/{student_id}/wiki/{subject}/{topic}")
    def student_wiki_article(student_id: str, subject: str, topic: str) -> dict[str, Any]:
        """Одна статья (URL-сегменты — человеческие имена; ищутся по slug)."""
        wiki = _wiki_for(student_id)
        art = wiki.article_by_slug(_slug(subject), _slug(topic)) if wiki is not None else None
        if art is None:
            raise HTTPException(status_code=404, detail="Тема не найдена в базе знаний")
        return art.to_dict()
```

`POST /wiki/enrich` (модель запроса — Pydantic):

```python
    @app.post("/student/{student_id}/wiki/enrich")
    async def student_wiki_enrich(student_id: str, body: WikiEnrichRequest) -> dict[str, Any]:
        """Обогащение тела статьи LLM по RAG-материалам темы (не 404 при пустых)."""
        wiki = _wiki_for(student_id)
        if wiki is None:
            return {"article": None, "note": "Wiki выключено."}
        art = wiki.get(body.subject, body.topic)
        if art is None:
            art = WikiArticle(subject=body.subject, topic=body.topic, title=body.topic)
        context = _rag_context(app, body.topic, body.subject, "")
        if not context:
            return {
                "article": None,
                "note": "Нет материалов по теме в базе знаний — пройдите квиз или добавьте источник, затем повторите.",
            }
        llm = LLMClientFactory.get_client(settings.region)
        models = LLMClientFactory.get_models_for_region(settings.region)
        res = await wiki_enrich.enrich_body(
            wiki, body.subject, body.topic, context, llm, model=models.get("fast", "fast")
        )
        fresh = wiki.get(body.subject, body.topic)
        return {"article": fresh.to_dict() if fresh is not None else None,
                "note": "" if res is not None else "Не удалось сформировать конспект."}
```

`WikiEnrichRequest` и `WikiArticle` импорт вверху файла; `DELETE`:

```python
    @app.delete("/student/{student_id}/wiki/{subject}/{topic}")
    def student_wiki_delete(student_id: str, subject: str, topic: str) -> dict[str, Any]:
        wiki = _wiki_for(student_id)
        ok = wiki.delete(subject, topic) if wiki is not None else False
        if not ok:
            raise HTTPException(status_code=404, detail="Тема не найдена в базе знаний")
        _log_wiki(app, "wiki.updated", student_id=student_id,
                  subject=subject, topic=topic, mastery=None, deleted=True)
        return {"deleted": True, "subject": subject, "topic": topic}
```

`create_app` — добавить `app.state.jsonl_logger` и `app.state.wiki_enriching: set`, `app.state._enrich_tasks: set`.

- [ ] **Step 4: API-тесты (в `tests/test_wiki.py` или `tests/test_api.py`)**

Фикстура `wiki_client(tmp_path)`: `create_app(runtime_factory=_fake_runtime_factory, student_store=StudentStore(...))`, wiki-каталог переопределить через `monkeypatch.setattr(settings, "knowledge_wiki_dir", str(tmp_path / "wiki"))` перед запросом (резолвится в `_wiki_for` через `settings.resolved_knowledge_wiki_dir`). Enrich-тесты: подменить `app.state.rag_engine` на объект с методом `search`, возвращающим один `RetrievalResult(chunk=DocChunk(id="x", text="факт...", metadata={}), score=1)`; LLM — через подмену `LLMClientFactory.get_client` не требуется: передать тест на уровне `_rag_context`+`enrich_body` уже покрыт (Task 4). Для эндпоинта достаточно проверить контракт 200/404/формат и «нет материалов» → note.

1. предзаполнить статьи прямым вызовом `KnowledgeWiki(tmp_path/"wiki", student_id).apply_record(...)` ×2 темы; `GET /student/stu_1/wiki` → `{"subjects": [...]}` c `mastery`/`accuracy`/`body`.
2. `GET .../wiki?subject=Математика` фильтрует; `GET .../wiki/{slug}/{slug}` → article; несуществующий → 404 detail «Тема не найдена в базе знаний».
3. `DELETE` существующей → `{"deleted": true}` + файл удалён; повторный → 404.
4. `POST /wiki/enrich` без RAG → `article: None` + note «Нет материалов…», статус 200.
5. Хук: POST /chat с evaluation-конвертом (фейк-рантайм, `topic` в запросе) → появилась статья в wiki (mastery 0.65 при правильном первом ответе и якоре 0.5) и запись в `session_records`.

- [ ] **Step 5: Validation**
Run: `.venv/Scripts/python.exe -m pytest tests/test_wiki.py tests/test_api.py -q`
Run: `.venv/Scripts/ruff.exe check src/api/server.py tests/test_wiki.py tests/test_api.py`

---

### Task 8: Эндпоинты экспорта (CSV + OKF)

**Files:**
- Modify: `src/api/server.py`
- Test: `tests/test_export.py` (API-блок)

**Interfaces:**
- `GET /student/{student_id}/export/csv?subject=&limit=` → download `text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="<student_id>_session_log.csv"`, тело в `utf-8-sig`.
- `GET /student/{student_id}/export/summary.csv?subject=` → `filename="<student_id>_summary.csv"`.
- `GET /student/{student_id}/export/okf?subject=&grade=` → манифест `{"dir", "conformant", "errors", "files"}`.

- [ ] **Step 1: сборка summary-строк (на стороне сервера, в `server.py` или helper)**

```python
def _summary_rows(store: StudentStore, student_id: str, records: list[dict],
                  mastered_topics: set[str]) -> list[dict[str, Any]]:
    """Группировка записей по сессии -> одна строка SUMMARY_COLUMNS."""
    by_session: dict[str, list[dict]] = {}
    for r in records:
        by_session.setdefault(r.get("session_id") or "", []).append(r)
    rows: list[dict[str, Any]] = []
    for session_id, rows_ in by_session.items():
        ts = [float(r.get("ts") or 0) for r in rows_]
        total = len(rows_)
        correct = sum(1 for r in rows_ if r.get("correct"))
        topics = sorted({str(r.get("topic") or "") for r in rows_ if r.get("topic")})
        mastered = sorted(t for t in topics if t in mastered_topics)
        rows.append({
            "session_id": session_id,
            "subject": rows_[0].get("subject") or "",
            "topic": " | ".join(topics),
            "started_at": min(ts) if ts else None,
            "ended_at": max(ts) if ts else None,
            "questions": total,
            "correct": correct,
            "accuracy": round(correct / total, 4) if total else 0.0,
            "mastered_topics": mastered,
        })
    return rows
```

`mastered_topics` — множество тем со статусом mastered (E2-колонки topics). Определение «освоена» на случай отсутствия E2-колонок: `attempts >= 3 and mastery/level >= 0.8`. В `server.py` маленький хелпер `_mastered_set(store, student_id)` поверх `store.list_topics(student_id)`, читающий `status`/`mastery`/`level` толерантно.

- [ ] **Step 2: CSV-эндпоинты**

```python
    @app.get("/student/{student_id}/export/csv")
    def export_csv(student_id: str,
                   subject: str = Query(default=""),
                   limit: int = Query(default=500, ge=1)) -> Response:
        """Скачивание CSV журнала вопросов (Excel: utf-8-sig)."""
        store: StudentStore | None = app.state.student_store
        if store is None:
            raise HTTPException(status_code=503, detail="хранилище недоступно")
        records = store.list_records(student_id, subject=subject or None, limit=limit)
        csv_text = csv_exporter.questions_csv(records)
        _log_export(app, "export.csv", student_id=student_id, count=len(records),
                    bytes=len(csv_text.encode("utf-8")))
        return Response(
            content=csv_text.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{student_id}_session_log.csv"'},
        )

    @app.get("/student/{student_id}/export/summary.csv")
    def export_summary_csv(student_id: str,
                           subject: str = Query(default=""),
                           limit: int = Query(default=500, ge=1)) -> Response:
        """Скачивание CSV сводки по сессиям."""
        store = app.state.student_store
        if store is None:
            raise HTTPException(status_code=503, detail="хранилище недоступно")
        records = store.list_records(student_id, subject=subject or None, limit=limit)
        mastered = _mastered_set(store, student_id)
        csv_text = csv_exporter.summary_csv(_summary_rows(store, student_id, records, mastered))
        return Response(
            content=csv_text.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{student_id}_summary.csv"'},
        )
```

`_log_export` — аналог `_log_wiki`, событие `export.csv`/`export.okf`.

- [ ] **Step 3: OKF-эндпоинт + источник графа**

Граф источника читается из E2-кэша. Контракт: модуль E2 отдаёт `dict {"nodes": [...], "edges": [...]}` для `subject|grade` (формат E2 §4.1). Хелпер:

```python
async def _source_graph(app: FastAPI, subject: str, grade: str,
                        store: StudentStore, student_id: str) -> dict[str, Any]:
    """Граф источника subject|grade.

    Сначала пробуем E2-кэш (src/kg): если доступен публичный ридер
    read_source_graph(subject, grade) — используем его; иначе строим «каркас»
    из изученных тем ученика (узлы topic:{topic} + part_of к book:{subject}).
    Возврат всегда dict {nodes, edges}.
    """
    try:
        from ..kg import cache as kg_cache  # E2
        loader = getattr(kg_cache, "read_source_graph", None)
        if loader is not None:
            if asyncio.iscoroutinefunction(loader):
                data = await loader(subject, grade)
            else:
                data = loader(subject, grade)
            if data:
                return data
    except Exception:
        pass
    # каркас из истории ученика (fallback, как E2-эвристика)
    topics = store.list_topics(student_id) if store is not None else []
    root_id = f"book:{subject or 'книга'}"
    nodes = [{"id": root_id, "title": f"Учебник «{subject or 'книга'}»", "type": "book"}]
    edges: list[dict[str, Any]] = []
    for t in topics:
        name = str(t.get("topic") or "").strip()
        if not name:
            continue
        nodes.append({"id": f"topic:{name}", "title": name, "type": "topic"})
        edges.append({"source": root_id, "target": f"topic:{name}", "relation": "part_of"})
    return {"nodes": nodes, "edges": edges}
```

> **NOTE для исполнителя:** имя ридера E2 (`src/kg/cache.read_source_graph`) — по фактической реализации `src/kg/*` (дизайн E2: JSON-файлы `data/knowledge_graphs/<sha1>.json`, fail-soft чтение). Если ридер отсутствует — «каркас» выше детерминирован и достаточен.

Мастерство для frontmatter — по title узлов из `store.list_topics` + wiki:

```python
    @app.get("/student/{student_id}/export/okf")
    async def export_okf(student_id: str,
                         subject: str = Query(default=""),
                         grade: str = Query(default="")) -> dict[str, Any]:
        """OKF-бандл графа источника subject|grade (манифест)."""
        store = app.state.student_store
        if store is None:
            raise HTTPException(status_code=503, detail="хранилище недоступно")
        if not subject:
            subject = _last_subject(store, student_id)
        graph = await _source_graph(app, subject, grade, store, student_id)
        mastery = _mastery_by_title(store, student_id)
        out_dir = Path(settings.resolved_okf_dir) / student_id / _slug(subject)
        okf_export.emit_okf_bundle(out_dir, subject=subject, grade=grade,
                                   curriculum="", graph=graph, mastery=mastery)
        manifest = okf_export.validate_bundle(out_dir)
        _log_export(app, "export.okf", student_id=student_id, subject=subject,
                    files=len(manifest["files"]), conformant=manifest["conformant"])
        return {"dir": str(out_dir), **manifest}
```

> В `server.py` добавить импорт `from pathlib import Path` (в начале файла рядом с `import time`). `_last_subject`: subject последней сессии ученика — из последней записи `store.list_records(student_id)`; пусто → `"общая тема"`. `_mastery_by_title`: `{topic/имя -> mastery}` из `store.list_topics(student_id)` (attempts>0; колонка `mastery`, при её отсутствии — `level`).

- [ ] **Step 4: API-тесты (в `tests/test_export.py`)**

1. Наполнить store записями (`append_record` ×3, две сессии, одна тема с `correct`, subject «Математика»); `GET /student/stu_1/export/csv` → 200, `content-type` начинается `text/csv`, `content-disposition` содержит `stu_1_session_log.csv`, тело начинается с `\ufeff` и содержит `timestamp,session_id,subject,...`.
2. `summary.csv` → заголовок `SUMMARY_COLUMNS`, одна строка на сессию; `mastered_topics` корректен.
3. пустой ученик → CSV только с заголовками (200).
4. `GET /student/stu_1/export/okf?subject=Математика&grade=7` → `{"dir", "conformant": true, "errors": [], "files": [...]}`; каталог на диске содержит `index.md`, `topics/`, `log.md`; topic-файл имеет mastery когда тема изучена.
5. `okf` при пустой истории → «каркас» из book-узла (или пустой topics) + conformant true.

- [ ] **Step 5: Validation**
Run: `.venv/Scripts/python.exe -m pytest tests/test_export.py tests/test_api.py -q`
Run: `.venv/Scripts/ruff.exe check src/api/server.py tests/test_export.py`

---

### Task 9: Фронтенд — Wiki-панель, ридер, тепловая карта, экспорт

**Files:**
- Modify: `frontend/src/api.js`
- Create: `frontend/src/components/KnowledgeWikiPanel.jsx`
- Create: `frontend/src/components/MasteryWall.jsx`
- Create: `frontend/src/components/TopicArticle.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/index.css`
- Test: `frontend/src/components/KnowledgeWikiPanel.test.jsx` (+ MasteryWall-тесты внутри)

**Interfaces:**
- `api.wiki(studentId, subject="")` → GET `/student/{id}/wiki`.
- `api.wikiArticle(studentId, subject, topic)` → GET article (encodeURIComponent на subject/topic).
- `api.enrichWiki(studentId, subject, topic)` → POST `/wiki/enrich`; возвращает `{article, note}`.
- `api.deleteWiki(studentId, subject, topic)` → DELETE.
- `api.downloadCsv(studentId)` / `api.downloadSummary(studentId)` — blob-download.
- `api.exportOkf(studentId, subject, grade)` → манифест.
- `KnowledgeWikiPanel({studentId, refreshKey, onNotice})`: список предметов/статей, кнопка «⬇ Экспорт», открытие `TopicArticle`; пустое состояние «Пройдите квиз по теме — конспекты появятся».
- `MasteryWall({articles, onOpen})`: квадратики по темам, `mastery >= 0.75 → .high`, `>= 0.45 → .mid`, иначе `.low`; клик → `onOpen(article)`.
- `TopicArticle({article, onClose, onEnrich, enriching})`: рендер body через `<Latex text={body} />`, шапка (title, mastery %, accuracy, attempts, notes), кнопка «Обогатить конспект» когда body ≤ 20 символов.
- Обновление: после каждого `done`-события и после `enrich` — увеличить `refreshKey`.

- [ ] **Step 1: `api.js`**

```js
export function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

async function requestBlob(path) {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.blob()
}
```

Добавить в объект `api`:

```js
  wiki: (studentId, subject = '') =>
    request(`/student/${encodeURIComponent(studentId)}/wiki${subject ? `?subject=${encodeURIComponent(subject)}` : ''}`),
  wikiArticle: (studentId, subject, topic) =>
    request(`/student/${encodeURIComponent(studentId)}/wiki/${encodeURIComponent(subject)}/${encodeURIComponent(topic)}`),
  enrichWiki: (studentId, subject, topic) =>
    request(`/student/${encodeURIComponent(studentId)}/wiki/enrich`, { method: 'POST', body: JSON.stringify({ subject, topic }) }),
  deleteWiki: (studentId, subject, topic) =>
    fetch(`${BASE}/student/${encodeURIComponent(studentId)}/wiki/${encodeURIComponent(subject)}/${encodeURIComponent(topic)}`, { method: 'DELETE' }),
  exportCsv: async (studentId) => {
    const blob = await requestBlob(`/student/${encodeURIComponent(studentId)}/export/csv`)
    downloadBlob(`${studentId}_session_log.csv`, blob)
  },
  exportSummary: async (studentId) => {
    const blob = await requestBlob(`/student/${encodeURIComponent(studentId)}/export/summary.csv`)
    downloadBlob(`${studentId}_summary.csv`, blob)
  },
  exportOkf: (studentId, subject, grade = '') =>
    request(`/student/${encodeURIComponent(studentId)}/export/okf?subject=${encodeURIComponent(subject)}&grade=${encodeURIComponent(grade)}`),
```

- [ ] **Step 2: `MasteryWall.jsx`** (цветовые классы из спеки §5.1)

```jsx
export function masteryClass(m) {
  if (m >= 0.75) return 'high'
  if (m >= 0.45) return 'mid'
  return 'low'
}

export default function MasteryWall({ groups, onOpen }) {
  const cells = []
  for (const g of groups || []) {
    for (const a of g.articles || []) {
      cells.push({ key: `${g.subject}::${a.topic}`, article: a })
    }
  }
  if (cells.length === 0) return null
  return (
    <div className="mastery-wall" aria-label="Тепловая карта мастерства">
      {cells.map(({ key, article }) => (
        <button key={key} className={`mw-cell ${masteryClass(article.mastery || 0)}`}
                title={`${article.title} — ${Math.round((article.mastery || 0) * 100)}%`}
                onClick={() => onOpen(article)}>
          {article.title}
        </button>
      ))}
    </div>
  )
}
```

- [ ] **Step 3: `TopicArticle.jsx`** (ридер статьи)

Props `{article, onClose, onEnrich, enriching}`; body рендерится `<Latex text={article.body || ''} />`; заголовок — `article.title`; статистика — mastery/accuracy/attempts; список `article.notes` (дата + feedback + «Ваш ответ: …», если есть); «Обогатить конспект» — кнопка при `(article.body || '').trim().length <= 20 && onEnrich`. `onClose` — крестик. Всё в `.topic-article panel` overlay (модальное окно поверх layout).

- [ ] **Step 4: `KnowledgeWikiPanel.jsx`**

```jsx
import { useEffect, useState } from 'react'
import api from '../api'
import MasteryWall, { masteryClass } from './MasteryWall'
import TopicArticle from './TopicArticle'

export default function KnowledgeWikiPanel({ studentId, refreshKey, subject, grade, onError }) {
  const [groups, setGroups] = useState([])
  const [article, setArticle] = useState(null)   // открытая статья
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  const load = async () => {
    if (!studentId) return
    try {
      const data = await api.wiki(studentId)
      setGroups(data.subjects || [])
    } catch { /* wiki пусто/выключено */ }
  }

  useEffect(() => { load() }, [studentId, refreshKey])

  const openArticle = async (a) => {
    setNote('')
    try {
      const full = await api.wikiArticle(studentId, a.subject, a.topic)
      setArticle(full)
    } catch (e) { onError?.(e.message) }
  }

  const doEnrich = async () => {
    if (!article || busy) return
    setBusy(true)
    setNote('')
    try {
      const res = await api.enrichWiki(studentId, article.subject, article.topic)
      setNote(res.note || '')
      if (res.article) setArticle(res.article)
      await load()
    } catch (e) { onError?.(e.message) } finally { setBusy(false) }
  }
  // ... render: панель «Конспекты» + MasteryWall + список групп +
  // кнопки «⬇ Экспорт» (CSV / сводка / OKF-манифест через api.exportCsv/Summary/exportOkf)
}
```

Рендер: если групп нет — `<p className="muted">Пройдите квиз по теме — конспекты появятся.</p>` и кнопки экспорта спрятаны/активны по `disabled={!studentId}`. Заголовок статьи в списке: `title` + `{masteryClass(...)}` бейдж `NN%` + `попыток: N`.

- [ ] **Step 5: `App.jsx` wiring**

- импорт `KnowledgeWikiPanel`; состояние `const [wikiVersion, setWikiVersion] = useState(0)` и `loadWiki = () => setWikiVersion((v) => v + 1)`.
- в правую колонку (рядом с `<AdaptivePanel/>`):

```jsx
<div className="right">
  <AdaptivePanel adaptive={feed.adaptive} onStudy={studyNext} />
  <KnowledgeWikiPanel
    studentId={studentId.current}
    refreshKey={wikiVersion}
    subject={current?.subject || ''}
    grade={current?.grade || ''}
  />
</div>
```

- в `done`-обработчике (`chatStream` в `runTurn`) после `refreshStudent(); loadSessions()` добавить `loadWiki()`.
- передать `current?.subject`/`grade` — панель использует для OKF-экспорта (текущий предмет).

- [ ] **Step 6: `index.css`**

Классы (в языке существующих `--card/--green/--amber/--red`): `.right { display:flex; flex-direction:column; gap: 12px; min-width: 260px; }`, `.wiki-panel`, `.wiki-group`, `.wiki-article-row`, `.mw-cell.high { background: var(--green-soft); border-color: var(--green); }`, `.mw-cell.mid { background: var(--amber-soft); border-color: var(--amber); }`, `.mw-cell.low { background: var(--red-soft); border-color: var(--red); }`, `.topic-article` (overlay), `.note`, `.export-row`. Соблюдать «тетрадную» палитру.

- [ ] **Step 7: vitest-тесты**

`KnowledgeWikiPanel.test.jsx` (мок `api.js`, `vi.mock('./api')`):
1. пустое состояние: `groups=[]` → текст «Пройдите квиз по теме — конспекты появятся».
2. рендер группы: subject «Литература», статья «Поэты…» с mastery 0.76 → бейдж `76%`; клик по названию вызывает `api.wikiArticle` и открывает ридер.
3. MasteryWall-классы: `masteryClass(0.8) === 'high'`, `(0.5) === 'mid'`, `(0.4) === 'low'`, границы `0.75/0.45`.
4. клик по ячейке `MasteryWall` вызывает `onOpen` с article.

Адаптировать существующий `App.test.jsx`: `api.wiki` в мок-объект (иначе unmocked методы упадут) — вернуть `Promise.resolve({subjects: []})`.

- [ ] **Step 8: Validation**
Run (cwd `frontend`): `npm test`
Run (cwd `frontend`): `npm run lint`

---

### Task 10: Документация (README + API)

**Files:**
- Modify: `adaptive_tutor/README.md`
- Modify: `adaptive_tutor/docs/api.md`
- Modify: `root README.md` (Features)

**Interfaces:** только текст.

- [ ] **Step 1: `adaptive_tutor/docs/api.md`** — добавить разделы:
  - `GET /student/{student_id}/wiki`, `GET /student/{id}/wiki/{subject}/{topic}`, `POST /student/{id}/wiki/enrich`, `DELETE /student/{id}/wiki/{subject}/{topic}` (примеры body/ответа, включая 404).
  - `GET /student/{id}/export/csv`, `GET /student/{id}/export/summary.csv` (download, `utf-8-sig`, Content-Disposition).
  - `GET /student/{id}/export/okf?subject=&grade=` (манифест `{dir, conformant, errors, files}`).
- [ ] **Step 2: `adaptive_tutor/README.md`** — краткое описание Wiki (пути `data/knowledge_wiki`, формат OKF v0.2, флаги `TUTOR_WIKI_*`) и экспорта (`data/okf`, CSV-колонки) + запуск тестов.
- [ ] **Step 3: `root README.md`** — список фич дополнить «Knowledge Wiki (конспекты с мастерством) и экспорт для учителя (CSV/OKF)».
- [ ] **Step 4: Validation**
Run (cwd `adaptive_tutor`): `.venv/Scripts/python.exe -m pytest tests/ -q` (все зелёные), `.venv/Scripts/ruff.exe check src/ tests/`
Run (cwd `frontend`): `npm test` и `npm run lint`

---

## Self-Review (план сверен со спекой)

| Спека E4 (раздел) | Task в плане |
|---|---|
| §1 Цель и границы | Global Constraints; File Structure |
| §2.1 Пути и slug (`_slug`, юникод, fallback `topic`) | Task 3 Step 1 (тесты Task 3 Step 6) |
| §2.2 Формат статьи OKF v0.2 (frontmatter-порядок, default-body, `# title`, accuracy производная) | Task 2 Steps 2–3 |
| §2.3 Классы (`WikiNote`, `WikiArticle`, EMA 0.7/0.3, attempts/correct, add_note дедуп по feedback[:180], кап 10, apply_record, sync_mastery, upsert/delete/list/to_summary_dict) | Task 2 Steps 1–4; Task 3 Steps 2–5 |
| §2.4 Синхронизация (evaluation + review → apply_record; ленивое enrich_body: fast/0.3/400/≤4000, accept len>20, swallow errors; мастерство-на-конец — не требуется) | Task 5 Steps 3–4; Task 7 Steps 1–2; Task 4 |
| §2.5 API Wiki (список/статья/enrich/DELETE, article_dict, 404-детали, human subject) | Task 7 Steps 3–4 |
| §3 Журнал `session_records` (SQL-схема, PK/индекс, `rec_<uuid12>`, ts, review question_id, append/list_records limit 500) | Task 5 Steps 1–2, 5 |
| §4.1 CSV (utf-8-sig, Content-Disposition, колонки вопросов, `" | "`, отдельный summary.csv, чистые функции) | Task 6 Steps 1,3; Task 8 Steps 1–2,4 |
| §4.2 OKF-бандл (index.md, topics/*.md не-book узлы, mastery/relations в frontmatter, log.md, validate_bundle, манифест) | Task 6 Steps 2–3; Task 8 Steps 3–4 |
| §4.3 Роли/доступ (student_id из запроса, тот же режим доверия) | Task 8 (нет авторизации; как `/student/{id}/sessions`) |
| §5 UI (KnowledgeWikiPanel, MasteryWall >=0.75/0.45/low + клик→статья, TopicArticle, обновление после done/enrich, empty-state, кнопка «⬇ Экспорт») | Task 9 Steps 1–6 |
| §6 Конфиг/deps (`knowledge_wiki_dir`, `okf_dir`, `wiki_enabled`, `wiki_enrich_enabled`, PyYAML>=6.0) | Task 1 |
| §7 Крайние случаи (битый файл → None, slug-коллизия, статья без ответов не создаётся, weak_areas зарезервировано, пустые CSV, лимит 500, ошибки не роняют чат, slug-пути URL) | Task 2 Step 6, Task 3 Steps 2/6, Task 5, Task 7/8 (fail-soft), Global Constraints |
| §8 Наблюдаемость (`wiki.updated`, `wiki.note`, `export.csv`, `export.okf`) | Task 7 Steps 2–3, Task 8 Steps 2–3 |
| §9 Тесты (slug/round-trip/EMA/дедуп/кап/apply_record/enrich/records/CSV/OKF/API/frontend) | Task 2–8 Validation; Task 9 Step 7 |
| §10 Файлы (создать/изменить списки) | File Structure |
