# GAP-аудит: edututor vs референсный проект

| Параметр | Значение |
|---|---|
| **Дата аудита** | 2026-09-04 |
| **Текущий проект (объект аудита)** | `C:\otus\edututor` (workspace) — адаптивный ИИ-тьютор: backend [`adaptive_tutor/`](adaptive_tutor) (Python) + frontend [`frontend/`](frontend) (React) |
| **Референсный проект (эталон)** | `C:\otus\project_work` — вне workspace, исследован read-only |
| **Режим аудита** | Read-only: чтение исходников, без изменений файлов |
| **Статус аудита** | Завершён (оба исследования проведены, результат синтезирован ниже) |

---

## 1. Резюме (общий вывод)

Текущий edututor реализует **«ядро» продукта**: FastAPI + LangGraph ReAct-агент, типизированные конверты контента, SQLite-профиль ученика с детерминированным `student_id`, историю сессий, квизы, оценку ответов с feedback, подсказки, RAG-провижининг с локальными эмбеддингами, `web_search` с fallback, каскад LLM, critic/валидацию, JSONL-наблюдаемость и безопасность.

Однако **две самые ценные фичи референса — ГРАФ ЗНАНИЙ и КАРТОЧКИ ДЛЯ ПОВТОРЕНИЙ (spaced repetition, SM-2) — в текущий проект НЕ перенесены** (отсутствуют в коде полностью). Также не перенесены: онбординг-интервью, персональная Knowledge Wiki, полноценный mastery-контур и рекомендации на основе графа, экспорт. Подробный разбор — в разделах 4–6, приоритезированный план переноса — в разделе 7.

## 2. Контекст и цель аудита

Проведён аудит **полноты переноса функциональности** из референсного проекта (эталон, `C:\otus\project_work`) в текущий проект edututor (`C:\otus\edututor`). Оба проекта реализуют одну и ту же идею — адаптивный ИИ-тьютор, — но референс доведён до полноценного педагогического контура, тогда как edututor представляет собой «ядро» этой идеи.

**Цель документа** — зафиксировать фактическое состояние обоих проектов по ключевым подсистемам, выявить расхождения (gap), свести их в приоритизированные таблицы и предложить порядок переноса упущенного функционала.

## 3. Методология

- **Рекурсивное чтение исходников** обоих проектов (backend-модули, API-роуты, фронтенд-компоненты, хранилища).
- **Контрольный поиск по ключевым словам** в текущем проекте (например: `knowledge graph`, `student_kg`, `review`, `SM-2`, `intake`, `wiki`, `LinUCB`, `bandit`, `scaffold`), включая поиск по файлам документации и тестам, для однозначного определения факта «реализовано / отсутствует».
- **Read-only**: никакие файлы проектов не изменялись.
- Факт наличия фичи в коде подтверждался не только по README/диаграммам, но и по реальным определениям (классы, роуты, компоненты). Единичные упоминания в документации без реализации в коде помечены явно.

---

## 4. Что реализовано в референсе (эталон)

> Пути в этом разделе — от корня `C:\otus\project_work` (далее «пути референса»).

### 4.1. Граф знаний (3 слоя)

**Слой 1 — граф учебника/источника** (`src/knowledge_graph.py`):
- `NetworkX DiGraph`; узлы: `book` / `section` / `lesson` / `topic` / `concept`; рёбра: `part_of` / `prerequisite` / `related`.
- Построение LLM-онтологией (промпт «онтолог», ≤14 вершин, вывод в JSON) с эвристическим фолбэком по структуре «Урок/Параграф N. Название» и по markdown-заголовкам.
- Фильтры мусора: mojibake CP1251, UI-элементы, лимит ≤30 узлов/страница.
- Кэш `sha1(схема:student:source:размер)` в `data/knowledge_graphs/`; `GRAPH_SCHEMA_VERSION=5`.

**Слой 2 — Student Knowledge Graph** (`src/student_kg.py` + поле `knowledge_graph` в профиле `data/students/<sid>.json`):
- `TopicStatus` (`not_studied` / `in_progress` / `mastered`), поля `mastery`, `attempts`/`correct`, `weak_areas`, `relations`; узлы копируются из графа учебника по совпадению `title`.
- Авто-`mastered` при `attempts >= 3` и `mastery >= 0.8`.
- Рекомендации `get_recommended_topics`: слабые (`accuracy < 0.5`) → пробелы пререквизитов → `in_progress` → `not_studied`.

**Слой 3 — Knowledge Wiki** (`src/wiki.py`):
- Персональные статьи `data/knowledge_wiki/<sid>/<subject>/<topic>.md` с YAML-frontmatter (OKF v0.2): `mastery`, `accuracy`, `attempts`, `weak_areas`, `relations`, `concepts`, `notes`.
- Экспоненциальное сглаживание `mastery = 0.7*старое + 0.3*score`.
- Заметки об ошибках `WikiNote` с дедупом; синхронизация на каждом ответе/квизе/уроке.

**API и UI**:
- `api/routes/graph.py`: `GET /graph` с mastery-оверлеем, `GET /graph/{node}/related`, `GET /graph/{node}/wiki`, `POST /topic` (fire-and-forget).
- `api/routes/students.py`: `knowledge-graph`, `recommendations`.
- Фронтенд: `KnowledgeGraphPanel.jsx` («Созвездие», Canvas, force-directed, zoom/pan/drag, цвета рёбер, mastery-цвет узлов, drill-down в wiki), `StudentKGPanel.jsx` («Мои знания»), `MasteryWall.jsx` (тепловая карта).

### 4.2. Карточки для повторений (spaced repetition, SM-2)

**Модель и хранилище** (`src/review.py`):
- `ReviewCard` (`card_id`, `subject`/`topic`, вопрос, `options`, `difficulty`, `interval_days` старт `1.0`, `ease` старт `2.5`, `reps`, `lapses`, `due_at`, `is_due`); дедуп `sha256(question)[:16]`.
- `ReviewBank` — JSON на ученика `data/review_bank/<sid>.json`, атомарная запись `tmp`+`replace`, лимит 200 карточек.

**SM-2-алгоритм**:
- Верный ответ: `reps += 1`; интервал `1.0` (1-е повторение) или `interval * ease`; `ease = max(1.3, ease + 0.1 − max(0, 3 − reps) * 0.05)`.
- Неверный ответ: `reps = 0`, `interval = 1.0`, `lapses += 1`, `ease = max(1.3, ease − 0.2)`.
- `due_at = now + interval`.

**Авто-добавление и блиц**:
- Ошибочный ответ квиза → `add_from_record` (`evaluation.py`).
- Блиц: `review_requested` → `generate_question_node` берёт due-карточки (лимит `REVIEW_QUIZ_SIZE = 5`), оборачивает в `QuizCard` с `id` = `review:<card_id>`, события `quiz.card` (`review=True`), `review.done`.
- Ответ на review-карточку → `bank.review_card` (SM-2), **без** создания новой карточки.

**API/UI**:
- `GET /api/students/{id}/review` (stats + due), `POST /api/sessions/{id}/review` (запуск блица).
- UI: кнопка «Повторить (N)» (`dueCount`) в `StudentKGPanel.jsx`, бейдж «повторение» на `QuizCard.jsx`.

### 4.3. Онбординг-интервью

- `src/intake.py` + карточка знакомства (имя, тип, класс, предмет, тема, учебник, режим), prefill из профиля, «emergency start», UI `IntakeCard`/`Wizard`.
- **Детерминированный `student_id`** из «ФИО + тип + класс» (FNV-1a, `frontend/src/identity.js`) — стабилен между устройствами без аккаунтов.

### 4.4. Прочее в референсе (по приоритету)

**Важно:**
- Структурированный урок (hook / определение / термины / диаграмма / секции «Проверь себя» / итог): `src/tutor.py` + `lesson_eval.py` (детерминированный судья-lite).
- LinUCB contextual bandit адаптивной сложности `easy`/`medium`/`hard`: `src/adaptive.py`.
- Scaffolding — лестница подсказок + декомпозиция на subtasks: `src/scaffold.py`.
- Mastery-гейт пререквизитов и reuse-гейт материалов.
- Гибридный RAG BM25 + RRF.
- Heartbeat / гранулярный прогресс / fire-and-forget фоновые шаги + WS-события.

**Средне:**
- Сверка с ФГОС (offline-база): `src/curriculum.py`.
- Каскад поисковиков `yandex → tavily → stepik → lesson_edu → fipi → ddgs` + SSRF-защита + `license_check`.
- OCR страниц сканов.
- Агентный режим ReAct (13 инструментов function calling).
- LLM-судья качества (`src/judge.py`, кнопка «Оценить»).
- История занятий + CSV-экспорт учителю и OKF-экспорт.
- SQLite-персистентность сессий.

**Второстепенно:**
- Диаграммы урока (dual-coding) и KaTeX.
- Политика источников ученика (whitelist доменов).
- Много-ролевой бюджет LLM (`tutor`/`expert`/`judge`/`cheap`) + `BudgetGuard` + circuit breaker.
- Детерминированная FSM + гибридный агент (`USE_AGENT_TUTOR`).

---

## 5. Что реализовано в текущем edututor

> Пути от корня `C:\otus\edututor` (workspace).

### 5.1. Backend `adaptive_tutor/` (Python 3.11+, FastAPI, LangGraph ReAct)

- **API**: [`src/api/server.py`](adaptive_tutor/src/api/server.py:375) — `POST /chat` (:421), `POST /chat/stream` (SSE, :448), `GET /chat/history/{session_id}` (:514), `GET /student/{student_id}` (:548), `GET /student/{student_id}/sessions` (:561), `GET /knowledge` (:529), `POST /knowledge/provision` (:538).
- **Сессии** in-memory с TTL ~1 ч и правилом владения: [`src/api/session_store.py`](adaptive_tutor/src/api/session_store.py:55).
- **Агент**: [`src/agent/loop.py`](adaptive_tutor/src/agent/loop.py:534) (граф и цикл), [`src/agent/tools.py`](adaptive_tutor/src/agent/tools.py:111) (`rag_search`/`web_search`), [`src/agent/prompts.py`](adaptive_tutor/src/agent/prompts.py:65), [`src/agent/envelope.py`](adaptive_tutor/src/agent/envelope.py:44) (JSON-конверты), [`src/agent/critic.py`](adaptive_tutor/src/agent/critic.py:38).
- **Модели**: [`src/models/schemas.py`](adaptive_tutor/src/models/schemas.py:53) — `ContentType`, `ContentEnvelope` (5 типов контента: `theory`/`practice`/`hint`/`quiz`/`evaluation`, :62), `AgentGraphState` (:71), `LearningStyle`.
- **Студент**: [`src/student/store.py`](adaptive_tutor/src/student/store.py:42) (SQLite: таблицы `students`/`topics`/`sessions`, `data/students.db`; уровень тем `level` 0..1, шаги `knowledge_delta`, `attempts`, `correct`), [`src/student/adaptive.py`](adaptive_tutor/src/student/adaptive.py:8) (общий уровень = среднее по темам, рекомендация = тема с минимальным уровнем).
- **RAG**: in-memory векторное хранилище + локальные эмбеддинги e5, провижининг веб-сниппетов по теме: [`src/rag/__init__.py`](adaptive_tutor/src/rag/__init__.py:127), [`src/rag/provisioning.py`](adaptive_tutor/src/rag/provisioning.py:40).
- **LLM**: каскад RouterAI (RU) → OpenRouter (GLOBAL) с ролевыми моделями и фолбэками: [`src/llm/`](adaptive_tutor/src/llm/base.py:141).
- **Поиск**: yandex/tavily → duckduckgo fallback: [`src/search/`](adaptive_tutor/src/search/base.py:36).
- **Безопасность**: circuit breaker, лимит итераций, бюджет, `OutputValidator` (LaTeX): [`src/safety/`](adaptive_tutor/src/safety/__init__.py:13).
- **Наблюдаемость**: JSONL-логи с `trace_id`, scrub секретов: [`src/observability/logger.py`](adaptive_tutor/src/observability/logger.py:72).

### 5.2. Frontend `frontend/` (React 19 + Vite, KaTeX)

- [`App.jsx`](frontend/src/App.jsx:9) — каркас: `TopicForm` + `SessionList` | `Chat` | `AdaptivePanel`, SSE.
- [`Chat.jsx`](frontend/src/components/Chat.jsx:11) — лента + диспетчер конвертов `feedReducer` + индикатор «думает» ([`StepIndicator.jsx`](frontend/src/components/StepIndicator.jsx:2)); блоки [`TheoryBlock.jsx`](frontend/src/components/TheoryBlock.jsx:3), [`PracticeBlock.jsx`](frontend/src/components/PracticeBlock.jsx:3), [`HintBlock.jsx`](frontend/src/components/HintBlock.jsx:3), [`QuizBlock.jsx`](frontend/src/components/QuizBlock.jsx:4), [`EvaluationBlock.jsx`](frontend/src/components/EvaluationBlock.jsx:3).
- [`AdaptivePanel.jsx`](frontend/src/components/AdaptivePanel.jsx:13) — уровень/прогресс/рекомендация.
- [`SessionList.jsx`](frontend/src/components/SessionList.jsx:1), [`TopicForm.jsx`](frontend/src/components/TopicForm.jsx:3).
- [`identity.js`](frontend/src/identity.js:11) — `student_id` в `localStorage`; [`api.js`](frontend/src/api.js:1); утилиты markdown/latex ([`Latex.jsx`](frontend/src/components/Latex.jsx:24)).

### 5.3. Статусы по ключевым пунктам

| Подсистема | Статус в edututor | Комментарий |
|---|---|---|
| **Граф знаний** | ❌ НЕ РЕАЛИЗОВАНО | 0 совпадений по контрольному поиску; база знаний — плоские сниппеты; визуализации нет. «Student Knowledge Graph» есть только на Mermaid-диаграмме желаемой архитектуры в [`adaptive_tutor/docs/architecture.md`](adaptive_tutor/docs/architecture.md:44) |
| **Карточки повторений** | ❌ НЕ РЕАЛИЗОВАНО | Нет модели, SM-2, хранилища, API, UI. Единственная реакция на ошибку — `evaluation` c `correct: false` и сдвиг `level` вниз, без создания карточки |
| **Онбординг/интервью** | ❌ НЕ РЕАЛИЗОВАНО | Вход только через форму `TopicForm` (предмет/класс/тема); `learning_style`/`fatigue_level` приходят готовыми в запросе, дефолты, не персистятся |
| **Профиль ученика** | ✅ РЕАЛИЗОВАНО | SQLite `StudentStore` (`students`/`topics`/`sessions`); детерминированный `student_id` в `localStorage` ([`identity.js`](frontend/src/identity.js:11)) + серверный fallback `stu_<hex>` с фиксацией на сессии; история занятий `GET /student/{id}/sessions` + [`SessionList.jsx`](frontend/src/components/SessionList.jsx:1); восстановление ленты |
| **Mastery тем** | ❌ НЕ РЕАЛИЗОВАНО | Плоская замена: `level` + `knowledge_delta` + `attempts`; порога «освоено» нет; mastery-гейтов/рекомендаций по графу нет |
| **Knowledge Wiki / конспекты** | ❌ НЕ РЕАЛИЗОВАНО | — |
| **Экспорт** | ❌ НЕ РЕАЛИЗОВАНО | — |
| **LinUCB / bandit** | ❌ НЕ РЕАЛИЗОВАНО | Заявлен в README/architecture, в коде отсутствует; `needs_scaffold` объявлен в [`schemas.py`](adaptive_tutor/src/models/schemas.py:83), но не используется |
| **Остальное** | ✅ РЕАЛИЗОВАНО | Квизы, оценка, feedback, структурированные блоки-конверты, подсказки (≤2 подряд), адаптивная панель (уровень/рекомендация), RAG, `web_search`, каскад LLM, critic, безопасность, наблюдаемость, LaTeX-рендер |

---

## 6. GAP-анализ (сводные таблицы)

### Таблица A. Приоритетные упущения (главные «лучшие» фичи референса)

| # | Возможность | В референсе (кратко, пример модуля) | В edututor | Приоритет переноса |
|---|---|---|---|---|
| 1 | Граф знаний учебника/источника + визуализация | `NetworkX DiGraph` + LLM-онтология + Canvas-панель | ОТСУТСТВУЕТ | **Критично** |
| 2 | Student Knowledge Graph (статусы тем, mastery, рёбра, рекомендации) | [`src/student_kg.py`] → мастерство, mastery-оверлей | ОТСУТСТВУЕТ (плоский `level` в `topics`) | **Критично** |
| 3 | Карточки повторений SM-2 (авто-добавление ошибок, блиц, due-счётчик) | `src/review.py` + интеграция с квизами | ОТСУТСТВУЕТ | **Критично** |
| 4 | Онбординг-интервью (карточка знакомства) | `src/intake.py` + `IntakeCard` | ОТСУТСТВУЕТ | Высокий |
| 5 | Knowledge Wiki (персональные конспекты с mastery) | `src/wiki.py` + панели | ОТСУТСТВУЕТ | Высокий |
| 6 | Детерминированный `student_id` из ФИО+класс (стабильный между устройствами) | FNV-1a в `identity.js` | `localStorage` (привязка к браузеру) | Средний (улучшение) |
| 7 | Экспорт для учителя (CSV/OKF) | `src/export.py` | ОТСУТСТВУЕТ | Средний |
| 8 | Mastery-гейты / гейт пререквизитов | `api/routes/graph.py` | ОТСУТСТВУЕТ | Высокий (связано с п. 2) |

### Таблица B. Пересекающийся функционал (уже есть в edututor — переносить не нужно, можно сверять качество)

| Функция | edututor | Референс (эталон реализации) |
|---|---|---|
| Профиль ученика (SQLite, история сессий) | [`store.py`](adaptive_tutor/src/student/store.py:42), [`server.py`](adaptive_tutor/src/api/server.py:548) | `student.py` (JSON) + `session_store.py` (SQLite) |
| Генерация квизов / оценка / feedback | `prompts` + [`QuizBlock.jsx`](frontend/src/components/QuizBlock.jsx:4) / [`EvaluationBlock.jsx`](frontend/src/components/EvaluationBlock.jsx:3) | `tutor.py` + `evaluation.py` (богаче: антидубль, цитата §N) |
| Структурированный контент (конверты) | `ContentEnvelope` ([`schemas.py`](adaptive_tutor/src/models/schemas.py:62)), [`Chat.jsx`](frontend/src/components/Chat.jsx:37) | `tutor.py` — структурированный урок (фиксированная карта урока) |
| Подсказки / помощь | [`PracticeBlock`](frontend/src/components/PracticeBlock.jsx:3) hint ≤2 | `scaffold.py` — лестница подсказок + subtasks (богаче) |
| Адаптивность (базовая) | [`AdaptivePanel`](frontend/src/components/AdaptivePanel.jsx:13) (level/рекомендация) | `adaptive.py` LinUCB (богаче) |
| RAG | [`rag/__init__.py`](adaptive_tutor/src/rag/__init__.py:127) (in-memory, e5) | `knowledge.py` — гибрид BM25+RRF, Qdrant/Chroma (богаче) |
| Web-поиск | [`search/`](adaptive_tutor/src/search/base.py:36) (yandex/tavily/ddg) | `source_finder.py` — каскад + SSRF + license-check |
| Каскад LLM | [`llm/cascade.py`](adaptive_tutor/src/llm/cascade.py:38) | `llm_client.py` (RouterAI → OpenRouter + фолбэк моделей) |
| Наблюдаемость / безопасность | [`observability/logger.py`](adaptive_tutor/src/observability/logger.py:72), [`safety/`](adaptive_tutor/src/safety/__init__.py:13) | `observability.py` + `guardrails.py` + budgets |
| LaTeX / рендер | [`Latex.jsx`](frontend/src/components/Latex.jsx:24), [`utils/latex.js`](frontend/src/utils/latex.js) | KaTeX |

### Таблица C. Упущения второстепенные (опционально, решить позже)

| Возможность | Комментарий |
|---|---|
| LinUCB contextual bandit | Заявлен в README/architecture; в коде отсутствует |
| Scaffolding subtasks | Декомпозиция заданий, лестница подсказок |
| LLM-судья (judge) и кнопка «Оценить» | Отдельная подсистема оценки качества ответа |
| ФГОС-сверка | Offline-база соответствия учебной программе |
| OCR страниц | Распознавание сканов учебников |
| Каскад поисковиков с whitelist/SSRF | Расширение текущего каскада + политика источников |
| Агентный режим (ReAct) | В edututor уже используется — частично пересекается |
| Диаграммы урока | Dual-coding визуализации |
| Heartbeat / fire-and-forget фоновых шагов с WS | В edututor — SSE |
| OKF-экспорт | Обмен форматами с персональными заметками |

---

## 7. Выводы и рекомендации

1. **Текущий edututor — «ядро» продукта, а референс — та же идея, доведённая до полноценного педагогического контура.** Основные пробелы — не косметика, а отсутствие целых подсистем (граф знаний, повторения SM-2, онбординг, Wiki, mastery-контур, экспорт).

2. **Рекомендуемый порядок переноса** (по ценности/связности):

   - **ЭТАП 1 (критично): карточки повторений SM-2** — автономная подсистема: аналог `review.py` + хранилище + API + кнопка в UI + авто-добавление при ошибке квиза.
   - **ЭТАП 2 (критично): граф знаний** — сначала «граф знаний ученика» поверх существующей SQLite-модели тем (статусы, mastery-порог, рёбра `prerequisite`) + рекомендации; затем построение графа учебника/источника из RAG-сниппетов; затем визуализация (Canvas) с mastery-оверлеем.
   - **ЭТАП 3 (высокий): онбординг-интервью** — интерактивная карточка знакомства перед стартом и до-заполнение профиля (имя, класс) для будущего детерминированного id.
   - **ЭТАП 4 (высокий): Knowledge Wiki** — персональные конспекты по темам с мастерством + экспорт.
   - **ЭТАП 5 (опционально):** LinUCB, scaffolding subtasks, judge, диаграммы.

3. **Методологическое замечание.** При переносе учитывать, что референс хранит персональные данные в JSON-файлах (`data/students/<sid>.json`, `data/review_bank/<sid>.json`, `data/knowledge_wiki/...`), а edututor — в SQLite ([`store.py`](adaptive_tutor/src/student/store.py:15), `data/students.db`). Схемы переносимых подсистем необходимо адаптировать под SQLite и существующие модели [`schemas.py`](adaptive_tutor/src/models/schemas.py:53).

---

## 8. Ключевые файлы, использованные при аудите

**Референс (`C:\otus\project_work`):**
```text
src/knowledge_graph.py       # граф учебника/источника (NetworkX + LLM-онтология)
src/student_kg.py            # Student Knowledge Graph (статусы, mastery, рекомендации)
src/wiki.py                  # Knowledge Wiki (персональные конспекты, OKF)
src/review.py                # ReviewCard / ReviewBank, SM-2, блицы
src/intake.py                # онбординг-интервью (карточка знакомства)
src/adaptive.py              # LinUCB contextual bandit
src/scaffold.py              # лестница подсказок + subtasks
src/tutor.py                 # структурированный урок
src/evaluation.py            # оценка ответов, add_from_record
src/source_finder.py         # каскад поисковиков
src/curriculum.py            # ФГОС-сверка
src/export.py                # экспорт CSV/OKF
api/routes/graph.py          # /graph, mastery-оверлей, wiki
api/routes/students.py       # knowledge-graph, recommendations
frontend/KnowledgeGraphPanel.jsx   # «Созвездие» (Canvas)
frontend/StudentKGPanel.jsx        # «Мои знания»
frontend/MasteryWall.jsx           # тепловая карта
frontend/IntakeCard.jsx            # онбординг-виджет
```

**Текущий edututor (`C:\otus\edututor`):**
- Backend [`adaptive_tutor/src/`](adaptive_tutor/src): [`api/server.py`](adaptive_tutor/src/api/server.py:375), [`api/session_store.py`](adaptive_tutor/src/api/session_store.py:55), [`agent/loop.py`](adaptive_tutor/src/agent/loop.py:534), [`agent/tools.py`](adaptive_tutor/src/agent/tools.py:111), [`agent/prompts.py`](adaptive_tutor/src/agent/prompts.py:65), [`agent/envelope.py`](adaptive_tutor/src/agent/envelope.py:44), [`agent/critic.py`](adaptive_tutor/src/agent/critic.py:38), [`models/schemas.py`](adaptive_tutor/src/models/schemas.py:53), [`student/store.py`](adaptive_tutor/src/student/store.py:42), [`student/adaptive.py`](adaptive_tutor/src/student/adaptive.py:8), [`rag/__init__.py`](adaptive_tutor/src/rag/__init__.py:127), [`rag/provisioning.py`](adaptive_tutor/src/rag/provisioning.py:40), [`llm/cascade.py`](adaptive_tutor/src/llm/cascade.py:38), [`llm/base.py`](adaptive_tutor/src/llm/base.py:141), [`search/`](adaptive_tutor/src/search/base.py:36), [`safety/__init__.py`](adaptive_tutor/src/safety/__init__.py:13), [`observability/logger.py`](adaptive_tutor/src/observability/logger.py:72).
- Frontend [`frontend/src/`](frontend/src): [`App.jsx`](frontend/src/App.jsx:9), [`Chat.jsx`](frontend/src/components/Chat.jsx:11) + блоки-конверты, [`AdaptivePanel.jsx`](frontend/src/components/AdaptivePanel.jsx:13), [`SessionList.jsx`](frontend/src/components/SessionList.jsx:1), [`TopicForm.jsx`](frontend/src/components/TopicForm.jsx:3), [`identity.js`](frontend/src/identity.js:11), [`api.js`](frontend/src/api.js:1), утилиты markdown/latex.
- Документация: [`adaptive_tutor/docs/architecture.md`](adaptive_tutor/docs/architecture.md:44) (Mermaid-диаграмма желаемой архитектуры — единственное место, где упомянут Student Knowledge Graph).

---

*Документ подготовлен по итогам read-only аудита 2026-09-04. Все утверждения о составе модулей основаны на фактическом содержимом исходников; факты сверх переданных данных в документ не добавлялись.*
