# E3 — Онбординг и детерминированный student_id: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить «карточку знакомства» (онбординг-интервью) перед началом занятия и детерминированный `student_id` из `ФИО|тип|класс` (FNV-1a 32-bit, `stu_`+8 hex), с персистентным backend-профилем (`name`, `learner_type`, `grade`) и prefill повторных заходов.

**Architecture:** Идентичность считается на клиенте чистыми функциями `frontend/src/identity.js` (FNV-1a без crypto, правила reconciliation 1:1 с референсом); локальная запись ученика хранится JSON-ом под `edututor_student` (с миграцией старого «сырого» анонимного id). Бэкенд лишь персистит поля профиля: лёгкая миграция `students` + `set_profile`, расширенный `GET /student/{id}` и новый `POST /student/{id}/profile` (upsert). UI: при неполном профиле в левой колонке вместо `TopicForm` показывается `IntakeCard`; сабмит карточки → `resolveStudentId` → сохранение записи → `studentId` переключается на новый (useState + синхронный ref) → `api.profile` (fail-soft) → `startTopic` в режиме «урок».

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, sqlite3 stdlib (`threading.Lock`), pytest, ruff (line-length 100); React 19 + Vite (vitest, @testing-library/react, user-event), oxlint.

**Spec:** `docs/superpowers/specs/2026-09-04-e3-onboarding-design.md`

## Global Constraints

- Все существующие тесты остаются зелёными (обновление ожиданий допускается только там, где меняется поведение: формат localStorage-записи ученика, начальный экран нового пользователя).
- Репозиторий БЕЗ коммитов: каждый task заканчивается «Validation» (`pytest` + `ruff` / `npm test` + `npm run lint`), НЕ `git commit`.
- Backend-команды запускаются из cwd `adaptive_tutor`: pytest — `.venv/Scripts/python.exe -m pytest <tests> -q`; ruff — `.venv/Scripts/ruff.exe check <files>`.
- Frontend-команды запускаются из cwd `frontend`: `npx vitest run <file>` / `npm test` и `npm run lint`.
- Python 3.11; Pydantic v2; sqlite3 stdlib + `threading.Lock` (не reentrant — миграция внутри `__init__` выполняется на `self._conn` напрямую, под уже взятым lock); ruff line-length 100.
- Русские docstring/комментарии в коде; интерфейсы и тексты ошибок — по спеке.
- `studentId` в `App.jsx` переводится с `useRef(getStudentId())` на **useState + синхронный ref** (spec §5.3): state для перерисовки/перезапуска, ref — чтобы асинхронный SSE-поток (`runTurn`) читал актуальное значение без stale-closure.
- Алгоритм FNV-1a и правила `resolveStudentId` — 1:1 со спекой §3.1/§3.2 (и референсом); не «улучшать» и не перевыводить.
- Идентичность/хеш: без транслитерации, кириллица как UTF-16 code units; `grade` участвует в ключе даже пустой.
- Новый формат localStorage `edututor_student` — JSON-запись `{student_id, student_name, learner_type, grade, identity, legacy}`; старый формат (сырая строка `stu_<12hex>`) читается как legacy-запись `{student_id}` (не ломать существующие браузеры).
- «Начать без карточки» не создаёт профиль: работает с анонимным id (текущее поведение), карточка снова появится при следующем запуске.
- Обновить e2e-мок (`frontend/e2e/tutor.spec.js`), чтобы `npm run e2e` остался зелёным (seed полноценной записи ученика через `addInitScript`).

---

## File Structure

| Файл | Действие |
|---|---|
| `adaptive_tutor/src/student/store.py` | Modify: миграция колонок + `set_profile` |
| `adaptive_tutor/tests/test_student_store.py` | Modify: тесты миграции/профиля |
| `adaptive_tutor/src/api/server.py` | Modify: `GET /student/{id}` поля профиля; `POST /student/{id}/profile`; модель `IntakeProfile` |
| `adaptive_tutor/tests/test_api.py` | Modify: тесты эндпоинтов профиля |
| `frontend/src/identity.js` | Modify: derive/hash/resolve/prefill + record-хелперы |
| `frontend/src/identity.test.js` | Modify: детерминизм, формат, нормализация, 4 правила resolve, record |
| `frontend/src/components/IntakeCard.jsx` | Create: карточка знакомства |
| `frontend/src/components/IntakeCard.test.jsx` | Create: рендер/валидация/сабмит/skip |
| `frontend/src/index.css` | Modify: стили карточки (переиспользуют `.topic-form`) |
| `frontend/src/api.js` | Modify: `api.profile(...)` |
| `frontend/src/components/TopicForm.jsx` | Modify: prefill grade из профиля |
| `frontend/src/App.jsx` | Modify: студент-стейт, intake-флоу, профиль |
| `frontend/src/App.test.jsx` | Modify: seed профиля + тесты intake |
| `frontend/e2e/tutor.spec.js` | Modify: seed профиля + мок `POST .../profile` |
| `README.md`, `adaptive_tutor/docs/api.md` | Modify: документация |

---

### Task 1: Backend — миграция `students` + `set_profile`

**Files:**
- Modify: `adaptive_tutor/src/student/store.py`
- Test: `adaptive_tutor/tests/test_student_store.py`

**Interfaces:**
- Consumes: существующие `self._conn`/`self._lock`/`self._exec`/`self._rows`/`self.get_student`.
- Produces:
  - Идемпотентная миграция при старте `StudentStore.__init__`: `students` дополняется колонками
    `learner_type TEXT DEFAULT ''`, `grade TEXT DEFAULT ''` (проверка через `PRAGMA table_info`).
  - `set_profile(student_id: str, *, name: str | None = None, learner_type: str | None = None,
    grade: str | None = None) -> dict` — upsert; `None`-аргумент не трогает текущее значение;
    возвращает `{"student_id", "name", "learner_type", "grade"}`.
  - `get_student(student_id) -> dict | None` — как сейчас (`SELECT *`), но строка теперь включает
    `learner_type`/`grade` (поведение расширяется автоматически миграцией).

- [ ] **Step 1: Миграция в `__init__`** (`store.py:52-54`)

Расширить блок `with self._lock:` — выполнить миграцию на `self._conn` напрямую (не через
`self._exec`, т.к. lock уже занят и `threading.Lock` не reentrant):

```python
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate_students()
            self._conn.commit()
```

Добавить метод миграции в класс (рядом с `_rows`):

```python
    @staticmethod
    def _existing_student_columns(conn: sqlite3.Connection) -> set[str]:
        """Имена колонок таблицы students (для идемпотентной миграции)."""
        rows = conn.execute("PRAGMA table_info(students)").fetchall()
        return {row[1] for row in rows}

    def _migrate_students(self) -> None:
        """Идемпотентно добавляет learner_type/grade (spec §4.1)."""
        columns = self._existing_student_columns(self._conn)
        if "learner_type" not in columns:
            self._conn.execute(
                "ALTER TABLE students ADD COLUMN learner_type TEXT DEFAULT ''"
            )
        if "grade" not in columns:
            self._conn.execute("ALTER TABLE students ADD COLUMN grade TEXT DEFAULT ''")
```

Примечание: `ALTER TABLE ... ADD COLUMN ... DEFAULT ''` в sqlite заполняет существующие
строки дефолтом — старые записи получают пустые профильные поля.

- [ ] **Step 2: Метод `set_profile`** (в класс, перед `close`)

```python
    def set_profile(
        self,
        student_id: str,
        *,
        name: str | None = None,
        learner_type: str | None = None,
        grade: str | None = None,
    ) -> dict:
        """Upsert полей профиля ученика; None-аргумент не меняет текущее значение."""
        row = self.get_student(student_id)
        current = (row or {}).get
        merged = {
            "name": str(name) if name is not None else current("name", ""),
            "learner_type": (
                str(learner_type) if learner_type is not None else current("learner_type", "")
            ),
            "grade": str(grade) if grade is not None else current("grade", ""),
        }
        now = time.time()
        self._exec(
            "INSERT INTO students (student_id, name, learner_type, grade, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(student_id) DO UPDATE SET "
            "name = excluded.name, learner_type = excluded.learner_type, "
            "grade = excluded.grade, updated_at = excluded.updated_at",
            (student_id, merged["name"], merged["learner_type"], merged["grade"], now, now),
        )
        return {"student_id": student_id, **merged}
```

- [ ] **Step 3: Тесты в `tests/test_student_store.py`**

Добавить тесты (переиспользуют существующий фикстур `store`):

```python
def test_migration_adds_profile_columns_and_is_idempotent(tmp_path):
    """Миграция добавляет learner_type/grade и не падает на повторном открытии."""
    import sqlite3

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE students (student_id TEXT PRIMARY KEY, "
        "name TEXT DEFAULT '', created_at REAL, updated_at REAL)"
    )
    conn.execute(
        "INSERT INTO students (student_id, name, created_at, updated_at) VALUES ('stu_old', '', 1, 1)"
    )
    conn.commit()
    conn.close()

    s1 = StudentStore(path)
    columns = {r["name"] for r in s1._rows("PRAGMA table_info(students)")}
    assert {"learner_type", "grade"} <= columns
    row = s1.get_student("stu_old")
    assert row["learner_type"] == "" and row["grade"] == ""
    s1.close()

    s2 = StudentStore(path)  # повторное открытие — миграция идемпотентна
    s2.close()


def test_set_profile_upserts_and_preserves_on_none(store):
    profile = store.set_profile(
        "stu_p1", name="Иван Иванов", learner_type="schoolchild", grade="8 класс"
    )
    assert profile == {
        "student_id": "stu_p1",
        "name": "Иван Иванов",
        "learner_type": "schoolchild",
        "grade": "8 класс",
    }
    # None-аргумент не затирает сохранённое
    after = store.set_profile("stu_p1", grade="9 класс")
    assert after["name"] == "Иван Иванов"
    assert after["learner_type"] == "schoolchild"
    assert after["grade"] == "9 класс"
    row = store.get_student("stu_p1")
    assert row["name"] == "Иван Иванов"
    assert row["learner_type"] == "schoolchild"
    assert row["grade"] == "9 класс"


def test_set_profile_new_id_is_upsert_and_get_student_returns_profile(store):
    store.set_profile("stu_new", name="Петя Петров", learner_type="student", grade="")
    row = store.get_student("stu_new")
    assert row is not None
    assert row["learner_type"] == "student"
    assert store.get_student("stu_missing") is None
```

Примечание: `_rows` возвращает `dict(row)`; строки `sqlite3.Row` индексируются по имени — `row[1]`
в `_existing_student_columns` это имя колонки (позиция 1 в `PRAGMA table_info`).

- [ ] **Step 4: Прогнать тесты**

Run (cwd `adaptive_tutor`): `.venv/Scripts/python.exe -m pytest tests/test_student_store.py -q`
Expected: старые 6 + новые 3 PASS.

- [ ] **Step 5: ruff**

Run: `.venv/Scripts/ruff.exe check src/student/store.py tests/test_student_store.py`
Expected: All checks passed.

---

### Task 2: Backend API — профиль ученика

**Files:**
- Modify: `adaptive_tutor/src/api/server.py`
- Test: `adaptive_tutor/tests/test_api.py`

**Interfaces:**
- Consumes: `store.set_profile` (Task 1), существующие Pydantic-хелперы `BaseModel/Field/field_validator/Literal` (уже импортированы).
- Produces:
  - Pydantic-модель `IntakeProfile` (рядом с `StudentProfile`):
    `name: str = ""`, `learner_type: str = ""`, `grade: str = ""` + валидаторы
    (name при непустом — ≥2 слов; learner_type — строго из `"" | "student" | "schoolchild"`).
  - `GET /student/{student_id}` — ответ дополнен `name`, `learner_type`, `grade` (404 неизвестного сохраняется).
  - `POST /student/{student_id}/profile` — body `IntakeProfile`; не 404 для нового id (upsert);
    возвращает сохранённый профиль `{student_id, name, learner_type, grade}`; 422 при
    «имя из одного слова» / «learner_type вне списка».

- [ ] **Step 1: Модель `IntakeProfile`** (после `StudentProfile`, до `ChatRequest`)

```python
class IntakeProfile(BaseModel):
    """Тело POST /student/{student_id}/profile (карточка знакомства)."""

    name: str = Field(default="", max_length=200, description="ФИО (имя и фамилия)")
    learner_type: str = Field(default="", description="student|schoolchild")
    grade: str = Field(default="", max_length=50, description="Класс (если школьник)")

    @field_validator("name")
    @classmethod
    def _name_two_words_if_present(cls, value: str) -> str:
        """Непустое имя должно содержать минимум два слова (иначе 422)."""
        value = value.strip()
        if value and len(value.split()) < 2:
            raise ValueError("Укажи имя и фамилию (минимум два слова)")
        return value

    @field_validator("learner_type")
    @classmethod
    def _learner_type_in_list(cls, value: str) -> str:
        """learner_type строго из пусто|student|schoolchild (иначе 422)."""
        value = value.strip().lower()
        if value not in {"", "student", "schoolchild"}:
            raise ValueError("learner_type должен быть student или schoolchild")
        return value
```

- [ ] **Step 2: Расширить `GET /student/{student_id}`** (`server.py:548-559`)

Взять строку один раз и дополнить ответ полями профиля:

```python
    @app.get("/student/{student_id}")
    def student_profile(student_id: str) -> dict[str, Any]:
        """Профиль ученика: темы, уровни, рекомендация, поля карточки знакомства."""
        store: StudentStore = app.state.student_store
        row = store.get_student(student_id) if store is not None else None
        if row is None:
            raise HTTPException(status_code=404, detail="студент не найден")
        topics = store.list_topics(student_id)
        return {
            "student_id": student_id,
            "name": row.get("name", ""),
            "learner_type": row.get("learner_type", ""),
            "grade": row.get("grade", ""),
            "topics": topics,
            "recommended_next": pick_recommendation(topics),
        }
```

- [ ] **Step 3: Роут `POST /student/{student_id}/profile`** (рядом с `student_profile`)

```python
    @app.post("/student/{student_id}/profile")
    def student_profile_update(student_id: str, body: IntakeProfile) -> dict[str, Any]:
        """Сохраняет профиль ученика из карточки знакомства (upsert, не 404)."""
        store: StudentStore = app.state.student_store
        if store is None:
            return {
                "student_id": student_id,
                "name": body.name,
                "learner_type": body.learner_type,
                "grade": body.grade,
            }
        try:
            return store.set_profile(
                student_id,
                name=body.name,
                learner_type=body.learner_type,
                grade=body.grade,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"внутренняя ошибка: {exc}") from exc
```

- [ ] **Step 4: Тесты в `tests/test_api.py`** (после `test_student_endpoint`, фикстур `client` уже есть)

```python
def test_student_endpoint_includes_profile_fields(client):
    client.post("/chat", json={"message": "hello", "student_id": "stu_prof1"})
    resp = client.get("/student/stu_prof1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["student_id"] == "stu_prof1"
    assert body["name"] == ""
    assert body["learner_type"] == ""
    assert body["grade"] == ""
    assert body["topics"] == []


def test_post_student_profile_upserts_and_is_returned_by_get(client):
    resp = client.post(
        "/student/stu_prof2/profile",
        json={"name": "Иван Иванов", "learner_type": "schoolchild", "grade": "8 класс"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "student_id": "stu_prof2",
        "name": "Иван Иванов",
        "learner_type": "schoolchild",
        "grade": "8 класс",
    }
    body = client.get("/student/stu_prof2").json()
    assert body["name"] == "Иван Иванов"
    assert body["learner_type"] == "schoolchild"
    assert body["grade"] == "8 класс"


def test_post_student_profile_overwrites_previous(client):
    client.post("/student/stu_prof3/profile", json={"name": "Петя Петров", "learner_type": "student"})
    resp = client.post(
        "/student/stu_prof3/profile",
        json={"name": "Пётр Петров", "learner_type": "student", "grade": "1 курс"},
    )
    body = resp.json()
    assert body["name"] == "Пётр Петров"
    assert body["grade"] == "1 курс"


def test_post_student_profile_empty_name_is_allowed(client):
    # Пустое имя валидно (422 только для непустого имени из одного слова).
    resp = client.post("/student/stu_prof4/profile", json={"name": ""})
    assert resp.status_code == 200
    assert resp.json()["name"] == ""


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Иван"},                      # одно слово
        {"name": "  Иван  "},                  # одно слово после strip
        {"learner_type": "teacher"},           # вне списка
    ],
)
def test_post_student_profile_validation_422(client, payload):
    resp = client.post("/student/stu_prof5/profile", json=payload)
    assert resp.status_code == 422
```

- [ ] **Step 5: Прогнать тесты**

Run (cwd `adaptive_tutor`): `.venv/Scripts/python.exe -m pytest tests/test_api.py tests/test_student_store.py -q`
Expected: все PASS (старые + новые).

- [ ] **Step 6: ruff**

Run: `.venv/Scripts/ruff.exe check src/api/server.py tests/test_api.py`
Expected: All checks passed.

---

### Task 3: Frontend — детерминированная личность в `identity.js`

**Files:**
- Modify: `frontend/src/identity.js`
- Test: `frontend/src/identity.test.js`

**Interfaces:**
- Consumes: `localStorage` (ключи `edututor_student`, `edututor_session`), существующие `hex`.
- Produces (все — 1:1 со спекой §3.1 и референсом):
  - `canonicalName(name) -> str`, `deriveIdentityKey(name, type, grade) -> str`,
    `hashStr(s) -> str` (FNV-1a 32-bit), `deriveStudentId(name, type, grade) -> "stu_"+8hex`.
  - `prefilledIdentity(card) -> str | null` — identity из префилла карточки (null при пустом префилле).
  - `resolveStudentId(name, type, grade, stored, card) -> {studentId, identity, legacy}`
    — правила spec §3.2.
  - Запись ученика: `loadStudentRecord() -> object` и `saveStudentRecord(record) -> void`;
    `edututor_student` хранит JSON `{student_id, student_name, learner_type, grade, identity, legacy}`;
    старый «сырой» id-формат читается как `{student_id}`.
  - Изменённые `getStudentId`/`saveSession` работают через record-хелперы (никогда не пишут
    «голую» строку под `edututor_student`).

- [ ] **Step 1: Добавить чистые функции** (в `identity.js`, после `hex`)

```javascript
// ---- Детерминированная личность (spec §3.1; 1:1 с референсом) ----

export function canonicalName(name) {
  return String(name || '').trim().replace(/\s+/g, ' ').toLowerCase()
}

export function deriveIdentityKey(name, type, grade) {
  return `${canonicalName(name)}|${String(type || '').trim().toLowerCase()}|${String(grade || '').trim().toLowerCase()}`
}

export function hashStr(s) {
  // FNV-1a 32-bit — стабильный детерминированный хеш (без обращения к crypto)
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(16).padStart(8, '0')
}

export function deriveStudentId(name, type, grade) {
  return `stu_${hashStr(deriveIdentityKey(name, type, grade))}`
}

// Идентичность, которая уже живёт в профиле (префилл карточки знакомства).
// null — если профиль пуст (новый браузер, ещё никто не заполнял карточку).
export function prefilledIdentity(card) {
  if (!card?.fields) return null
  const get = (key) => {
    const f = card.fields.find((x) => x.key === key)
    return f ? String(f.value ?? '') : ''
  }
  const name = get('name')
  const type = get('learner_type')
  const grade = get('grade')
  if (!name && !type && !grade) return null
  return deriveIdentityKey(name, type, grade)
}

// Единственная точка принятия решения «какой student_id использовать» (spec §3.2).
//
// Правила:
// 1. Та же личность (identity совпадает) и сохранённый id канонический (детерминированный
//    для этой identity) или честно закреплён при апгрейде (legacy) — оставляем сохранённый id.
// 2. Известна прежняя личность, но карточка заполнена ДРУГАЯ → новый детерминированный id
//    (изолированная ветка данных).
// 3. Первое заполнение после «апгрейда» (в localStorage анонимный id без identity,
//    префилл профиля совпадает с identity карточки) → исторический id, помечаем legacy: true.
// 4. Иначе — детерминированный id из заполненной карточки.
export function resolveStudentId(name, type, grade, stored, card) {
  const identity = deriveIdentityKey(name, type, grade)
  const deterministic = deriveStudentId(name, type, grade)
  if (stored?.identity) {
    if (stored.identity === identity) {
      if (stored.student_id === deterministic || stored.legacy === true) {
        return { studentId: stored.student_id, identity, legacy: stored.legacy === true }
      }
    }
    return { studentId: deterministic, identity }
  }
  const legacy = prefilledIdentity(card)
  if (stored?.student_id && legacy === identity) {
    return { studentId: stored.student_id, identity, legacy: true }
  }
  return { studentId: deterministic, identity }
}
```

- [ ] **Step 2: Record-хелперы + переписать `getStudentId`/`saveSession`**

Заменить `getStudentId` целиком и `saveSession`, добавив record-функции (между ними):

```javascript
// ---- Локальная запись ученика (JSON под edututor_student) ----
export function loadStudentRecord() {
  try {
    const raw = localStorage.getItem(STUDENT_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && typeof parsed.student_id === 'string') {
      return parsed
    }
    if (typeof parsed === 'string' && parsed) return { student_id: parsed }
    return {}
  } catch {
    // Старый формат: под ключом лежала «голая» строка анонимного id.
    const raw = localStorage.getItem(STUDENT_KEY)
    return raw ? { student_id: String(raw) } : {}
  }
}

export function saveStudentRecord(record) {
  try {
    localStorage.setItem(STUDENT_KEY, JSON.stringify(record || {}))
  } catch {
    /* ignore */
  }
}

export function getStudentId() {
  const record = loadStudentRecord()
  if (record.student_id) return record.student_id
  const id = `stu_${hex(6)}`
  saveStudentRecord({ student_id: id })
  return id
}
```

`saveSession` (чтобы не затирать JSON-запись «голым» id):

```javascript
export function saveSession(sessionId, studentId) {
  try {
    if (sessionId) localStorage.setItem(SESSION_KEY, sessionId)
    if (studentId) saveStudentRecord({ ...loadStudentRecord(), student_id: studentId })
  } catch {
    /* ignore */
  }
}
```

`getSessionId/setSessionId/clearSession` не меняются.

- [ ] **Step 3: Обновить существующие тесты + добавить новые в `identity.test.js`**

Обновить импорт: добавить `deriveIdentityKey, deriveStudentId, resolveStudentId, hashStr,
loadStudentRecord, saveStudentRecord`. Первые два существующих теста перевести на новый формат:

```javascript
  it('generates and persists student id', () => {
    const id = getStudentId()
    expect(id).toMatch(/^stu_[0-9a-f]{12}$/)
    expect(JSON.parse(localStorage.getItem('edututor_student')).student_id).toBe(id)
    expect(getStudentId()).toBe(id)
  })
```

и `saveSession persists both`:

```javascript
  it('saveSession persists both', () => {
    saveSession('ses_2', 'stu_x')
    expect(getSessionId()).toBe('ses_2')
    expect(JSON.parse(localStorage.getItem('edututor_student')).student_id).toBe('stu_x')
  })
```

Новый блок (после существующего `describe('identity', ...)`):

```javascript
describe('deterministic student id', () => {
  it('fnv-1a is stable, 8 hex, format stu_', () => {
    const a = deriveStudentId('Иван Иванов', 'schoolchild', '8 класс')
    const b = deriveStudentId('Иван Иванов', 'schoolchild', '8 класс')
    expect(a).toBe(b)
    expect(a).toBe('stu_8debd364') // золотое значение FNV-1a (см. spec §3.1)
    expect(a).toMatch(/^stu_[0-9a-f]{8}$/)
  })

  it('different identity -> different id (кириллица хэшируется как есть)', () => {
    expect(deriveStudentId('Иван Иванов', 'schoolchild', '8 класс')).not.toBe(
      deriveStudentId('Мария Сидорова', 'student', '')
    )
  })

  it('normalizes case and whitespace, grade participates', () => {
    expect(deriveStudentId('  ИВАН  ИВАНОВ ', 'SCHOOLCHILD', '8 КЛАСС')).toBe('stu_8debd364')
    // grade входит в ключ даже пустой — «студент» и «студент 8 класс» разные люди
    expect(deriveStudentId('Мария Сидорова', 'student', '')).not.toBe(
      deriveStudentId('Мария Сидорова', 'student', '8 класс')
    )
  })

  it('canonicalName/deriveIdentityKey collapse spaces', () => {
    expect(deriveIdentityKey(' Иван   Петров ', 'student', '1 курс')).toBe(
      'иван петров|student|1 курс'
    )
  })
})

describe('resolveStudentId — правила spec §3.2', () => {
  const key = (name, type, grade) => deriveIdentityKey(name, type, grade)

  it('правило 1: та же личность + legacy id -> сохраняет id', () => {
    const stored = { student_id: 'stu_hist', identity: key('Иван Иванов', 'schoolchild', '8 класс'), legacy: true }
    const out = resolveStudentId('Иван Иванов', 'schoolchild', '8 класс', stored, { fields: [] })
    expect(out).toEqual({
      studentId: 'stu_hist',
      identity: key('Иван Иванов', 'schoolchild', '8 класс'),
      legacy: true,
    })
  })

  it('правило 1b: та же личность + канонический детерминированный id -> сохраняет', () => {
    const stored = { student_id: 'stu_8debd364', identity: key('Иван Иванов', 'schoolchild', '8 класс') }
    const out = resolveStudentId('Иван Иванов', 'schoolchild', '8 класс', stored, { fields: [] })
    expect(out.studentId).toBe('stu_8debd364')
    expect(out.legacy).toBe(false)
  })

  it('правило 2: другая личность -> новый детерминированный id (изоляция)', () => {
    const stored = { student_id: 'stu_8debd364', identity: key('Иван Иванов', 'schoolchild', '8 класс') }
    const out = resolveStudentId('Мария Сидорова', 'student', '', stored, { fields: [] })
    expect(out.studentId).toBe(deriveStudentId('Мария Сидорова', 'student', ''))
    expect(out.legacy).toBe(false)
  })

  it('правило 3: первый филл после апгрейда совпадает с префиллом -> legacy id', () => {
    const stored = { student_id: 'stu_anon_old', identity: '' }
    const card = {
      fields: [
        { key: 'name', value: 'Иван Иванов' },
        { key: 'learner_type', value: 'schoolchild' },
        { key: 'grade', value: '8 класс' },
      ],
    }
    const out = resolveStudentId('Иван Иванов', 'schoolchild', '8 класс', stored, card)
    expect(out.studentId).toBe('stu_anon_old')
    expect(out.legacy).toBe(true)
  })

  it('правило 4: иначе (новый браузер/расхождение) -> детерминированный id', () => {
    const out = resolveStudentId('Иван Иванов', 'schoolchild', '8 класс', {}, { fields: [] })
    expect(out.studentId).toBe('stu_8debd364')
    expect(out.identity).toBe(key('Иван Иванов', 'schoolchild', '8 класс'))
    expect(out.legacy).toBe(false)
  })
})

describe('student record в localStorage', () => {
  it('сохраняет и читает JSON-запись', () => {
    saveStudentRecord({ student_id: 'stu_x', student_name: 'Иван Иванов', learner_type: 'schoolchild', grade: '8 класс', identity: 'иван иванов|schoolchild|8 класс', legacy: true })
    expect(loadStudentRecord().student_name).toBe('Иван Иванов')
    expect(JSON.parse(localStorage.getItem('edututor_student')).legacy).toBe(true)
  })

  it('читает старый «сырой» анонимный id как legacy-запись', () => {
    localStorage.setItem('edututor_student', 'stu_old123456')
    expect(loadStudentRecord()).toEqual({ student_id: 'stu_old123456' })
    expect(getStudentId()).toBe('stu_old123456') // id не перегенерируется
  })

  it('не создаёт профиль без student_name: getStudentId пишет только id', () => {
    const id = getStudentId()
    expect(loadStudentRecord().student_name).toBeUndefined()
    expect(loadStudentRecord().student_id).toBe(id)
  })
})
```

- [ ] **Step 4: Прогнать тесты**

Run (cwd `frontend`): `npx vitest run src/identity.test.js`
Expected: все PASS (обновлённые + новые).

- [ ] **Step 5: lint**

Run (cwd `frontend`): `npm run lint`
Expected: clean.

---

### Task 4: Frontend — `IntakeCard.jsx` + стили + тесты

**Files:**
- Create: `frontend/src/components/IntakeCard.jsx`
- Create: `frontend/src/components/IntakeCard.test.jsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `useState`.
- Produces:
  - `IntakeCard({ prefill = {}, onSubmit, onSkip })` — форма класса `panel topic-form intake-card`;
    `onSubmit(values)` вызывается валидной формой; `values = {name, learner_type, grade,
    subject, topic, mode}` (mode всегда `'lesson'`); `onSkip()` — «Начать без карточки».
  - Поля spec §5.2 (порядок): name(required, ≥2 слова), learner_type(choice, required:
    schoolchild «Школьник»/student «Студент»), grade(required если schoolchild), subject(required),
    topic(required), mode(choice, только lesson «Урок (изучим тему)»).
  - Валидация: submit-кнопка «Начать занятие» disabled пока невалидна; hint
    «Укажи имя и фамилию (минимум два слова).» при 1 слове.

- [ ] **Step 1: Создать `frontend/src/components/IntakeCard.jsx`**

```jsx
import { useState } from 'react'

const LEARNER_TYPES = [
  { value: 'schoolchild', label: 'Школьник' },
  { value: 'student', label: 'Студент' },
]

export default function IntakeCard({ prefill = {}, onSubmit, onSkip }) {
  const [values, setValues] = useState({
    name: prefill.name || '',
    learner_type: prefill.learner_type || '',
    grade: prefill.grade || '',
    subject: prefill.subject || '',
    topic: prefill.topic || '',
    mode: 'lesson',
  })

  const setField = (key, val) => setValues((v) => ({ ...v, [key]: val }))

  const text = (key) => String(values[key] ?? '').trim()
  const nameWords = text('name').split(/\s+/).filter(Boolean).length
  const badName = text('name') && nameWords < 2 ? 'Укажи имя и фамилию (минимум два слова).' : ''
  const gradeRequired = values.learner_type === 'schoolchild'
  const missing = [
    !text('name') && 'имя',
    !text('learner_type') && 'тип',
    gradeRequired && !text('grade') && 'класс',
    !text('subject') && 'предмет',
    !text('topic') && 'тему',
  ].filter(Boolean)
  const invalid = missing.length > 0 || !!badName

  const submit = (e) => {
    e.preventDefault()
    if (invalid) return
    onSubmit({
      name: text('name'),
      learner_type: values.learner_type,
      grade: text('grade'),
      subject: text('subject'),
      topic: text('topic'),
      mode: values.mode || 'lesson',
    })
  }

  return (
    <form className="panel topic-form intake-card" onSubmit={submit}>
      <h3>Знакомство и план занятия</h3>

      <label>Как тебя зовут (имя и фамилия)?
        <input value={values.name} onChange={(e) => setField('name', e.target.value)} placeholder="Иван Иванов" />
      </label>

      <label>Ты школьник или студент?
        <select value={values.learner_type} onChange={(e) => setField('learner_type', e.target.value)}>
          <option value="">— выбери —</option>
          {LEARNER_TYPES.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>

      {gradeRequired && (
        <label>Класс
          <input value={values.grade} onChange={(e) => setField('grade', e.target.value)} placeholder="8 класс" />
        </label>
      )}

      <label>Предмет
        <input value={values.subject} onChange={(e) => setField('subject', e.target.value)} placeholder="математика" />
      </label>

      <label>Тема
        <input value={values.topic} onChange={(e) => setField('topic', e.target.value)} placeholder="квадратные уравнения" />
      </label>

      <label>Что делаем?
        <select value={values.mode} onChange={(e) => setField('mode', e.target.value)}>
          <option value="lesson">Урок (изучим тему)</option>
        </select>
      </label>

      {missing.length > 0 && <p className="intake-card__hint muted">Заполни: {missing.join(', ')}</p>}
      {badName && <p className="intake-card__hint muted">{badName}</p>}

      <button className="btn" type="submit" disabled={invalid}>Начать занятие</button>
      <button className="btn intake-skip" type="button" onClick={onSkip}>Начать без карточки</button>
    </form>
  )
}
```

> Дизайн-решение: `grade` показывается только для `schoolchild` (условный рендер).
> `mode` — единственная опция `lesson`, как в spec §5.2; расширенные режимы — вне E3.

- [ ] **Step 2: Стили в `frontend/src/index.css`** (в конец, после секции `/* ---------- Topic form ---------- */`)

Карточка переиспользует `.topic-form label/input/.btn`; добавить `select`, skip-кнопку и hint:

```css
/* ---------- Intake card (онбординг) ---------- */
.intake-card select {
  padding: 8px 11px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: var(--card);
  color: var(--ink);
  font-size: 14px;
  outline: none;
  width: 100%;
}

.intake-card select:focus {
  border-color: var(--green);
  box-shadow: 0 0 0 3px var(--green-soft);
}

.intake-card .intake-skip {
  width: 100%;
  margin-top: 6px;
  color: var(--muted);
  border-color: var(--line);
}

.intake-card .intake-skip:hover:not(:disabled) {
  border-color: var(--green);
  color: var(--green-strong);
  background: var(--card);
}

.intake-card__hint {
  margin: 2px 0 10px;
  font-size: 12.5px;
  color: var(--err);
}
```

- [ ] **Step 3: Создать `frontend/src/components/IntakeCard.test.jsx`**

```jsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import IntakeCard from './IntakeCard'

describe('<IntakeCard/>', () => {
  const validValues = {
    name: 'Иван Иванов',
    learner_type: 'schoolchild',
    grade: '8 класс',
    subject: 'математика',
    topic: 'квадратные уравнения',
    mode: 'lesson',
  }

  it('renders fields and disabled submit for empty form', () => {
    render(<IntakeCard onSubmit={vi.fn()} onSkip={vi.fn()} />)
    expect(screen.getByText('Знакомство и план занятия')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Иван Иванов')).toBeInTheDocument()
    const btn = screen.getByRole('button', { name: 'Начать занятие' })
    expect(btn).toBeDisabled()
  })

  it('shows hint when name has a single word', async () => {
    const user = userEvent.setup()
    render(<IntakeCard onSubmit={vi.fn()} onSkip={vi.fn()} />)
    await user.type(screen.getByPlaceholderText('Иван Иванов'), 'Иван')
    expect(screen.getByText('Укажи имя и фамилию (минимум два слова).')).toBeInTheDocument()
  })

  it('submits values when valid', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<IntakeCard onSubmit={onSubmit} onSkip={vi.fn()} />)
    await user.type(screen.getByPlaceholderText('Иван Иванов'), 'Иван Иванов')
    await user.selectOptions(screen.getByLabelText('Ты школьник или студент?'), 'schoolchild')
    await user.type(screen.getByPlaceholderText('8 класс'), '8 класс')
    await user.type(screen.getByPlaceholderText('математика'), 'математика')
    await user.type(screen.getByPlaceholderText('квадратные уравнения'), 'квадратные уравнения')
    await user.click(screen.getByRole('button', { name: 'Начать занятие' }))
    expect(onSubmit).toHaveBeenCalledWith(validValues)
  })

  it('grade not required for student, required for schoolchild', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    const { rerender } = render(<IntakeCard onSubmit={onSubmit} onSkip={vi.fn()} />)
    await user.type(screen.getByPlaceholderText('Иван Иванов'), 'Мария Сидорова')
    await user.selectOptions(screen.getByLabelText('Ты школьник или студент?'), 'student')
    expect(screen.queryByPlaceholderText('8 класс')).not.toBeInTheDocument()
    await user.type(screen.getByPlaceholderText('математика'), 'алгебра')
    await user.type(screen.getByPlaceholderText('квадратные уравнения'), 'производные')
    await user.click(screen.getByRole('button', { name: 'Начать занятие' }))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ learner_type: 'student', grade: '' }))
    // schoolchild без класса -> кнопка остаётся неактивной
    rerender(<IntakeCard onSubmit={onSubmit} onSkip={vi.fn()} />)
    await user.selectOptions(screen.getByLabelText('Ты школьник или студент?'), 'schoolchild')
    await user.type(screen.getByPlaceholderText('8 класс'), '8 класс')
  })

  it('«Начать без карточки» вызывает onSkip', async () => {
    const user = userEvent.setup()
    const onSkip = vi.fn()
    render(<IntakeCard onSubmit={vi.fn()} onSkip={onSkip} />)
    await user.click(screen.getByRole('button', { name: 'Начать без карточки' }))
    expect(onSkip).toHaveBeenCalled()
  })

  it('prefill попадает в поля (повторный заход)', () => {
    render(
      <IntakeCard
        prefill={{ name: 'Иван Иванов', learner_type: 'schoolchild', grade: '8 класс' }}
        onSubmit={vi.fn()}
        onSkip={vi.fn()}
      />,
    )
    expect(screen.getByPlaceholderText('Иван Иванов').value).toBe('Иван Иванов')
    expect(screen.getByPlaceholderText('8 класс').value).toBe('8 класс')
    expect(screen.getByLabelText('Ты школьник или студент?').value).toBe('schoolchild')
  })
})
```

- [ ] **Step 4: Прогнать тесты**

Run (cwd `frontend`): `npx vitest run src/components/IntakeCard.test.jsx`
Expected: все PASS.

- [ ] **Step 5: lint**

Run (cwd `frontend`): `npm run lint`
Expected: clean.

---

### Task 5: Frontend — `api.js`, `TopicForm`, `App.jsx`, интеграция + тесты + e2e

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/components/TopicForm.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/App.test.jsx`
- Modify: `frontend/e2e/tutor.spec.js`

**Interfaces:**
- Consumes: `api.request`, identity-функции Task 3, `IntakeCard` (Task 4).
- Produces:
  - `api.profile(studentId, body)` → `request('/student/<id>/profile', {method:'POST', ...})`.
  - `TopicForm({ onStart, prefill = {} })` — grade инициализируется из `prefill.grade`.
  - В `App.jsx`: `studentId` как `useState` + синхронный `useRef`; `profile`-состояние
    из `loadStudentRecord()`; `intakeRequired = !profile.student_name && !intakeSkipped`;
    монтинг-эффект (fail-soft повторный `api.profile`, spec §6); `handleIntake(values)` по §5.3;
    анонимный старт (skip) сохраняет текущее поведение; prefill `TopicForm` классом из профиля.

- [ ] **Step 1: `api.js`** — добавить метод в объект `api` (после `studentSessions`)

```javascript
  profile: (studentId, body) =>
    request(`/student/${encodeURIComponent(studentId)}/profile`, { method: 'POST', body: JSON.stringify(body) }),
```

- [ ] **Step 2: `TopicForm.jsx`** — prefill grade из профиля

```jsx
export default function TopicForm({ onStart, prefill = {} }) {
  const [subject, setSubject] = useState('математика')
  const [grade, setGrade] = useState(prefill.grade || '')
  const [topic, setTopic] = useState('')
```

Остальное без изменений (`topic` placeholder «квадратные уравнения», disabled по `topic.trim()`).

- [ ] **Step 3: `App.jsx` — импорты и состояние**

```jsx
import { useCallback, useEffect, useRef, useState } from 'react'
import api from './api'
import {
  getStudentId, getSessionId, setSessionId, clearSession,
  loadStudentRecord, saveStudentRecord, resolveStudentId,
} from './identity'
import Chat, { feedReducer } from './components/Chat'
import AdaptivePanel from './components/AdaptivePanel'
import TopicForm from './components/TopicForm'
import IntakeCard from './components/IntakeCard'
import SessionList from './components/SessionList'

function profileToCard(rec) {
  return {
    fields: [
      { key: 'name', value: rec?.student_name || '' },
      { key: 'learner_type', value: rec?.learner_type || '' },
      { key: 'grade', value: rec?.grade || '' },
    ],
  }
}
```

Заменить строку `const studentId = useRef(getStudentId())` (`App.jsx:14`) на state + sync-ref
(spec §5.3: id переключаемый после карточки; ref исключает stale-closure в SSE-потоке):

```jsx
  const studentIdRef = useRef(getStudentId())
  const [studentId, setStudentIdState] = useState(studentIdRef.current)
  const applyStudentId = useCallback((id) => {
    studentIdRef.current = id
    setStudentIdState(id)
  }, [])
  const [profile, setProfile] = useState(() => loadStudentRecord())
  const [intakeSkipped, setIntakeSkipped] = useState(false)
  const intakeRequired = !profile?.student_name && !intakeSkipped
```

- [ ] **Step 4: `App.jsx` — все чтения `studentId.current` → `studentIdRef.current`**

Точки: `refreshStudent` (`api.student(studentId.current)`), `loadSessions`
(`api.studentSessions(studentId.current)` и `const sid = data.student_id || studentId.current`),
`runTurn` (`student_id: studentId.current`). Deps этих `useCallback` остаются `[]`/прежними —
значение берётся из ref (синхронно актуально после `applyStudentId`). Пример `runTurn`:

```jsx
      student_id: studentIdRef.current,
```

- [ ] **Step 5: `App.jsx` — fail-soft повторный POST профиля (spec §6)**

Монтинг-эффект: если локальный профиль полон — «до-сохраняем» на сервер (upsert идемпотентен,
повторяется на каждом запуске, пока сервер не примет):

```jsx
  useEffect(() => {
    const rec = loadStudentRecord()
    if (rec?.student_name && rec?.student_id) {
      api.profile(rec.student_id, {
        name: rec.student_name,
        learner_type: rec.learner_type || '',
        grade: rec.grade || '',
      }).catch(() => { /* fail-soft: повторим при следующем запуске */ })
    }
  }, [])
```

- [ ] **Step 6: `App.jsx` — обработчик карточки (§5.3) и анонимный старт**

Добавить после `studyNext` (до текущего mount-эффекта):

```jsx
  const handleIntake = useCallback((values) => {
    const name = String(values.name || '').trim()
    const type = String(values.learner_type || '')
    const grade = String(values.grade || '').trim()
    const stored = loadStudentRecord()
    const { studentId: sid, identity, legacy } = resolveStudentId(
      name, type, grade, stored, profileToCard(profile),
    )
    const record = {
      student_id: sid,
      student_name: name,
      learner_type: type,
      grade,
      identity,
      legacy: legacy === true,
    }
    saveStudentRecord(record)
    setProfile(record)
    applyStudentId(sid)
    clearSession()
    startTopic({ subject: String(values.subject || '').trim(), grade, topic: String(values.topic || '').trim() })
  }, [profile, startTopic, applyStudentId])
```

Анонимный старт — просто скрывает карточку до конца сессии (профиль не создаётся; при
следующем запуске записи нет — карточка снова появится):

```jsx
  const skipIntake = useCallback(() => {
    setIntakeSkipped(true)
  }, [])
```

- [ ] **Step 7: `App.jsx` — рендер IntakeCard вместо TopicForm**

Заменить блок в левой колонке:

```jsx
        {intakeRequired ? (
          <IntakeCard
            prefill={{ name: profile?.student_name || '', learner_type: profile?.learner_type || '', grade: profile?.grade || '' }}
            onSubmit={handleIntake}
            onSkip={skipIntake}
          />
        ) : (
          <TopicForm
            onStart={startTopic}
            prefill={{ grade: profile?.learner_type === 'schoolchild' ? profile?.grade || '' : '' }}
          />
        )}
```

- [ ] **Step 8: `App.test.jsx` — мок `api.profile` + seed профиля**

В `vi.mock('./api', ...)` добавить `profile: vi.fn(() => Promise.resolve({}))`. В `beforeEach`
обоих `describe` — seed полноценной записи (инвариант: чтобы старые тесты видели `TopicForm`) и
дефолт `api.profile`:

```jsx
  function seedProfile() {
    localStorage.setItem('edututor_student', JSON.stringify({
      student_id: 'stu_x',
      student_name: 'Иван Иванов',
      learner_type: 'schoolchild',
      grade: '8 класс',
      identity: 'иван иванов|schoolchild|8 класс',
      legacy: false,
    }))
  }
```

и в `beforeEach`: `seedProfile()` + `api.profile.mockResolvedValue({})`. В тесте
`loadHistory при ошибке не записывает мёртвый session_id` по-прежнему отдельно кладётся
`edututor_session`.

- [ ] **Step 9: новые App-тесты (intake-флоу)**

```jsx
import { deriveStudentId } from './identity'

describe('<App/> онбординг (E3)', () => {
  function installStream() {
    const bodies = []
    api.chatStream.mockImplementation((body) => { bodies.push(body); return () => {} })
    return { bodies }
  }

  beforeEach(() => {
    localStorage.clear()
    api.history.mockRejectedValue(new Error('нет'))
    api.student.mockRejectedValue(new Error('нет'))
    api.studentSessions.mockResolvedValue({ student_id: 'stu_x', sessions: [] })
    api.chatStream.mockReset()
    api.chatStream.mockImplementation(() => () => {})
    api.profile.mockReset()
    api.profile.mockResolvedValue({})
  })

  it('новый пользователь видит карточку знакомства вместо формы', async () => {
    render(<App />)
    expect(await screen.findByText('Знакомство и план занятия')).toBeInTheDocument()
    expect(screen.queryByText('Новое занятие')).not.toBeInTheDocument()
  })

  it('сабмит карточки: resolveStudentId → profile POST → занятие под детерминированным id', async () => {
    const user = userEvent.setup()
    const { bodies } = installStream()
    render(<App />)

    const sid = deriveStudentId('Иван Иванов', 'schoolchild', '8 класс')
    await user.type(await screen.findByPlaceholderText('Иван Иванов'), 'Иван Иванов')
    await user.selectOptions(screen.getByLabelText('Ты школьник или студент?'), 'schoolchild')
    await user.type(screen.getByPlaceholderText('8 класс'), '8 класс')
    await user.type(screen.getByPlaceholderText('математика'), 'математика')
    await user.type(screen.getByPlaceholderText('квадратные уравнения'), 'квадратные уравнения')
    await user.click(screen.getByRole('button', { name: 'Начать занятие' }))

    await waitFor(() => expect(api.profile).toHaveBeenCalledWith(
      sid,
      { name: 'Иван Иванов', learner_type: 'schoolchild', grade: '8 класс' },
    ))
    expect(JSON.parse(localStorage.getItem('edututor_student')).student_id).toBe(sid)
    expect(bodies[0].student_id).toBe(sid)
    expect(bodies[0].topic).toBe('квадратные уравнения')
    expect(screen.getByText('Новое занятие')).toBeInTheDocument() // карточка закрыта
  })

  it('«Начать без карточки» скрывает карточку и не создаёт профиль', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Начать без карточки' }))
    expect(screen.getByText('Новое занятие')).toBeInTheDocument()
    expect(api.profile).not.toHaveBeenCalled()
    expect(JSON.parse(localStorage.getItem('edututor_student')).student_name).toBeUndefined()
  })

  it('вернувшийся ученик с полным профилем: сразу TopicForm + grade префилл', async () => {
    seedProfile()
    render(<App />)
    expect(await screen.findByText('Новое занятие')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('8 класс').value).toBe('8 класс')
  })
})
```

Замечание по тесту сабмита: `handleIntake` зовёт `startTopic` в том же тике — `runTurn`
читает `studentIdRef.current`, который уже обновлён `applyStudentId(sid)`, поэтому
`bodies[0].student_id === sid` (без stale-closure).

- [ ] **Step 10: e2e `frontend/e2e/tutor.spec.js` — seed профиля + мок profile**

В `mockApi(page)`, ДО существующего `'**/student/**'`, добавить:

```js
  await page.route('**/student/*/profile', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  )
```

В начале теста (до `page.goto`):

```js
  await page.addInitScript(() => {
    localStorage.setItem('edututor_student', JSON.stringify({
      student_id: 'stu_e2e',
      student_name: 'Иван Иванов',
      learner_type: 'schoolchild',
      grade: '8 класс',
      identity: 'иван иванов|schoolchild|8 класс',
      legacy: false,
    }))
  })
```

(`App` видит полный профиль → `TopicForm` сразу, существующий сценарий теории сохраняется.)

- [ ] **Step 11: Прогнать тесты и lint**

Run (cwd `frontend`): `npx vitest run src/identity.test.js src/App.test.jsx src/components/IntakeCard.test.jsx`
Expected: все PASS.
Run (cwd `frontend`): `npm test` и `npm run lint`
Expected: все PASS / clean.

- [ ] **Step 12: e2e**

Run (cwd `frontend`): `npm run e2e`
Expected: 1 passed (Playwright; без запущенного бэкенда, на моках).

---

### Task 6: Документация и финальная проверка

**Files:**
- Modify: `README.md` (корень репозитория)
- Modify: `adaptive_tutor/docs/api.md`

- [ ] **Step 1: README.md** — таблица HTTP API: в описании `GET /student/{student_id}` указать,
  что теперь возвращает `name`, `learner_type`, `grade`; добавить строку
  `| POST /student/{student_id}/profile | Сохранить профиль ученика из карточки знакомства (upsert; name ≥2 слов, learner_type из student/schoolchild) |`;
  кратко описать онбординг в разделе про фронтенд (карточка знакомства при неполном профиле,
  детерминированный `student_id` из ФИО/тип/класс, «Начать без карточки»).

- [ ] **Step 2: `adaptive_tutor/docs/api.md`** — новый раздел после «Привязка сессии <-> ученик»
  (до «Knowledge»): описать `GET /student/{student_id}` (поля профиля в ответе) и
  `POST /student/{student_id}/profile`:

```text
## Профиль ученика (карточка знакомства)

- `GET /student/{student_id}` → 200: `{student_id, name, learner_type, grade, topics, recommended_next}`;
  404, если ученик неизвестен.
- `POST /student/{student_id}/profile` body:
  `{"name": "Иван Иванов", "learner_type": "schoolchild", "grade": "8 класс"}` →
  сохраняет профиль (upsert, для нового id не 404) и возвращает
  `{student_id, name, learner_type, grade}`.
  `learner_type`: `"" | "student" | "schoolchild"`; `name` при непустом — минимум два слова,
  иначе `422`. Фронтенд шлёт этот POST из «карточки знакомства» (fail-soft: при недоступном
  сервере профиль хранится в localStorage и POST повторяется при следующем запуске).
```

- [ ] **Step 3: финальная проверка бэкенда**

Run (cwd `adaptive_tutor`): `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: все PASS.
Run: `.venv/Scripts/ruff.exe check src/ tests/`
Expected: All checks passed.

- [ ] **Step 4: финальная проверка фронтенда**

Run (cwd `frontend`): `npm test` и `npm run lint`
Expected: все PASS / clean.

---

## Self-Review (план сверен со спекой)

- **Спека §1 (цель/границы, emergency-старт)** → Task 4 (`onSkip`), Task 5 (skip-флоу).
- **Спека §2 (текущее состояние: `getStudentId` в localStorage, `students` без профиля)** → Task 1 (миграция), Task 3 (record-хелперы, миграция «сырого» id), Task 5.
- **Спека §3.1 (FNV-1a, `canonicalName/deriveIdentityKey/hashStr/deriveStudentId`, кириллица, формат `stu_`+8hex)** → Task 3 (код 1:1, золотое значение `stu_8debd364`).
- **Спека §3.2 (resolveStudentId, 4 правила, хранимая запись `edututor_student`, сироты)** → Task 3 (+тесты 4 правил), Task 5.
- **Спека §4.1 (миграция SQL, идемпотентность)** → Task 1.
- **Спека §4.2 (`set_profile` upsert, `get_student` с профилем)** → Task 1.
- **Спека §4.3 (GET `/student/{id}` поля профиля, POST `/student/{id}/profile`, валидация 422, не-404 для нового id)** → Task 2.
- **Спека §5.1 (состояние App, `intakeRequired`, IntakeCard вместо/поверх TopicForm)** → Task 5.
- **Спека §5.2 (таблица полей IntakeCard, валидация, «Начать без карточки»)** → Task 4.
- **Спека §5.3 (сабмит: resolve → localStorage → state studentId → clearSession → api.profile fail-soft → startTopic(lesson))** → Task 5 (Steps 3–7).
- **Спека §5.4 (повторный заход, prefill, TopicForm-grade из профиля)** → Task 4 (prefill), Task 5 (Step 2/7), Task 6.
- **Спека §5.5 (api.js `profile`, расширенный `student`)** → Task 5 (Step 1), Task 2 (серверный ответ).
- **Спека §6 (крайние случаи: два устройства, смена ФИО, класс-строка, сервер недоступен → повторный POST)** → Task 3 (правила), Task 5 (fail-soft mount-эффект), глобальные ограничения.
- **Спека §7 (тесты)** → Task 1–5 (identity: стабильность/формат/нормализация/4 правила; IntakeCard: рендер/disabled/hint/skip; backend: миграция, set_profile, GET/POST валидация).
- **Спека §8 (файлы)** → File Structure; e2e-мок обновлён дополнительно (чтобы `npm run e2e` не падал).

Известные ограничения (фиксируются, не чинятся в E3): уже существующие анонимные сессии/темы под
старым id остаются «осиротевшими» и доступны через SessionList только в том же браузере до
переключения id; merge не делаем; серверный профиль дописывается повторным POST при следующем
запуске (idempotent upsert).
