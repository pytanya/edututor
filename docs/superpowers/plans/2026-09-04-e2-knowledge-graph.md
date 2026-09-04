# E2 — Граф знаний (3 слоя): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перенести в edututor трёхслойный граф знаний: Слой 2 (Student Knowledge Graph поверх существующего SQLite — статусы/mastery EMA/weak_areas/relations/рекомендации), Слой 1 (граф источника `subject|grade`, построенный LLM-онтологом из RAG-сниппетов с эвристическим фолбэком и JSON-кэшем) и Слой 3 (Knowledge Wiki — только заглушка `/graph/{node}/wiki → wiki: null`, реальный порт — E4).

**Architecture:** Чистая математика/алгоритмы отделены от sqlite и от LLM: `src/student/mastery.py` (EMA+статусы без БД), `src/student/store.py` — SQLite-методы Слоя 2, `src/kg/*` — чистый граф на `networkx` (`graph.py`), junk-фильтры (`junk.py`), LLM-онтология+фолбэк (`ontology.py`), JSON-кэш (`cache.py`), сборка из RAG-сниппетов (`build.py`). Все хуки мастерства/гейта/построения живут в `src/api/server.py` рядом с `touch_topic`-блоком; агент о графе не знает. Сборка графа ленивая: GET `/graph` возвращает «каркас из тем» сразу, полный LLM-граф строится fire-and-forget и эмитится SSE-событием `graph.ready` при следующем ходе.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, sqlite3 stdlib + `threading.Lock`, `networkx>=3.0`, pytest, ruff; React 19 + Vite (vitest, testing-library, oxlint).

**Spec:** `docs/superpowers/specs/2026-09-04-e2-knowledge-graph-design.md`

## Global Constraints

- Python >= 3.11; все существующие тесты остаются зелёными.
- `ruff check src/ tests/` — чисто (line-length 100).
- Репозиторий без коммитов: каждый таск заканчивается шагом «Validation» (pytest + ruff) вместо `git commit`. Коммит — только по явному запросу пользователя.
- Русские docstring/комментарии; Pydantic v2; один sqlite-connection + `threading.Lock` в `StudentStore`.
- `.env` не менять; новые переменные — только в `.env.example`.
- Агент (LLM-цикл) не должен знать про граф/мастерство/БД: хуки только в `src/api/server.py`.
- Единый ключ темы = строка `topic` из `ChatRequest`; в answer record и граф источника передаётся та же тема.
- `touch_topic(...)` и `pick_recommendation(...)` сохраняются как legacy (используются тестами), но сервер переводится на `apply_result(record)` и `recommend_topics(...)`.
- Существующие ответы конвертов, уходящие клиенту, проходят санитайзер (E1); секреты квиза только в `ChatSession.last_quiz`.
- Заголовки/junk/промпт/формулы — дословно по спеке и референсу (EMAs, `_norm_topic`, промпт онтолога, ключ кэша, наборы `_GRAPH_JUNK_*`/`_WEB_NOISE`).

**Cross-plan dependency (E1, НЕ перепланируется здесь):** E1 уже дал `src/student/answer_record.py`
(dataclass `AnswerRecord` + `build_answer_record(...)`, см. спека E1 §3), `ChatSession.subject/grade/last_quiz`
и review-ветку `_run_review(...)` с грейдером. E2 считает их существующими и только расширяет.

---

## File Structure

Создать (backend):
- `adaptive_tutor/src/student/mastery.py` — чистые `apply_mastery`/`derive_status`/`is_mastered` (без sqlite; переиспользуется в E4).
- `adaptive_tutor/src/student/student_kg.py` — чистые payload/статы помощники (row → topic-dict, `knowledge_graph_payload`, `stats_of`) для API-ответов §5; sqlite-свободный «двойник» референсного `student_kg.py`.
- `adaptive_tutor/src/kg/__init__.py`
- `adaptive_tutor/src/kg/graph.py` — `KnowledgeGraph` на `nx.DiGraph` (add_topic/add_edge/to_dict/from_dict/neighbors/stats), `NODE_COLORS`, константы рёбер.
- `adaptive_tutor/src/kg/junk.py` — дословный перенос фильтров мусора из референса `knowledge_graph.py`.
- `adaptive_tutor/src/kg/ontology.py` — промпт онтолога, парсинг/валидация, `build_ontology_graph`, `build_heuristic_graph`.
- `adaptive_tutor/src/kg/cache.py` — ключ кэша, `load_cached_graph`, `save_graph`.
- `adaptive_tutor/src/kg/build.py` — сбор сниппетов из чанков RAG, `build_or_load_graph`, `build_topic_scaffold`, `sync_relations_from_graph`.
- Тесты: `tests/test_mastery.py`, `tests/test_student_kg_store.py`, `tests/test_student_kg_payload.py`, `tests/test_kg_graph.py`, `tests/test_kg_ontology.py`, `tests/test_kg_cache.py`, `tests/test_kg_build.py`, `tests/test_kg_api.py`, `tests/test_kg_server_hooks.py`.

Создать (frontend):
- `frontend/src/components/StudentKGPanel.jsx` — «Мои знания» (статусы, weak_areas, «Повторить (N)»).
- `frontend/src/components/MasteryWall.jsx` — тепловая карта мастерства.
- `frontend/src/components/KnowledgeGraphPanel.jsx` — canvas «Созвездие».
- Тесты: `StudentKGPanel.test.jsx`, `MasteryWall.test.jsx`, `KnowledgeGraphPanel.test.jsx`.

Изменить (backend): `pyproject.toml`, `requirements.txt`, `.env.example`, `src/config.py`,
`src/student/store.py`, `src/rag/__init__.py`, `src/api/session_store.py`, `src/api/server.py`.

Изменить (frontend): `src/api.js`, `src/App.jsx`, `src/components/AdaptivePanel.jsx`,
`src/components/Chat.jsx`, `src/index.css`, тесты `src/api.test.js`, `src/App.test.jsx`, `src/components/Chat.test.jsx`.

Изменить (docs): `README.md`, `adaptive_tutor/docs/api.md`.

---

### Task 1: Зависимость `networkx` + конфиг графа знаний

**Files:**
- Modify: `pyproject.toml:15-28` (список `dependencies`)
- Modify: `adaptive_tutor/requirements.txt`
- Modify: `adaptive_tutor/src/config.py` (после свойства `resolved_student_db_path`, ~строка 96)
- Modify: `adaptive_tutor/.env.example`
- Test: нет (проверка инлайн)

**Interfaces:**
- Consumes: `pydantic_settings.BaseSettings` (уже есть).
- Produces: `settings.knowledge_graph_dir: str`, `settings.resolved_knowledge_graph_dir: str` (property, резолв от `adaptive_tutor/` как `resolved_student_db_path`), `settings.graph_schema_version: int = 1`, `settings.ontology_max_vertices: int = 14`, `settings.mastery_mastered_threshold: float = 0.8`, `settings.mastery_mastered_attempts: int = 3`.

- [ ] **Step 1: `networkx>=3.0` в зависимости**

В `pyproject.toml` в список `dependencies` добавить строку `"networkx>=3.0",`; то же в
`adaptive_tutor/requirements.txt`.

- [ ] **Step 2: Поля в `src/config.py`**

После свойства `resolved_student_db_path` добавить:

```python
    # Граф знаний (E2): кэш графов источника + пороги мастерства
    knowledge_graph_dir: str = Field(
        default="data/knowledge_graphs", description="Каталог JSON-кэша графов источника"
    )
    graph_schema_version: int = Field(
        default=1, description="Версия схемы графа — входит в ключ кэша (пересборка)"
    )
    ontology_max_vertices: int = Field(
        default=14, ge=1, le=50, description="Макс. вершин LLM-онтологии"
    )
    mastery_mastered_threshold: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Порог mastery для статуса mastered"
    )
    mastery_mastered_attempts: int = Field(
        default=3, ge=1, description="Мин. попыток для статуса mastered"
    )

    @property
    def resolved_knowledge_graph_dir(self) -> str:
        """Абсолютный путь к кэшу графов: относительно adaptive_tutor/ как student_db_path."""
        import os
        if os.path.isabs(self.knowledge_graph_dir):
            return self.knowledge_graph_dir
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, self.knowledge_graph_dir)
```

- [ ] **Step 3: `.env.example`**

```text
# Граф знаний (E2)
TUTOR_KNOWLEDGE_GRAPH_DIR=data/knowledge_graphs
TUTOR_GRAPH_SCHEMA_VERSION=1
TUTOR_ONTOLOGY_MAX_VERTICES=14
TUTOR_MASTERY_MASTERED_THRESHOLD=0.8
TUTOR_MASTERY_MASTERED_ATTEMPTS=3
```

- [ ] **Step 4: Проверка**

Run: `.venv/Scripts/python.exe -c "import networkx; from src.config import settings as s; print(networkx.__version__); print(s.graph_schema_version, s.ontology_max_vertices, s.mastery_mastered_threshold, s.mastery_mastered_attempts)"` (cwd `adaptive_tutor`)
Expected: версия networkx >= 3.0 и `1 14 0.8 3`.

- [ ] **Step 5: ruff**

Run: `.venv/Scripts/ruff.exe check src/config.py`
Expected: All checks passed.

---

### Task 2: Чистое ядро мастерства `src/student/mastery.py`

**Files:**
- Create: `src/student/mastery.py`
- Test: `tests/test_mastery.py`

**Interfaces:**
- Consumes: stdlib только.
- Produces:
  - `apply_mastery(old_mastery: float, correct: bool, prior_attempts: int) -> float` — EMA
    `score01 = 1.0 if correct else 0.0`; при `prior_attempts <= 0` якорь `old = 0.5` (иначе
    `old = old_mastery`); `return round(0.7 * old + 0.3 * score01, 4)`.
  - `derive_status(attempts: int, mastery: float, threshold: float = 0.8, min_attempts: int = 3) -> str` —
    `attempts == 0 → "not_studied"`; `attempts >= min_attempts and mastery >= threshold → "mastered"`;
    `attempts > 0 → "in_progress"`; иначе `"not_studied"`.
  - `is_mastered(attempts: int, mastery: float, status: str, threshold: float = 0.8, min_attempts: int = 3) -> bool` —
    `status == "mastered" or (attempts >= min_attempts and mastery >= threshold)`.

- [ ] **Step 1: Создать модуль** (заголовок-докстринг на русском; константы `EMA_ALPHA = 0.7`, `EMA_WEIGHT = 0.3`, `MASTERY_ANCHOR = 0.5` как module-level для тестов). Формулы — 1:1 спека §2.2 и референс `states.py:156-159`.

```python
def apply_mastery(old_mastery: float, correct: bool, prior_attempts: int) -> float:
    """EMA мастерства: 0.7*старое + 0.3*результат. Якорь 0.5 на первом ответе."""
    old = MASTERY_ANCHOR if prior_attempts <= 0 else float(old_mastery)
    score01 = 1.0 if correct else 0.0
    return round(EMA_ALPHA * old + EMA_WEIGHT * score01, 4)
```

- [ ] **Step 2: Создать `tests/test_mastery.py`**

```python
def test_three_correct_in_a_row_mastered():
    m = apply_mastery(0.0, True, 0)   # anchor: 0.5 -> 0.65
    assert m == 0.65
    m = apply_mastery(m, True, 1)     # 0.755
    assert m == 0.755
    m = apply_mastery(m, True, 2)     # 0.8285
    assert m == 0.8285
    assert derive_status(3, m) == "mastered"
    assert is_mastered(3, m, "mastered")

def test_two_correct_then_wrong_in_progress():
    m = apply_mastery(apply_mastery(0.0, True, 0), True, 1)
    m = apply_mastery(m, False, 2)    # 0.7*0.755 = 0.5285
    assert m == 0.5285
    assert derive_status(3, m) == "in_progress"
    assert not is_mastered(3, m, "in_progress")

def test_first_wrong_anchor():
    assert apply_mastery(0.0, False, 0) == 0.35   # 0.7*0.5 + 0.3*0

def test_statuses():
    assert derive_status(0, 0.0) == "not_studied"
    assert derive_status(1, 0.65) == "in_progress"
    assert derive_status(2, 0.5) == "in_progress"
```

- [ ] **Step 3: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mastery.py -q`
Expected: 4 PASS.

- [ ] **Step 4: ruff**

Run: `.venv/Scripts/ruff.exe check src/student/mastery.py tests/test_mastery.py`
Expected: All checks passed.

---

### Task 3: `StudentStore` — миграция колонок + методы Слоя 2

**Files:**
- Modify: `src/student/store.py` (`_SCHEMA:22-30`, `__init__:52-54`, `list_topics:78-82`, перед `close:151`)
- Test: `tests/test_student_kg_store.py`

**Interfaces:**
- Consumes: `src/student/mastery.py` (Task 2), `src/student/answer_record.AnswerRecord` (E1-зависимость), stdlib `json`.
- Produces (методы `StudentStore`):
  - `_migrate_topics()` — идемпотентные `ALTER TABLE` по `PRAGMA table_info(topics)`.
  - `apply_result(student_id: str, topic: str, subject: str, record: AnswerRecord) -> dict`
  - `set_relations(student_id: str, topic: str, relations: dict) -> None`
  - `get_topic(student_id: str, topic: str) -> dict | None`
  - `list_topics(student_id)` — расширяется: выбирает ВСЕ колонки, JSON-колонки декодирует; старые ключи `topic/level/attempts/correct/last_seen` сохраняются.
  - `get_weak_topics(student_id, subject="", threshold=0.5, min_attempts=2) -> list[dict]`
  - `get_in_progress_topics(student_id, subject="") -> list[dict]`
  - `get_mastered_topics(student_id, subject="") -> list[dict]`
  - `get_prerequisite_gaps(student_id, topic) -> list[str]`
  - `recommend_topics(student_id, subject="", current_topic="", limit=5) -> list[dict]`
  - Хелпер `_topic_payload(row) -> dict` — `{topic, subject, status, mastery, attempts, correct, accuracy, weak_areas, last_seen, relations}` (accuracy = `round(correct/attempts, 4)`, `0.0` при `attempts==0`).
  - `register_session(student_id, session_id, topic, subject="", grade="")` и `get_last_session(student_id) -> dict | None` — поддержка резолва subject/grade в API (колонки `sessions.subject/grade` добавляются той же миграцией, `list_sessions` НЕ меняется).

- [ ] **Step 1: Миграция колонок в `__init__`**

После `executescript(_SCHEMA)` вызвать `self._migrate_topics()`. Добавить метод:

```python
    def _migrate_topics(self) -> None:
        """Идемпотентная миграция: новые колонки topics/sessions (если отсутствуют)."""
        def _columns(table: str) -> set[str]:
            with self._lock:
                cur = self._conn.execute(f"PRAGMA table_info({table})")
                return {row[1] for row in cur.fetchall()}
        with self._lock:
            for col, ddl in {
                "subject": "subject TEXT DEFAULT ''",
                "mastery": "mastery REAL DEFAULT 0.0",
                "status": "status TEXT DEFAULT 'not_studied'",
                "weak_areas": "weak_areas TEXT DEFAULT '[]'",
                "relations": "relations TEXT DEFAULT '{\"prerequisite\":[],\"related\":[]}'",
            }.items():
                if col not in _columns("topics"):
                    self._conn.execute(f"ALTER TABLE topics ADD COLUMN {ddl}")
            for col, ddl in {"subject": "subject TEXT DEFAULT ''",
                             "grade": "grade TEXT DEFAULT ''"}.items():
                if col not in _columns("sessions"):
                    self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {ddl}")
            self._conn.commit()
```

Учесть: не вызывать `_rows`/`_exec` внутри `_migrate_topics` из-за повторного `self._lock` (только голый `self._conn.execute` под уже взятым lock, как выше).

- [ ] **Step 2: `list_topics` + JSON-деколеры**

`list_topics` выбирает `SELECT topic, subject, level, mastery, status, attempts, correct, weak_areas, relations, last_seen FROM topics WHERE student_id = ? ORDER BY last_seen DESC` и декодирует JSON через `json.loads` для `weak_areas`/`relations` (на ошибку — `[]`/`{"prerequisite": [], "related": []}`). Внутренний `_topic_payload(row)` возвращает поля из «Interfaces» (accuracy вычисляется).

- [ ] **Step 3: `apply_result`**

```python
    def apply_result(self, student_id: str, topic: str, subject: str, record) -> dict:
        """EMA-мастерство по answer record; level поддерживается = mastery."""
        rows = self._rows(
            "SELECT mastery, attempts, correct, weak_areas FROM topics "
            "WHERE student_id = ? AND topic = ?", (student_id, topic))
        if rows:
            prev = rows[0]
            attempts = int(prev["attempts"]) + 1
            correct_count = int(prev["correct"]) + (1 if record.correct else 0)
            mastery = apply_mastery(float(prev["mastery"] or 0.0),
                                    bool(record.correct), int(prev["attempts"]))
            weak = json.loads(prev["weak_areas"] or "[]")
        else:
            attempts = 1
            correct_count = 1 if record.correct else 0
            mastery = apply_mastery(0.0, bool(record.correct), 0)
            weak = []
        if not record.correct and (record.feedback or "").strip():
            fb = record.feedback.strip()
            weak = [w for w in weak if w != fb] + [fb]
            weak = weak[-3:]                       # не более 3, удаляем старые
        status = derive_status(attempts, mastery)
        now = time.time()
        self._exec(
            "INSERT INTO topics (student_id, topic, subject, level, mastery, status, "
            "attempts, correct, weak_areas, relations, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(student_id, topic) DO UPDATE SET "
            "subject = excluded.subject, level = excluded.level, mastery = excluded.mastery, "
            "status = excluded.status, attempts = excluded.attempts, correct = excluded.correct, "
            "weak_areas = excluded.weak_areas, last_seen = excluded.last_seen",
            (student_id, topic, subject or "", mastery, mastery, status, attempts, correct_count,
             json.dumps(weak, ensure_ascii=False), "{}", now),
        )
        self.upsert_student(student_id)
        return self._topic_payload({"topic": topic, "subject": subject or "", "mastery": mastery,
                                    "status": status, "attempts": attempts, "correct": correct_count,
                                    "weak_areas": weak, "relations": {}, "last_seen": now})
```

Внимание: `level` и `mastery` обновляются синонимично (`level = mastery`) — §3.1 спеки. Импорты `apply_mastery`/`derive_status` — через локальный `from .mastery import ...` внутри методов (паттерн `touch_topic`, избегаем циклов). `AnswerRecord` импортировать только для аннотации: `from __future__ import annotations` уже есть; тип можно `record: Any`.

- [ ] **Step 4: `set_relations`, `get_topic`, выборки**

- `set_relations`: читает `relations` строки (JSON), merge по union для ключей `prerequisite`/`related`, `UPDATE topics SET relations=? WHERE student_id=? AND topic=?` (upsert-строку НЕ создаёт, если темы нет — тихий no-op).
- `get_topic`: SELECT по PK → `_topic_payload` или None.
- `get_weak_topics`: по всем строкам ученика (фильтр `subject` по колонке) вернуть `_topic_payload` c `attempts >= min_attempts and accuracy < threshold`, сортировка accuracy asc.
- `get_in_progress_topics`: `status == "in_progress"` (+subject), сортировка `last_seen` desc.
- `get_mastered_topics`: `is_mastered(attempts, mastery, status)` (+subject), сортировка mastery desc.
- `get_prerequisite_gaps(student_id, topic)`: `ts = get_topic(...)`; для каждого id в `relations.prerequisite`: если `get_topic(id)` нет или не `is_mastered(...)` → gap. None/нет relations → `[]`.
- `recommend_topics(student_id, subject="", current_topic="", limit=5)`: по всем строкам (после subject-фильтра) строит результат без дублей в порядке (спека §3.3):
  1. слабые: `attempts>=2 and accuracy<0.5` (порог 0.5, `min_attempts=2`), сортировка accuracy asc;
  2. пробелы пререквизитов `current_topic` (не вошедшие выше);
  3. `in_progress`, сортировка `last_seen` desc;
  4. `not_studied` (статус), у которых `get_prerequisite_gaps` пуст.
  Обрезать до `limit`.

- [ ] **Step 5: `register_session` + `get_last_session`**

`register_session(student_id, session_id, topic, subject="", grade="")`: тот же upsert, дополнительно пишет `subject`/`grade` в `excluded.subject`/`excluded.grade` (INSERT со всеми 6 колонками). Старые вызовы с 3 аргументами продолжают работать. `get_last_session(student_id)` → `SELECT * FROM sessions WHERE student_id=? ORDER BY started_at DESC LIMIT 1` (или None). `list_sessions` не трогать (SELECT фиксированных 4 колонок — существующие тесты проверяют точный набор ключей).

- [ ] **Step 6: Тесты `tests/test_student_kg_store.py`**

```python
"""Слой 2: миграция колонок и методы StudentStore (EMA/статусы/relations/рекомендации)."""

def _rec(correct: bool, feedback: str = ""):
    return SimpleNamespace(correct=correct, feedback=feedback)

def test_migration_on_old_db(tmp_path):            # БД без новых колонок
    import sqlite3
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE topics (student_id TEXT, topic TEXT, level REAL DEFAULT 0.5, "
        "attempts INT DEFAULT 0, correct INT DEFAULT 0, last_seen REAL, "
        "PRIMARY KEY (student_id, topic));"
        "CREATE TABLE sessions (student_id TEXT, session_id TEXT, topic TEXT, "
        "started_at REAL, ended_at REAL, UNIQUE (student_id, session_id));")
    conn.execute("INSERT INTO topics VALUES ('stu_1', 'старая', 0.5, 1, 1, 1.0)")
    conn.commit(); conn.close()
    s = StudentStore(db)
    cols = {r[1] for r in s._rows("PRAGMA table_info(topics)")}     # pragma-обёртка в тесте
    assert {"subject", "mastery", "status", "weak_areas", "relations"} <= cols
    assert "subject" in {r[1] for r in s._rows("PRAGMA table_info(sessions)")}
    s.close()

def test_apply_result_ema_and_status(store):
    store.apply_result("stu_1", "тема", "физика", _rec(True, ""))
    store.apply_result("stu_1", "тема", "физика", _rec(True, ""))
    t = store.apply_result("stu_1", "тема", "физика", _rec(True, ""))
    assert t["mastery"] == pytest.approx(0.8285)
    assert t["status"] == "mastered"
    assert t["level"] == t["mastery"]          # key присутствует (level=mastery)

def test_apply_result_weak_areas_cap_and_dedupe(store):
    for i, fb in enumerate(["п1", "п2", "п3", "п1", "п4"]):
        store.apply_result("stu_1", "т", "физика", _rec(False, fb))
    t = store.get_topic("stu_1", "т")
    assert t["weak_areas"] == ["п2", "п3", "п4"]   # без дублей, не более 3, свежие

def test_set_relations_merge_and_gaps(store):
    store.apply_result("stu_1", "B", "м", _rec(True, ""))          # in_progress (1 попытка)
    store.set_relations("stu_1", "B", {"prerequisite": ["A"], "related": ["C"]})
    store.set_relations("stu_1", "B", {"prerequisite": ["A"], "related": ["D"]})
    t = store.get_topic("stu_1", "B")
    assert t["relations"]["prerequisite"] == ["A"]
    assert sorted(t["relations"]["related"]) == ["C", "D"]
    assert store.get_prerequisite_gaps("stu_1", "B") == ["A"]       # A не изучена
    for _ in range(3):
        store.apply_result("stu_1", "A", "м", _rec(True, ""))
    assert store.get_prerequisite_gaps("stu_1", "B") == []          # A освоена

def test_recommend_order_weak_first_then_in_progress_then_not_studied(store):
    store.apply_result("stu_1", "слабая", "физика", _rec(False, "x"))
    store.apply_result("stu_1", "слабая", "физика", _rec(False, "x"))   # acc 0.0, attempts 2 -> weak
    store.apply_result("stu_1", "процесс", "физика", _rec(True, ""))    # in_progress
    rec = store.recommend_topics("stu_1", subject="физика", limit=10)
    names = [r["topic"] for r in rec]
    assert names[0] == "слабая"
    assert names.index("слабая") < names.index("процесс")

def test_get_mastered_and_weak_filters(store):
    for _ in range(3): store.apply_result("stu_1", "освоена", "физика", _rec(True, ""))
    assert [t["topic"] for t in store.get_mastered_topics("stu_1", "физика")] == ["освоена"]
    assert store.get_weak_topics("stu_1", "физика") == []
```

- [ ] **Step 7: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_student_kg_store.py tests/test_student_store.py -q`
Expected: новые PASS + старые PASS (существующие тесты не знают про новые колонки — проверяется).

- [ ] **Step 8: ruff**

Run: `.venv/Scripts/ruff.exe check src/student/store.py tests/test_student_kg_store.py`
Expected: All checks passed.

---

### Task 4: Обёртка графа `src/kg/graph.py` (networkx)

**Files:**
- Create: `src/kg/__init__.py`
- Create: `src/kg/graph.py`
- Test: `tests/test_kg_graph.py`

**Interfaces:**
- Consumes: `networkx>=3.0` (Task 1), `src.kg.junk.is_junk_topic` (Task 5 — реализуется раньше применения; при мёрже порядок тасков допускает Task 5 раньше. **Порядок исполнения: выполнить Task 5 до Task 4**, т.к. `add_topic` фильтрует junk).
- Produces:
  - Константы: `PART_OF = "part_of"`, `PREREQUISITE = "prerequisite"`, `RELATED = "related"`,
    `NODE_COLORS = {"book": "#F4A261", "lesson": "#64DFDF", "section": "#B388FF", "topic": "#69F0AE", "default": "#FF8A80"}`.
  - `class KnowledgeGraph`: `__init__` создаёт `self.graph = nx.DiGraph()`.
    - `add_topic(node_id, title, node_type="topic", section_number=None, parent_id=None, allow_url=False, **attrs)` — поведение референса `knowledge_graph.py:388-418`: если `not allow_url and _is_url_like(title)` → return; если `node_type not in ("book", "lesson") and is_junk_topic(title)` → return; атрибуты `{id, title, type, color: NODE_COLORS.get(...)}` + опциональные `parent_id`/`section_number`.
    - `add_edge(source, target, relation=RELATED)` — рёбра только между существующими разными узлами.
    - `neighbors(node_id, max_depth=2) -> list[dict]` — DFS (посещённые), элементы `{source, source_title, relation, target, target_title, target_type, depth}`.
    - `to_dict() -> {"nodes": [{id,title,type,color,section_number?,parent_id?}], "edges": [{source,target,relation}]}` — whitelist атрибутов.
    - `from_dict(data) -> KnowledgeGraph` (classmethod, rebuild через add_topic/add_edge).
    - `stats() -> {"nodes": int, "edges": int}`.

- [ ] **Step 1: `src/kg/__init__.py`**

```python
"""Граф знаний (E2): источник (Слой 1) — сеть узлов/рёбер, кэш, онтология."""
```

- [ ] **Step 2: Реализовать `src/kg/graph.py`**

Перенести дословно (адаптировав импорты на `..kg`): константы и `class KnowledgeGraph` из референса `knowledge_graph.py:26-38` и `:381-509`. Методы `add_topic`/`add_edge` фильтруют мусор через `src.kg.junk` (`_is_url_like`, `is_junk_topic`) по правилам из «Interfaces».

- [ ] **Step 3: Тесты `tests/test_kg_graph.py`**

```python
def test_add_root_and_topic():
    kg = KnowledgeGraph()
    kg.add_topic("book:физ7", "Учебник «физ7»", node_type="book")
    kg.add_topic("topic:интегралы", "интегралы", node_type="topic", parent_id="book:физ7")
    kg.add_edge("book:физ7", "topic:интегралы", PART_OF)
    d = kg.to_dict()
    assert d["nodes"][0]["type"] == "book"
    assert d["nodes"][1]["parent_id"] == "book:физ7"
    assert d["edges"][0]["relation"] == "part_of"

def test_add_topic_filters_junk_and_url():
    kg = KnowledgeGraph()
    kg.add_topic("book:x", "Учебник «x»", node_type="book")
    kg.add_topic("t1", "Улучшить свой запрос", node_type="topic")   # junk
    kg.add_topic("t2", "https://example.com/foo", node_type="topic")  # url
    kg.add_topic("t3", "слабые темы", node_type="topic")
    assert set(n["id"] for n in kg.to_dict()["nodes"]) == {"book:x", "t3"}
    assert kg.add_edge("нет", "book:x", PART_OF) is None or "нет" not in kg.graph

def test_from_dict_roundtrip():
    kg = KnowledgeGraph(); kg.add_topic("n1", "A", "topic"); kg.add_topic("n2", "B", "concept")
    kg.add_edge("n1", "n2", PREREQUISITE)
    kg2 = KnowledgeGraph.from_dict(kg.to_dict())
    assert kg2.to_dict() == kg.to_dict()
    assert kg2.stats() == {"nodes": 2, "edges": 1}

def test_neighbors_depth_2():
    kg = KnowledgeGraph()
    for i, t in enumerate(["R", "A", "B", "C"]): kg.add_topic(f"n{i}", t, "topic")
    kg.add_edge("n0", "n1", RELATED); kg.add_edge("n1", "n2", PART_OF); kg.add_edge("n2", "n3", RELATED)
    rel = kg.neighbors("n0", max_depth=2)
    assert {r["target"] for r in rel} == {"n1", "n2"}
    assert all("target_title" in r and "relation" in r for r in rel)
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kg_graph.py -q`
Expected: 4 PASS.

- [ ] **Step 5: ruff**

Run: `.venv/Scripts/ruff.exe check src/kg/ tests/test_kg_graph.py`
Expected: All checks passed.

---

### Task 5: Junk-фильтры (`src/kg/junk.py`) + LLM-онтология и фолбэк (`src/kg/ontology.py`)

> Выполнять ДО Task 4 (Task 4 использует `junk.is_junk_topic`/`junk._is_url_like`).

**Files:**
- Create: `src/kg/junk.py`
- Create: `src/kg/ontology.py`
- Test: `tests/test_kg_ontology.py`

**Interfaces:**
- Consumes: `settings.ontology_max_vertices` (Task 1), stdlib.
- Produces (`junk.py`, имена/поведение дословно из референса `knowledge_graph.py`):
  - `clean_title(text) -> str`, `_try_fix_mojibake(text) -> str`, `_is_mojibake_heavy(text) -> bool`,
    `_is_url_like(title) -> bool`, `is_junk_topic(title) -> bool`, `_is_ui_element(title) -> bool`,
    `_is_paragraph_number(title) -> bool`, `_is_valid_topic_title(title, min_words=2) -> bool`.
  - Module-наборы `_GRAPH_JUNK_EXACT`, `_GRAPH_JUNK_SUBSTR`, `_WEB_NOISE`, regex `_URL_PATTERN`.
- Produces (`ontology.py`):
  - `ontology_prompt(text, source, max_vertices) -> list[dict]` — точный промпт спеки §4.3/референса `:593-612`.
  - `extract_section_number(section) -> str` — `re.search(r"(\d{1,3})", ...)`.
  - `parse_ontology(raw) -> dict` — извлечение JSON-объекта (паттерн `_extract_json` из `src/agent/envelope.py:12-41`, локальная копия) → dict; мусор → `{}`.
  - `build_ontology_graph(text, source, raw_data) -> KnowledgeGraph | None` — валидация по §4.3; `None` если 0 узлов.
  - `build_heuristic_graph(root, topics, snippet_text) -> KnowledgeGraph` — фолбэк §4.3.
- [async-интеграция, используется Task 7] `async def chat_ontology(llm, model, messages) -> str` — `await llm.chat(messages, model=model, temperature=0.0, max_tokens=900)`; любой `Exception` → `""`.

- [ ] **Step 1: Создать `src/kg/junk.py`**

Перенести из референса `knowledge_graph.py:74-378` (комментарии — на русском; пути без внешних зависимостей):
`_URL_PATTERN`, `_WEB_NOISE`, `_GRAPH_JUNK_EXACT`, `_GRAPH_JUNK_SUBSTR`, `_try_fix_mojibake`,
`clean_title`, `_is_valid_topic_title`, `_is_url_like`, `is_junk_topic`, `_is_ui_element`,
`_is_paragraph_number`, `_is_mojibake_heavy`. Копировать **дословно** наборы/регэкспы — план требует точного переноса (спека §4.3: «Полные наборы списков — в референсе; в код переносятся дословно»).

- [ ] **Step 2: Создать `src/kg/ontology.py` (промпт + парсинг + онтология)**

Промпт (системный) — дословно спека §4.3:

```python
def ontology_prompt(text: str, source: str, max_vertices: int) -> list[dict]:
    system = (
        "Ты — онтолог образовательного агента EduTutor. По фрагменту учебного материала построй "
        "граф знаний: ВЕРШИНЫ — темы и ключевые понятия из текста, РЁБРА — смысловые связи между ними. "
        "Решения, что станет вершинами и рёбрами, принимаешь ТЫ по содержанию. "
        "Правила: "
        "1) Только понятия, реально присутствующие в тексте; не выдумывай. "
        f"2) Не больше {max_vertices} вершин; название 1-4 слова. "
        "3) ПРЕДПОЧИТАЙ ПОНЯТИЙНЫЕ узлы (термины и концепции). НЕ включай структурные узлы: "
        "номера уроков/классов («7 класс», «Урок 5»), рубрики, оглавление. "
        "4) relation только: part_of (входит в), prerequisite (опирается на), related (связан). "
        "5) Для каждой вершины укажи section — §N/параграф из текста (если есть, иначе пусто). "
        "Верни строго JSON: "
        '{"nodes":[{"id":"c1","title":"...","type":"topic|concept","section":""}], '
        '"edges":[{"source":"c1","target":"c2","relation":"part_of"}]}'
    )
    user = f"Источник: {source}\n\nТекст материала:\n{text[:12000]}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
```

`build_ontology_graph(text, source, raw_data)` — алгоритм референса `build_model_graph` (`:621-688`):
- корень `root_id = f"book:{source}"`, title `Учебник «{source}»`, type `book`;
- для каждого узла: `title = clean_title(...)`; отбросить если пусто/`len < 2`/`is_junk_topic`/дубль lower-title; `ntype` вне `{"topic","concept","section","lesson"}` → `"concept"`; `section = extract_section_number(...)`; id = `n.get("id") or f"concept:{source}:{added}"`; `add_topic(... parent_id=root_id)` + `add_edge(root, nid, PART_OF)`; стоп при `added >= settings.ontology_max_vertices`;
- 0 узлов → вернуть `None`;
- рёбра: оба конца в `node_ids` и разные; relation вне `{part_of, prerequisite, related}` → `related`.

`build_heuristic_graph(root, topics, snippet_text)` — §4.3:
- `kg.add_topic(f"book:{root}", f"Учебник «{root}»", node_type="book")`;
- для каждой известной темы `t` (по одной на тему `subject|grade`): узел `topic:{t}`, title = `t`, type `topic`, parent_id root, ребро `part_of`;
- markdown-заголовки `^#{1,3}\s+(.+)$` и нумерованные `^\s*\d{1,2}[.)]\s+(.{2,90})$` из `snippet_text` → узлы `topic`/`section` (id `sec:{root}:web:{len(ids)}`) с фильтрами `junk.py` (длина ≥3, не url/noise/ui/paragraph-number/дубль), лимит 30 узлов (паттерн референса `_web_headings :303-357`);
- если узлов нет — деградация: `topic:{root}` + title `Тема «{root}»` + `part_of`.
Возвращает `KnowledgeGraph` (гарантированно ≥1 узла).

- [ ] **Step 3: Тесты `tests/test_kg_ontology.py`**

```python
from src.kg.ontology import (build_ontology_graph, build_heuristic_graph,
                             ontology_prompt, parse_ontology, extract_section_number)

VALID = '{"nodes":[{"id":"c1","title":"Сила тяжести","type":"concept","section":"§12"},'
        '{"id":"c2","title":"G=mg","type":"concept","section":"§12"}],'
        '"edges":[{"source":"c1","target":"c2","relation":"part_of"}]}'

def test_prompt_has_rules_and_limit():
    msgs = ontology_prompt("текст", "физ7", 14)
    assert msgs[0]["role"] == "system" and "Не больше 14 вершин" in msgs[0]["content"]
    assert "12000" not in msgs[0]["content"] and "Текст материала:" in msgs[1]["content"]

def test_build_ontology_valid():
    kg = build_ontology_graph("текст", "физ7", parse_ontology(VALID))
    d = kg.to_dict()
    assert d["nodes"][0]["id"] == "book:физ7"
    assert any(n["title"] == "Сила тяжести" for n in d["nodes"])
    assert d["nodes"][1]["section_number"] == "12"

def test_build_ontology_invalid_types_and_dup_dropped():
    raw = parse_ontology('{"nodes":[{"id":"a","title":"Сила","type":"бред","section":""},'
                         '{"id":"b","title":"Сила","type":"topic","section":""},'
                         '{"id":"c","title":"Улучшить свой запрос","type":"topic","section":""},'
                         '{"id":"d","title":"x","type":"topic","section":""}],"edges":[]}')
    kg = build_ontology_graph("текст", "s", raw)
    titles = [n["title"] for n in kg.to_dict()["nodes"]]
    assert titles == ["Учебник «s»", "Сила"]            # дедуп по lower, junk/короткий выброшен

def test_build_ontology_none_when_no_nodes():
    assert build_ontology_graph("т", "s", {"nodes": [], "edges": []}) is None

def test_build_ontology_edges_unknown_relation_related():
    raw = parse_ontology('{"nodes":[{"id":"a","title":"A B","type":"topic","section":""},'
                         '{"id":"b","title":"C D","type":"topic","section":""}],'
                         '"edges":[{"source":"a","target":"b","relation":"xyz"}]}')
    kg = build_ontology_graph("т", "s", raw)
    assert kg.to_dict()["edges"][0]["relation"] == "related"

def test_junk_filters():
    from src.kg.junk import (is_junk_topic, _is_paragraph_number, _is_ui_element,
                             _is_url_like, clean_title)
    assert is_junk_topic("Улучшить свой запрос") and is_junk_topic("похожие запросы")
    assert not is_junk_topic("Сила тяжести")
    assert _is_paragraph_number("§ 58") and _is_paragraph_number("12.")
    assert _is_url_like("https://example.com/x") and _is_url_like("www.ya.ru")
    assert _is_ui_element("Скачать:") or _is_ui_element("Скачать")
    assert clean_title("  Сила  — ") == "Сила  — ".strip(" *#-–—")  # без mojibake не трогает

def test_heuristic_fallback_and_degenerate():
    kg = build_heuristic_graph("физ7", ["Сила", "Скорость"], "## Ускорение\nтекст\n## Ускорение")
    d = kg.to_dict()
    assert d["nodes"][0]["id"] == "book:физ7"
    topics = {n["title"] for n in d["nodes"] if n["type"] == "topic"}
    assert {"Сила", "Скорость", "Ускорение"} <= topics      # маркерный заголовок добавлен, дедуп сработал
    single = build_heuristic_graph("x", [], "")
    assert single.to_dict()["nodes"][-1]["title"] == "Тема «x»"
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kg_ontology.py -q`
Expected: ~9 PASS.

- [ ] **Step 5: ruff**

Run: `.venv/Scripts/ruff.exe check src/kg/ tests/test_kg_ontology.py`
Expected: All checks passed.

---

### Task 6: JSON-кэш графа `src/kg/cache.py`

**Files:**
- Create: `src/kg/cache.py`
- Test: `tests/test_kg_cache.py`

**Interfaces:**
- Consumes: `settings.resolved_knowledge_graph_dir`, `settings.graph_schema_version` (Task 1).
- Produces:
  - `graph_cache_key(subject: str, grade: str, count: int, size: int) -> str` —
    `hashlib.sha1(f"v{settings.graph_schema_version}:{subject.lower()}:{grade}:{count}:{size}".encode("utf-8")).hexdigest()[:16]`.
  - `load_cached_graph(key: str, graph_dir: str | None = None) -> dict | None` — читает
    `{graph_dir}/{key}.json`; отсутствие/любое исключение (битый JSON, неверная структура) → `None`.
  - `save_graph(key: str, graph_dict: dict, graph_dir: str | None = None) -> str` — mkdir-parents,
    запись `json.dumps(..., ensure_ascii=False, indent=1)`, возвращает путь.

- [ ] **Step 1: Реализовать** (кэш оперирует СЛОВАРЁМ `to_dict()`-формата, а не объектом — обезличенный контентный граф на диске, §2.1/§4.4).

```python
def graph_cache_key(subject: str, grade: str, count: int, size: int) -> str:
    """Ключ кэша: схема + предмет/класс + кол-во тем + суммарный размер сниппетов.

    Схема и размер входят в ключ, чтобы граф пересобирался при росте базы и при
    изменении структуры (спека §4.4)."""
    fingerprint = f"v{settings.graph_schema_version}:{(subject or '').lower()}:{grade or ''}:{count}:{size}"
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 2: Тесты `tests/test_kg_cache.py`**

```python
def test_key_stable_and_sensitive_to_inputs(tmp_path):
    a = graph_cache_key("Физика", "7", 3, 1000)
    b = graph_cache_key("физика", "7", 3, 1000)     # lower-case одинаков
    assert a == b and len(a) == 16
    assert graph_cache_key("Физика", "7", 4, 1000) != a   # рост базы -> другой ключ
    assert graph_cache_key("Физика", "7", 3, 2000) != a   # рост размера -> другой ключ
    assert graph_cache_key("Физика", "8", 3, 1000) != a

def test_save_and_load_roundtrip(tmp_path):
    key = graph_cache_key("физика", "7", 1, 10)
    d = {"nodes": [{"id": "book:x", "title": "Учебник «x»", "type": "book"}], "edges": []}
    path = save_graph(key, d, graph_dir=str(tmp_path))
    assert Path(path).exists()
    assert load_cached_graph(key, str(tmp_path)) == d

def test_load_missing_and_corrupt_returns_none(tmp_path):
    assert load_cached_graph("deadbeef", str(tmp_path)) is None
    (tmp_path / "bad.json").write_text("{не json", encoding="utf-8")
    assert load_cached_graph("bad", str(tmp_path)) is None
```

- [ ] **Step 3: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kg_cache.py -q`
Expected: 3 PASS.

- [ ] **Step 4: ruff**

Run: `.venv/Scripts/ruff.exe check src/kg/cache.py tests/test_kg_cache.py`
Expected: All checks passed.

---

### Task 7: Сборка графа источника `src/kg/build.py` (RAG-сниппеты, scaffold, sync relations)

**Files:**
- Modify: `src/rag/__init__.py` (`InMemoryVectorStore` + `RAGEngine`)
- Create: `src/kg/build.py`
- Test: `tests/test_kg_build.py`

**Interfaces:**
- Consumes: `DocChunk`/`RAGEngine` (`src/rag/__init__.py`), `src.kg.ontology.*` (Task 5), `src.kg.cache.*` (Task 6), `settings` (Task 1).
- Produces:
  - `InMemoryVectorStore.list_chunks(filters: dict | None = None) -> list[DocChunk]` — все чанки,
    отфильтрованные по равенству метаданных (как `_match` в `search`); порядок вставки (свежие последними).
  - `RAGEngine.list_chunks(filters=None) -> list[DocChunk]` — прокси на `store.list_chunks`.
  - `collect_snippets(chunks, per_topic: int = 8) -> tuple[list[str], str, int, int]` — чистая функция:
    темы = уникальные `chunk.metadata["topic"]` в порядке первого появления; по теме берутся последние
    ≤`per_topic` текстов; `merged = "\n\n".join(...)`; возвращает `(topics, merged[:?], count, size)` где
    `count = len(topics)`, `size = sum(len(t) for t in texts)` (суммарный ДО обрезки).
  - `build_topic_scaffold(root: str, topics: list[str]) -> dict` — быстрый каркас
    `{"nodes":[{id:"book:{root}",...}]+[{id:"topic:{t}", title:t, type:"topic", parent_id, color}]...,
      "edges":[{"source":"book:{root}","target":"topic:{t}","relation":"part_of"}...]}` (>=1 узел; пустой topics → один `topic:{root}`).
  - `async def build_or_load_graph(*, subject, grade, chunks, graph_dir, llm, model) -> dict` —
    `collect_snippets`; `key = graph_cache_key(...)`; `cached = load_cached_graph(key)`; если есть → вернуть;
    иначе `root = f"{subject}|{grade}"` (или `subject`/`grade`-непустые части); `text = merged[:12000]`;
    `messages = ontology_prompt(text, root, settings.ontology_max_vertices)`;
    `raw = await chat_ontology(llm, model, messages)`; `kg = build_ontology_graph(text, root, parse_ontology(raw))`;
    если `None` → `kg = build_heuristic_graph(root, topics, merged)`; `save_graph(key, kg.to_dict())`; вернуть `kg.to_dict()`.
    Любое исключение LLM/кэша → фолбэк (не роняет).
  - `sync_relations_from_graph(rows: list[dict], nodes: list[dict], edges: list[dict]) -> dict[str, dict]` —
    чистая функция матчинга по нормализованному title (спека §3.4 + референс `graph.py:28-37`):
    нормализация `_PREFIX_RE = ^(?:урок|параграф|lesson|section|module|unit|тема|раздел)\s*\d+[.:\s—–-]*\s*`
    → убрать префикс, lowercase, сжать пробелы; вернуть `{topic: {"prerequisite": [...], "related": [...]}}`
    для тем, чей нормализованный title совпал с узлом, из рёбер типа `prerequisite`/`related`
    (ключ ребра — `relation`; таргет мапится обратно на topic по нормализованному title).

- [ ] **Step 1: `list_chunks` в RAG**

Добавить в `InMemoryVectorStore` и `RAGEngine` методы из «Interfaces» (фильтр — точное равенство значений метаданных, как `_match`). В `search` уже есть приватный `_match` — переиспользовать в `list_chunks`.

- [ ] **Step 2: `collect_snippets`**

Реализовать как чистую функцию: принимает список `DocChunk`; группирует по `metadata["topic"]`
(чанки без topic пропускает); по теме берёт «хвост» списка ≤`per_topic`; `size`/`count` считает до обрезки текста в 12000 (обрезку применяет `build_or_load_graph`).

- [ ] **Step 3: `build_or_load_graph` + `build_topic_scaffold`**

По «Interfaces». Ключ root-узла: `root = "|".join(part for part in (subject, grade) if part.strip()) or "subject"`; title корня `Учебник «{root}»` (title узла использует отображаемую форму; сам `root` — компактный id без пробелов — используй `subject|grade` как есть, normalize пробелы → `_`).

- [ ] **Step 4: `sync_relations_from_graph`**

Матчинг: построить `low_node_title -> node_id` и `low_node_title -> topic` из rows по `_norm_topic`; для рёбер, исходящих из совпавшего node_id, собрать relations. Возвращать только темы, у которых есть хотя бы одно отношение.

- [ ] **Step 5: Тесты `tests/test_kg_build.py`**

```python
import asyncio
from src.rag import DocChunk
from src.kg.build import (build_topic_scaffold, collect_snippets, sync_relations_from_graph)

def _chunk(topic, text):
    return DocChunk(id=f"c{hash(text)}", text=text, metadata={"topic": topic, "subject": "физика", "grade": "7"})

def test_collect_snippets_groups_and_counts():
    chunks = [_chunk("сила", "текст А1"), _chunk("скорость", "текст Б1"),
              _chunk("сила", "текст А2")]
    topics, merged, count, size = collect_snippets(chunks)
    assert topics == ["сила", "скорость"] and count == 2
    assert size == len("текст А1") + len("текст Б1") + len("текст А2")
    assert "текст А2" in merged

def test_collect_snippets_per_topic_cap():
    chunks = [_chunk("т", f"текст {i}") for i in range(12)]
    _, merged, count, _ = collect_snippets(chunks, per_topic=8)
    assert count == 1 and merged.count("текст") == 8

def test_build_topic_scaffold():
    d = build_topic_scaffold("физика|7", ["Сила", "Скорость"])
    ids = {n["id"] for n in d["nodes"]}
    assert "book:физика|7" in ids and {"topic:Сила", "topic:Скорость"} <= ids
    assert len(d["edges"]) == 2
    single = build_topic_scaffold("x", [])
    assert len(single["nodes"]) >= 1 and single["nodes"][-1]["type"] == "topic"

def test_sync_relations_normalized_title_match():
    rows = [{"topic": "Атмосфера"}, {"topic": "Погода"}]
    nodes = [{"id": "sec:x:12", "title": "Параграф 12: Атмосфера"},
             {"id": "sec:x:13", "title": "Параграф 13: Погода"}]
    edges = [{"source": "sec:x:12", "target": "sec:x:13", "relation": "prerequisite"}]
    out = sync_relations_from_graph(rows, nodes, edges)
    assert out["Атмосфера"]["prerequisite"] == ["Погода"]
```

- [ ] **Step 6: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kg_build.py tests/test_rag.py -q`
Expected: новые PASS + старые PASS.

- [ ] **Step 7: ruff**

Run: `.venv/Scripts/ruff.exe check src/rag/ src/kg/build.py tests/test_kg_build.py`
Expected: All checks passed.

---

### Task 8: Сервер — хуки мастерства, гейт, adaptive

**Files:**
- Modify: `src/api/session_store.py` (`ChatSession`, ~строки 23-33)
- Modify: `src/api/server.py` (`_run_chat` evaluation-блок ~354-366; `_build_adaptive` ~172-199; review-ветка из E1)
- Test: `tests/test_kg_server_hooks.py`

**Interfaces:**
- Consumes: `store.apply_result` (Task 3), `store.recommend_topics`/`get_prerequisite_gaps`/`list_topics` (Task 3), `AnswerRecord`/`build_answer_record`/`_run_review` (E1), `JsonlLogger` (сущ.).
- Produces:
  - `ChatSession.last_gate_topic: str = ""`.
  - В `_run_chat`, НЕ трогая агента:
    - evaluation-конверт: вместо `store.touch_topic(...)` — `store.apply_result(student_id, record.topic, record.subject, record)` (guard: `record.topic` непустой; try/except; сбой БД не роняет чат). Логировать `mastery.update` `{topic, mastery, status}`.
    - review-ветка (E1 `_run_review`): после грейда каждого ответа обновлять мастерство темы карточки через `store.apply_result(...)` c записью review-ответа (карточку НЕ создаёт; секрета квиза нет).
    - mastery-гейт: ДО `run_agent`, если `body.topic` непустой и `!= session.last_gate_topic`: `gaps = store.get_prerequisite_gaps(student_id, body.topic)`; если gaps → `on_event("system", {...})`; всегда `session.last_gate_topic = body.topic`.
  - `_build_adaptive(...)`: добавить `topic_status`, `topic_mastery`, `topic_accuracy` для текущей темы; `recommended_next` — из `store.recommend_topics(student_id, subject=body.subject, current_topic=topic, limit=1)[0]["topic"]`, фолбэк на legacy `pick_recommendation`.

- [ ] **Step 1: Поле сессии `last_gate_topic`**

В dataclass `ChatSession` (после полей E1 `subject/grade/last_quiz`) добавить `last_gate_topic: str = ""`.

- [ ] **Step 2: Замена `touch_topic` → `apply_result` в evaluation-пути**

В `src/api/server.py` блок `if store is not None and envelope.type.value == "evaluation":` (текущий вызов `touch_topic`, ~строка 359) заменить на:

```python
    if store is not None and envelope.type.value == "evaluation":
        payload = envelope.payload or {}
        # AnswerRecord строится в E1-хуке выше (last_user/feedback/secret); здесь — mastery.
        if getattr(record, "topic", None):
            try:
                state_row = store.apply_result(
                    student_id, record.topic, record.subject or body.subject, record)
                jsonl.log(trace_id, "INFO", "mastery.update",
                          topic=record.topic, mastery=state_row["mastery"], status=state_row["status"])
            except Exception as exc:  # noqa: BLE001
                print(f"[student] не удалось применить мастерство: {exc}")
```

Уточнение для исполнителя: `record` — AnswerRecord, уже построенный E1-хуком в этом же блоке; если в текущем коде он строится ниже `touch_topic`, поднять построение записи ДО этого вызова (порядок внутри блока свободен, но `apply_result` должен получить ту же запись, что идёт в review-банк). `jsonl = JsonlLogger(settings.log_file)` — локальный инстанс либо `app.state.jsonl` (создать в `create_app`, см. Task 9).

- [ ] **Step 3: Mastery-гейт**

После регистрации сессии в SQLite и ДО ветки hint/review и `run_agent` (рядом с `_provision_for`) добавить (только при `on_event is not None`):

```python
    if on_event is not None and store is not None and body.topic.strip():
        if session.last_gate_topic != body.topic:
            session.last_gate_topic = body.topic
            try:
                gaps = store.get_prerequisite_gaps(student_id, body.topic)
            except Exception:  # noqa: BLE001
                gaps = []
            if gaps:
                on_event("system", {
                    "message": f"Совет: прежде чем «{body.topic}», стоит повторить: {', '.join(gaps)}.",
                    "kind": "mastery.gate", "gaps": gaps, "topic": body.topic})
```

- [ ] **Step 4: Review-ответы тоже обновляют мастерство**

В E1 `_run_review` там, где грейдится ответ карточки (после `store.review_card(...)`), добавить
`store.apply_result(student_id, card["topic"], card.get("subject") or "", record)` — запись строится из
карточки (`question/options/answer_type/correct_answer/difficulty`) и результата грейда (`correct`, feedback).
Обернуть в try/except. Guard: `card["topic"]` непустой.

- [ ] **Step 5: `_build_adaptive` — mastery/status/rec**

Расширить возвращаемый dict:

```python
    topic_payload = None
    if store is not None and topic:
        try:
            topic_payload = store.get_topic(student_id, topic)
        except Exception:  # noqa: BLE001
            topic_payload = None
    rec = None
    if store is not None:
        try:
            recs = store.recommend_topics(student_id, subject="", current_topic=topic, limit=1)
            rec = recs[0]["topic"] if recs else pick_recommendation(topics, next_from_model=next_from_model)
        except Exception:  # noqa: BLE001
            rec = pick_recommendation(topics, next_from_model=next_from_model)
    return {..., "topic_status": (topic_payload or {}).get("status"),
            "topic_mastery": (topic_payload or {}).get("mastery"),
            "topic_accuracy": (topic_payload or {}).get("accuracy"),
            "recommended_next": rec}
```

Замечание: `_build_adaptive` сигнатура не меняется (topic/subject приходят через `body.topic`; для subject-фильтра рекомендаций используйте `body.subject`, если нужно передать — расширьте сигнатуру опциональным `subject: str = ""`).

- [ ] **Step 6: Тесты `tests/test_kg_server_hooks.py`** (TestClient + фейковый SequencePlanner из `tests/test_review_auto.py`)

```python
def test_evaluation_applies_mastery(tmp_path):   # планировщик: quiz -> evaluation(correct:true)
    # после двух верных ответов (2 хода evaluation) get_topic(...)["mastery"] == 0.755,
    # а level == mastery; legacy touch_topic-тесты остаются зелёными отдельно
    ...

def test_mastery_gate_emitted_once(tmp_path):    # ставим relations заранее через store,
    # /chat/stream с topic="B" (B имеет незакрытый prerequisite "A") -> в тексте SSE есть
    # 'mastery.gate', 'Совет: прежде чем «B»'; второй ход с тем же topic гейт НЕ дублирует
    ...

def test_adaptive_has_mastery_fields(client):
    # POST /chat (теория) -> body["adaptive"] содержит topic_status/topic_mastery/topic_accuracy
```

Подсказка для исполнителя: мастерство не обновится, если тестовая последовательность конвертов не заканчивается evaluation; используйте очередь конвертов, как в `test_review_auto.py` (`quiz` → `evaluation`), и ОДИН session_id.

- [ ] **Step 7: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kg_server_hooks.py tests/test_api.py tests/test_review_auto.py tests/test_adaptive.py -q`
Expected: новые PASS + существующие PASS.

- [ ] **Step 8: ruff**

Run: `.venv/Scripts/ruff.exe check src/api/session_store.py src/api/server.py tests/test_kg_server_hooks.py`
Expected: All checks passed.

---

### Task 9: API графа знаний + payload-помощники `src/student/student_kg.py`

**Files:**
- Create: `src/student/student_kg.py`
- Modify: `src/api/server.py` (endpoints после `/student/{id}/sessions`, ~строка 566; `create_app` state ~387-400)
- Test: `tests/test_kg_api.py`, `tests/test_student_kg_payload.py`

**Interfaces:**
- Consumes: `store.list_topics/get_topic/get_mastered_topics/get_in_progress_topics/get_weak_topics/`
  `get_prerequisite_gaps/recommend_topics` (Task 3), `src.kg.build` (Task 7), `src.kg.cache` (Task 6),
  `RAGEngine.list_chunks` (Task 7), `sync_relations_from_graph` (Task 7), `settings` (Task 1).
- Produces:
  - `src/student/student_kg.py` (чистые, без sqlite):
    - `accuracy(attempts, correct) -> float`
    - `row_payload(row) -> dict` — `{topic, subject, status, mastery, attempts, correct, accuracy, weak_areas, last_seen, relations}`.
    - `knowledge_graph_payload(student_id, subject, rows: list[dict]) -> dict` — §5 точная форма:
      `{"student_id", "subject", "topics": {t: row_payload...}, "stats": {mastered, in_progress, not_studied, total}}`.
    - `stats_of(rows) -> dict`.
  - `create_app(...)` новые state-поля: `app.state.graph_building: set[str]`, `app.state.graph_ready_events: dict[str, list[dict]]` (ключ `subject|grade`), `app.state.jsonl = JsonlLogger(settings.log_file)`.
  - Хелперы в `server.py`:
    - `_resolve_subject_grade(app, student_id, subject, grade) -> tuple[str, str]` — параметры либо последняя сессия (`store.get_last_session`).
    - `_overlay_mastery(rows_by_norm_topic, nodes) -> None` — для каждого узла: `_norm_topic(title)` (референс `graph.py:28-37`) → row → добавить `mastery/attempts/correct/accuracy/status` (только если тема существует).
    - `_ensure_graph_build(app, subject, grade)` — fire-and-forget (ключ в `app.state.graph_building`), при завершении: сохранить кэш, положить `app.state.graph_ready_events[key]`, лог `kg.ready`, диск из `graph_building`.
    - `_drain_graph_ready(app, on_event, subject, grade)` — эмит накопленных `graph.ready` в начале хода (в `_run_chat`, только при `on_event`).
  - Endpoints (все отдают plain dict):
    - `GET /student/{student_id}/knowledge-graph?subject=`
    - `GET /student/{student_id}/recommendations?current_topic=&subject=&limit=` (limit 1..20)
    - `GET /student/{student_id}/graph?subject=&grade=`
    - `GET /student/{student_id}/graph/{node_id}/related`
    - `GET /student/{student_id}/graph/{node_id}/wiki` (заглушка `wiki: null`; 404 «Узел не найден» для неизвестного узла)

- [ ] **Step 1: `src/student/student_kg.py`**

Реализовать чистые функции из «Interfaces». `stats_of` считает по `status` строки, но `mastered` — по `is_mastered(attempts, mastery, status)`.

- [ ] **Step 2: Состояние приложения**

В `create_app` добавить: `app.state.graph_building = set()`, `app.state.graph_ready_events = {}`, `app.state.jsonl = JsonlLogger(settings.log_file)`. (Модульный импорт `from src.student.student_kg import knowledge_graph_payload, stats_of` наверху `server.py`.)

- [ ] **Step 3: Хелперы `_resolve_subject_grade`, `_overlay_mastery`, `_norm_topic`**

`_norm_topic` — дословно референс `graph.py:28-37`:

```python
_PREFIX_RE = re.compile(r"^(?:урок|параграф|lesson|section|module|unit|тема|раздел)\s*\d+[.:\s—–-]*\s*", re.IGNORECASE)

def _norm_topic(title: str) -> str:
    t = _PREFIX_RE.sub("", title or "").strip()
    return re.sub(r"\s+", " ", t).lower()
```

- [ ] **Step 4: Endpoints**

`GET /knowledge-graph`:
```python
@app.get("/student/{student_id}/knowledge-graph")
def student_knowledge_graph(student_id: str, subject: str = "") -> dict:
    """Слой 2: статусы тем ученика (mastery/статистика)."""
    store: StudentStore = app.state.student_store
    if store is None:
        return {"student_id": student_id, "subject": subject, "topics": {}, "stats": {...нули...}}
    rows = [r for r in store.list_topics(student_id)
            if not subject or (r.get("subject") or "") == subject]
    return knowledge_graph_payload(student_id, subject, rows)
```
(смотреть: у пустого списка всё равно `stats` из нулей; существующий референс отдаёт без stats — для edututor отдаём `stats` всегда, это допустимо и проще для UI).

`GET /recommendations`: `{"recommendations": [row_payload...], "weak_topics": [...], "prerequisite_gaps": [...gaps]}`.

`GET /graph` (async):
```python
@app.get("/student/{student_id}/graph")
async def student_graph(student_id: str, subject: str = "", grade: str = "") -> dict:
    """Граф источника subject|grade с мастерством ученика; каркас + fire-and-forget сборка."""
    store: StudentStore = app.state.student_store
    subject, grade = _resolve_subject_grade(app, student_id, subject, grade)
    rag: RAGEngine | None = app.state.rag_engine
    chunks = rag.list_chunks({"subject": subject, "grade": grade}) if rag is not None else []
    topics, merged, count, size = collect_snippets(chunks)
    key = graph_cache_key(subject, grade, count, size)
    graph = load_cached_graph(key, settings.resolved_knowledge_graph_dir)
    source = None
    if graph is None:
        graph = build_topic_scaffold(f"{subject}|{grade}" if (subject or grade) else "subject", topics)
        _ensure_graph_build(app, subject, grade, chunks)          # fire-and-forget
    _maybe_sync_relations(app, store, student_id, subject, graph)
    nodes = [dict(n) for n in graph["nodes"]]
    rows = [r for r in (store.list_topics(student_id) if store else []) if r["subject"] == subject]
    _overlay_mastery(rows, nodes)
    return {"root": nodes[0]["id"] if nodes else None, "nodes": nodes,
            "edges": graph["edges"], "active_topic": None,
            "stats": {"nodes": len(nodes), "edges": len(graph["edges"])}}
```

`GET /graph/{node_id}/related`: найти узел в текущем графе (кэш или каркас по тем же правилам subject/grade; без запуска сборки), `KnowledgeGraph.from_dict(...)`, вернуть `{"node": node, "related": kg.neighbors(node_id, max_depth=2)}`; узел неизвестен → `{"node": None, "related": []}` (спека §8; для wiki — 404).

`GET /graph/{node_id}/wiki`: узел из графа; нет → `HTTPException(404, "Узел не найден")`; иначе `{"node": node, "wiki": None}` (E2-заглушка; E4 вернёт статью).

`_ensure_graph_build(app, subject, grade, chunks)` — асинхронная задача:
- ключ сборки `f"{subject}|{grade}"`; если он уже в `app.state.graph_building` → return (гонки §8);
- добавить в set, `asyncio.create_task(_build_background(app, subject, grade, chunks))`;
- `_build_background`: `graph = await build_or_load_graph(...)` (llm из `app.state.runtime_factory().llm`, model `models["fast"]`); сохранить кэш (уже внутри build_or_load); после — sync relations для всех студентов не делаем (синк только при чтении); `app.state.graph_ready_events[key].append({"root":..., "stats":...})`; лог `kg.ready`; `app.state.graph_building.discard(key)` в finally.

`_drain_graph_ready(app, on_event, subject, grade)`: в начале `_run_chat` (при `on_event`) для ключа текущей сессии вынуть и эмитить все накопленные события.

- [ ] **Step 5: Тесты `tests/test_student_kg_payload.py`**

```python
def test_payload_stats():
    rows = [{"topic": "a", "subject": "ф", "status": "mastered", "mastery": 0.9, "attempts": 3, "correct": 3,
             "weak_areas": [], "relations": {"prerequisite": [], "related": []}, "last_seen": 1.0},
            {"topic": "b", "subject": "ф", "status": "in_progress", "mastery": 0.5, "attempts": 1, "correct": 1,
             "weak_areas": [], "relations": {"prerequisite": [], "related": []}, "last_seen": 2.0}]
    out = knowledge_graph_payload("stu", "ф", rows)
    assert out["topics"]["a"]["accuracy"] == 1.0
    assert out["stats"] == {"mastered": 1, "in_progress": 1, "not_studied": 0, "total": 2}
```

- [ ] **Step 6: Тесты `tests/test_kg_api.py`** (TestClient + tmp_path store + ручной RAGEngine c `embedder=FakeEmbedder`)

```python
def test_knowledge_graph_endpoint_empty_and_filled(client):
    # пусто: GET /student/stu_1/knowledge-graph -> 200, topics == {}, stats.total == 0
    # после store.apply_result 3 верных -> stats.mastered == 1

def test_recommendations_endpoint(client):
    # prerequisites/gaps согласованы со store

def test_graph_scaffold_and_overlay(client):
    # rag с чанками (subject=физика grade=7) -> GET /student/stu_1/graph?subject=физика&grade=7
    # -> nodes[0]["id"] == "book:физика|7" (или root), topic-узлы темы; после apply_result по теме
    # "Сила" у узла topic:Сила появились ключи mastery/attempts/correct/accuracy/status

def test_graph_related_and_wiki(client):
    # related отдаёт соседей глубины 2 либо пусто; wiki всегда {"wiki": null};
    # wiki неизвестного узла -> 404

def test_graph_ready_event_drained(client):
    # кладём в app.state.graph_ready_events ключ; /chat/stream следующим ходом содержит
    # 'event: graph.ready' c root/stats
```

Fake-embedder: класс с методом `embed(texts) -> list[list[float]]` (нулевые векторы) — уже-используемый паттерн в `test_rag.py`/`test_provisioning.py`; graph endpoint с каркасом не требует эмбеддингов (используется `list_chunks`, не `search`), поэтому достаточно RAG-движка без реальной модели: `RAGEngine(store=InMemoryVectorStore(), embedder=<fake>)` + `ingest(...)`. В `client`-fixture внедрить `rag_engine` через `create_app(...)`.

- [ ] **Step 7: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kg_api.py tests/test_student_kg_payload.py tests/test_api.py -q`
Expected: новые PASS + существующие PASS.

- [ ] **Step 8: ruff**

Run: `.venv/Scripts/ruff.exe check src/student/student_kg.py src/api/server.py tests/test_kg_api.py tests/test_student_kg_payload.py`
Expected: All checks passed.

---

### Task 10: Фронтенд — api.js, «Мои знания» (StudentKGPanel) и MasteryWall

**Files:**
- Modify: `frontend/src/api.js`
- Create: `frontend/src/components/StudentKGPanel.jsx`
- Create: `frontend/src/components/MasteryWall.jsx`
- Modify: `frontend/src/components/AdaptivePanel.jsx` (удалить кнопку «Повторить (N)» — переезжает в StudentKGPanel; оставить mastery-поля, если добавлены в Task 8)
- Modify: `frontend/src/index.css`
- Modify: `frontend/src/api.test.js`
- Test: `frontend/src/components/StudentKGPanel.test.jsx`, `frontend/src/components/MasteryWall.test.jsx`

**Interfaces:**
- Consumes: API `GET /student/{id}/knowledge-graph?subject=`, `GET /student/{id}/review` (E1).
- Produces:
  - `api.getKnowledgeGraph(studentId, subject='')`, `api.getRecommendations(studentId, opts)`, `api.getGraph(studentId, subject, grade)`, `api.getGraphRelated(studentId, nodeId)`, `api.getGraphWiki(studentId, nodeId)`.
  - `StudentKGPanel` props `{studentId, subject='', onStartReview=null, busy=false, reloadKey=0}`:
    - статус-цвета `not_studied:'#9ca3af' / in_progress:'#fbbf24' / mastered:'#4ade80'`, метки `Не изучалось/В процессе/Освоено`;
    - шапка `всего / освоено / в процессе / не изучено` из `data.stats`;
    - строка темы: статус-бар, `{correct}/{attempts} · {accuracy*100:.0f}%`, слабые места (первые 2, при большем — `…`), 
      кнопка «Повторить (N)» — видна когда `onStartReview` задан и `dueCount>0`, `disabled` при `busy`, `N = data.due.stats.due` (второй запрос `api.getReview(studentId)`), клик → `onStartReview()`;
    - сортировка: `in_progress(0) → not_studied(1) → mastered(2)`, внутри — `last_seen` desc;
    - empty-state «Тем пока нет. Пройдите квиз по теме — знания накопятся.»;
    - экспорт `masteryColor`, `statusMeta` (для тестов).
  - `MasteryWall` props `{topics=[], onSelect=null}` (данные из `/knowledge-graph`, спека §6.1):
    - цвет ячейки `masteryColor(m)`: `m>=0.75 → 'high'`, `m>=0.45 → 'mid'`, else `'low'`; заголовок `Усвоение`, счётчик `{mastered}/{total} · {pct}%`;
    - клик по ячейке → `onSelect(topic)`; `null` при пустом списке.

- [ ] **Step 1: `api.js`**

```js
  getKnowledgeGraph: (studentId, subject = '') =>
    request(`/student/${encodeURIComponent(studentId)}/knowledge-graph${subject ? `?subject=${encodeURIComponent(subject)}` : ''}`),
  getRecommendations: (studentId, { current_topic = '', subject = '', limit = 5 } = {}) =>
    request(`/student/${encodeURIComponent(studentId)}/recommendations?current_topic=${encodeURIComponent(current_topic)}&subject=${encodeURIComponent(subject)}&limit=${limit}`),
  getGraph: (studentId, subject = '', grade = '') =>
    request(`/student/${encodeURIComponent(studentId)}/graph?subject=${encodeURIComponent(subject)}&grade=${encodeURIComponent(grade)}`),
  getGraphRelated: (studentId, nodeId) =>
    request(`/student/${encodeURIComponent(studentId)}/graph/${encodeURIComponent(nodeId)}/related`),
  getGraphWiki: (studentId, nodeId) =>
    request(`/student/${encodeURIComponent(studentId)}/graph/${encodeURIComponent(nodeId)}/wiki`),
```

- [ ] **Step 2: `StudentKGPanel.jsx`**

Данные: `useEffect` грузит `api.getKnowledgeGraph(studentId, subject)` и `api.getReview(studentId)`; рефетч на `[studentId, subject, reloadKey]`. Рендер по «Interfaces» (без canvas). Экспортировать `masteryColor` и `statusMeta`.

- [ ] **Step 3: `AdaptivePanel.jsx`**

Убрать кнопку «Повторить (N)» (она в StudentKGPanel); оставшиеся mastery-поля из Task 8 (`topic_status`, `topic_mastery`) можно показать строкой/баром на усмотрение, без смены контракта props (`adaptive`, `onStudy`, `onReview` больше не нужен — при необходимости приём `onReview` остаётся no-op до удаления в App).

- [ ] **Step 4: `MasteryWall.jsx`**

Порт референса `MasteryWall.jsx` (без `/api/wiki`): props `{topics, onSelect}`; `masteryClass(m)` CSS-классы `high/mid/low`; вернуть `null` при пустом. Экспортировать `masteryClass`.

- [ ] **Step 5: CSS**

В `index.css`: классы `.kg-panel`, `.kg-topic`, `.kg-status-{not_studied,in_progress,mastered}`,
`.kg-weak`, `.btn.review`, `.mastery-wall`, `.mw-cell.{high,mid,low}`, `.kg-empty`. Стили в палитре «тетради».

- [ ] **Step 6: Тесты**

`StudentKGPanel.test.jsx` — мок `api.getKnowledgeGraph`:
```jsx
const data = { student_id: 'stu_x', subject: 'физика', topics: {
  'Сила': { topic: 'Сила', subject: 'физика', status: 'mastered', mastery: 0.83, attempts: 3, correct: 3,
            accuracy: 1, weak_areas: [], last_seen: 1, relations: {} },
}, stats: { mastered: 1, in_progress: 0, not_studied: 0, total: 1 } }
// рендер: «Освоено», «1/1», getReview мок {stats:{due:2}} -> кнопка «Повторить (2)»; клик зовёт onStartReview
// sortedByStatus: in_progress идёт раньше not_studied
```
`MasteryWall.test.jsx`: `masteryClass(0.8)==='high'`, `masteryClass(0.5)==='mid'`, `masteryClass(0.2)==='low'`; клик по ячейке зовёт `onSelect`; пустой список → не рендерит.

- [ ] **Step 7: Проверка**

Run (cwd `frontend`): `npm test` и `npm run lint`
Expected: все PASS / lint clean.

---

### Task 11: Фронтенд — KnowledgeGraphPanel (Canvas «Созвездие»)

**Files:**
- Create: `frontend/src/components/KnowledgeGraphPanel.jsx`
- Test: `frontend/src/components/KnowledgeGraphPanel.test.jsx`

**Interfaces:**
- Consumes: данные `GET /student/{id}/graph` (узлы/рёбра), `getGraphRelated`, `getGraphWiki` (Task 10).
- Produces:
  - `KnowledgeGraphPanel` props `{nodes=[], edges=[], activeTopic=null, onSelect=null, sessionId=null}`.
  - Чистые функции для тестов: `masteryColor(mastery)` (спека §6.2), `EDGE_COLORS`, `TYPE_LABELS`, `filterStructural(nodes)`.
  - Рендер: `<canvas>` + пустой-state (`nodes.length===0`); zoom/pan/drag, hover-тултип, поиск, чекбокс «Показываем разделы и уроки» (фильтр `section/lesson`), клик по узлу → параллельные `getGraphRelated`/`getGraphWiki`, кнопка «📖 Изучить тему» → `onSelect(node)`.
  - Цвета рёбер `part_of:'#64DFDF' / prerequisite:'#FFB703' / related:'#B388FF'`; узлы — мастерство `>=0.61:'#4ade80' / >=0.31:'#fbbf24' / else '#f87171'`, иначе `node.color` (с бэкенда).
  - Без внешних библиотек графа (только `<canvas>` + requestAnimationFrame + ручная force-simulation).

- [ ] **Step 1: Компонент**

Порт референса `KnowledgeGraphPanel.jsx` на текущие пропсы/цвета. `masteryColor` вернуть `null`, если `mastery` отсутствует (узел не окрашивать, использовать `node.color`).

- [ ] **Step 2: Тесты** (без пиксель-снапшотов, спека §10)

```jsx
test('empty state renders without crash', () => render(<KnowledgeGraphPanel nodes={[]} edges={[]} />))
test('renders canvas with data', () => {
  const nodes = [{ id: 'book:x', title: 'Учебник «x»', type: 'book', color: '#F4A261' },
                 { id: 'topic:Сила', title: 'Сила', type: 'topic', color: '#69F0AE', mastery: 0.9 }]
  render(<KnowledgeGraphPanel nodes={nodes} edges={[{ source: 'book:x', target: 'topic:Сила', relation: 'part_of' }]} />)
})
test('masteryColor thresholds', () => {
  expect(masteryColor(0.7)).toBe('#4ade80'); expect(masteryColor(0.5)).toBe('#fbbf24')
  expect(masteryColor(0.2)).toBe('#f87171'); expect(masteryColor(undefined)).toBeNull()
})
test('filterStructural hides section/lesson', () => { /* ... */ })
```

- [ ] **Step 3: Проверка**

Run (cwd `frontend`): `npm test` и `npm run lint`
Expected: все PASS / lint clean.

---

### Task 12: Фронтенд — App wiring, feedReducer, баннер гейта, layout

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/components/Chat.jsx` (рендер баннеров + `feedReducer`)
- Modify: `frontend/src/index.css`
- Modify: `frontend/src/App.test.jsx`, `frontend/src/components/Chat.test.jsx`
- Test: расширения в тех же файлах

**Interfaces:**
- Consumes: `feedReducer` (Chat.jsx), `api.*` (Task 10), компоненты (Task 10/11), `current.{subject,grade}`.
- Produces:
  - `feedReducer`: 
    - `system` c `data.kind === 'mastery.gate'` → добавить в `items` элемент `{id, kind:'mastery.gate', message, gaps, topic}`;
    - `graph.ready` → добавить системную строку «Построен граф знаний: N тем» как элемент `{id, kind:'system-note', content}` (значения из `data.stats`);
    - прочие события — без изменений.
  - `Chat` рендерит элементы `kind==='mastery.gate'` как баннер (кнопки «Всё равно продолжить» → dismiss, «Перейти к «{g}»» → `onGoTopic(g)`); элементы `system-note` — обычной системной строкой.
  - `App`: правая колонка — `AdaptivePanel` + `MasteryWall` + `StudentKGPanel`; граф «Созвездие» — отдельная секция над чатом в центральной колонке (раскрывается, когда есть subject/grade).
  - Обработка событий в `runTurn`: `graph.ready` → `refreshGraph()` (GET graph по текущим subject/grade + передать nodes/edges в KnowledgeGraphPanel) и `reloadKGPanel()` (инкремент `kgReloadKey`); после `done` → обновить панель «Мои знания» и граф (мастерство могло измениться).
  - `startTopic`/`studyNext` уже существуют; баннер «Перейти к «{gap}»» вызывает `studyNext(gap)`/`startTopic({topic: gap, subject, grade})`.

- [ ] **Step 1: `feedReducer` в `Chat.jsx`**

```js
  if (name === 'system') {
    if (data.kind === 'mastery.gate') {
      return { ...feed, items: [...feed.items,
        { id: `g${feed.items.length}`, kind: 'mastery.gate', message: data.message || '',
          gaps: data.gaps || [], topic: data.topic || '' }] }
    }
    return feed
  }
  if (name === 'graph.ready') {
    const n = (data && data.stats && data.stats.nodes) || 0
    return { ...feed, items: [...feed.items,
      { id: `s${feed.items.length}`, kind: 'system-note', content: `Построен граф знаний: ${n} тем.` }] }
  }
```

- [ ] **Step 2: `Chat` рендер баннера**

Для `item.kind === 'mastery.gate'` рендерить `<div className="banner gate">` с текстом `item.message`, кнопкой «Всё равно продолжить» (`onDismiss(id)`) и для каждого `gap` — кнопкой `Перейти к «{gap}»` (`onGoTopic(gap, topic)`)`. Сигнатуры: `Chat` props расширяются `onDismissBanner(id)` и `onGoTopic(gap, topic)` (no-op по умолчанию). `system-note` — строка `.system-note`.

- [ ] **Step 3: `App` — layout и данные**

- Состояние: `const [graph, setGraph] = useState({ nodes: [], edges: [], activeTopic: null })`, `const [kgReloadKey, setKgReloadKey] = useState(0)`.
- `refreshGraph` (subject/grade из `current` или ''): `api.getGraph(studentId, subject, grade).then(d => setGraph({...}))` с `.catch(noop)`.
- `reloadKg = () => setKgReloadKey(k => k + 1)`.
- В `runTurn` (дополнительно к текущему `else feedReducer(...)`):
```js
      } else if (ev.event === 'graph.ready') {
        setFeed((f) => feedReducer(f, ev))
        refreshGraph()
        reloadKg()
      } else if (ev.event === 'system' && ev.data?.kind === 'mastery.gate') {
        setFeed((f) => feedReducer(f, ev))
      } else {
        setFeed((f) => feedReducer(f, ev))
      }
```
- В `message`/`done`-обработчиках вызвать `reloadKg()`/`refreshGraph()` (после `done` мастерство обновилось).
- Правая колонка: `<AdaptivePanel .../>`, `<MasteryWall topics={kgTopics} onSelect={studyNext}/>`, `<StudentKGPanel studentId={...} subject={current?.subject || ''} onStartReview={...} reloadKey={kgReloadKey} busy={busy}/>`. `kgTopics` берём из fetch `getKnowledgeGraph` (общий эффект с панелью либо состояние `kgData`).
- Граф: над `<Chat>` в центральной колонке `<KnowledgeGraphPanel nodes={graph.nodes} edges={graph.edges} activeTopic={graph.activeTopic} onSelect={(node) => studyNext(node.title)} sessionId={current?.session_id || ''} />`, когда есть subject/grade либо nodes.
- `onDismissBanner={(id) => setFeed(f => ({ ...f, items: f.items.filter(i => i.id !== id) }))}`, `onGoTopic={(gap) => studyNext(gap)}`.

- [ ] **Step 4: App.test.jsx — расширить мок**

В `vi.mock('./api', ...)` добавить `getKnowledgeGraph: vi.fn(() => Promise.resolve({ topics: {}, stats: {total:0, mastered:0, in_progress:0, not_studied:0} }))`, `getReview: vi.fn(() => Promise.resolve({ stats: { due: 0 } }))`, `getGraph: vi.fn(() => Promise.resolve({ nodes: [], edges: [] }))`, `getRecommendations: vi.fn(() => Promise.resolve({ recommendations: [] }))`. Существующие тесты не должны сломаться.

- [ ] **Step 5: Chat.test.jsx**

```jsx
test('feedReducer adds mastery.gate banner', () => {
  const feed = feedReducer(emptyFeed, { event: 'system', data: { kind: 'mastery.gate', message: 'Совет: ...', gaps: ['Сила'], topic: 'B' } })
  expect(feed.items[0].kind).toBe('mastery.gate')
})
test('banner renders message and gap button', () => { /* render Chat c items [gate]; кнопка «Перейти к «Сила»» */ })
test('graph.ready adds system note', () => { ... })
```

- [ ] **Step 6: Проверка**

Run (cwd `frontend`): `npm test` и `npm run lint`
Expected: все PASS / lint clean.

---

### Task 13: Документация и финальная валидация

**Files:**
- Modify: `README.md`
- Modify: `adaptive_tutor/docs/api.md`

- [ ] **Step 1: README**

Добавить: описание графа знаний (3 слоя), таблицу API с новыми эндпоинтами
(`GET /student/{id}/knowledge-graph`, `/recommendations`, `/graph`, `/graph/{node}/related`,
`/graph/{node}/wiki`), заметку про `data/knowledge_graphs/` и конфиг-переменные `TUTOR_*`.

- [ ] **Step 2: `adaptive_tutor/docs/api.md`**

Описать: JSON-формы ответов §5 спеки, SSE-события `system kind="mastery.gate"` и `graph.ready`,
нормализацию title в оверлее (`_norm_topic`), ленивую сборку графа (каркас → fire-and-forget).

- [ ] **Step 3: Финальная проверка бэкенда**

Run (cwd `adaptive_tutor`): `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: все PASS (старые + новые).

- [ ] **Step 4: Финальный ruff**

Run (cwd `adaptive_tutor`): `.venv/Scripts/ruff.exe check src/ tests/`
Expected: All checks passed.

- [ ] **Step 5: Финальная проверка фронтенда**

Run (cwd `frontend`): `npm test` и `npm run lint`
Expected: все PASS / lint clean.

---

## Self-Review (план сверен со спекой)

- **Спека §1 (слои/порядок: Слой 2 → Слой 1; Wiki — E4)** → порядок Task 2/3 (Слой 2) → Task 4-7/9 (Слой 1); заглушка wiki — Task 9.
- **Спека §2 (решения: sqlite=true, EMA=0.7/0.3, статусы авто, единый ключ темы, мягкие гейты, touch_topic legacy, networkx)** → Global Constraints + Task 1, 2, 3, 8.
- **Спека §3.1 (миграция колонок topics)** → Task 3.
- **Спека §3.2 (методы StudentStore)** → Task 3.
- **Спека §3.3 (алгоритмы: derive_status, is_mastered, weak_areas, gaps, recommend)** → Task 2, 3.
- **Спека §3.4 (синхронизация: apply_result на evaluation и review; relations по title)** → Task 7 (sync_relations_from_graph), Task 8 (apply_result), Task 9 (overlay/sync в GET).
- **Спека §4.1 (модель networkx + JSON: узлы/рёбра, id-конвенции, to_dict/from_dict)** → Task 4.
- **Спека §4.2 (источник: RAG-сниппеты, list_topics, app.state.provisioned, merged 12000)** → Task 7 (list_chunks/collect_snippets) + Task 9 (server gather через rag.list_chunks).
- **Спека §4.3 (LLM-онтология: роль fast, temp 0.0, max_tokens 900, промпт, валидация; фолбэк; junk-наборы)** → Task 5.
- **Спека §4.4 (кэш: sha1-ключ со схемой/размером, fail-soft, каркас + graph.ready fire-and-forget)** → Task 6, Task 7 (build_or_load), Task 9 (endpoint/_ensure_graph_build/_drain_graph_ready).
- **Спека §4.5 (мастерство-оверлей, гейт пререквизитов, рекомендации)** → Task 8, Task 9 (_overlay_mastery).
- **Спека §5 (эндпоинты + SSE graph.ready/system mastery.gate)** → Task 9; формы ответов — Task 9 + `student_kg.py` (Task 9).
- **Спека §6 (UI: StudentKGPanel, MasteryWall, KnowledgeGraphPanel, feedReducer)** → Task 10, 11, 12.
- **Спека §7 (конфиг/зависимости)** → Task 1.
- **Спека §8 (крайние случаи: пустой граф, мусор LLM→фолбэк, пересборка кэша, дубли/регистр, гонки graph_building, level vs mastery, существующие тесты зелёные)** → Task 1-9 (явные guard-тесты в каждом).
- **Спека §9 (наблюдаемость: kg.build/kg.ready/mastery.update/mastery.gate)** → Task 8 (mastery.update, mastery.gate), Task 9 (jsonl + kg.ready + app.state.jsonl).
- **Спека §10 (тесты бэкенд/фронтенд)** → tasks 2,3,5,6,7,8,9 (backend), 10,11,12 (frontend).
- **Спека §11 (файлы)** → File Structure; `src/kg/*`, `src/student/mastery.py`, `src/student/student_kg.py`, тесты, фронтенд-компоненты, правки store/schemas/server/adaptive/config/env/pyproject/requirements/App/AdaptivePanel/api.js/index.css/README/api.md.

Известные отклонения/решения: `src/models/schemas.py` НЕ меняется — API отдаёт plain dict как весь существующий `server.py` (ответные Pydantic-модели не используются); профиль-слой Слоя 2 живёт в SQLite (не в JSON-файле) — `src/student/student_kg.py` содержит чистые payload/статы, а не персистентный класс референса; `sessions` получают необязательные колонки `subject/grade` (без изменения `list_sessions`), чтобы резолвить «последнюю сессию» для `/graph` без нарушения существующих тестов; relation-sync читает ключ ребра `relation` (а не `type`) — осознанное исправление quirk референса, зафиксировано в порт-брифе.
