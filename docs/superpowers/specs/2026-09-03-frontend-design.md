# Adaptive Tutor — Фронтенд + типизированный контент (дизайн)

> Дата: 2026-09-03. Статус: согласован в чате по секциям.
> Проект не в git-репозитории — файл спеки без коммита.

## Цель

Построить веб-интерфейс адаптивного репетитора поверх существующего лёгкого
бэкенда (`adaptive_tutor`, FastAPI) и расширить бэкенд типизированным контентом
и персистентным профилем ученика. Референс по идеям — `C:\otus\project_work`
(там «раздутый» стек; мы делаем лёгкий аналог).

### Ключевые требования (от пользователя)

1. Корректный рендер математических формул через KaTeX (критично).
2. Поддержка разных типов образовательного контента: теория, практика,
   подсказки, квизы.
3. Визуализация адаптивности: прогресс, уровень знаний, рекомендации.
4. Лёгкий, читаемый, модульный фронтенд.

### Решения, принятые в диалоге

| Вопрос | Решение |
|--------|---------|
| Направление | Богаче UI: квиз/уроки/панели (без монстров референса) |
| Язык фронтенда | Plain JS (React + Vite), как референс |
| Расположение | `C:\otus\edututor\frontend`, свежий код (не форк) |
| Демо-релиз | Одноэкранный чат + панели (история сессий, темы, профиль/адаптивность) |
| Откуда типы контента | Типизированный контент от бэкенда |
| Адаптивность | Персистентный профиль по `student_id` (SQLite) |
| Интерактивность | Полный цикл с проверкой ответов |
| Архитектура контента | Подход A: «конверт в цикле» (finalize пакует ответ в JSON-конверт) |

---

## Обзор архитектуры

```
Frontend (edututor/frontend, React+Vite+KaTeX)
   │  REST: /chat, /chat/history, /student, /knowledge
   │  SSE:  POST /chat/stream  (agent.step/agent.tool/agent.finalize/message/done/error/heartbeat)
   ▼
Backend  (adaptive_tutor, FastAPI, Python 3.11+, LangGraph ReAct)
   ├─ agent/loop.py        — ReAct: planner → tools → … → finalize
   │                         finalize пакует final_answer → ContentEnvelope
   ├─ agent/prompts.py     — системный промпт: финальный ответ = JSON-конверт
   ├─ student/store.py     — SQLite: students / topics / sessions (NEW)
   ├─ student/adaptive.py  — байесовский апдейт уровня, рекомендации (NEW)
   ├─ api/server.py        — эндпоинты, SSE, DI, запись профиля после evaluation
   └─ models/schemas.py    — ContentEnvelope, новые поля состояния
```

Ключевой принцип: **агент не знает про SQLite**. Сервер передаёт профиль на вход
(`student_profile`, `student_id`) и после ответа сам обновляет БД. Это сохраняет
агента лёгким и переиспользуемым.

---

## 1. Конверт контента (контракт backend → frontend)

Единый JSON-конверт, который модель производит как финальный ответ, а `finalize`
валидирует. Появляется в SSE `message` и в ответе `POST /chat`.

```json
{
  "v": 1,
  "type": "theory" | "practice" | "hint" | "quiz" | "evaluation",
  "text": "markdown с формулами $...$ / $$...$$ (KaTeX)",
  "payload": { },
  "difficulty": "easy" | "medium" | "hard"
}
```

### Семантика типов

- `theory` — объяснение/урок. `payload`: `{topic, next_topic?}` (рекомендация).
- `practice` — задача. `text` = условие. `payload`: `{task_ref?}`.
- `hint` — подсказка к активной задаче. `payload`: `{task_ref?}`.
- `quiz` — вопрос. `payload`: `{answer_type: single|open, options?: [..]}`
  (без правильного ответа — не утекает ученику).
- `evaluation` — проверка ответа. `payload`:
  `{correct: bool, feedback: str, knowledge_delta: float}`.

### Как производится (Подход A, «конверт в цикле»)

1. `SYSTEM_PROMPT` (в `agent/prompts.py`) предписывает: когда модель готова
   ответить — финальное сообщение строго JSON-конверт, без текста вне JSON.
2. `AgentRuntime.finalize` (в `agent/loop.py`):
   - парсит контент ответа (пытается извлечь JSON, в т.ч. из markdown-обёртки
     ` ```json … ``` `);
   - при не-JSON/невалидном — фолбэк `{type: "theory", text: <исходный текст>}`
     (чат никогда не падает);
   - сохраняет конверт в новое поле состояния `state.content_envelope`;
   - продолжает существующие OutputValidator/LaTeX-проверку и Critic по `text`
     конверта (текст ответа — тот же, что и раньше).
3. `run_agent` шлёт событие `message` с конвертом. История сессии хранит и
   `content` (= `envelope.text`, для контекста LLM), и `envelope` (для
   ре-рендера).

### Pydantic-модели (в `models/schemas.py`)

```python
class ContentType(StrEnum):
    THEORY = "theory"
    PRACTICE = "practice"
    HINT = "hint"
    QUIZ = "quiz"
    EVALUATION = "evaluation"

class ContentEnvelope(BaseModel):
    v: int = 1
    type: ContentType
    text: str
    payload: dict[str, Any] = {}
    difficulty: Literal["easy", "medium", "hard"] = "medium"
```

Новое поле в `AgentGraphState`: `content_envelope: ContentEnvelope | None = None`.

---

## 2. Профиль ученика (student_id) и адаптивность

### Хранилище: SQLite (stdlib `sqlite3`, без ORM)

Файл `data/students.db` (путь настраивается через `settings`, см. ниже).

```sql
CREATE TABLE students (
  student_id TEXT PRIMARY KEY,
  name TEXT DEFAULT '',
  created_at REAL,
  updated_at REAL
);
CREATE TABLE topics (
  student_id TEXT,
  topic TEXT,
  level REAL DEFAULT 0.5,          -- знание темы 0..1
  attempts INT DEFAULT 0,
  correct INT DEFAULT 0,
  last_seen REAL,
  PRIMARY KEY (student_id, topic)
);
CREATE TABLE sessions (
  student_id TEXT,
  session_id TEXT,
  topic TEXT,
  started_at REAL,
  ended_at REAL,
  UNIQUE (student_id, session_id)
);
```

Общие атрибуты (learning_style, fatigue) приходят в каждом запросе через
`student_profile` (уже есть) и хранятся только в памяти сессии — не в БД.
SQLite хранит то, что должно пережить рестарт: темы с уровнями, попытки, сессии.

### Модель знаний

- Per-topic `level` + счётчики попыток.
- Обновление из `evaluation.knowledge_delta`:
  `new = clamp01(level + delta)`, `delta ∈ {-0.1, +0.2..+0.5}` (задаётся моделью).
- `current_knowledge_level` — среднее по темам текущей сессии (тема из запроса),
  либо хранимое среднее по всем темам при отсутствии темы.
- Рекомендация: тема с минимальным `level` ИЛИ предложенная моделью
  (`theory.next_topic`); сервер выбирает приоритет: `next_topic` из конверта,
  иначе SQLite-минимум, иначе `null`.

### Эндпоинты

- `GET /student/{student_id}` → профиль:
  ```json
  {
    "student_id": "…",
    "topics": [{"topic": "…", "level": 0.7, "attempts": 3, "correct": 2, "last_seen": 123.0}],
    "recommended_next": "теорема Виета" | null
  }
  ```
  404, если студента нет.
- `POST /chat` и `/chat/stream` принимают `student_id` (опц.); если нет —
  генерируется (`stu_<hex>`) и возвращается, при этом fallback фиксируется на
  сессии (`session.student_id`): повторные ходы без `student_id` продолжают того
  же ученика и не теряют прогресс. Сессия имеет владельца: чужой непустой
  `student_id` по чужой `session_id` не получает историю — сервер заводит новую
  сессию. Ответ/SSE `message` содержат блок `adaptive` (см. §3).

### Код

- `src/student/store.py` — чистый SQLite: `get_student`, `upsert_student`,
  `touch_topic`, `record_attempt`, `list_topics`, `register_session`,
  `get_recommendation`; thread-safe через один `sqlite3` connection + lock.
- `src/student/adaptive.py` — `apply_evaluation(level, delta)`,
  `current_knowledge_level(topics, session_topic)`, выбор рекомендации.
- `src/api/server.py` — `app.state.student_store = StudentStore(settings)` (DI),
  эндпоинт, запись после evaluation-ходов. Сбой БД не роняет чат: try/except + лог.

---

## 3. Контракты API

### `POST /chat` и `POST /chat/stream` — тело

```json
{
  "message": "текст",
  "session_id": "…",
  "student_id": "…",
  "kind": "message",                 // "message" | "hint_request"
  "student_profile": {
    "current_knowledge_level": 0.7,
    "learning_style": "visual",
    "fatigue_level": 0.2
  },
  "topic": "квадратные уравнения",
  "subject": "алгебра",
  "grade": "8 класс"
}
```

- Все поля, кроме `message`, опциональны; `student_id`/`session_id` при пустом —
  генерируются и возвращаются.
- `kind=hint_request`: сервер кладёт в контекст LLM служебную строку
  `[ученик просит подсказку к текущей задаче]` (вместо обычного текста
  пользователя — чтобы модель не считала это ответом на задачу); системный
  промпт требует ответить `hint`. В ленте фронта клик по «Подсказка» рисуется
  локально как пузырь «💡 Подсказка» — в историю сессии это сообщение НЕ
  пишется (только конверт `hint` ассистента).

### Ответ `POST /chat` (200)

```json
{
  "reply": "…",                      // = envelope.text (совместимость)
  "envelope": { "v": 1, "type": "theory", "text": "…", "payload": {}, "difficulty": "medium" },
  "adaptive": {
    "student_id": "…",
    "current_knowledge_level": 0.62,
    "topic": "квадратные уравнения",
    "topic_level": 0.7,
    "attempts": 3,
    "correct": 2,
    "difficulty": "medium",
    "recommended_next": "теорема Виета" | null
  },
  "error": null,
  "trace_id": "…",
  "session_id": "…",
  "difficulty": "medium",
  "steps": 3,
  "terminated": true
}
```

### SSE `POST /chat/stream`

`message` data становится:
```json
{ "content": "…", "envelope": {…}, "adaptive": {…}, "error": null, "session_id": "…" }
```
Остальные события (`agent.step`, `agent.tool`, `agent.finalize`, `done`,
`error`, `heartbeat`) — без изменений.

Примечание про `difficulty`: поле присутствует и в `envelope` (сложность
конкретного блока контента), и в `adaptive` (текущая сложность ведения сессии) —
это разные уровни, дублирование намеренное.

### История сессии

Запись: `{role, content, kind?: "<type конверта>", envelope?, adaptive?}`.
- `SessionStore.to_llm_context` возвращает ТОЛЬКО `{role, content}` (правка
  существующего метода — обрезать служебные поля).
- `GET /chat/history/{session_id}` → полные записи с конвертами для ре-рендера.

---

## 4. Фронтенд `C:\otus\edututor\frontend`

### Стек

Vite 8 + React 19 + KaTeX; Plain JS; oxlint + vitest + @testing-library/react +
Playwright (как референс). Без TS/роутера/стейт-библиотек.

### Структура

```
frontend/
  index.html
  vite.config.js            # proxy /api -> http://127.0.0.1:8000
  src/
    main.jsx                # root; импорт katex/dist/katex.min.css; без <StrictMode> (как референс)
    identity.js             # student_id в localStorage (edututor_student)
    api.js                  # REST (chat, history, student, knowledge) + SSE-клиент /chat/stream
    utils/latex.js          # токенизация $…$/$$…$$ (KaTeX), escapeHtml
    components/
      Latex.jsx             # рендер markdown+KaTeX (обёртка над utils/latex)
      Chat.jsx              # лента: user-bubble + рендер конвертов по типу
      TheoryBlock.jsx       # theory: текст, тема, «дальше: X»
      PracticeBlock.jsx     # practice: условие + кнопка «Подсказка»
      HintBlock.jsx         # hint
      QuizBlock.jsx         # quiz: варианты / открытый ответ -> отправка как сообщение
      EvaluationBlock.jsx   # evaluation: ✅/❌, feedback, сдвиг уровня
      StepIndicator.jsx     # agent.step/agent.tool — «репетитор думает…»
      AdaptivePanel.jsx     # уровень, прогресс, рекомендация, difficulty
      SessionList.jsx       # список сессий
      TopicForm.jsx         # предмет/класс/тема для старта занятия
    index.css               # один лёгкий css
```

Каждый блок-компонент — чистый (props in → render out), <150 строк.

### Поток данных

1. Пользователь выбирает тему (`TopicForm`) → `POST /chat/stream`.
2. SSE: `agent.step/tool` → `StepIndicator`; `message` → по `data.envelope.type`
   рендерится нужный Block; `adaptive` → `AdaptivePanel`; `error` → баннер;
   `done` → стоп индикатора.
3. Ответ на квиз/задачу — следующее сообщение в ту же сессию.
4. «Подсказка» (`PracticeBlock`) — `kind: "hint_request"`.
5. «Изучить →» по `recommended_next` и старт новой темы в `TopicForm` начинают
   НОВУЮ сессию: старый `session_id` сбрасывается (localStorage + текущий ход),
   тема/предмет/класс берутся из нового блока.
6. `session_id` + `student_id` сохраняются; при перезагрузке — `GET /chat/history`
   (лента) + `GET /student/{id}` (панель). Незавершённый SSE не дожимается.
7. Если `GET /chat/history` для сохранённой сессии вернул 404 (in-memory сессия
   потеряна после рестарта) — «мёртвый» `session_id` НЕ сохраняется: он
   очищается, и следующий ход стартует чистую сессию.

### Рендер формул (требование 1)

`utils/latex.js` токенизирует `$$…$$` (display) и `$…$` (inline) до KaTeX;
ошибки KaTeX — graceful-fallback (показываем исходник). Весь текст конвертов
идёт через `Latex`. Покрыто unit-тестами, включая битые формулы.

### Визуализация адаптивности (требование 3)

Шкала уровня 0..1, полоска `topic_level`, difficulty последнего хода, карточка
`recommended_next` с кнопкой «Изучить →».

---

## 5. Ошибки и лимиты

- SSE всегда завершается `done`; `error` → баннер. Клиент — экспоненциальный
  reconnect (игнорирует только close 4004 «сессия удалена»).
- Сбой записи в SQLite не роняет чат: try/except + лог; адаптив просто не
  обновляется.
- Не более 2 подсказок подряд на одну задачу (счётчик в сессии) — чтобы модель
  не выдавала решение сразу. Существующая защита агента от бесконечных циклов
  не меняется.
- Валидация конверта в `finalize` с фолбэком `theory` (никогда не 500).
- Безопасность/логи: ничего сверх текущего не логируем; секреты/PII не пишем.

---

## 6. Порядок реализации (декомпозиция)

1. **Бэкенд-контракт**: `ContentEnvelope` + поле в состоянии; промпт; `finalize`
   с парсингом/фолбэком; прокидывание конверта в SSE `message` и `POST /chat`;
   история с конвертами + чистый `to_llm_context`. Тесты.
2. **Профиль**: `student/store.py` + `student/adaptive.py`; `GET /student/{id}`;
   `student_id` в запросах; запись после `evaluation`; блок `adaptive` в ответах.
   Тесты.
3. **Фронтенд**: scaffold Vite; `Latex`/`utils/latex`; чат-лента с блоками по
   типам; `AdaptivePanel`; `TopicForm`/`SessionList`; reconnect/history.
   Unit + один e2e-сценарий.
4. **Интеграция/полировка**: ручной smoke против живого бэкенда, правки,
   `docs/api.md` обновление.

Каждый пункт — отдельный план и цикл реализации (writing-plans).

---

## Критерии готовности (Definition of Done)

- KaTeX рендерит формулы из ответов агента; битые формулы не валят UI.
- Конверты всех 5 типов рендерятся корректно; квиз/практика отправляют ответы и
  получают `evaluation`.
- `AdaptivePanel` показывает уровень/прогресс/рекомендацию по `student_id`;
  профиль переживает рестарт бэкенда (SQLite).
- `SessionList` восстанавливает ленту из `/chat/history`.
- Backend: тесты зелёные (существующие + новые), ruff чистый.
- Frontend: vitest зелёные, e2e-сценарий проходит.
