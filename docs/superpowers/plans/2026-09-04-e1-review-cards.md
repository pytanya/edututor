# E1 — Карточки повторений (SM-2): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить автономную подсистему интервальных повторений SM-2: банк карточек в SQLite, авто-добавление ошибочных ответов квизов, блиц «Повторить (N)» через существующий SSE-чат, UI-кнопку и бейдж.

**Architecture:** Чистые алгоритмы SM-2 (`src/review/sm2.py`) отделены от SQLite. Сервер при каждом evaluation строит детерминированный «answer record» из секрета последнего квиза (`ChatSession.last_quiz`, не из истории) и последнего user-сообщения; review-режим — это ветка в `_run_chat` ДО агента, отдающая карточки как quiz-конверты и грейдящая ответы без вызова LLM-агента (закрытые — сравнением, открытые — коротким LLM-грейдером).

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, sqlite3 stdlib, pytest, ruff; React 19 + Vite (vitest).

**Spec:** `docs/superpowers/specs/2026-09-04-e1-review-cards-design.md`

## Global Constraints

- Python >= 3.11. Все существующие тесты остаются зелёными.
- `ruff check src/ tests/` — чисто (line-length 100).
- Репозиторий без коммитов: шаг «Commit» заменяется на «pytest + ruff». Коммит — только по явному запросу пользователя.
- Русские docstring/комментарии, async/await, Pydantic v2, `threading.Lock` для разделяемого состояния.
- `.env` не менять; новые переменные добавлять в `.env.example`.
- Агент (LLM-цикл) не должен знать про карточки/БД: хуки только в `src/api/server.py`.
- Все конверты, уходящие клиенту, проходят санитайзер (нет `_correct_answer`/`correct_answer`).
- Формулы SM-2 и идентичность карточек — точно по спецe (раздел 4).
- Запрос `chat`/`chat_stream` с `kind="review_request"` НЕ вызывает агента.

---

### Task 1: Конфиг подсистемы повторений

**Files:**
- Modify: `src/config.py` (секция `# Адаптивное обучение (review)` после `# Профиль ученика`)
- Modify: `.env.example`
- Test: нет (проверка инлайн)

**Interfaces:**
- Consumes: `pydantic_settings.BaseSettings` (уже есть).
- Produces: `settings.review_enabled: bool`, `settings.review_quiz_size: int`,
  `settings.review_bank_max_cards: int`.

- [ ] **Step 1: Добавить поля в `src/config.py`**

После секции «Профиль ученика» (после `resolved_student_db_path`) добавить:

```python
    # Адаптивное обучение: интервальные повторения (SM-2)
    review_enabled: bool = Field(
        default=True, description="Включить карточки для повторений (SM-2)"
    )
    review_quiz_size: int = Field(
        default=5, ge=1, le=20, description="Размер блица повторения (кол-во due-карточек)"
    )
    review_bank_max_cards: int = Field(
        default=200, ge=1, description="Максимум карточек повторения на ученика"
    )
```

- [ ] **Step 2: Добавить в `.env.example`**

```text
# Интервальные повторения (SM-2)
TUTOR_REVIEW_ENABLED=true
TUTOR_REVIEW_QUIZ_SIZE=5
TUTOR_REVIEW_BANK_MAX_CARDS=200
```

- [ ] **Step 3: Проверка**

Run: `.venv/Scripts/python.exe -c "from src.config import settings; print(settings.review_enabled, settings.review_quiz_size, settings.review_bank_max_cards)"` (cwd `adaptive_tutor`)
Expected: `True 5 200`

- [ ] **Step 4: ruff**

Run: `.venv/Scripts/ruff.exe check src/config.py`
Expected: All checks passed.

---

### Task 2: Чистое ядро SM-2 (`src/review/`)

**Files:**
- Create: `src/review/__init__.py`
- Create: `src/review/sm2.py`
- Test: `tests/test_review_sm2.py`

**Interfaces:**
- Consumes: stdlib только (`datetime`, `hashlib`).
- Produces:
  - `now_iso() -> str` — `datetime.now().isoformat(timespec="seconds")`.
  - `parse_iso(value: str) -> datetime | None` — `fromisoformat`, на ошибку None.
  - `card_id_for(question: str) -> str` — `sha256(question.strip().encode("utf-8")).hexdigest()[:16]`.
  - `is_due(card: dict) -> bool` — пустой/непарсящийся `due_at` → True; иначе `due_at <= now`.
  - `apply_sm2(card: dict, correct: bool) -> dict` — вернёт обновлённые поля
    `{reps, interval_days, ease, lapses, last_reviewed, due_at}` (см. код ниже).

- [ ] **Step 1: Создать `src/review/__init__.py`**

```python
"""Интервальные повторения (SM-2): чистая логика + грейдер."""
```

- [ ] **Step 2: Создать `src/review/sm2.py`**

```python
"""SM-2 (SuperMemo-2): чистые функции без sqlite.

Формулы и константы — 1:1 с референсом project_work (src/review.py).
"""

import datetime
import hashlib

MIN_EASE = 1.3
INITIAL_EASE = 2.5
INITIAL_INTERVAL = 1.0


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def card_id_for(question: str) -> str:
    """Стабильный ID карточки по тексту вопроса (дедуп)."""
    return hashlib.sha256((question or "").strip().encode("utf-8")).hexdigest()[:16]


def is_due(card: dict) -> bool:
    """Due = нет due_at / непарсится / наступило."""
    if not card.get("due_at"):
        return True
    due = parse_iso(str(card["due_at"]))
    return due is None or due <= datetime.datetime.now()


def apply_sm2(card: dict, correct: bool) -> dict:
    """Применяет SM-2 к SM-2-полям карточки и возвращает обновлённые поля.

    Не мутирует входной dict. Вызывающий сохраняет результат в БД.
    """
    reps = int(card.get("reps", 0))
    interval = float(card.get("interval_days", INITIAL_INTERVAL))
    ease = float(card.get("ease", INITIAL_EASE))
    lapses = int(card.get("lapses", 0))
    now = datetime.datetime.now()
    if correct:
        reps += 1
        interval = INITIAL_INTERVAL if reps == 1 else round(interval * ease, 1)
        ease = max(MIN_EASE, round(ease + (0.1 - max(0, 3 - reps) * 0.05), 2))
    else:
        reps = 0
        interval = INITIAL_INTERVAL
        lapses += 1
        ease = max(MIN_EASE, round(ease - 0.2, 2))
    due_at = (now + datetime.timedelta(days=interval)).isoformat(timespec="seconds")
    return {
        "reps": reps,
        "interval_days": interval,
        "ease": ease,
        "lapses": lapses,
        "last_reviewed": now_iso(),
        "due_at": due_at,
    }
```

- [ ] **Step 3: Создать `tests/test_review_sm2.py`**

```python
"""Тесты чистого SM-2-ядра."""

import datetime

from src.review.sm2 import apply_sm2, card_id_for, is_due, now_iso


def _card(**overrides):
    base = {
        "card_id": "x", "due_at": now_iso(), "interval_days": 1.0,
        "ease": 2.5, "reps": 0, "lapses": 0,
    }
    base.update(overrides)
    return base


def test_card_id_stable_and_trimmed():
    assert card_id_for("  вопрос?  ") == card_id_for("вопрос?")
    assert len(card_id_for("вопрос")) == 16


def test_first_correct_interval_one_day():
    out = apply_sm2(_card(reps=0), True)
    assert out["reps"] == 1
    assert out["interval_days"] == 1.0
    assert out["ease"] == 2.5  # бонус при reps=1 равен 0.0
    assert out["lapses"] == 0


def test_second_correct_multiplies_interval():
    out = apply_sm2(_card(reps=1, interval_days=1.0, ease=2.5), True)
    assert out["reps"] == 2
    assert out["interval_days"] == 2.5
    assert out["ease"] == 2.55


def test_incorrect_resets_and_lapses():
    out = apply_sm2(_card(reps=5, interval_days=10.0, ease=2.5), False)
    assert out["reps"] == 0
    assert out["interval_days"] == 1.0
    assert out["lapses"] == 1
    assert out["ease"] == 2.3


def test_ease_min_clamp():
    out = apply_sm2(_card(reps=0, ease=1.3), False)
    assert out["ease"] == 1.3


def test_due_at_in_future_after_correct():
    out = apply_sm2(_card(reps=0), True)
    assert out["due_at"] > now_iso()


def test_is_due_empty_and_parse():
    assert is_due(_card(due_at=""))
    assert is_due(_card(due_at="не-дата"))
    future = (datetime.datetime.now() + datetime.timedelta(days=2)).isoformat()
    assert not is_due(_card(due_at=future))
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_review_sm2.py -q`
Expected: 7 PASS.

- [ ] **Step 5: ruff**

Run: `.venv/Scripts/ruff.exe check src/review/ tests/test_review_sm2.py`
Expected: All checks passed.

---

### Task 3: Answer record + санитайзер конвертов

**Files:**
- Create: `src/student/answer_record.py`
- Modify: `src/agent/envelope.py` (добавить `sanitize_envelope`)
- Test: `tests/test_answer_record.py`

**Interfaces:**
- Consumes: `schemas.ContentEnvelope` (`src/models/schemas.py`).
- Produces:
  - `AnswerRecord` dataclass с полями: `record_id: str`, `session_id: str`, `student_id: str`,
    `topic: str`, `subject: str`, `correct: bool`, `feedback: str`, `question: str = ""`,
    `options: list | None = None`, `answer_type: str = "open"`, `difficulty: str = "medium"`,
    `correct_answer: str = ""`, `student_answer: str = "", ts: float`.
  - `build_answer_record(*, session_id, student_id, topic, subject, student_answer, correct,
    feedback, last_quiz: dict | None) -> AnswerRecord` — генерирует `record_id = rec_<uuid12>`,
    `ts = time.time()`, поля квиза из `last_quiz` (при наличии).
  - `sanitize_envelope(env: ContentEnvelope) -> ContentEnvelope` — копия без payload-ключей,
    начинающихся с `_`, и без ключа `correct_answer`.

- [ ] **Step 1: Создать `src/student/answer_record.py`**

```python
"""Answer record — единый «результат ответа» (серверная склейка квиза и оценки).

Источник правды для review-карточек (E1), mastery (E2) и wiki/CSV (E4).
"""

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class AnswerRecord:
    record_id: str
    session_id: str
    student_id: str
    topic: str
    subject: str
    correct: bool
    feedback: str
    question: str = ""
    options: list | None = None
    answer_type: str = "open"
    difficulty: str = "medium"
    correct_answer: str = ""
    student_answer: str = ""
    ts: float = field(default_factory=time.time)


def build_answer_record(
    *,
    session_id: str,
    student_id: str,
    topic: str,
    subject: str,
    student_answer: str,
    correct: bool,
    feedback: str,
    last_quiz: dict | None,
) -> AnswerRecord:
    """Собирает AnswerRecord из секрета последнего квиза и фидбека evaluation."""
    q = last_quiz or {}
    return AnswerRecord(
        record_id=f"rec_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        student_id=student_id,
        topic=topic,
        subject=subject,
        correct=bool(correct),
        feedback=feedback or "",
        question=str(q.get("question") or ""),
        options=q.get("options"),
        answer_type=str(q.get("answer_type") or "open"),
        difficulty=str(q.get("difficulty") or "medium"),
        correct_answer=str(q.get("correct_answer") or ""),
        student_answer=student_answer or "",
    )
```

- [ ] **Step 2: Добавить санитайзер в `src/agent/envelope.py`**

```python
_HIDDEN_PAYLOAD_PREFIXES = ("_",)


def sanitize_envelope(env: ContentEnvelope) -> ContentEnvelope:
    """Копия конверта без скрытых полей payload (_... и correct_answer)."""
    payload = {
        k: v for k, v in (env.payload or {}).items()
        if not (k.startswith(_HIDDEN_PAYLOAD_PREFIXES) or k == "correct_answer")
    }
    return env.model_copy(update={"payload": payload})
```

- [ ] **Step 3: Создать `tests/test_answer_record.py`**

```python
"""Тесты answer record и санитайзера."""

from src.agent.envelope import sanitize_envelope
from src.models.schemas import ContentEnvelope
from src.student.answer_record import build_answer_record


def test_record_without_quiz():
    r = build_answer_record(
        session_id="ses_1", student_id="stu_1", topic="t", subject="s",
        student_answer="ответ", correct=False, feedback="нет", last_quiz=None,
    )
    assert r.correct is False
    assert r.question == ""
    assert r.correct_answer == ""
    assert r.record_id.startswith("rec_")
    assert r.ts > 0


def test_record_with_quiz_secret():
    quiz = {"question": "Q?", "options": ["a", "b"], "answer_type": "single",
            "difficulty": "hard", "correct_answer": "a"}
    r = build_answer_record(
        session_id="ses_1", student_id="stu_1", topic="t", subject="s",
        student_answer="Ответ: b", correct=False, feedback="нет", last_quiz=quiz,
    )
    assert r.question == "Q?"
    assert r.options == ["a", "b"]
    assert r.answer_type == "single"
    assert r.correct_answer == "a"


def test_sanitize_removes_secret_keys():
    env = ContentEnvelope(
        type="quiz", text="Q?", payload={"answer_type": "single", "options": ["a", "b"],
                                         "_correct_answer": "a", "correct_answer": "a"},
    )
    out = sanitize_envelope(env)
    assert "_correct_answer" not in out.payload
    assert "correct_answer" not in out.payload
    assert out.payload["options"] == ["a", "b"]
    assert out.text == "Q?"


def test_sanitize_keeps_other_keys():
    env = ContentEnvelope(type="theory", text="hi", payload={"topic": "t"})
    assert sanitize_envelope(env).payload == {"topic": "t"}
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_answer_record.py -q`
Expected: 4 PASS.

- [ ] **Step 5: ruff**

Run: `.venv/Scripts/ruff.exe check src/student/answer_record.py src/agent/envelope.py tests/test_answer_record.py`
Expected: All checks passed.

---

### Task 4: Review-методы `StudentStore` (таблица + SM-2)

**Files:**
- Modify: `src/student/store.py`
- Test: `tests/test_review_store.py`

**Interfaces:**
- Consumes: `src.review.sm2.*` (Task 2).
- Produces (методы `StudentStore`):
  - `add_review_card(student_id: str, record: dict) -> bool` — True если добавлена новая;
    False при пустом question или если уже была (тогда только refresh).
  - `get_due_review(student_id: str, subject: str = "", limit: int = 5) -> list[dict]`
  - `review_card(student_id: str, card_id: str, correct: bool) -> dict | None`
  - `review_stats(student_id: str) -> dict` — `{"total", "due", "lapses", "by_topic"}`
  - `list_review_cards(student_id: str, limit: int = 50, only_due: bool = False) -> list[dict]`
  - Хелпер `card_to_dict(row) -> dict` — словарь карточки с ключами модели.

- [ ] **Step 1: Добавить таблицу в `_SCHEMA`** (`src/student/store.py:15-39`)

```sql
CREATE TABLE IF NOT EXISTS review_cards (
  student_id TEXT NOT NULL,
  card_id    TEXT NOT NULL,
  subject    TEXT DEFAULT '',
  topic      TEXT DEFAULT '',
  question   TEXT NOT NULL,
  options    TEXT DEFAULT NULL,
  answer_type TEXT DEFAULT 'open',
  correct_answer TEXT DEFAULT '',
  difficulty TEXT DEFAULT 'medium',
  added_at   TEXT DEFAULT '',
  last_reviewed TEXT DEFAULT '',
  due_at     TEXT DEFAULT '',
  interval_days REAL DEFAULT 1.0,
  ease       REAL DEFAULT 2.5,
  reps       INTEGER DEFAULT 0,
  lapses     INTEGER DEFAULT 0,
  PRIMARY KEY (student_id, card_id)
);
CREATE INDEX IF NOT EXISTS idx_review_due
  ON review_cards (student_id, subject, due_at);
```

- [ ] **Step 2: Добавить методы** (в конец класса `StudentStore`, перед `close`)

```python
    @staticmethod
    def _card_from_row(row: dict) -> dict:
        row = dict(row)
        row["options"] = json.loads(row["options"]) if row.get("options") else None
        return row

    def add_review_card(self, student_id: str, record: dict) -> bool:
        """Добавить/освежить карточку по записи. True — добавлена новая."""
        question = str(record.get("question") or "").strip()
        if not question:
            return False
        cid = card_id_for(question)
        now = now_iso()
        existing = self._rows(
            "SELECT * FROM review_cards WHERE student_id = ? AND card_id = ?",
            (student_id, cid),
        )
        if not existing:
            options = record.get("options")
            with self._lock:
                self._conn.execute(
                    "INSERT INTO review_cards (student_id, card_id, subject, topic, "
                    "question, options, answer_type, correct_answer, difficulty, "
                    "added_at, due_at, interval_days, ease) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        student_id, cid,
                        str(record.get("subject") or ""), str(record.get("topic") or ""),
                        question,
                        json.dumps(options, ensure_ascii=False) if options else None,
                        str(record.get("answer_type") or "open"),
                        str(record.get("correct_answer") or ""),
                        str(record.get("difficulty") or "medium"),
                        now, now, 1.0, 2.5,
                    ),
                )
                # Кап банка: удалить самые старые добавленные сверх лимита.
                self._conn.execute(
                    "DELETE FROM review_cards WHERE student_id = ? AND card_id IN ("
                    "  SELECT card_id FROM review_cards WHERE student_id = ? "
                    "  ORDER BY added_at DESC LIMIT -1 OFFSET ?)",
                    (student_id, student_id, int(settings.review_bank_max_cards)),
                )
                self._conn.commit()
            return True
        # Refresh существующей (SM-2-состояние не трогаем).
        self._exec(
            "UPDATE review_cards SET topic = CASE WHEN ? <> '' THEN ? ELSE topic END, "
            "subject = CASE WHEN ? <> '' THEN ? ELSE subject END, "
            "correct_answer = CASE WHEN ? <> '' THEN ? ELSE correct_answer END, "
            "last_reviewed = '' "
            "WHERE student_id = ? AND card_id = ?",
            (
                str(record.get("topic") or ""), str(record.get("topic") or ""),
                str(record.get("subject") or ""), str(record.get("subject") or ""),
                str(record.get("correct_answer") or ""), str(record.get("correct_answer") or ""),
                student_id, cid,
            ),
        )
        return False

    def get_due_review(self, student_id: str, subject: str = "", limit: int = 5) -> list[dict]:
        rows = self._rows(
            "SELECT * FROM review_cards WHERE student_id = ? "
            "AND (subject = ? OR ? = '') AND due_at <> '' "
            "ORDER BY due_at ASC LIMIT ?",
            (student_id, subject, subject, int(limit)),
        )
        return [self._card_from_row(r) for r in rows]

    def review_card(self, student_id: str, card_id: str, correct: bool) -> dict | None:
        rows = self._rows(
            "SELECT * FROM review_cards WHERE student_id = ? AND card_id = ?",
            (student_id, card_id),
        )
        if not rows:
            return None
        card = self._card_from_row(rows[0])
        updated = apply_sm2(card, correct)
        self._exec(
            "UPDATE review_cards SET reps = ?, interval_days = ?, ease = ?, "
            "lapses = ?, last_reviewed = ?, due_at = ? "
            "WHERE student_id = ? AND card_id = ?",
            (
                updated["reps"], updated["interval_days"], updated["ease"],
                updated["lapses"], updated["last_reviewed"], updated["due_at"],
                student_id, card_id,
            ),
        )
        card.update(updated)
        return card

    def review_stats(self, student_id: str) -> dict:
        rows = self._rows(
            "SELECT card_id, due_at, lapses, topic FROM review_cards WHERE student_id = ?",
            (student_id,),
        )
        total = len(rows)
        due = sum(1 for r in rows if is_due(r))
        by_topic: dict[str, int] = {}
        for r in rows:
            by_topic[r["topic"]] = by_topic.get(r["topic"], 0) + 1
        return {"total": total, "due": due, "lapses": sum(r["lapses"] for r in rows), "by_topic": by_topic}

    def list_review_cards(self, student_id: str, limit: int = 50, only_due: bool = False) -> list[dict]:
        sql = "SELECT * FROM review_cards WHERE student_id = ?"
        if only_due:
            sql += " AND due_at <> ''"
        sql += " ORDER BY due_at ASC LIMIT ?"
        rows = self._rows(sql, (student_id, int(limit)))
        return [self._card_from_row(r) for r in rows]
```

Не забудьте импорты в `src/student/store.py`: `import json`, и
`from ..config import settings` (только если settings ещё не импортирован — проверьте; при
нежелательной зависимости от config передавайте `max_cards` аргументом: лучше
`add_review_card(..., max_cards: int | None = None)` и читать `settings.review_bank_max_cards`
через локальный импорт внутри метода, как уже делает `touch_topic` для `.adaptive`).

> Примечание по SQL `OFFSET ?` c `LIMIT -1`: это корректный sqlite-паттерн «все, кроме первых N».
> Если ruff/mypy мешают — замените на два запроса (SELECT card_id OFFSET, затем DELETE по id).

- [ ] **Step 3: Создать `tests/test_review_store.py`**

```python
"""Тесты review-методов StudentStore (SQLite)."""

import pytest

from src.student.store import StudentStore


@pytest.fixture
def store(tmp_path):
    s = StudentStore(str(tmp_path / "students.db"))
    yield s
    s.close()


def _rec(question="Q?", **kw):
    data = {"question": question, "topic": "t", "subject": "s",
            "answer_type": "single", "options": ["a", "b"], "difficulty": "medium"}
    data.update(kw)
    return data


def test_add_and_dedupe(store):
    assert store.add_review_card("stu_1", _rec(question="Вопрос один?")) is True
    assert store.add_review_card("stu_1", _rec(question="Вопрос один?")) is False
    stats = store.review_stats("stu_1")
    assert stats["total"] == 1
    assert stats["due"] == 1


def test_add_empty_question(store):
    assert store.add_review_card("stu_1", _rec(question="   ")) is False
    assert store.review_stats("stu_1")["total"] == 0


def test_due_filter_by_subject_and_limit(store):
    store.add_review_card("stu_1", _rec(question="A?", subject="физика"))
    store.add_review_card("stu_1", _rec(question="B?", subject="математика"))
    due = store.get_due_review("stu_1", subject="физика")
    assert [c["question"] for c in due] == ["A?"]
    assert store.get_due_review("stu_1", limit=1)  # лимит по всем предметам


def test_review_card_sm2_advances(store):
    store.add_review_card("stu_1", _rec(question="C?"))
    card = store.list_review_cards("stu_1", limit=1)[0]
    updated = store.review_card("stu_1", card["card_id"], True)
    assert updated is not None
    assert updated["reps"] == 1
    assert store.review_stats("stu_1")["due"] == 0


def test_review_unknown_card(store):
    assert store.review_card("stu_1", "nope", True) is None
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_review_store.py tests/test_student_store.py -q`
Expected: 5 новых PASS + старые PASS.

- [ ] **Step 5: ruff**

Run: `.venv/Scripts/ruff.exe check src/student/store.py tests/test_review_store.py`
Expected: All checks passed.

---

### Task 5: Поля сессии для review/секрета квиза

**Files:**
- Modify: `src/api/session_store.py`
- Test: `tests/test_session_store.py`

**Interfaces:**
- Consumes: существующий `SessionStore`.
- Produces:
  - `ChatSession` новые поля: `subject: str = ""`, `grade: str = ""`,
    `last_quiz: dict | None = None`, `review_requested: bool = False`,
    `review_active: bool = False`, `review_cards: list = []`, `review_index: int = 0`,
    `review_correct: int = 0`, `review_reviewed: int = 0`.
  - `get_or_create(session_id, student_profile, student_id, topic, subject="", grade="")`
    — при создании/обновлении сохраняет `subject`/`grade` (как сейчас `topic`).

- [ ] **Step 1: Обновить `ChatSession` и `_new_session`**

В dataclass `ChatSession` (после `topic`) добавить поля из «Produces»; `_new_session` —
принимать и класть `subject`/`grade`.

- [ ] **Step 2: Обновить `get_or_create` и `create`**

Добавить параметры `subject: str = ""`, `grade: str = ""`; в ветке «существующая сессия»
обновлять `existing.subject`/`existing.grade` при непустых значениях; при создании новой —
прокидывать в `_new_session`.

- [ ] **Step 3: Добавить тесты в `tests/test_session_store.py`**

```python
def test_session_subject_grade_roundtrip():
    store = SessionStore()
    s = store.create(student_profile={}, student_id="stu_1", topic="t",
                     subject="физика", grade="7")
    assert s.subject == "физика"
    assert s.grade == "7"


def test_get_or_create_updates_subject():
    store = SessionStore()
    s = store.get_or_create(student_id="stu_1", topic="t1", subject="физика")
    s2 = store.get_or_create(session_id=s.session_id, student_id="stu_1",
                             topic="t2", subject="математика")
    assert s2 is s
    assert s2.subject == "математика"
    assert s.last_quiz is None
    assert s.review_active is False
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_session_store.py -q`
Expected: старые + 2 новых PASS.

- [ ] **Step 5: ruff**

Run: `.venv/Scripts/ruff.exe check src/api/session_store.py tests/test_session_store.py`
Expected: All checks passed.

---

### Task 6: Промпт — скрытый эталонный ответ квиза

**Files:**
- Modify: `src/agent/prompts.py`

- [ ] **Step 1: Обновить описание типа `quiz` в `SYSTEM_PROMPT`**

В блоке «Типы» заменить строку про `quiz`:

```text
- quiz — вопрос для проверки понимания; payload: {"answer_type": "single"|"open",
  "options": [...], "_correct_answer": "<эталонный ответ>"}. ВАЖНО: _correct_answer
  — скрытый эталон для сервера (сравнение/повторение), его НИКОГДА не пиши в видимый
  text/options и не помещай правильный вариант в text.
```

Оставить остальной промпт без изменений. Скрытый ключ начинается с `_` — санитайзер сервера
вырежет его перед отправкой ученику (см. Task 3).

- [ ] **Step 2: Проверка промпта (добавить в `tests/test_envelope.py`)**

```python
from src.agent.prompts import SYSTEM_PROMPT


def test_prompt_mentions_hidden_correct_answer():
    assert "_correct_answer" in SYSTEM_PROMPT
```

- [ ] **Step 3: ruff**

Run: `.venv/Scripts/ruff.exe check src/agent/prompts.py tests/test_envelope.py`
Expected: All checks passed.

---

### Task 7: Сервер — хуки evaluation, секрет квиза, санитайзер на выходе

**Files:**
- Modify: `src/api/server.py`
- Test: `tests/test_review_auto.py`

**Interfaces:**
- Consumes: `sanitize_envelope` (Task 3), `build_answer_record` (Task 3),
  `store.add_review_card` (Task 4), `JsonlLogger` (сущ.).
- Produces:
  - Хелпер `_quiz_secret(envelope) -> dict | None` — из quiz-конверта без `review` берёт
    `{question, options, answer_type, difficulty, correct_answer}` (или None).
  - В `_run_chat` (основной путь, где сейчас `append_message` assistant):
    - quiz-конверт → сохранить `session.last_quiz = _quiz_secret(envelope)`, в `meta.envelope`
      положить **санитизированную** копию;
    - любой assistant-конверт в историю кладётся санитизированным.
  - При `envelope.type == "evaluation"`: построить AnswerRecord, `session.last_quiz = None`,
    если `not correct` и есть question+correct_answer → `store.add_review_card(...)`;
    лог `review.add`.
  - Ответы клиенту (SSE `message`, `_to_response`) — через санитизированный конверт.

- [ ] **Step 1: Добавить хелперы** (после `_to_response`)

```python
def _quiz_secret(envelope: ContentEnvelope) -> dict | None:
    """Секрет квиза для answer record (None для не-quiz / review-карточек)."""
    if envelope is None or envelope.type.value != "quiz":
        return None
    if (envelope.payload or {}).get("review"):
        return None
    return {
        "question": envelope.text,
        "options": (envelope.payload or {}).get("options"),
        "answer_type": (envelope.payload or {}).get("answer_type", "open"),
        "difficulty": envelope.difficulty,
        "correct_answer": (envelope.payload or {}).get("_correct_answer") or "",
    }
```

- [ ] **Step 2: Применять санитайзер к истории и к выходящим конвертам**

В `_run_chat` основной путь заменяется: вместо прямого `append_message(..., envelope=...)`
использовать:

```python
    safe_env = sanitize_envelope(envelope) if envelope is not None else None
    if envelope is not None and envelope.type.value == "quiz":
        session.last_quiz = _quiz_secret(envelope)
    if reply:
        app.state.sessions.append_message(
            session_id, "assistant", reply,
            meta={"kind": envelope.type.value, "envelope": safe_env.model_dump()},
        )
```

В `_to_response` и в `chat_stream.run()` (событие `message`) envelope для клиента брать
`sanitize_envelope(envelope)`. (Секрет продолжает жить только в `session.last_quiz`.)

- [ ] **Step 3: Хук answer record + карточка** (в блоке `if envelope.type.value == "evaluation"`)

После существующего `touch_topic`:

```python
        last_user = next(
            (m.get("content") for m in reversed(app.state.sessions.list_messages(session_id))
             if m.get("role") == "user"),
            "",
        )
        record = build_answer_record(
            session_id=session_id, student_id=student_id, topic=body.topic,
            subject=body.subject, student_answer=last_user or body.message,
            correct=bool(payload.get("correct", False)),
            feedback=envelope.text or "",
            last_quiz=session.last_quiz,
        )
        session.last_quiz = None
        if not record.correct and record.question and record.correct_answer:
            try:
                store.add_review_card(student_id, {
                    "question": record.question, "topic": record.topic,
                    "subject": record.subject or body.subject,
                    "options": record.options, "answer_type": record.answer_type,
                    "correct_answer": record.correct_answer,
                    "difficulty": record.difficulty,
                })
            except Exception as exc:  # noqa: BLE001
                print(f"[review] не удалось добавить карточку: {exc}")
```

Обратите внимание: этот блок стоит ПОД веткой review-режима (Task 8), т.е. обычный агентный
evaluation остаётся прежним; evaluation-подобных конвертов в review-режиме нет (там фидбек шлём
сами и в `_run_chat` они не попадают в этот путь).

- [ ] **Step 4: `_build_adaptive` — счётчик due** (в `src/api/server.py`)

Добавить в возвращаемый dict ключ `"review_due": <int>` (0 при ошибке БД):

```python
    review_due = 0
    if store is not None:
        try:
            review_due = store.review_stats(student_id)["due"]
        except Exception:  # noqa: BLE001
            review_due = 0
```
и `"review_due": review_due` в словарь.

- [ ] **Step 5: Тесты `tests/test_review_auto.py`** (TestClient + фейковый runtime)

```python
"""Авто-добавление карточки при неверном ответе квиза."""

import pytest
from fastapi.testclient import TestClient

from src.agent.critic import Critic
from src.agent.loop import AgentRuntime
from src.agent.tools import ToolContext
from src.api.server import create_app
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.student.store import StudentStore


class _TC:
    def parse_usage(self, raw):
        return TokenUsage()

    def estimate(self, text):
        return 0


class SequencePlanner(LLMClient):
    """Возвращает payload по очереди (по одному на вызов planner)."""

    def __init__(self, payloads):
        super().__init__(token_counter=_TC())
        self._payloads = list(payloads)

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None):
        payload = self._payloads.pop(0) if self._payloads else '{"type":"theory","text":"…"}'
        return LLMResponse(content=payload, model=model,
                           usage=TokenUsage(1, 1), finish_reason="stop")

    async def chat_stream(self, *a, **k):
        yield ""


class ApproveJudge(LLMClient):
    def __init__(self):
        super().__init__(token_counter=_TC())

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None):
        return LLMResponse(content='{"passed": true, "issues": []}', model=model,
                           usage=TokenUsage(1, 1), finish_reason="stop")

    async def chat_stream(self, *a, **k):
        yield ""


def _app(tmp_path, first_payload, second_payload, student_store):
    payloads = [first_payload, second_payload]

    def fake_factory():
        return AgentRuntime(
            llm=SequencePlanner(payloads),
            models={"planner": "p", "fast": "f", "judge": "j"},
            tool_context=ToolContext(region="GLOBAL"),
            critic=Critic(llm=ApproveJudge(), model="j"),
        )

    return create_app(runtime_factory=fake_factory, student_store=student_store)


@pytest.mark.asyncio
async def test_wrong_quiz_auto_adds_card(tmp_path):
    store = StudentStore(str(tmp_path / "s.db"))
    quiz = '{"type":"quiz","text":"Сколько будет 2+2?","payload":{"answer_type":"single","options":["3","4","5"],"_correct_answer":"4"}}'
    ev = '{"type":"evaluation","text":"Неверно. Правильный ответ: 4","payload":{"correct":false,"knowledge_delta":-0.1}}'
    app = _app(tmp_path, quiz, ev, store)
    client = TestClient(app)
    base = {"session_id": "ses_test", "student_id": "stu_1",
            "topic": "тема", "subject": "математика", "grade": "5"}
    client.post("/chat", json={**base, "message": "Изучаем тему X"})       # -> quiz
    client.post("/chat", json={**base, "message": "Ответ: 3"})             # -> evaluation: неверно
    stats = store.review_stats("stu_1")
    assert stats["total"] == 1
    cards = store.list_review_cards("stu_1", limit=5)
    assert cards[0]["correct_answer"] == "4"
    store.close()
```

Примечание: оба POST используют ОДИН `session_id` (`ses_test`), иначе evaluation уйдёт в новую
сессию без `last_quiz`. `SequencePlanner` отдаёт quiz при первом вызове и evaluation — при
втором (factory вызывается на каждый запрос, очередь общая через замыкание).

- [ ] **Step 6: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_review_auto.py tests/test_api.py -q`
Expected: новые PASS + существующие PASS.

- [ ] **Step 7: ruff**

Run: `.venv/Scripts/ruff.exe check src/api/server.py tests/test_review_auto.py`
Expected: All checks passed. (Если ruff ругнётся на неиспользуемую переменную — поправьте.)

---

### Task 8: Review-режим (блиц) на сервере

**Files:**
- Modify: `src/api/server.py`
- Create: `src/review/grader.py`
- Test: `tests/test_review_blitz.py`

**Interfaces:**
- Consumes: `store.get_due_review/review_card` (Task 4), `sanitize_envelope` (Task 3),
  LLM-клиент из `runtime_factory().llm`, модели `get_models_for_region`.
- Produces:
  - `src/review/grader.py::grade_answer(llm, model, *, question, correct_answer, answer,
    options=None) -> tuple[bool, str]` — детерминированно для `answer_type in ("single", ...)`
    со `options`, иначе LLM (JSON `{"correct": bool, "feedback": str}`).
  - `_run_review(...)` — основной обслуживатель очереди (см. шаги).
  - `_run_chat` возвращает список `messages: list[dict]` (см. «Механика SSE» в спеке).
  - `ChatRequest.kind` + литерал `"review_request"`.

- [ ] **Step 1: `ChatRequest.kind` расширить**

`kind: Literal["message", "hint_request", "review_request"]`.

- [ ] **Step 2: Создать `src/review/grader.py`**

```python
"""Грейдер ответов на review-карточку (без создания новой карточки)."""

import json


def _extract_single_choice(answer: str, prefix: str = "Ответ:") -> str:
    a = (answer or "").strip()
    if a.lower().startswith(prefix.lower()):
        a = a[len(prefix):].strip()
    return a


def _eq(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def grade_deterministic(answer: str, correct_answer: str, options: list | None) -> bool:
    """Закрытый вопрос: выбор сравнивается с эталоном."""
    if not options:
        return False
    return any(_eq(_extract_single_choice(answer), opt) and _eq(opt, correct_answer)
               for opt in options)


async def grade_answer(llm, model: str, *, question: str, correct_answer: str,
                       answer: str, options: list | None = None) -> tuple[bool, str]:
    """Детерминированно для закрытых, LLM — для открытых."""
    if options:
        ok = grade_deterministic(answer, correct_answer, options)
        return ok, ("Верно!" if ok else "Ошибка")
    prompt = (
        "Ты — проверяющий ответов. Оцени ответ ученика на вопрос. "
        "Верни строго JSON: {\"correct\": true|false, \"feedback\": \"...\"}.\n"
        f"Вопрос: {question}\nЭталонный ответ: {correct_answer}\n"
        f"Ответ ученика: {answer}"
    )
    try:
        resp = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            model=model, temperature=0.0, max_tokens=300,
        )
        data = json.loads(resp.content)
        return bool(data.get("correct")), str(data.get("feedback") or "Ошибка")
    except Exception:  # noqa: BLE001
        return False, "Не удалось проверить ответ"
```

- [ ] **Step 3: `_run_review` в `src/api/server.py`**

Реализуйте по спеке (раздел 5.2) со следующим контрактом:

```python
def _review_message(kind_meta: str, env: ContentEnvelope) -> dict:
    return {"content": env.text, "envelope": sanitize_envelope(env).model_dump(),
            "kind": kind_meta, "review": True}
```

Логика шагов 1–4 спека. Итогом функция возвращает список `messages` (0..2 элемента) для фида.
В review-режиме не трогать `run_agent`, вызывать `store.review_card`, писать сообщения в
историю сессии через `append_message` (с `meta={"kind": ..., "envelope": sanitized}`), после
последней карточки сбросить review-поля сессии.

Для грейда открытых вопросов использовать:
`runtime = app.state.runtime_factory(); llm = runtime.llm; model = models["fast"]`.

- [ ] **Step 4: Встроить review-ветку в `_run_chat` и вернуть `messages`**

В начале `_run_chat` (после регистрации сессии в SQLite и до `_provision_for`) добавить:

```python
    review_mode = body.kind == "review_request" or session.review_requested or session.review_active
    if review_mode:
        if body.kind != "review_request":
            _append_user_if_new(app.state.sessions, session_id, body.message)
        messages = await _run_review(
            app=app, session=session, student_id=student_id,
            incoming=None if body.kind == "review_request" else body.message,
        )
        # собрать состояние/конверт из последнего сообщения
        last = messages[-1] if messages else None
        env = ContentEnvelope.model_validate(last["envelope"]) if last else None
        state = AgentGraphState(messages=[], final_answer=last["content"] if last else "",
                                terminated=True)
        state.content_envelope = env
        adaptive = _build_adaptive(store, student_id, body.topic, "medium")
        return state, session_id, "", env, adaptive, messages
```

`_run_chat` теперь возвращает 6-кортеж (добавить `messages`). Обновить оба вызова:
- `/chat`: `reply = messages[-1]["content"]`, `envelope = ContentEnvelope.model_validate(...)`;
- `/chat/stream`: цикл по `messages`, каждое как `message`-событие (content/envelope/adaptive),
  затем `done`.

Hint-короткое замыкание и обычный путь также возвращают список из одного элемента (сообщение
фидбек/ответ), чтобы контракт был единым.

- [ ] **Step 5: Тесты `tests/test_review_blitz.py`**

```python
"""Блиц повторений: запуск через kind=review_request, ответы грейдятся без агента."""

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.student.store import StudentStore


class _NoAgent:
    """Runtime, который падает при вызове — агент не должен вызываться в review."""

    def __call__(self):
        raise AssertionError("агент вызван в review-режиме")


@pytest.fixture
def client_with_cards(tmp_path):
    store = StudentStore(str(tmp_path / "s.db"))
    store.add_review_card("stu_1", {"question": "2+2?", "topic": "арифметика",
                                    "subject": "математика", "options": ["3", "4", "5"],
                                    "answer_type": "single", "correct_answer": "4"})
    app = create_app(runtime_factory=_NoAgent(), student_store=store)
    return TestClient(app), store


def test_review_request_serves_card_and_grade(client_with_cards):
    client, store = client_with_cards
    # старт блица (первый ход): отдаётся 1 карточка
    r = client.post("/chat/stream", json={"message": "", "kind": "review_request",
                                          "student_id": "stu_1", "topic": "арифметика",
                                          "subject": "математика"})
    text = r.text
    assert '"type": "quiz"' in text
    assert "2+2?" in text
    # ответ верный (детерминированно) -> карточка уходит из due
    client.post("/chat/stream", json={"message": "Ответ: 4", "kind": "message",
                                      "student_id": "stu_1", "topic": "арифметика",
                                      "subject": "математика"})
    assert store.review_stats("stu_1")["due"] == 0
```

Примечание: SSE-ответ через TestClient — это `r.text` с событиями; для строгой проверки событий
можно парсить `event:`/`data:`-блоки (утилита `parseSSEChunk` на фронте, на бэке достаточно
присутствия фрагментов).

- [ ] **Step 6: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_review_blitz.py tests/test_integration.py tests/test_api.py -q`
Expected: новые PASS + существующие PASS.

- [ ] **Step 7: ruff**

Run: `.venv/Scripts/ruff.exe check src/api/server.py src/review/grader.py tests/test_review_blitz.py`
Expected: All checks passed.

---

### Task 9: Эндпоинт `GET /student/{id}/review`

**Files:**
- Modify: `src/api/server.py`
- Test: расширить `tests/test_review_blitz.py` (или `tests/test_review_api.py`)

- [ ] **Step 1: Добавить роут** (рядом с `/student/{id}/sessions`)

```python
    @app.get("/student/{student_id}/review")
    def student_review(student_id: str) -> dict[str, Any]:
        """SM-2: статистика + due-карточки."""
        store: StudentStore = app.state.student_store
        try:
            stats = store.review_stats(student_id) if store is not None else {}
            due = (store.list_review_cards(student_id, limit=50, only_due=True)
                   if store is not None else [])
            return {"stats": stats, "due": due}
        except Exception:  # noqa: BLE001
            return {"stats": {"total": 0, "due": 0, "lapses": 0, "by_topic": {}}, "due": []}
```

- [ ] **Step 2: Тест**

```python
def test_review_endpoint_empty_and_filled(client_with_cards):
    client, store = client_with_cards
    r = client.get("/student/stu_1/review")
    assert r.status_code == 200
    body = r.json()
    assert body["stats"]["total"] == 1
    assert body["stats"]["due"] == 1
    assert len(body["due"]) == 1
```

- [ ] **Step 3: ruff + тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_review_api.py tests/test_review_blitz.py -q`
Expected: PASS.

---

### Task 10: Фронтенд — бейдж, кнопка «Повторить (N)», счётчик

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/components/QuizBlock.jsx`
- Modify: `frontend/src/components/AdaptivePanel.jsx` (или новый блок в нём)
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/blocks.test.jsx` / новый тест
- Modify: `frontend/src/index.css`

**Interfaces:**
- Produces:
  - `api.getReview(studentId)` → `{stats, due}`.
  - Кнопка «Повторить (N)» в правой колонке; клик → `runTurn('', 'review_request')`.
  - QuizBlock: бейдж «повторение», когда `payload.review === true`.

- [ ] **Step 1: `api.js`**

```js
  getReview: (studentId) => request(`/student/${encodeURIComponent(studentId)}/review`),
```

- [ ] **Step 2: `QuizBlock.jsx` — бейдж**

В шапке блока (перед «Вопрос») при `payload.review` добавить `<span className="badge review">повторение</span>`.

- [ ] **Step 3: Кнопка «Повторить (N)»**

`AdaptivePanel` принимает новый prop `onReview`; если `adaptive.review_due > 0` — кнопка
«Повторить (N)» (`disabled` при `busy`); клик → `onReview()`. CSS-класс `.btn.review`.

- [ ] **Step 4: `App.jsx` wiring**

- В `runTurn` разрешить `kind='review_request'` с пустым `message` (bubble пользователя не
  добавляется — в `runTurn` уже условие `message ? [...] : f.items`).
- Прокинуть `onReview={() => runTurn('', 'review_request')}` в `AdaptivePanel`.
- После события `done` и после SSE `message` с `adaptive` — запоминать `adaptive.review_due`
  (уже в `feed.adaptive`); обновление счётчика через `refreshStudent()` (расширить `api.student`
  не нужно — due приходит в `adaptive.review_due` каждого `message`).

- [ ] **Step 5: тест `blocks.test.jsx`**

```jsx
import { render, screen } from '@testing-library/react'
import QuizBlock from './QuizBlock'

test('review badge shown when payload.review', () => {
  render(<QuizBlock envelope={{ text: 'Q?', payload: { review: true, answer_type: 'open' } }} onSend={() => {}} />)
  expect(screen.getByText('повторение')).toBeTruthy()
})
```

- [ ] **Step 6: Проверка фронтенда**

Run (cwd `frontend`): `npm test` и `npm run lint`
Expected: все PASS / lint clean.

---

### Task 11: Документация

**Files:**
- Modify: `README.md`
- Modify: `adaptive_tutor/docs/api.md`

- [ ] **Step 1: README** — добавить строки в таблицу API (`GET /student/{student_id}/review`),
  раздел про карточки повторений (где лежит, как включить).
- [ ] **Step 2: api.md** — описать `GET /student/{id}/review` и `kind="review_request"` + протокол
  review-событий.
- [ ] **Step 3: финальная проверка всего бэкенда**

Run (cwd `adaptive_tutor`): `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: все PASS (старые + новые).

---

## Self-Review (план сверен со спекой)

- **Спека §3 (answer record/санитайзер/секрет на сессии)** → Task 3, 5, 7.
- **Спека §4 (таблица, SM-2 формулы, дедуп, кап)** → Task 2, 4.
- **Спека §5.1 (авто-добавление)** → Task 7.
- **Спека §5.2–5.3 (блиц, грейдинг без новой карточки)** → Task 8 (grader), 7 (контракт messages).
- **Спека §6 (GET /review, kind)** → Task 8 (kind), 9 (роут).
- **Спека §7 (конфиг)** → Task 1.
- **Спека §8 (UI)** → Task 10.
- **Спека §10–11 (наблюдаемость/тесты)** → review.add/grade в лог, Task 2–10.

Известные ограничения зафиксированы: review-состояние сессии живёт в памяти (TTL); кап банка —
по дате добавления; прерванный блиц не восстановим после TTL.
