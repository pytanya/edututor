# Переработка раздела «Конспекты» — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать конспекты читаемыми (автообогащение тел, карточки-заметки по типу), привязать конспекты к реальному предмету (канонизация + миграция) и переработать панель/ридер (stats-бар, чипы-фильтры, карточки с прогресс-баром).

**Architecture:** Backend — канонизация предмета и `_infer_subject` в `store.py`, fallback `session.subject` и исправление условия автообогащения в `server.py`, одноразовый merge-инструмент `tools/wiki_normalize.py`. Frontend — переработка `KnowledgeWikiPanel` (чипы/stats/карточки) и `TopicArticle` (заметки-карточки, placeholder, навигация).

**Tech Stack:** Python 3.11 / FastAPI / PyYAML, React 19 / Vite / vitest / oxlint. Тесты: pytest + vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-18-wiki-panel-redesign-design.md`

## Global Constraints

- Python: line-length 100, ruff select `["E","F","I","N","UP","B","A","SIM"]`, target py311.
- Не менять человекочитаемые имена предметов сверх регистра/пробелов/известных алиасов.
- Коммиты только после зелёного прогона тестов таска.
- Backend команды выполняются из `adaptive_tutor/` (`pytest`, `ruff check src tests`), frontend из `frontend/` (`npm test`, `npm run lint`).
- Автообогащение — fail-soft: ошибки LLM не роняют поток, тело остаётся заглушкой.

---

### Task 1: Канонизация предмета в store (backend)

**Files:**
- Modify: `adaptive_tutor/src/config.py` (после `wiki_enrich_enabled`, ~строка 137)
- Modify: `adaptive_tutor/src/wiki/store.py` (модульные функции + методы `KnowledgeWiki`)
- Test: create `adaptive_tutor/tests/test_subject_canonicalize.py`

**Interfaces:**
- Produces: `canonicalize(subject: str, aliases: dict[str, str] | None = None) -> str` (модульная функция в `store.py`); методы `KnowledgeWiki.resolve_subject(subject: str = "", topic: str = "") -> str`, `KnowledgeWiki._infer_subject(topic: str) -> str | None`. `settings.subject_aliases: dict[str, str]` в `config.py`.

- [ ] **Step 1: Добавить конфиг алиасов предметов**

В `adaptive_tutor/src/config.py` после поля `wiki_enrich_enabled` (строки 135–137):

```python
    subject_aliases: dict[str, str] = Field(
        default_factory=dict,
        description="Алиасы предметов: вариант -> канон (пустой канон = предмет не задан)",
    )
```

- [ ] **Step 2: Добавить canonicalize и методы в store**

В `adaptive_tutor/src/wiki/store.py` после функции `slug` (модульная функция `canonicalize`), а методы — внутрь класса `KnowledgeWiki` (перед `apply_record`):

```python
_DEFAULT_SUBJECT_ALIASES = {"общая тема": ""}

def canonicalize(subject: str, aliases: dict[str, str] | None = None) -> str:
    """Нормализация предмета: пробелы/регистр/точка + алиас-мапа -> канон.

    Возвращает пустую строку, когда вариант означает «предмет не задан»
    («общая тема» по умолчанию; остальные алиасы — из настроек).
    Встроенные алиасы нельзя переопределить — дополняются настройками.
    """
    s = " ".join((subject or "").split())
    if s.endswith("."):
        s = s[:-1]
    if not s:
        return ""
    alias_map = {**_DEFAULT_SUBJECT_ALIASES, **(aliases if aliases is not None else (settings.subject_aliases or {}))}
    low = s.lower()
    for variant, canon in alias_map.items():
        if low == str(variant or "").strip().lower():
            return (canon or "").strip()
    return s
```

Методы класса (перед `apply_record`):

```python
    def _infer_subject(self, topic: str) -> str | None:
        """Предмет по похожей теме среди существующих статей (slug-совпадение)."""
        if not topic:
            return None
        ts = slug(topic)
        for art in self.list_articles():
            if slug(art.topic) == ts and canonicalize(art.subject):
                return art.subject
        return None

    def resolve_subject(self, subject: str = "", topic: str = "") -> str:
        """Канонический предмет: алиасы -> по похожей теме -> «общая тема»."""
        s = canonicalize(subject)
        if not s:
            s = self._infer_subject(topic) or "общая тема"
        return s
```

- [ ] **Step 3: Применить resolve_subject в apply_record и sync_mastery**

В `apply_record` заменить строку 181:

```python
        human_subject = subject or r.get("subject") or "общая тема"
```

на:

```python
        human_subject = self.resolve_subject(subject or r.get("subject") or "", topic)
```

В `sync_mastery` в начале цикла (после `if not topic: continue`) добавить:

```python
            subject = self.resolve_subject(subject, topic)
```

(строка `art = self.get(subject, topic)` и ниже уже используют `subject`).

- [ ] **Step 4: Написать тесты**

Create `adaptive_tutor/tests/test_subject_canonicalize.py`:

```python
"""Канонизация предмета и выведение предмета по теме (спека §3.1)."""

import pytest

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
```

- [ ] **Step 5: Запустить новые и регрессионные тесты**

Run (из `adaptive_tutor/`): `python -m pytest tests/test_subject_canonicalize.py tests/test_wiki.py -q`
Expected: PASS (в `test_apply_record_creates_and_is_idempotent` предмет становится «общая тема» через `resolve_subject` — утверждение `wiki.get("общая тема", "Дроби")` остаётся верным).

- [ ] **Step 6: Линт и коммит**

Run (из `adaptive_tutor/`): `ruff check src/wiki/store.py src/config.py tests/test_subject_canonicalize.py`
Expected: no errors.

```bash
git add adaptive_tutor/src/config.py adaptive_tutor/src/wiki/store.py adaptive_tutor/tests/test_subject_canonicalize.py
git commit -m "feat(wiki): канонизация предмета и выведение предмета по теме"
```

---

### Task 2: Fallback предмета сессии в evaluation-ветке (backend)

**Files:**
- Modify: `adaptive_tutor/src/api/server.py:1922`
- Test: modify `adaptive_tutor/tests/test_wiki.py` (добавить тест)

**Interfaces:**
- Consumes: `session` (уже в скоупе `_run_chat`, `server.py:1452`).
- Produces: статьи wiki в evaluation-ветке создаются под `body.subject or session.subject`.

- [ ] **Step 1: Заменить subject в wiki.apply_record**

В `adaptive_tutor/src/api/server.py` строки 1920–1925:

```python
                art = wiki.apply_record(
                    rec_data,
                    subject=body.subject,
                    grade=body.grade,
                    curriculum="",
                )
```

заменить на:

```python
                art = wiki.apply_record(
                    rec_data,
                    subject=body.subject or session.subject or "",
                    grade=body.grade,
                    curriculum="",
                )
```

- [ ] **Step 2: Написать тест**

В `adaptive_tutor/tests/test_wiki.py` после `test_wiki_hook_on_evaluation_creates_article_and_record` добавить:

```python
def test_wiki_hook_evaluation_falls_back_to_session_subject(tmp_path, monkeypatch):
    """Пустой body.subject -> предмет сессии (если сессия уже знает его)."""
    monkeypatch.setattr(settings, "knowledge_wiki_dir", str(tmp_path / "wiki"))
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(
        runtime_factory=_eval_runtime_factory(
            '{"type": "evaluation", "text": "Верно!",'
            '"payload": {"correct": true, "feedback": "ок", "knowledge_delta": 0.2}}'
        ),
        student_store=store,
    )
    app.state.rag_engine = None
    app.state.provisioner = None
    with TestClient(app) as c:
        c.post(
            "/chat",
            json={
                "message": "Изучаем тему: Крымская война",
                "session_id": "ses_fb",
                "student_id": "stu_fb",
                "topic": "Крымская война",
                "subject": "История",
            },
        )
        resp = c.post(
            "/chat",
            json={
                "message": "Ответ: 3/4",
                "session_id": "ses_fb",
                "student_id": "stu_fb",
                "topic": "Реформы Александра II",
                "subject": "",
            },
        )
        assert resp.status_code == 200
        wiki_body = c.get("/student/stu_fb/wiki").json()
    subjects = [g["subject"] for g in wiki_body["subjects"]]
    assert "История" in subjects
    assert "общая тема" not in subjects
    store.close()
```

- [ ] **Step 3: Запустить тест**

Run (из `adaptive_tutor/`): `python -m pytest tests/test_wiki.py -q -k "falls_back_to_session_subject or on_evaluation"`
Expected: PASS.

- [ ] **Step 4: Линт и коммит**

Run: `ruff check src/api/server.py tests/test_wiki.py`
Expected: no errors.

```bash
git add adaptive_tutor/src/api/server.py adaptive_tutor/tests/test_wiki.py
git commit -m "fix(wiki): предмет сессии как fallback при записи конспекта"
```

---

### Task 3: Автообогащение тел-заглушек (backend)

**Files:**
- Modify: `adaptive_tutor/src/wiki/enrich.py` (функция `is_stub_body` + условие в `enrich_body`)
- Modify: `adaptive_tutor/src/api/server.py:577` (условие `_schedule_enrich`)
- Test: modify `adaptive_tutor/tests/test_wiki.py`

**Interfaces:**
- Produces: `is_stub_body(body: str) -> bool` в `src/wiki/enrich.py`.

- [ ] **Step 1: Добавить is_stub_body и обновить enrich_body**

В `adaptive_tutor/src/wiki/enrich.py` после `_MAX_CONTEXT_CHARS` добавить:

```python
def is_stub_body(body: str) -> bool:
    """Тело — дефолтная заглушка или пустое (подлежит обогащению)."""
    body = (body or "").strip()
    return not body or "накапливается по мере прохождения квизов" in body
```

В `enrich_body` заменить условие (строка 40):

```python
    if art is None or len((art.body or "").strip()) > 20:
```

на:

```python
    if art is None or not is_stub_body(art.body):
```

- [ ] **Step 2: Обновить условие _schedule_enrich**

В `adaptive_tutor/src/api/server.py` строки 577–578:

```python
    if (art.body or "").strip():
        return
```

заменить на:

```python
    if not wiki_enrich.is_stub_body(art.body):
        return
```

- [ ] **Step 3: Написать тесты**

В `adaptive_tutor/tests/test_wiki.py` дополнить импорт и добавить тесты:

```python
from src.wiki.enrich import build_messages, enrich_body, is_stub_body
```

```python
def test_is_stub_body_detects_placeholder_and_empty():
    assert is_stub_body("") is True
    assert (
        is_stub_body("Материал по теме «Дроби» накапливается по мере прохождения квизов.")
        is True
    )
    assert is_stub_body("Полноценный конспект по дробям.") is False


async def test_enrich_body_enriches_default_stub_body(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    wiki.upsert(WikiArticle(subject="Математика", topic="Дроби"))
    llm = _FakeLLM(text="Конспект: дроби, числитель, знаменатель, сложение дробей.")
    result = await enrich_body(
        wiki, "Математика", "Дроби", ["сниппет о дробях"], llm, model=""
    )
    assert result is not None
    assert "числитель" in result["body"]
```

- [ ] **Step 4: Запустить тесты**

Run (из `adaptive_tutor/`): `python -m pytest tests/test_wiki.py -q -k "is_stub_body or enrich_body or skips_existing_long_body"`
Expected: PASS (существующий `test_enrich_body_skips_existing_long_body` по-прежнему PASS — длинное тело не заглушка).

- [ ] **Step 5: Линт и коммит**

Run: `ruff check src/wiki/enrich.py src/api/server.py tests/test_wiki.py`
Expected: no errors.

```bash
git add adaptive_tutor/src/wiki/enrich.py adaptive_tutor/src/api/server.py adaptive_tutor/tests/test_wiki.py
git commit -m "fix(wiki): автообогащение тел-заглушек вместо только пустых"
```

---

### Task 4: Миграция/merge существующих данных (backend)

**Files:**
- Create: `adaptive_tutor/src/tools/__init__.py`
- Create: `adaptive_tutor/src/tools/wiki_normalize.py`
- Test: create `adaptive_tutor/tests/test_wiki_normalize.py`

**Interfaces:**
- Consumes: `KnowledgeWiki.resolve_subject`, `KnowledgeWiki._read_file`, `WikiArticle`, `is_stub_body`.
- Produces: `normalize_student(student_dir: Path, stats: dict[str, int]) -> None`, `main() -> None` (вызов `python -m adaptive_tutor.tools.wiki_normalize`).

- [ ] **Step 1: Создать пакет и модуль**

Create `adaptive_tutor/src/tools/__init__.py`:

```python
"""Разовые утилиты и миграции (обслуживание данных)."""
```

Create `adaptive_tutor/src/tools/wiki_normalize.py`:

```python
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
from ..wiki.store import KnowledgeWiki
from ..wiki.store import _INDEX_NAME


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
        if any(x.suffix == ".md" for x in subj_dir.iterdir()):
            continue
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
```

- [ ] **Step 2: Написать тесты**

Create `adaptive_tutor/tests/test_wiki_normalize.py`:

```python
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
```

- [ ] **Step 3: Запустить тесты**

Run (из `adaptive_tutor/`): `python -m pytest tests/test_wiki_normalize.py -q`
Expected: PASS.

- [ ] **Step 4: Линт и коммит**

Run: `ruff check src/tools tests/test_wiki_normalize.py`
Expected: no errors.

```bash
git add adaptive_tutor/src/tools adaptive_tutor/tests/test_wiki_normalize.py
git commit -m "feat(wiki): утилита нормализации предметов и merge-дедупа конспектов"
```

---

### Task 5: Переработка панели «Конспекты» (frontend)

**Files:**
- Rewrite: `frontend/src/components/KnowledgeWikiPanel.jsx`
- Test: rewrite `frontend/src/components/KnowledgeWikiPanel.test.jsx`
- Modify: `frontend/src/index.css` (добавить `.wiki-stats/.wiki-chips/.wiki-card/.wiki-dot/.wiki-bar/.wiki-pct/.wiki-count`, удалить `.wiki-panel .panel.mastery-wall*`, `.wiki-article-row`, `.wiki-badge*`)

**Interfaces:**
- Consumes: `api.wiki(studentId)`, `api.wikiArticle`, `api.enrichWiki`, `api.deleteWiki`, `api.exportCsv`, `api.exportOkf`; `masteryClass` из `./MasteryWall`; `TopicArticle` (с новыми props `siblings`, `topicIndex`, `onNavigate`).
- Produces: props `subject` (активный предмет сессии) уже есть; новые внутренние состояния `chip`, `collapsedSubjects`, `topicIndex`.

- [ ] **Step 1: Переписать компонент**

Rewrite `frontend/src/components/KnowledgeWikiPanel.jsx` полностью:

```jsx
// KnowledgeWikiPanel — «Конспекты»: stats-бар, чипы-фильтры по предметам,
// карточки статей с прогресс-баром, ридер TopicArticle, экспорт CSV/OKF.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import api from '../api'
import { masteryClass } from './MasteryWall'
import TopicArticle from './TopicArticle'

const EMPTY_TEXT = 'Пройдите квиз по теме — конспекты появятся.'

function toPct(m) {
  return Math.round((Number(m) || 0) * 100)
}

function shortDate(iso) {
  return iso ? String(iso).slice(0, 10) : ''
}

export default function KnowledgeWikiPanel({ studentId, refreshKey = 0, subject = '', grade = '', onError = null }) {
  const [groups, setGroups] = useState([])
  const [article, setArticle] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [query, setQuery] = useState('')
  const [chip, setChip] = useState('')
  const [collapsedSubjects, setCollapsedSubjects] = useState({})
  const chipTouched = useRef(false)

  const load = useCallback(async () => {
    if (!studentId) return
    try {
      const data = await api.wiki(studentId)
      setGroups(data?.subjects || [])
    } catch {
      setGroups([])
    }
  }, [studentId])

  useEffect(() => {
    load()
  }, [load, refreshKey])

  const fail = (e) => onError?.(e?.message || String(e))

  const subjects = useMemo(() => (groups || []).map((g) => g.subject || '').filter(Boolean), [groups])

  const subjectCount = (s) =>
    (groups.find((g) => (g.subject || '') === s)?.articles || []).length

  // Активный предмет по умолчанию — предмет текущей сессии (если он есть в конспектах).
  useEffect(() => {
    if (!chipTouched.current && subject && subjects.includes(subject)) {
      setChip(subject)
    }
  }, [subject, subjects])

  const active = chip || (subject && subjects.includes(subject) ? subject : '')

  const toggleSubject = (subj) => {
    setCollapsedSubjects((prev) => ({ ...prev, [subj]: !prev[subj] }))
  }

  // По умолчанию раскрыт только активный предмет; «Все» — все раскрыты.
  const isCollapsed = (subj) =>
    collapsedSubjects[subj] !== undefined ? collapsedSubjects[subj] : active !== '' && subj !== active

  const openArticle = async (a) => {
    if (!studentId) return
    setNote('')
    try {
      const full = await api.wikiArticle(studentId, a.subject, a.topic)
      setArticle(full)
    } catch (e) {
      fail(e)
    }
  }

  const doEnrich = async () => {
    if (!studentId || !article || busy) return
    setBusy(true)
    setNote('')
    try {
      const res = await api.enrichWiki(studentId, article.subject, article.topic)
      if (res?.note) setNote(res.note)
      if (res?.article) setArticle(res.article)
      await load()
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  const removeArticle = async (a) => {
    if (!studentId) return
    try {
      await api.deleteWiki(studentId, a.subject, a.topic)
      setArticle((cur) =>
        cur && cur.subject === a.subject && cur.topic === a.topic ? null : cur,
      )
      setNote('')
      await load()
    } catch (e) {
      fail(e)
    }
  }

  const exportCsv = async () => {
    try {
      await api.exportCsv(studentId)
    } catch (e) {
      fail(e)
    }
  }

  const exportOkf = async () => {
    try {
      const res = await api.exportOkf(studentId, subject, grade)
      const files = Array.isArray(res?.files) ? res.files.length : 0
      const status = res?.conformant ? 'соответствует OKF' : 'есть ошибки валидации'
      setNote(`OKF: ${files} файлов · ${status}${res?.dir ? ` · ${res.dir}` : ''}`)
    } catch (e) {
      setNote(`OKF: ${e?.message || String(e)}`)
    }
  }

  if (!studentId) return null

  const totalArticles = (groups || []).reduce((n, g) => n + (g.articles || []).length, 0)

  const filteredGroups = useMemo(() => {
    let list = (groups || []).map((g) => ({
      ...g,
      articles: [...(g.articles || [])].sort((a, b) =>
        String(b.last_studied || '').localeCompare(String(a.last_studied || '')),
      ),
    }))
    const q = query.trim().toLowerCase()
    if (q) {
      list = list
        .map((g) => ({
          ...g,
          articles: (g.articles || []).filter(
            (a) =>
              (a.topic || a.title || '').toLowerCase().includes(q) ||
              (a.subject || g.subject || '').toLowerCase().includes(q) ||
              (a.concepts || []).some((c) => String(c).toLowerCase().includes(q)),
          ),
        }))
        .filter((g) => g.articles.length > 0)
    }
    if (active) {
      list = list.filter((g) => (g.subject || '') === active)
    }
    return [...list].sort((a, b) => {
      const aActive = (a.subject || '') === active ? 0 : 1
      const bActive = (b.subject || '') === active ? 0 : 1
      if (aActive !== bActive) return aActive - bActive
      return (a.subject || '').localeCompare(b.subject || '')
    })
  }, [groups, query, active])

  const stats = useMemo(() => {
    let attempts = 0
    let correct = 0
    let topics = 0
    for (const g of filteredGroups) {
      for (const a of g.articles) {
        topics += 1
        attempts += a.attempts || 0
        correct += a.correct || 0
      }
    }
    const avgMastery = topics
      ? filteredGroups.reduce(
          (s, g) => s + g.articles.reduce((ss, a) => ss + (a.mastery || 0), 0),
          0,
        ) / topics
      : 0
    return { topics, attempts, avgMastery, accuracy: attempts ? correct / attempts : 0 }
  }, [filteredGroups])

  const siblings = article
    ? ((groups || []).find((g) => (g.subject || '') === (article.subject || ''))?.articles || [])
        .map((a) => ({ subject: a.subject || article.subject, topic: a.topic }))
    : []
  const curIndex = article ? siblings.findIndex((s) => s.topic === article.topic) : -1

  return (
    <section className="panel wiki-panel">
      <div className="wiki-head">
        <h3>Конспекты</h3>
        <div className="export-row">
          <button type="button" className="btn small" disabled={!studentId} onClick={exportCsv}>⬇ Журнал (CSV)</button>
          <button type="button" className="btn small" disabled={!studentId} onClick={exportOkf}>OKF</button>
        </div>
      </div>

      {note ? <div className="wiki-note">{note}</div> : null}

      {totalArticles === 0 ? (
        <p className="muted">{EMPTY_TEXT}</p>
      ) : (
        <>
          <div className="wiki-stats">
            <div className="wiki-stat"><b>{stats.topics}</b><span>тем</span></div>
            <div className="wiki-stat"><b>{stats.attempts}</b><span>попыток</span></div>
            <div className="wiki-stat"><b>{Math.round(stats.avgMastery * 100)}%</b><span>ср.мастерство</span></div>
            <div className="wiki-stat"><b>{Math.round(stats.accuracy * 100)}%</b><span>точность</span></div>
          </div>

          <div className="wiki-chips">
            <button type="button" className={`wiki-chip${active === '' ? ' active' : ''}`} onClick={() => setChip('')}>Все · {totalArticles}</button>
            {subjects.map((s) => (
              <button type="button" key={s} className={`wiki-chip${active === s ? ' active' : ''}`} onClick={() => { chipTouched.current = true; setChip(s) }}>{s} · {subjectCount(s)}</button>
            ))}
          </div>

          <div className="wiki-search">
            <input
              type="text"
              placeholder="Поиск по конспектам…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          <div className="wiki-groups">
            {(filteredGroups || []).map((g) => (
              <div className="wiki-group" key={g.subject || 'subject'}>
                <div className={`wiki-group-header${g.subject === active ? ' current' : ''}`} onClick={() => toggleSubject(g.subject || '')}>
                  <h4 className="wiki-subject">{g.subject}</h4>
                  <span className="wiki-count">{(g.articles || []).length}</span>
                  <span className={`collapsible-arrow ${isCollapsed(g.subject || '') ? '' : 'open'}`}>▾</span>
                </div>
                {!isCollapsed(g.subject || '') && (
                  <ul className="wiki-articles">
                    {(g.articles || []).map((a) => {
                      const pct = toPct(a.mastery)
                      const cls = masteryClass(Number(a.mastery) || 0)
                      return (
                        <li className="wiki-card" key={a.topic || a.title}>
                          <div className="wiki-card-row">
                            <span className={`wiki-dot ${cls}`} />
                            <button type="button" className="wiki-article-title" onClick={() => openArticle({ ...a, subject: a.subject || g.subject })}>
                              {a.title || a.topic}
                            </button>
                            <span className="wiki-pct">{pct}%</span>
                          </div>
                          <div className="wiki-bar"><div className={`wiki-bar-fill ${cls}`} style={{ width: `${pct}%` }} /></div>
                          <div className="wiki-card-foot">
                            <span className="wiki-attempts">
                              попыток: {a.attempts || 0}{a.last_studied ? ` · ${shortDate(a.last_studied)}` : ''}
                            </span>
                            <button type="button" className="wiki-delete" aria-label={`Удалить ${a.title || a.topic}`} title="Удалить конспект" onClick={() => removeArticle({ ...a, subject: a.subject || g.subject })}>✕</button>
                          </div>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {article ? (
        <TopicArticle
          article={article}
          onClose={() => setArticle(null)}
          onEnrich={doEnrich}
          enriching={busy}
          siblings={siblings}
          topicIndex={curIndex}
          onNavigate={openArticle}
        />
      ) : null}
    </section>
  )
}

export { EMPTY_TEXT }
```

- [ ] **Step 2: Обновить CSS**

В `frontend/src/index.css`:

Удалить ныне неиспользуемые блоки: `.wiki-panel .panel.mastery-wall`, `.wiki-panel .panel.mastery-wall + .wiki-groups` (~строки 1665–1677), `.wiki-article-row` и `.wiki-badge`, `.wiki-badge.high/mid/low` (~строки 1709–1764). Классы `.wiki-article-title`, `.wiki-attempts`, `.wiki-delete`, `.wiki-articles`, `.wiki-subject`, `.collapsible-arrow` — используются и остаются.

Добавить в раздел `/* ---------- Wiki «Конспекты» (KnowledgeWikiPanel) ---------- */`:

```css
.wiki-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
}

.wiki-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 6px 2px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 9px;
}

.wiki-stat b {
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--ink);
}

.wiki-stat span {
  font-family: var(--font-mono);
  font-size: 8.5px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
}

.wiki-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.wiki-chip {
  appearance: none;
  border: 1px solid var(--line);
  background: var(--card);
  color: var(--ink-soft);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  cursor: pointer;
}

.wiki-chip:hover { border-color: var(--green); color: var(--green-strong); }

.wiki-chip.active {
  background: var(--green-soft);
  color: var(--green-strong);
  border-color: var(--green);
}

.wiki-card {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 7px 9px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 9px;
}

.wiki-card-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.wiki-dot {
  flex-shrink: 0;
  width: 9px;
  height: 9px;
  border-radius: 50%;
}

.wiki-dot.high { background: var(--green); }
.wiki-dot.mid { background: var(--amber); }
.wiki-dot.low { background: var(--red); }

.wiki-pct {
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  color: var(--ink-soft);
}

.wiki-bar {
  height: 4px;
  background: var(--paper-deep);
  border-radius: 3px;
  overflow: hidden;
}

.wiki-bar-fill {
  height: 100%;
  border-radius: 3px;
}

.wiki-bar-fill.high { background: var(--green); }
.wiki-bar-fill.mid { background: var(--amber); }
.wiki-bar-fill.low { background: var(--red); }

.wiki-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.wiki-group-header.current .wiki-subject { color: var(--green-strong); }

.wiki-count {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--muted);
}
```

`var(--paper-deep)`, `var(--green)`, `var(--amber)`, `var(--red)` уже определены в проекте (используются существующими классами).

- [ ] **Step 3: Переписать тесты панели**

Rewrite `frontend/src/components/KnowledgeWikiPanel.test.jsx`:

```jsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import KnowledgeWikiPanel, { EMPTY_TEXT } from './KnowledgeWikiPanel'
import { masteryClass } from './MasteryWall'
import api from '../api'

vi.mock('../api', () => ({
  default: {
    wiki: vi.fn(),
    wikiArticle: vi.fn(),
    enrichWiki: vi.fn(),
    deleteWiki: vi.fn(),
    exportCsv: vi.fn(),
    exportSummary: vi.fn(),
    exportOkf: vi.fn(),
  },
}))

const lit = (over = {}) => ({
  subject: 'Литература',
  topic: 'Поэты Серебряного века',
  title: 'Поэты Серебряного века',
  mastery: 0.76,
  accuracy: 0.67,
  attempts: 3,
  last_studied: '2026-09-15T10:00:00',
  body: '## Конспект\n\nМатериал по теме накоплен.',
  ...over,
})

const litGroup = (article = lit()) => ({ subject: article.subject, articles: [article] })

beforeEach(() => {
  api.wiki.mockReset()
  api.wikiArticle.mockReset()
  api.enrichWiki.mockReset()
  api.deleteWiki.mockReset()
  api.exportCsv.mockReset()
  api.exportSummary.mockReset()
  api.exportOkf.mockReset()
})

describe('MasteryWall helpers', () => {
  it('masteryClass пороги', () => {
    expect(masteryClass(0.8)).toBe('high')
    expect(masteryClass(0.75)).toBe('high')
    expect(masteryClass(0.74)).toBe('mid')
    expect(masteryClass(0.44)).toBe('low')
    expect(masteryClass(NaN)).toBe('low')
  })
})

describe('<KnowledgeWikiPanel/>', () => {
  it('пустое состояние: конспектов нет', async () => {
    api.wiki.mockResolvedValue({ subjects: [] })
    render(<KnowledgeWikiPanel studentId="stu_x" />)
    expect(await screen.findByText(EMPTY_TEXT)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '⬇ Журнал (CSV)' })).toBeInTheDocument()
  })

  it('рендерит stats-бар, карточку с прогрессом и датой; клик открывает ридер', async () => {
    const user = userEvent.setup()
    const article = lit()
    api.wiki.mockResolvedValue({ subjects: [litGroup(article)] })
    api.wikiArticle.mockResolvedValue({ ...article })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    expect(await screen.findByRole('button', { name: /^Литература/ })).toBeInTheDocument()
    expect(container.querySelector('.wiki-pct').textContent).toBe('76%')
    expect(screen.getByText('попыток: 3', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('2026-09-15', { exact: false })).toBeInTheDocument()
    expect(container.querySelector('.wiki-stats')).not.toBeNull()

    await user.click(screen.getByRole('button', { name: 'Поэты Серебряного века' }))
    expect(api.wikiArticle).toHaveBeenCalledWith('stu_x', 'Литература', 'Поэты Серебряного века')
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('активный предмет сессии — первой группой и раскрытой; остальные свёрнуты', async () => {
    const math = lit({ subject: 'Математика', topic: 'Дроби', title: 'Дроби' })
    api.wiki.mockResolvedValue({
      subjects: [litGroup(), { subject: 'Математика', articles: [math] }],
    })
    api.wikiArticle.mockResolvedValue({ ...math })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" subject="Математика" />)

    await screen.findByRole('button', { name: /^Математика/ })
    const groups = [...container.querySelectorAll('.wiki-group')]
    expect(groups[0].querySelector('.wiki-subject').textContent).toBe('Математика')
    expect(groups[0].querySelector('.wiki-article-title')).not.toBeNull()
    expect(groups[1].querySelector('.wiki-article-title')).toBeNull()
  })

  it('чип-фильтр: клик по предмету оставляет только его', async () => {
    const user = userEvent.setup()
    const math = lit({ subject: 'Математика', topic: 'Дроби', title: 'Дроби' })
    api.wiki.mockResolvedValue({
      subjects: [litGroup(), { subject: 'Математика', articles: [math] }],
    })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    await screen.findByRole('button', { name: /^Математика/ })
    await user.click(screen.getByRole('button', { name: /^Математика/ }))
    expect(container.querySelectorAll('.wiki-group').length).toBe(1)
    expect([...container.querySelectorAll('.wiki-group')][0].querySelector('.wiki-subject').textContent).toBe('Математика')
  })

  it('поиск ищет по концепциям', async () => {
    const user = userEvent.setup()
    const a = lit({ concepts: ['Символизм'] })
    const b = lit({ topic: 'Другая тема', title: 'Другая тема' })
    api.wiki.mockResolvedValue({ subjects: [{ subject: 'Литература', articles: [a, b] }] })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    const titles = () =>
      [...container.querySelectorAll('.wiki-article-title')].map((n) => n.textContent)

    await screen.findByRole('button', { name: /^Литература/ })
    expect(titles().length).toBe(2)
    await user.type(screen.getByPlaceholderText('Поиск по конспектам…'), 'Символизм')
    expect(titles()).toEqual(['Поэты Серебряного века'])
  })

  it('сортировка по last_studied: свежие сверху', async () => {
    const old = lit({ topic: 'Старая тема', title: 'Старая тема', last_studied: '2026-09-01T00:00:00' })
    const fresh = lit({ topic: 'Свежая тема', title: 'Свежая тема', last_studied: '2026-09-18T00:00:00' })
    api.wiki.mockResolvedValue({ subjects: [{ subject: 'Литература', articles: [old, fresh] }] })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    const titles = () =>
      [...container.querySelectorAll('.wiki-article-title')].map((n) => n.textContent)

    await screen.findByRole('button', { name: /^Литература/ })
    expect(titles()).toEqual(['Свежая тема', 'Старая тема'])
  })

  it('«Обогатить конспект» видна при теле-заглушке', async () => {
    const user = userEvent.setup()
    const article = lit({ body: 'Материал по теме «Поэты» накапливается по мере прохождения квизов.' })
    api.wiki.mockResolvedValue({ subjects: [litGroup(article)] })
    api.wikiArticle.mockResolvedValue({ ...article })
    api.enrichWiki.mockResolvedValue({ note: 'ok', article: { ...article, body: 'Полный конспект.' } })
    render(<KnowledgeWikiPanel studentId="stu_x" />)

    await screen.findByRole('button', { name: /^Литература/ })
    await user.click(screen.getByRole('button', { name: 'Поэты Серебряного века' }))
    const enrich = await screen.findByRole('button', { name: 'Обогатить конспект' })
    await user.click(enrich)
    expect(api.enrichWiki).toHaveBeenCalledWith('stu_x', 'Литература', 'Поэты Серебряного века')
    expect(await screen.findByText('Полный конспект.')).toBeInTheDocument()
  })

  it('удаление конспекта: deleteWiki и перезагрузка', async () => {
    const user = userEvent.setup()
    api.wiki.mockResolvedValue({ subjects: [litGroup()] })
    api.deleteWiki.mockResolvedValue({ deleted: true })
    render(<KnowledgeWikiPanel studentId="stu_x" />)

    await screen.findByRole('button', { name: /^Литература/ })
    await user.click(screen.getByRole('button', { name: 'Удалить Поэты Серебряного века' }))
    expect(api.deleteWiki).toHaveBeenCalledWith('stu_x', 'Литература', 'Поэты Серебряного века')
    expect(api.wiki).toHaveBeenCalledTimes(2)
  })

  it('сворачивание subject: клик скрывает статьи предмета', async () => {
    const user = userEvent.setup()
    const math = lit({ subject: 'Математика', topic: 'Дроби', title: 'Дроби' })
    api.wiki.mockResolvedValue({ subjects: [litGroup(), { subject: 'Математика', articles: [math] }] })
    api.wikiArticle.mockResolvedValue({ ...lit(), subject: 'Литература' })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    await screen.findByRole('button', { name: /^Литература/ })
    const groupOf = (subj) =>
      [...container.querySelectorAll('.wiki-group')].find(
        (g) => g.querySelector('.wiki-subject')?.textContent === subj,
      )
    await user.click(within(groupOf('Литература')).getByRole('heading', { name: 'Литература' }))
    expect(groupOf('Литература').querySelector('.wiki-article-title')).toBeNull()
  })
})
```

- [ ] **Step 4: Запустить тесты панели**

Run (из `frontend/`): `npm test -- KnowledgeWikiPanel.test.jsx`
Expected: PASS.

- [ ] **Step 5: Линт**

Run (из `frontend/`): `npm run lint`
Expected: no errors.

- [ ] **Step 6: Коммит**

```bash
git add frontend/src/components/KnowledgeWikiPanel.jsx frontend/src/components/KnowledgeWikiPanel.test.jsx frontend/src/index.css
git commit -m "feat(ui): переработка панели «Конспекты» — stats, чипы-фильтры, карточки"
```

---

### Task 6: Переработка ридера TopicArticle (frontend)

**Files:**
- Rewrite: `frontend/src/components/TopicArticle.jsx`
- Test: create `frontend/src/components/TopicArticle.test.jsx`
- Modify: `frontend/src/index.css` (заметки-карточки, placeholder, навигация)

**Interfaces:**
- Consumes: `article` (поля `body/source/notes/concepts/weak_areas`), props `siblings: [{subject, topic}]`, `topicIndex`, `onNavigate(articleLike)`.
- Produces: `isStubBody(body) -> bool`, `noteType(n) -> 'error'|'clarification'|'info'`.

- [ ] **Step 1: Переписать компонент**

Rewrite `frontend/src/components/TopicArticle.jsx` полностью:

```jsx
// TopicArticle — ридер конспекта: мета, статистика, изложение, заметки-карточки
// с цветом по типу, концепции, слабые места, навигация по темам предмета.
import { useState } from 'react'
import Latex from './Latex'
import { masteryClass } from './MasteryWall'

export function isStubBody(body) {
  const b = (body || '').trim()
  return !b || b.includes('накапливается по мере прохождения квизов')
}

export function noteType(n) {
  const fb = String(n?.feedback || '').toLowerCase()
  if (/ошибк|неверн|неправильн/.test(fb)) return 'error'
  if (n?.question || n?.student_answer || n?.correct_answer) return 'clarification'
  return 'info'
}

const ICONS = { error: '🔴', clarification: '🟡', info: '🟢' }

export default function TopicArticle({ article, onClose = null, onEnrich = null, enriching = false, siblings = [], topicIndex = -1, onNavigate = null }) {
  const [openNotes, setOpenNotes] = useState(() => new Set([0]))
  if (!article) return null
  const mastery = typeof article.mastery === 'number' ? article.mastery : 0
  const accuracy = typeof article.accuracy === 'number' ? article.accuracy : 0
  const attempts = article.attempts || 0
  const body = article.body || ''
  const notes = Array.isArray(article.notes) ? article.notes : []
  const concepts = Array.isArray(article.concepts) ? article.concepts : []
  const weakAreas = Array.isArray(article.weak_areas) ? article.weak_areas : []
  const stub = isStubBody(body)
  const pct = Math.round(mastery * 100)
  const cls = masteryClass(mastery)

  const toggleNote = (i) => {
    setOpenNotes((prev) => {
      const next = new Set(prev)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })
  }

  const prevArt = topicIndex > 0 ? siblings[topicIndex - 1] : null
  const nextArt = topicIndex >= 0 && topicIndex < siblings.length - 1 ? siblings[topicIndex + 1] : null

  return (
    <div className="topic-overlay" role="dialog" aria-modal="true" aria-label={article.title}>
      <div className="topic-article panel">
        <div className="topic-head">
          <div className="topic-heading">
            <h2 className="topic-title">{article.title}</h2>
            <div className="topic-meta">
              {article.subject ? <span>{article.subject}</span> : null}
              {article.grade ? <span>· {article.grade}</span> : null}
              {article.curriculum ? <span>· {article.curriculum}</span> : null}
              {article.source ? <span>· 📎 {article.source}</span> : null}
            </div>
          </div>
          <div className="topic-actions">
            {onEnrich ? (
              <button type="button" className="btn small" disabled={enriching} onClick={onEnrich}>
                {enriching ? 'Обогащаем…' : 'Обогатить конспект'}
              </button>
            ) : null}
            {onClose ? (
              <button type="button" className="topic-close" aria-label="Закрыть" onClick={onClose}>✕</button>
            ) : null}
          </div>
        </div>

        <div className="topic-stats">
          <div className="topic-stat">
            <span className="topic-stat-label">Освоенность</span>
            <div className="topic-mastery-track"><div className={`topic-mastery-fill ${cls}`} style={{ width: `${pct}%` }} /></div>
            <span className={`topic-stat-value ${cls}`}>{pct}%</span>
          </div>
          <div className="topic-stat">
            <span className="topic-stat-label">Точность</span>
            <span className="topic-stat-value">{Math.round(accuracy * 100)}%</span>
          </div>
          <div className="topic-stat">
            <span className="topic-stat-label">Попытки</span>
            <span className="topic-stat-value">{attempts}</span>
          </div>
        </div>

        {stub ? (
          <div className="topic-placeholder">
            ИИ ещё не написал конспект по этой теме{onEnrich ? ' — нажмите «Обогатить конспект»' : ''}.
          </div>
        ) : (
          <Latex text={body} />
        )}

        {weakAreas.length > 0 ? (
          <div className="topic-weak">Слабые места: {weakAreas.join(', ')}</div>
        ) : null}

        {notes.length > 0 ? (
          <div className="topic-block">
            <h3 className="topic-block-title">Заметки ({notes.length})</h3>
            <ul className="topic-notes">
              {notes.map((n, i) => {
                const t = noteType(n)
                return (
                  <li key={`${n.date || ''}-${i}`} className={`note-card ${t}`}>
                    <button type="button" className="note-card-head" aria-expanded={openNotes.has(i)} onClick={() => toggleNote(i)}>
                      <span className="note-card-icon">{ICONS[t]}</span>
                      <span className="note-card-title">{n.feedback}</span>
                      {n.date ? <span className="note-date">{n.date}</span> : null}
                    </button>
                    {openNotes.has(i) ? (
                      <div className="note-card-body">
                        {n.question ? <div className="note-row"><span className="note-label">Вопрос:</span> {n.question}</div> : null}
                        {n.student_answer ? <div className="note-row"><span className="note-label">Ваш ответ:</span> <em>{n.student_answer}</em></div> : null}
                        {n.correct_answer ? <div className="note-row correct"><span className="note-label">Правильный ответ:</span> <strong>{n.correct_answer}</strong></div> : null}
                      </div>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          </div>
        ) : null}

        {concepts.length > 0 ? (
          <div className="topic-block">
            <h3 className="topic-block-title">Концепции</h3>
            <div className="topic-concepts">
              {concepts.map((c) => <span className="concept-chip" key={c}>{c}</span>)}
            </div>
          </div>
        ) : null}

        {prevArt || nextArt ? (
          <div className="topic-nav">
            {prevArt ? (
              <button type="button" className="topic-nav-prev" onClick={() => onNavigate && onNavigate(prevArt)}>← {prevArt.title || prevArt.topic}</button>
            ) : <span />}
            {nextArt ? (
              <button type="button" className="topic-nav-next" onClick={() => onNavigate && onNavigate(nextArt)}>{nextArt.title || nextArt.topic} →</button>
            ) : <span />}
          </div>
        ) : null}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Обновить CSS**

В `frontend/src/index.css` в раздел `/* ---------- TopicArticle (ридер конспекта) ---------- */` добавить:

```css
.topic-placeholder {
  padding: 12px 14px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--muted);
  background: var(--card);
  border: 1px dashed var(--line);
  border-radius: 10px;
}

.note-card {
  border-left: 4px solid var(--line);
  background: var(--card);
  border-radius: 8px;
  overflow: hidden;
}

.note-card.error { border-left-color: var(--red); }
.note-card.clarification { border-left-color: var(--amber); }
.note-card.info { border-left-color: var(--green); }

.note-card-head {
  appearance: none;
  border: 0;
  background: none;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  text-align: left;
  cursor: pointer;
}

.note-card-icon { flex-shrink: 0; }

.note-card-title {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.note-card-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0 10px 10px;
  border-top: 1px solid var(--line);
}

.note-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12.5px;
  color: var(--ink-soft);
}

.note-row.correct strong { color: var(--green-strong); }

.note-label {
  font-family: var(--font-mono);
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
}

.topic-nav {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-top: 4px;
  border-top: 1px dashed var(--line);
  padding-top: 10px;
}

.topic-nav button {
  appearance: none;
  border: 0;
  background: none;
  padding: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--green-strong);
  cursor: pointer;
  text-align: left;
}

.topic-nav button:hover { text-decoration: underline; }
```

- [ ] **Step 3: Написать тесты ридера**

Create `frontend/src/components/TopicArticle.test.jsx`:

```jsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TopicArticle, { isStubBody, noteType } from './TopicArticle'

describe('TopicArticle helpers', () => {
  it('isStubBody распознаёт заглушку и пустоту', () => {
    expect(isStubBody('')).toBe(true)
    expect(isStubBody('Материал по теме «X» накапливается по мере прохождения квизов.')).toBe(true)
    expect(isStubBody('Реальный конспект')).toBe(false)
  })

  it('noteType типизирует заметки', () => {
    expect(noteType({ feedback: 'Неверно. Правильный ответ: 3/4' })).toBe('error')
    expect(noteType({ feedback: 'Ошибка в расчётах' })).toBe('error')
    expect(noteType({ feedback: 'Уточни', question: 'Вопрос', student_answer: '1', correct_answer: '2' })).toBe('clarification')
    expect(noteType({ feedback: 'Всё верно' })).toBe('info')
  })
})

const art = (over = {}) => ({
  title: 'Крымская война',
  subject: 'История',
  grade: '9',
  mastery: 0.58,
  accuracy: 0.71,
  attempts: 4,
  body: 'Крымская война 1853–1856 — конфликт России против коалиции.',
  notes: [
    { date: '2026-09-15', feedback: 'Неверно', question: 'Кто командовал?', student_answer: 'Нахимов', correct_answer: 'Нахимов' },
    { date: '2026-09-16', feedback: 'Уточнение', question: 'Повод войны?', student_answer: 'Синоп', correct_answer: 'Спор о святых местах' },
  ],
  concepts: ['Парижский мир 1856'],
  weak_areas: ['причины и повод'],
  ...over,
})

describe('<TopicArticle/>', () => {
  it('placeholder при теле-заглушке и кнопка «Обогатить»', () => {
    const a = art({ body: 'Материал по теме «X» накапливается по мере прохождения квизов.' })
    render(<TopicArticle article={a} onEnrich={vi.fn()} />)
    expect(screen.getByText(/ИИ ещё не написал конспект/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Обогатить конспект' })).toBeInTheDocument()
  })

  it('полное тело рендерится без placeholder', () => {
    render(<TopicArticle article={art()} />)
    expect(screen.queryByText(/ИИ ещё не написал конспект/)).toBeNull()
    expect(screen.getByText(/Крымская война 1853/)).toBeInTheDocument()
  })

  it('заметки: класс по типу и раскрытие по клику', async () => {
    const user = userEvent.setup()
    const { container } = render(<TopicArticle article={art()} />)
    const notes = container.querySelectorAll('.note-card')
    expect(notes[0].classList.contains('error')).toBe(true)
    expect(notes[1].classList.contains('clarification')).toBe(true)
    await user.click(screen.getByRole('button', { name: /Уточнение/ }))
    expect(container.querySelectorAll('.note-card-body').length).toBe(2)
  })

  it('показывает источник в мета', () => {
    render(<TopicArticle article={art({ source: 'https://ru.wikipedia.org' })} />)
    expect(screen.getByText(/📎 https:\/\/ru\.wikipedia\.org/)).toBeInTheDocument()
  })

  it('навигация пред./след. зовёт onNavigate', async () => {
    const user = userEvent.setup()
    const onNav = vi.fn()
    const siblings = [
      { subject: 'История', topic: 'Предыдущая' },
      { subject: 'История', topic: 'Крымская война' },
      { subject: 'История', topic: 'Реформы' },
    ]
    render(<TopicArticle article={art()} siblings={siblings} topicIndex={1} onNavigate={onNav} />)
    await user.click(screen.getByRole('button', { name: /← Предыдущая/ }))
    expect(onNav).toHaveBeenCalledWith({ subject: 'История', topic: 'Предыдущая' })
    await user.click(screen.getByRole('button', { name: /Реформы →/ }))
    expect(onNav).toHaveBeenCalledWith({ subject: 'История', topic: 'Реформы' })
  })
})
```

- [ ] **Step 4: Запустить тесты**

Run (из `frontend/`): `npm test -- TopicArticle.test.jsx KnowledgeWikiPanel.test.jsx`
Expected: PASS.

- [ ] **Step 5: Линт**

Run (из `frontend/`): `npm run lint`
Expected: no errors.

- [ ] **Step 6: Коммит**

```bash
git add frontend/src/components/TopicArticle.jsx frontend/src/components/TopicArticle.test.jsx frontend/src/index.css
git commit -m "feat(ui): ридер конспекта — заметки-карточки по типу, навигация, placeholder"
```

---

### Task 7: Полная проверка и запуск миграции

**Files:**
- No source changes (verification + run).

- [ ] **Step 1: Полный прогон backend**

Run (из `adaptive_tutor/`): `python -m pytest -q`
Expected: PASS (318 существующих + новые).

- [ ] **Step 2: Полный прогон frontend**

Run (из `frontend/`): `npm test && npm run lint`
Expected: PASS, no errors.

- [ ] **Step 3: Запустить миграцию на реальных данных**

Run (из `adaptive_tutor/`): `python -m adaptive_tutor.tools.wiki_normalize`
Expected: отчёт `Готово: {...}`; в `data/knowledge_wiki/` не должно остаться пустых каталогов и дублей «общая-тема», если был кандидат на слияние.

- [ ] **Step 4: Коммит оставшегося (если миграция изменила данные)**

```bash
git add adaptive_tutor/data
git commit -m "chore(wiki): нормализация предметов и слияние дублей конспектов"
```

Skip commit, если данные не изменились.