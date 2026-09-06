# ФГОС-сверка: соответствие тем учебной программе — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Внедрить ФГОС-сверку: статический каталог учебной программы
`(subject, grade)` с разделами, серверные эндпоинты (список программы, сверка
темы, покрытие программы темами из `student_kg`), системную SSE-заметку
`fgos.note` в чате для тем вне программы и блок «Программа (ФГОС)» в панели
«Мои знания» (`StudentKGPanel`).

**Architecture:** Чистый пакет `src/curriculum/` (`catalog.py` — данные/seed +
приведение subject/grade/названий, `service.py` — сверка и покрытие, без
sqlite/LLM) + 3 эндпоинта в `create_app` (`src/api/server.py`) + поле
`ChatSession.last_fgos_topic` и SSE-эмиссия в `_run_chat` + фронтенд (api.js,
Chat feedReducer, StudentKGPanel). Схема БД НЕ меняется: покрытие считается на
лету из `store.list_topics`.

**Tech Stack:** Python 3.11+, FastAPI, pytest, ruff (line-length 100, py311);
React 19 + vitest + RTL + oxlint.

**Spec:** `docs/superpowers/specs/2026-09-06-fgos-alignment-design.md`

## Global Constraints

- Никаких изменений схемы БД и новых колонок `topics/students/sessions`.
- Детерминированная сверка — только нормализованное точное равенство; LLM в
  MVP не вызывается (fail-soft отсутствует, каталог-нет = «не проверено»).
- Каталог — стартовый seed ровно из спеки §4.1 (3 предмета), не полный ФГОС;
  состав/порядок разделов фиксированы и проверяются тестами.
- Все обращения к store/сверке в чате fail-soft: `try/except` не должен ронять
  чат (паттерн mastery-gate, `src/api/server.py:1410-1433`).
- SSE-заметка — только при `on_event is not None` (stream), один раз на тему в
  сессии; совпавшие темы и неизвестный класс не шумят.
- Запуск тестов из `adaptive_tutor/`:
  `.venv/Scripts/python.exe -m pytest tests/<file> -q`; стиль:
  `.venv/Scripts/python.exe -m ruff check src tests -q`.
  Фронтенд из `frontend/`: `npm test` (vitest run) и `npm run lint`.
- Коммиты не делать без явной просьбы (AGENTS.md); коммит после имплементации
  выполняет драйвер по инструкции пользователя, в шаги плана не входит.

---

### Task 1: Конфиг-флаг и заготовка пакета `src/curriculum/`

**Files:**
- Modify: `src/config.py` (блок после «Граф знаний (E2): …», `:134-158`)
- Modify: `.env.example`
- Create: `src/curriculum/__init__.py`

**Interfaces:**
- Produces: `settings.curriculum_enabled: bool` (default True).

- [ ] **Step 1:** В `src/config.py` (класс `Settings`, после блока
  «Граф знаний (E2)» перед «Адаптивное обучение: интервальные повторения»,
  `:160`):

```python
    # ФГОС-сверка (2026-09-06)
    curriculum_enabled: bool = Field(default=True, description="Включить ФГОС-сверку")
```

- [ ] **Step 2:** В `.env.example` (в конец, после LinUCB-блока):

```
# ФГОС-сверка тем с учебной программой
TUTOR_CURRICULUM_ENABLED=true
```

- [ ] **Step 3:** Создать `src/curriculum/__init__.py` (пустой файл с
  docstring-модуля — пакет; `__init__.py` останется пустым, re-export не нужен):

```python
"""ФГОС-сверка: каталог учебной программы (subject, grade) и покрытие темами."""
```

- [ ] **Step 4:** Проверка чтения дефолта:

```bash
.venv/Scripts/python.exe -c "from src.config import settings; print(settings.curriculum_enabled)"
```

Expected: `True`.

---

### Task 2: `src/curriculum/catalog.py` — данные и приведение + юнит-тесты

**Files:**
- Create: `src/curriculum/catalog.py`
- Create: `tests/test_curriculum.py`

**Interfaces:**
- Produces (используются в Task 3/4/5):
  - `@dataclass(frozen=True) CurriculumSection`: `code: str`, `section: str`,
    `topics: tuple[str, ...]`;
  - `@dataclass(frozen=True) CurriculumLookup`: `subject: str`,
    `grade: int | None`, `sections: tuple[CurriculumSection, ...]`,
    `found: bool`;
  - `SUBJECT_SYNONYMS: dict[str, tuple[str, ...]]`;
  - `CURRICULUM: dict[str, dict[str, tuple[CurriculumSection, ...]]]`;
  - `normalize_title(value: str) -> str`;
  - `resolve_subject(subject: str) -> str`;
  - `grade_number(grade: str | None) -> int | None`;
  - `grade_matches(key: str, g: int | None) -> bool`;
  - `curriculum_for(subject: str, grade: str | None) -> CurriculumLookup`.

- [ ] **Step 1: падающий тест** — `tests/test_curriculum.py` (часть catalog):

```python
"""Юнит-тесты ФГОС-каталога (src/curriculum/catalog.py)."""

from src.curriculum.catalog import (
    CURRICULUM,
    SUBJECT_SYNONYMS,
    curriculum_for,
    grade_matches,
    grade_number,
    normalize_title,
    resolve_subject,
)


def test_normalize_title_lowercases_collapses_and_strips_prefix():
    assert normalize_title("  Квадратные Уравнения  ") == "квадратные уравнения"
    assert normalize_title("Параграф 12: Теорема Виета") == "теорема виета"
    assert normalize_title("Урок 5 — квадратные уравнения") == "квадратные уравнения"


def test_resolve_subject_aliases_case_and_unknown():
    assert resolve_subject("Алгебра") == "математика"
    assert resolve_subject("алгебре") == "математика"
    assert resolve_subject("физика") == "физика"
    assert resolve_subject("русский-язык") == "русский язык"
    assert resolve_subject("астрономия") == "астрономия"
    assert resolve_subject("") == ""


def test_grade_number_parses_first_int():
    assert grade_number("8 класс") == 8
    assert grade_number("8") == 8
    assert grade_number("8-й класс") == 8
    assert grade_number("") is None
    assert grade_number("abc") is None
    assert grade_number(None) is None


def test_grade_matches_equal_and_range():
    assert grade_matches("8", 8) is True
    assert grade_matches("8", 7) is False
    assert grade_matches("5-6", 5) is True
    assert grade_matches("5-6", 6) is True
    assert grade_matches("5-6", 7) is False
    assert grade_matches("8", None) is False


def test_curriculum_for_known_math8():
    lookup = curriculum_for("математика", "8 класс")
    assert lookup.found is True
    assert lookup.subject == "математика"
    assert lookup.grade == 8
    assert len(lookup.sections) == 3
    assert lookup.sections[0].code == "МАТ.8.1"
    assert lookup.sections[0].section == "Квадратные уравнения"


def test_curriculum_for_algebra_synonym():
    lookup = curriculum_for("алгебра", "8")
    assert lookup.found is True
    assert lookup.subject == "математика"


def test_curriculum_for_unknown():
    assert curriculum_for("математика", "9").found is False
    assert curriculum_for("физика", "8 класс").found is False
    assert curriculum_for("астрономия", "8 класс").found is False


def test_curriculum_seed_shape_exact():
    assert sorted(CURRICULUM) == ["история", "математика", "физика"]
    assert [s.code for s in CURRICULUM["математика"]["8"]] == [
        "МАТ.8.1", "МАТ.8.2", "МАТ.8.3",
    ]
    assert len(CURRICULUM["физика"]["7"]) == 3
    assert len(CURRICULUM["история"]["6"]) == 2
    assert "теорема виета" in CURRICULUM["математика"]["8"][0].topics
    assert resolve_subject("история") in SUBJECT_SYNONYMS
```

- [ ] **Step 2: реализация** — `src/curriculum/catalog.py` (seed ровно из спеки
  §4.1; ниже полный код файла):

```python
"""Статический каталог учебной программы (ФГОС-сверка, спека §4).

Каталог — стартовый seed на 3 предмета (математика-8, физика-7, история-6),
НЕ исчерпывающий ФГОС. Коды разделов — правдоподобные примеры.
Чистые функции (без I/O): приведение subject/grade/названий и выборка каталога.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SUBJECT_WS_RE = re.compile(r"\s+")

# Префикс «Урок/Параграф N …» срезается при нормализации (§3.2).
_PREFIX_RE = re.compile(
    r"^(?:урок|параграф|lesson|section|module|unit|тема|раздел)"
    r"\s*\d+[.:\s—–-]*\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CurriculumSection:
    code: str
    section: str
    topics: tuple[str, ...]


@dataclass(frozen=True)
class CurriculumLookup:
    subject: str
    grade: int | None
    sections: tuple[CurriculumSection, ...]
    found: bool


SUBJECT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "математика": ("математика", "математике", "алгебра", "алгебре",
                   "геометрия", "геометрии", "мат", "алг"),
    "физика": ("физика", "физике", "физ"),
    "история": ("история", "истории", "истор", "всеобщая история"),
    "русский язык": ("русский", "русскому", "русский язык", "русский-язык"),
    "биология": ("биология", "биологии", "биол"),
    "химия": ("химия", "химии", "хим"),
    "география": ("география", "географии", "геогр"),
    "литература": ("литература", "литературе", "лит"),
}

CURRICULUM: dict[str, dict[str, tuple[CurriculumSection, ...]]] = {
    "математика": {
        "8": (
            CurriculumSection("МАТ.8.1", "Квадратные уравнения", (
                "квадратные уравнения", "неполные квадратные уравнения",
                "формула корней квадратного уравнения", "теорема виета",
                "квадратный трёхчлен")),
            CurriculumSection("МАТ.8.2", "Квадратичная функция", (
                "квадратичная функция", "график квадратичной функции",
                "парабола", "свойства квадратичной функции")),
            CurriculumSection("МАТ.8.3", "Четырёхугольники", (
                "четырёхугольники", "параллелограмм", "прямоугольник",
                "ромб", "квадрат", "трапеция")),
        ),
    },
    "физика": {
        "7": (
            CurriculumSection("ФИЗ.7.1", "Механические явления", (
                "механические явления", "механическое движение", "скорость",
                "инерция", "взаимодействие тел", "масса",
                "плотность вещества")),
            CurriculumSection("ФИЗ.7.2", "Давление", (
                "давление", "давление твёрдых тел",
                "давление жидкостей и газов", "закон паскаля",
                "атмосферное давление")),
            CurriculumSection("ФИЗ.7.3", "Строение вещества", (
                "строение вещества", "молекулы", "диффузия",
                "агрегатные состояния вещества")),
        ),
    },
    "история": {
        "6": (
            CurriculumSection("ИСТ.6.2", "Средневековый мир", (
                "средние века", "средневековье", "крестовые походы",
                "византия", "феодализм")),
            CurriculumSection("ИСТ.6.3", "Древняя Русь", (
                "древняя русь", "киевская русь", "древнерусское государство")),
        ),
    },
}


def normalize_title(value: str) -> str:
    """Нормализованный заголовок для точного матчинга (спека §4)."""
    t = _PREFIX_RE.sub("", value or "")
    return _SUBJECT_WS_RE.sub(" ", t).strip().lower()


def resolve_subject(subject: str) -> str:
    """Canonical-предмет по синонимам (подстрока в обе стороны); иначе как есть."""
    s = (subject or "").strip().lower()
    if not s:
        return ""
    for canonical, synonyms in SUBJECT_SYNONYMS.items():
        if s == canonical:
            return canonical
        for syn in synonyms:
            if syn in s or s in syn:
                return canonical
    return s


def grade_number(grade: str | None) -> int | None:
    """Первое целое из строки класса («8 класс» → 8); нет числа → None."""
    if not grade:
        return None
    match = re.search(r"\d+", grade)
    return int(match.group()) if match else None


def grade_matches(key: str, g: int | None) -> bool:
    """Равенство ключа («8») либо диапазон («5-6») числу класса."""
    if g is None:
        return False
    key = key.strip()
    if "-" in key:
        try:
            lo, hi = (int(p) for p in key.split("-", 1))
        except ValueError:
            return False
        return lo <= g <= hi
    try:
        return int(key) == g
    except ValueError:
        return False


def curriculum_for(subject: str, grade: str | None) -> CurriculumLookup:
    """Каталог для предмета+класса: canonical subject, число класса, разделы."""
    canonical = resolve_subject(subject)
    g = grade_number(grade)
    grades = CURRICULUM.get(canonical)
    if grades is None or g is None:
        return CurriculumLookup(subject=canonical, grade=g,
                                sections=(), found=False)
    for key, sections in grades.items():
        if grade_matches(key, g):
            return CurriculumLookup(subject=canonical, grade=g,
                                    sections=sections, found=True)
    return CurriculumLookup(subject=canonical, grade=g, sections=(), found=False)
```

- [ ] **Step 3:** Прогон юнитов и стиль:

```bash
.venv/Scripts/python.exe -m pytest tests/test_curriculum.py -q
.venv/Scripts/python.exe -m ruff check src/curriculum tests/test_curriculum.py -q
```

Expected: 8 PASS; ruff чист.

---

### Task 3: `src/curriculum/service.py` — сверка и покрытие + юнит-тесты

**Files:**
- Create: `src/curriculum/service.py`
- Modify: `tests/test_curriculum.py` (добавить секцию service)

**Interfaces:**
- Consumes: `CurriculumLookup`, `CurriculumSection`, `normalize_title`,
  `resolve_subject` из `src.curriculum.catalog`.
- Produces (используются в Task 4/5):
  - `section_match(lookup: CurriculumLookup, topic: str)
    -> CurriculumSection | None`;
  - `align_topic(lookup: CurriculumLookup, topic: str) -> dict`;
  - `build_coverage(lookup: CurriculumLookup, rows: list[dict]) -> dict`.

- [ ] **Step 1: падающий тест** — в `tests/test_curriculum.py` расширить
  верхний блок импорта (см. Task 2 Step 1): добавить строки
  `align_topic, build_coverage, section_match` из
  `src.curriculum.service` (иначе ruff E402 «import not at top»). Затем
  дописать в конец файла тесты service:

```python
def test_section_match_normalized_equal_and_prefix():
    lookup = curriculum_for("математика", "8 класс")
    sec = section_match(lookup, "Параграф 3: Теорема Виета")
    assert sec is not None
    assert sec.code == "МАТ.8.1"
    assert section_match(lookup, "квадратные уравнения").code == "МАТ.8.1"
    assert section_match(lookup, "параллелограмм").code == "МАТ.8.3"


def test_section_match_none_out_of_program():
    lookup = curriculum_for("математика", "8 класс")
    assert section_match(lookup, "интегралы") is None
    assert section_match(curriculum_for("математика", "9"), "квадратные уравнения") is None


def test_align_topic_statuses():
    matched = align_topic(curriculum_for("математика", "8 класс"), "квадратные уравнения")
    assert matched["status"] == "matched"
    assert matched["code"] == "МАТ.8.1"
    assert matched["section"] == "Квадратные уравнения"
    assert matched["matched_topic"] == "квадратные уравнения"
    miss = align_topic(curriculum_for("математика", "8 класс"), "интегралы")
    assert miss["status"] == "not_found"
    assert miss["code"] is None
    no_cat = align_topic(curriculum_for("математика", "11"), "интегралы")
    assert no_cat["status"] == "catalog_unavailable"


def _row(topic, subject, mastery=0.0, status="not_studied"):
    return {"topic": topic, "subject": subject, "mastery": mastery,
            "status": status, "attempts": 1, "correct": 1}


def test_build_coverage_counts_and_ignores_foreign_subject():
    lookup = curriculum_for("математика", "8 класс")
    rows = [
        _row("квадратные уравнения", "математика", mastery=0.9, status="mastered"),
        _row("парабола", "астрономия", mastery=0.8),
        _row("теорема виета", "история", mastery=0.5),
    ]
    cov = build_coverage(lookup, rows)
    assert cov["stats"] == {"total": 3, "covered": 1, "remaining": 2}
    s0 = cov["sections"][0]
    assert s0["status"] == "covered"
    assert s0["student_topics"] == ["квадратные уравнения"]
    assert s0["mastery"] == 0.9
    assert cov["sections"][1]["status"] == "not_covered"
    assert cov["remaining_sections"] == ["Квадратичная функция", "Четырёхугольники"]


def test_build_coverage_resolves_subject_synonym():
    lookup = curriculum_for("математика", "8 класс")
    cov = build_coverage(lookup, [_row("параллелограмм", "алгебра", mastery=0.6)])
    assert cov["stats"]["covered"] == 1
    assert cov["sections"][2]["status"] == "covered"
    assert cov["sections"][2]["student_topics"] == ["параллелограмм"]


def test_build_coverage_empty_and_unavailable():
    lookup = curriculum_for("математика", "8 класс")
    cov = build_coverage(lookup, [])
    assert cov["stats"]["covered"] == 0
    assert cov["stats"]["total"] == 3
    none_lookup = curriculum_for("математика", "9")
    assert build_coverage(none_lookup, [])["found"] is False
```

- [ ] **Step 2: реализация** — `src/curriculum/service.py`:

```python
"""Чистые функции сверки темы и покрытия программы (без sqlite/LLM).

Спека §5. Входные строки тем — payload Слоя 2 (row_payload,
src/student/student_kg.py:24-60): topic/subject/mastery/status/…
"""

from __future__ import annotations

from .catalog import CurriculumLookup, CurriculumSection, normalize_title, resolve_subject


def section_match(lookup: CurriculumLookup, topic: str) -> CurriculumSection | None:
    """Первый раздел с алиасом, равным нормализованной теме (иначе None)."""
    if not lookup.found:
        return None
    norm = normalize_title(topic)
    if not norm:
        return None
    for section in lookup.sections:
        for alias in section.topics:
            if normalize_title(alias) == norm:
                return section
    return None


def align_topic(lookup: CurriculumLookup, topic: str) -> dict:
    """Сверка одной темы: status matched|not_found|catalog_unavailable."""
    base = {
        "subject": lookup.subject,
        "grade": lookup.grade,
        "found": lookup.found,
        "topic": topic,
    }
    if not lookup.found:
        return {**base, "status": "catalog_unavailable",
                "code": None, "section": None, "matched_topic": None}
    section = section_match(lookup, topic)
    if section is None:
        return {**base, "status": "not_found",
                "code": None, "section": None, "matched_topic": None}
    return {**base, "status": "matched", "code": section.code,
            "section": section.section, "matched_topic": topic}


def build_coverage(lookup: CurriculumLookup, rows: list[dict]) -> dict:
    """Покрытие программы темами ученика (строки Слоя 2, спека §7.3)."""
    if not lookup.found:
        return {
            "subject": lookup.subject,
            "grade": lookup.grade,
            "found": False,
            "sections": [],
            "stats": {"total": 0, "covered": 0, "remaining": 0},
            "remaining_sections": [],
        }
    by_title: dict[str, list[dict]] = {}
    for row in rows:
        if resolve_subject(str(row.get("subject") or "")) != lookup.subject:
            continue
        by_title.setdefault(normalize_title(str(row.get("topic") or "")), []).append(row)

    sections_out: list[dict] = []
    covered = 0
    remaining: list[str] = []
    for section in lookup.sections:
        matched: list[dict] = []
        for alias in section.topics:
            matched.extend(by_title.get(normalize_title(alias), []))
        if matched:
            status = "covered"
            covered += 1
        else:
            status = "not_covered"
            remaining.append(section.section)
        mastery = max(
            (float(m.get("mastery") or 0.0) for m in matched), default=None
        )
        sections_out.append({
            "code": section.code,
            "section": section.section,
            "topics": list(section.topics),
            "status": status,
            "student_topics": [str(m.get("topic") or "") for m in matched],
            "mastery": mastery,
        })
    total = len(lookup.sections)
    return {
        "subject": lookup.subject,
        "grade": lookup.grade,
        "found": True,
        "sections": sections_out,
        "stats": {"total": total, "covered": covered,
                  "remaining": total - covered},
        "remaining_sections": remaining,
    }
```

- [ ] **Step 3:** Прогон:

```bash
.venv/Scripts/python.exe -m pytest tests/test_curriculum.py -q
.venv/Scripts/python.exe -m ruff check src/curriculum tests/test_curriculum.py -q
```

Expected: 14 PASS (8 из Task 2 + 6 новых); ruff чист.

---

### Task 4: Server API — три эндпоинта + интеграционные тесты

**Files:**
- Modify: `src/api/server.py`
- Create: `tests/test_curriculum_api.py`

**Interfaces:**
- Consumes: `src.curriculum.catalog.curriculum_for`,
  `src.curriculum.service.align_topic/build_coverage`;
  `StudentStore.list_topics` (`src/student/store.py:182-189`).
- Produces (форматы — спека §7.1-§7.3):
  - `GET /curriculum` (query `subject`, `grade`);
  - `GET /curriculum/match` (query `subject`, `grade`, обязательный `topic`);
  - `GET /student/{student_id}/curriculum` (query `subject`, `grade`);
  - helper `_curriculum_ctx(app, student_id, subject, grade)`.

- [ ] **Step 1: падающий тест** — `tests/test_curriculum_api.py`:

```python
"""Интеграция API ФГОС-сверки (спека §7). Стиль test_kg_server_hooks.py:67-93."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.api.server import create_app
from src.student.store import StudentStore


class _NoAgent:
    def __call__(self):
        raise AssertionError("агент не должен вызываться на ФГОС-эндпоинтах")


def _app(tmp_path, store=None):
    if store is None:
        store = StudentStore(str(tmp_path / "s.db"))
    return create_app(runtime_factory=_NoAgent(), student_store=store)


def _rec(correct: bool):
    return SimpleNamespace(correct=correct)


def test_curriculum_list_found(tmp_path):
    client = TestClient(_app(tmp_path))
    resp = client.get("/curriculum", params={"subject": "математика", "grade": "8 класс"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["subject"] == "математика"
    assert body["grade"] == 8
    assert [s["code"] for s in body["sections"]] == ["МАТ.8.1", "МАТ.8.2", "МАТ.8.3"]


def test_curriculum_list_not_found(tmp_path):
    client = TestClient(_app(tmp_path))
    resp = client.get("/curriculum", params={"subject": "физика", "grade": "8 класс"})
    body = resp.json()
    assert body["found"] is False
    assert body["sections"] == []


def test_curriculum_match_statuses(tmp_path):
    client = TestClient(_app(tmp_path))
    ok = client.get("/curriculum/match", params={
        "subject": "алгебра", "grade": "8", "topic": "квадратные уравнения"})
    assert ok.json()["status"] == "matched"
    assert ok.json()["code"] == "МАТ.8.1"
    miss = client.get("/curriculum/match", params={
        "subject": "математика", "grade": "8 класс", "topic": "интегралы"})
    assert miss.json()["status"] == "not_found"
    unav = client.get("/curriculum/match", params={
        "subject": "математика", "grade": "11", "topic": "интегралы"})
    assert unav.json()["status"] == "catalog_unavailable"


def test_curriculum_coverage_with_student(tmp_path):
    store = StudentStore(str(tmp_path / "s.db"))
    store.apply_result("stu_c", "квадратные уравнения", "математика", _rec(True))
    store.apply_result("stu_c", "интегралы", "математика", _rec(True))  # не в программе
    client = TestClient(_app(tmp_path, store=store))
    resp = client.get("/student/stu_c/curriculum", params={
        "subject": "математика", "grade": "8 класс"})
    body = resp.json()
    assert body["found"] is True
    assert body["stats"]["total"] == 3
    assert body["stats"]["covered"] == 1
    assert body["stats"]["remaining"] == 2
    assert body["sections"][0]["status"] == "covered"
    assert body["sections"][1]["status"] == "not_covered"


def test_curriculum_coverage_empty_student_and_unknown_program(tmp_path):
    store = StudentStore(str(tmp_path / "s.db"))
    client = TestClient(_app(tmp_path, store=store))
    resp = client.get("/student/stu_x/curriculum", params={
        "subject": "математика", "grade": "8 класс"})
    body = resp.json()
    assert body["found"] is True
    assert body["stats"] == {"total": 3, "covered": 0, "remaining": 3}
    unknown = client.get("/student/stu_x/curriculum", params={
        "subject": "математика", "grade": "11"})
    assert unknown.json()["found"] is False
    store.close()
```

- [ ] **Step 2: helper и эндпоинты** — в `src/api/server.py`:

  1) Сразу после `_resolve_subject_grade` (`:718-734`) добавить helper
  `_curriculum_ctx` (код — спека §7.4, как есть).

  2) Вставить три эндпоинта в `create_app` между закрытием
  `/student/{student_id}/recommendations` (`:2127`) и
  `@app.get("/student/{student_id}/graph")` (`:2129`):

```python
    @app.get("/curriculum")
    def curriculum_list(subject: str = "", grade: str = "") -> dict[str, Any]:
        """Список разделов учебной программы для предмета и класса (спека §7.1)."""
        from src.curriculum.catalog import curriculum_for

        lookup = curriculum_for(subject, grade)
        if not settings.curriculum_enabled or not lookup.found:
            return {"subject": lookup.subject, "grade": lookup.grade,
                    "found": False, "sections": []}
        return {
            "subject": lookup.subject,
            "grade": lookup.grade,
            "found": True,
            "sections": [
                {"code": s.code, "section": s.section,
                 "topics": list(s.topics), "order": i}
                for i, s in enumerate(lookup.sections)
            ],
        }

    @app.get("/curriculum/match")
    def curriculum_match(
        subject: str = "",
        grade: str = "",
        topic: str = Query(..., min_length=1, description="Тема для сверки"),
    ) -> dict[str, Any]:
        """Сверка темы с программой предмета/класса (спека §7.2)."""
        from src.curriculum.catalog import curriculum_for
        from src.curriculum.service import align_topic

        lookup = curriculum_for(subject, grade)
        if not settings.curriculum_enabled:
            return {"subject": lookup.subject, "grade": lookup.grade,
                    "found": False, "topic": topic,
                    "status": "catalog_unavailable", "code": None,
                    "section": None, "matched_topic": None}
        return align_topic(lookup, topic)

    @app.get("/student/{student_id}/curriculum")
    def student_curriculum(
        student_id: str, subject: str = "", grade: str = ""
    ) -> dict[str, Any]:
        """Покрытие учебной программы темами ученика (спека §7.3)."""
        from src.curriculum.service import build_coverage

        lookup, store = _curriculum_ctx(app, student_id, subject, grade)
        if not settings.curriculum_enabled or not lookup.found:
            return {
                "student_id": student_id, "subject": lookup.subject,
                "grade": lookup.grade, "found": False, "sections": [],
                "stats": {"total": 0, "covered": 0, "remaining": 0},
                "remaining_sections": [],
            }
        rows: list[dict] = []
        if store is not None:
            try:
                rows = store.list_topics(student_id)
            except Exception:  # noqa: BLE001 — сбой БД не роняет ответ
                rows = []
        return {"student_id": student_id, **build_coverage(lookup, rows)}
```

  Эндпоинты определены внутри `create_app` и используют замыкание на локальную
  `app` (паттерн `/student/{id}/knowledge-graph`, `server.py:2090`);
  `_curriculum_ctx` — модульный helper, принимает `app` первым аргументом.

- [ ] **Step 3:** Прогон:

```bash
.venv/Scripts/python.exe -m pytest tests/test_curriculum_api.py -q
.venv/Scripts/python.exe -m ruff check src/api/server.py tests/test_curriculum_api.py -q
```

Expected: 5 PASS; ruff чист.

---

### Task 5: SSE-заметка `fgos.note` в `_run_chat` + тесты

**Files:**
- Modify: `src/api/session_store.py`
- Modify: `src/api/server.py`
- Modify: `tests/test_kg_server_hooks.py`

**Interfaces:**
- Produces: `ChatSession.last_fgos_topic: str = ""`.
- Emits SSE: `on_event("system", {kind: "fgos.note", message, topic, subject,
  grade})` — один раз на тему в сессии, только для известной программы при
  несовпадении темы.

- [ ] **Step 1: падающий тест** — добавить в `tests/test_kg_server_hooks.py`
  (после `test_mastery_gate_emitted_once`, `:108-127`):

```python
def test_fgos_note_emitted_once_for_out_of_program_topic(tmp_path):
    """Тема вне программы математики-8 -> system-событие fgos.note один раз."""
    store = StudentStore(str(tmp_path / "s.db"))
    app = _app(tmp_path, ['{"type":"theory","text":"Изучаем интегралы."}'], store)
    client = TestClient(app)
    base = {"message": "расскажи про интегралы", "session_id": "ses_f",
            "student_id": "stu_f", "topic": "интегралы",
            "subject": "математика", "grade": "8 класс"}

    first = client.post("/chat/stream", json=base)
    assert first.status_code == 200
    assert "fgos.note" in first.text
    assert "не входит в учебную программу" in first.text

    second = client.post("/chat/stream", json=base)
    assert second.status_code == 200
    assert "fgos.note" not in second.text
    store.close()


def test_fgos_note_absent_for_in_program_topic(tmp_path):
    """Тема «квадратные уравнения» (МАТ.8.1) не порождает fgos.note."""
    store = StudentStore(str(tmp_path / "s.db"))
    app = _app(tmp_path, ['{"type":"theory","text":"Квадратные уравнения."}'], store)
    client = TestClient(app)
    base = {"message": "изучаем", "session_id": "ses_g2",
            "student_id": "stu_g2", "topic": "квадратные уравнения",
            "subject": "математика", "grade": "8 класс"}
    resp = client.post("/chat/stream", json=base)
    assert resp.status_code == 200
    assert "fgos.note" not in resp.text
    store.close()
```

- [ ] **Step 2:** `src/api/session_store.py` — поле в `ChatSession` сразу после
  `last_gate_topic: str = ""` (`:33`):

```python
    last_fgos_topic: str = ""
```

- [ ] **Step 3:** `src/api/server.py` — в `_run_chat` после mastery-гейта
  (`:1433`) и до `if body.topic:` провижининга (`:1435`) вставить блок эмиссии
  (код — спека §8.2, как есть).

- [ ] **Step 4:** Прогон:

```bash
.venv/Scripts/python.exe -m pytest tests/test_kg_server_hooks.py -q
.venv/Scripts/python.exe -m ruff check src/api/server.py src/api/session_store.py tests/test_kg_server_hooks.py -q
```

Expected: существующие + 2 новых PASS; ruff чист.

---

### Task 6: Фронтенд — api.js, feedReducer `fgos.note`, тесты

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/components/Chat.jsx`
- Modify: `frontend/src/api.test.js`
- Modify: `frontend/src/components/Chat.test.jsx`

**Interfaces:**
- Produces: `api.getCurriculum(subject, grade)`,
  `api.matchCurriculum(subject, grade, topic)`,
  `api.getCurriculumCoverage(studentId, subject, grade)`.
- `feedReducer`: `system` + `kind === 'fgos.note'` → элемент
  `{kind: 'system-note', content: message}`.

- [ ] **Step 1: падающие тесты.**
  В `frontend/src/api.test.js` (после блока про `getGraph`, `:95-102`):

```js
  it('getCurriculum requests /curriculum with subject and grade', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ found: true }) }))
    await api.getCurriculum('математика', '8 класс')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining(
        `/curriculum?subject=${encodeURIComponent('математика')}&grade=${encodeURIComponent('8 класс')}`,
      ),
      expect.anything(),
    )
  })

  it('matchCurriculum requests /curriculum/match with topic', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ status: 'matched' }) }))
    await api.matchCurriculum('математика', '8', 'квадратные уравнения')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining(
        `/curriculum/match?subject=${encodeURIComponent('математика')}&grade=8&topic=${encodeURIComponent('квадратные уравнения')}`,
      ),
      expect.anything(),
    )
  })

  it('getCurriculumCoverage requests /student/{id}/curriculum', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ found: false }) }))
    await api.getCurriculumCoverage('stu_x', 'физика', '7 класс')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining(
        `/student/stu_x/curriculum?subject=${encodeURIComponent('физика')}&grade=${encodeURIComponent('7 класс')}`,
      ),
      expect.anything(),
    )
  })
```

  В `frontend/src/components/Chat.test.jsx` (в describe feedReducer):

```js
  it('feedReducer turns fgos.note into a system-note item', () => {
    const feed = feedReducer(emptyFeed, {
      event: 'system',
      data: { kind: 'fgos.note', message: 'Тема вне программы.', topic: 'интегралы', subject: 'математика', grade: '8 класс' },
    })
    expect(feed.items[0].kind).toBe('system-note')
    expect(feed.items[0].content).toBe('Тема вне программы.')
  })

  it('renders fgos.note content as a system-note line', () => {
    const feed = feedWith([
      { id: 'c0', kind: 'system-note', content: 'Тема «интегралы» не входит в программу.' },
    ])
    render(<Chat feed={feed} busy={false} onSendUser={vi.fn()} />)
    expect(screen.getByText('Тема «интегралы» не входит в программу.')).toBeInTheDocument()
  })
```

- [ ] **Step 2:** `frontend/src/api.js` — добавить три метода (после
  `getRecommendations`, `:96-97`, стиль экранирования `:94-99`):

```js
  getCurriculum: (subject, grade) =>
    request(`/curriculum?subject=${encodeURIComponent(subject)}&grade=${encodeURIComponent(grade)}`),
  matchCurriculum: (subject, grade, topic) =>
    request(`/curriculum/match?subject=${encodeURIComponent(subject)}&grade=${encodeURIComponent(grade)}&topic=${encodeURIComponent(topic)}`),
  getCurriculumCoverage: (studentId, subject, grade) =>
    request(`/student/${encodeURIComponent(studentId)}/curriculum?subject=${encodeURIComponent(subject)}&grade=${encodeURIComponent(grade)}`),
```

- [ ] **Step 3:** `frontend/src/components/Chat.jsx`, ветка `name === 'system'`
  (`:34-45`), перед `return feed`:

```js
    if (data.kind === 'fgos.note') {
      return {
        ...feed,
        items: [
          ...feed.items,
          { id: `c${feed.items.length}`, kind: 'system-note', content: data.message || '' },
        ],
      }
    }
```

- [ ] **Step 4:** Прогон из `frontend/`:

```bash
npm test -- src/api.test.js src/components/Chat.test.jsx
npm run lint
```

Expected: PASS; lint чист.

---

### Task 7: Фронтенд — блок «Программа (ФГОС)» в `StudentKGPanel`

**Files:**
- Modify: `frontend/src/components/StudentKGPanel.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/index.css`
- Modify: `frontend/src/components/StudentKGPanel.test.jsx`
- Modify: `frontend/src/App.test.jsx`

**Interfaces:**
- `StudentKGPanel` новый проп `grade = ''`; эффект загрузки
  `api.getCurriculumCoverage(studentId, subject, grade)` в состояние `fgos`
  (fail-soft); рендер блока между `.kg-stats` и `.kg-filters`.
- `App.jsx` передаёт `grade={current?.grade || ''}`.

- [ ] **Step 1: падающие тесты.**
  В `frontend/src/components/StudentKGPanel.test.jsx`: расширить
  `vi.mock('../api', …)` ключом `getCurriculumCoverage: vi.fn()`, добавить
  `api.getCurriculumCoverage.mockReset()` в `beforeEach`, затем тесты:

```js
  it('рендерит блок «Программа (ФГОС)» с непройденными разделами при subject+grade', async () => {
    const user = userEvent.setup()
    const onStudy = vi.fn()
    api.getKnowledgeGraph.mockResolvedValue(data())
    api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
    api.getCurriculumCoverage.mockResolvedValue({
      student_id: 'stu_x', subject: 'математика', grade: 8, found: true,
      sections: [], stats: { total: 3, covered: 1, remaining: 2 },
      remaining_sections: ['Квадратичная функция', 'Четырёхугольники'],
    })
    render(<StudentKGPanel studentId="stu_x" subject="математика" grade="8 класс" onStudy={onStudy} />)
    expect(await screen.findByText('Программа (ФГОС)')).toBeInTheDocument()
    expect(screen.getByText('пройдено 1 из 3 разделов')).toBeInTheDocument()
    const buttons = screen.getAllByRole('button', { name: 'Изучить' })
    await user.click(buttons[1])
    expect(onStudy).toHaveBeenCalledWith('Четырёхугольники')
  })

  it('found=false показывает строку «пока не добавлена»', async () => {
    api.getKnowledgeGraph.mockResolvedValue(data())
    api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
    api.getCurriculumCoverage.mockResolvedValue({
      found: false, sections: [], stats: { total: 0, covered: 0, remaining: 0 }, remaining_sections: [],
    })
    render(<StudentKGPanel studentId="stu_x" subject="физика" grade="9 класс" />)
    expect(await screen.findByText(/пока не добавлена/)).toBeInTheDocument()
  })

  it('без grade не вызывает getCurriculumCoverage', async () => {
    api.getKnowledgeGraph.mockResolvedValue(data())
    api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
    render(<StudentKGPanel studentId="stu_x" subject="математика" />)
    await screen.findByText('Сила')
    expect(api.getCurriculumCoverage).not.toHaveBeenCalled()
  })
```

  В `frontend/src/App.test.jsx`: в `vi.mock('./api', …)` (аналог существующих
  ключей) добавить:

```js
    getCurriculumCoverage: vi.fn(() => Promise.resolve({
      found: false, sections: [],
      stats: { total: 0, covered: 0, remaining: 0 },
      remaining_sections: [],
    })),
```

- [ ] **Step 2:** `frontend/src/components/StudentKGPanel.jsx`:

```jsx
  grade = '',
```

  после `reloadKey` в деструктуризации пропсов; эффект (после review-эффекта,
  `:61-75`):

```jsx
  const [fgos, setFgos] = useState(null)
  useEffect(() => {
    if (!studentId || !subject || !grade) {
      setFgos(null)
      return undefined
    }
    let alive = true
    api
      .getCurriculumCoverage(studentId, subject, grade)
      .then((d) => { if (alive) setFgos(d) })
      .catch(() => { if (alive) setFgos(null) })
    return () => { alive = false }
  }, [studentId, subject, grade, reloadKey])
```

  рендер — между закрытием `.kg-stats` (`:149`) и `.kg-filters` (`:151`):

```jsx
              {fgos && (
                <div className="fgos">
                  <div className="fgos-title">Программа (ФГОС)</div>
                  {fgos.found === false ? (
                    <div className="fgos-empty">Программа (ФГОС) для предмета и класса пока не добавлена.</div>
                  ) : (
                    <>
                      <div className="fgos-count">
                        пройдено {fgos.stats?.covered || 0} из {fgos.stats?.total || 0} разделов
                      </div>
                      {(fgos.remaining_sections || []).length > 0 && (
                        <ul className="fgos-list">
                          {(fgos.remaining_sections || []).map((section) => (
                            <li key={section} className="fgos-item">
                              <span className="fgos-item-name">{section}</span>
                              {onStudy ? (
                                <button
                                  type="button"
                                  className="fgos-btn"
                                  onClick={() => onStudy(section)}
                                >
                                  Изучить
                                </button>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                      )}
                    </>
                  )}
                </div>
              )}
```

- [ ] **Step 3:** `frontend/src/App.jsx` — в `<StudentKGPanel … />`
  (`:286-293`) добавить проп `grade={current?.grade || ''}`.

- [ ] **Step 4:** `frontend/src/index.css` — минимальные стили после блока
  `.kg-*` (`:896-965`), на палитре CSS-переменных:

```css
.fgos {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}
.fgos-title {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: var(--ink-soft);
}
.fgos-count { margin: 4px 0 6px; font-size: 12px; color: var(--muted); }
.fgos-empty { font-size: 12px; color: var(--muted); }
.fgos-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.fgos-item { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.fgos-item-name { font-size: 13px; color: var(--ink); }
.fgos-btn { font-family: var(--font-mono); font-size: 11px; padding: 2px 8px; border-radius: 999px; background: var(--green-soft); color: var(--green-strong); border: 1px solid rgba(47, 107, 79, 0.18); cursor: pointer; }
```

- [ ] **Step 5:** Прогон из `frontend/`:

```bash
npm test -- src/components/StudentKGPanel.test.jsx src/App.test.jsx
npm run lint
```

Expected: PASS; lint чист.

---

### Task 8: Полная проверка

**Files:** нет (только прогоны).

- [ ] **Step 1:** Backend — все тесты и ruff из `adaptive_tutor/`:

```bash
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m ruff check src tests -q
```

Ожидается: весь набор PASS (прирост ≈ +21: 14 `test_curriculum.py`,
5 `test_curriculum_api.py`, 2 в `test_kg_server_hooks.py`), ruff чист.

- [ ] **Step 2:** Frontend — весь набор и lint из `frontend/`:

```bash
npm test
npm run lint
```

Ожидается: PASS (прирост ≈ +8: 3 `api.test.js`, 2 `Chat.test.jsx`,
3 `StudentKGPanel.test.jsx`), lint чист.

- [ ] **Step 3:** Ручная сверка ответов эндпоинтов (данные спеки §7):

```bash
.venv/Scripts/python.exe -m pytest tests/test_curriculum.py tests/test_curriculum_api.py tests/test_kg_server_hooks.py -q
```

Expected: 21 PASS суммарно по трём файлам.

---

## Что осталось (после имплементации, вне шагов плана)

- Коммит выполняет драйвер после имплементации по инструкции пользователя
  (в шаги не включён, AGENTS.md).
- Live-smoke с реальным LLM: SSE-заметка `fgos.note` и блок «Программа (ФГОС)»
  в UI (спека §13).
