# EduTutor — адаптивный AI-репетитор

EduTutor — это легковесный адаптивный репетитор: бэкенд-агент на Python
(FastAPI + агентный ReAct-цикл на LangGraph) и фронтенд на React + Vite.
Ученик осваивает тему в диалоге (математика, физика и т. п.), а агент
сам решает, когда обратиться к базе знаний (`rag_search`), когда — к
актуальному веб-поиску (`web_search`), а когда ответить сразу. Ответы
возвращаются в виде типизированных «конвертов» контента —
`theory` / `practice` / `hint` / `quiz` / `evaluation`, математические
формулы оформляются в LaTeX и рендерятся на фронтенде через KaTeX.

Сложность подстраивается под ученика: на каждый ход собирается адаптивный
профиль (уровень знаний, стиль обучения, усталость), а прогресс по темам
хранится в SQLite. Региональная маршрутизация (`RU` / `GLOBAL`) выбирает
LLM-агрегатор и поисковый движок под доступность сервисов в регионе.

## Структура репозитория

| Путь | Назначение |
|------|------------|
| Путь | Назначение |
|------|------------|
| `run.py` | Единый лаунчер: `python run.py` — бэкенд, `python run.py --frontend` — бэкенд + Vite |
| `README.md` | Этот файл: обзор проекта, запуск, тесты, соответствие курсу |
| `.gitignore` | Игнорируемые артефакты Python + Node |
| `adaptive_tutor/` | Бэкенд: FastAPI-приложение, агент, RAG, безопасность |
| `frontend/` | Фронтенд: React + Vite, рендер блоков и LaTeX (KaTeX) |
| `docs/` | Служебные материалы воркспейса (планы и спеки) |

SQLite-файл профилей живёт в `adaptive_tutor/data/students.db`, JSONL-логи —
в `adaptive_tutor/logs/`.

Внутри `adaptive_tutor/`:

| Путь | Назначение |
|------|------------|
| `src/agent/` | ReAct-цикл на LangGraph (`loop.py`), промпты, инструменты (`tools.py`), критик |
| `src/api/` | FastAPI-слой (`server.py`), in-memory хранилище сессий (`session_store.py`) |
| `src/llm/` | LLM-клиенты и фабрика: RouterAI (RU), OpenRouter (GLOBAL) |
| `src/search/` | Поисковый роутер с fallback: Yandex/Tavily → DuckDuckGo |
| `src/rag/` | RAG-движок: `InMemoryVectorStore`, локальные эмбеддинги, провижининг |
| `src/safety/` | Circuit breaker, лимит итераций, бюджет, валидация вывода |
| `src/student/` | SQLite-хранилище профилей учеников и адаптивные рекомендации |
| `src/observability/` | Структурированные JSONL-логи с `trace_id` и маскированием секретов |
| `src/models/` | Pydantic-схемы состояния агента, конвертов и типов контента |
| `tests/` | Юнит- и интеграционные тесты агента, API, RAG, безопасности |
| `docs/` | Документация проекта: `architecture.md`, `api.md`, `logging-example.md` |
| `pyproject.toml`, `requirements.txt` | Зависимости и dev-инструменты (pytest, ruff, mypy) |
| `.env.example` | Шаблон конфигурации (регион, ключи, лимиты) |
| `README.md` | SOP-документ: процедуры агентного цикла, инструментов, безопасности |

Файл `Qwen_text_20260903_l1e69gfax.txt` в корне — посторонний экспортный
артефакт и не относится к проекту.

## Архитектура

Агентный цикл — классический ReAct, собранный как граф на LangGraph
(`src/agent/loop.py`):

```
START → planner → (cond) → tools → planner → (cond) → … → final → END
```

- `planner` — LLM получает историю диалога (и, при необходимости, RAG-контекст)
  и сама решает следующий шаг: вызвать инструмент (`rag_search` / `web_search`)
  или сформировать финальный ответ (`src/agent/loop.py`, `plan`).
- `tools` — исполняет выбранные инструменты через `execute_tool`: таймаут 15 c,
  retry до 2 раз, per-tool circuit breaker (`ToolFailureTracker`), результат
  ограничен 4000 символов и возвращается модели как наблюдение
  (`src/agent/tools.py`).
- `final` — парсит ответ в типизированный конверт, прогоняет `OutputValidator`
  (проверка LaTeX на парность `$`/`$$`) и LLM-критика `Critic` по ролевой
  модели `judge`.
- `should_continue` (условные рёбра графа) — повторять `tools`, принудительно
  финализировать или завершить `END`; остановка гарантируется лимитом шагов
  (`max_agent_steps`), `IterationLimiter` и глобальным таймаутом
  `max_agent_time_sec` через `asyncio.wait_for` в `run_agent`.

Память и поиск:

- RAG построен на `InMemoryVectorStore` — внешняя векторная БД не нужна.
  Локальные эмбеддинги — `LocalEmbedder` на sentence-transformers
  (модель `intfloat/multilingual-e5-small`), классы в `src/rag/__init__.py`.
- База знаний наполняется провижинингом: по теме (`topic`/`subject`/`grade`)
  ищутся материалы через веб-поиск и индексируются сниппетами
  (`src/rag/provisioning.py`). Если `rag_search` ничего не нашёл — агент
  переключается на `web_search` либо честно сообщает, что материала
  недостаточно.
- Веб-поиск маршрутизируется по региону: RU → Yandex → DuckDuckGo,
  GLOBAL → Tavily → DuckDuckGo (`src/search/base.py`, `SearchRouter`).

Модели и провайдеры:

- Ролевые модели `planner` / `fast` / `judge` задаются на регион в
  `LLMClientFactory.get_models_for_region` (`src/llm/base.py`).
- `TUTOR_REGION=RU` → клиент RouterAI; `TUTOR_REGION=GLOBAL` → клиент OpenRouter.
  Каждому клиенту подключён провайдер-специфичный `TokenCounter`.

Состояние и хранение:

- Диалоговая сессия живёт в памяти (`SessionStore`, TTL ~1 час бездействия),
  история доступна по `session_id`.
- Адаптивный профиль ученика (уровни тем, попытки, правильные ответы,
  рекомендация следующей темы) персистится в SQLite
  (`src/student/store.py`, `src/student/adaptive.py`).

## Быстрый старт

### Требования

- Python 3.11+
- Node.js 18+ и npm

### 1. Бэкенд

```bash
cd adaptive_tutor
python -m venv .venv
.venv\Scripts\activate            # Windows (bash: source .venv/Scripts/activate)
pip install -e ".[dev]"           # или: pip install -r requirements.txt
```

Зависимости уже включают `langgraph` (агентный цикл) — отдельно ничего
докидывать не нужно.

Затем скопируйте шаблон конфигурации и заполните ключи под свой регион:

```bash
copy .env.example .env            # Windows (bash: cp .env.example .env)
```

### 2. Запуск (из корня репозитория)

```bash
python run.py                     # бэкенд на http://127.0.0.1:8000
python run.py --frontend          # + Vite dev server на http://localhost:5173
python run.py --help              # флаги: --host, --port, --reload
```

Лаунчер сам находит venv (`adaptive_tutor/.venv`) и запускает uvicorn из
каталога `adaptive_tutor`. Проверка: `GET http://127.0.0.1:8000/health`
должен вернуть `{"status": "ok", ...}`.

### 2.1 Ручной запуск uvicorn с авто-перезапуском (режим разработки)

Та же команда, но без лаунчера. **Важно:** запускать из каталога
`adaptive_tutor/` (модуль `src` лежит в нём), иначе будет
`ModuleNotFoundError: No module named 'src'`.

```bash
# Linux / macOS
cd adaptive_tutor
.venv/bin/python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000 --reload

# Windows — PowerShell / cmd
cd adaptive_tutor
.venv\Scripts\python.exe -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000 --reload

# Windows — Git Bash
cd adaptive_tutor
.venv/Scripts/python.exe -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000 --reload
```

Флаг `--reload` перезапускает сервер при изменении кода в `src/`.

### 3. Настройка `.env`

Конфигурация читается из файла `.env` с префиксом `TUTOR_` (`src/config.py`).
Основное:

- `TUTOR_REGION` — регион: `RU` или `GLOBAL` (по умолчанию `GLOBAL`).
  Выбирает LLM-агрегатор и цепочку поисковых движков.
- Для региона `RU`: `TUTOR_ROUTERAI_API_KEY` (обязателен), опционально
  `TUTOR_YANDEX_API_KEY` для Yandex Search как основного поисковика.
- Для региона `GLOBAL`: `TUTOR_OPENROUTER_API_KEY` (обязателен), опционально
  `TUTOR_TAVILY_API_KEY`.
- `TUTOR_DDGS_ENABLED=true` — DuckDuckGo как fallback-поисковик (не требует
  ключа), поэтому веб-поиск заработает и без Yandex/Tavily.
- Остальные ключи (`TUTOR_MAX_AGENT_STEPS`, `TUTOR_MAX_AGENT_TIME_SEC`,
  лимиты бюджета, логирование и т. п.) уже имеют безопасные значения по
  умолчанию — их можно не трогать.
- Каскад LLM (по умолчанию включён): основной провайдер региона (RU →
  RouterAI, GLOBAL → OpenRouter) + фолбек на второй шлюз и запасные модели,
  retry с backoff при временных ошибках и переход на другую модель при пустом
  ответе. Настраивается `TUTOR_LLM_*` (см. `.env.example`): `LLM_PRIMARY_PROVIDER`,
  `LLM_RETRIES`, `LLM_TIMEOUT_SEC`, `LLM_FALLBACK_MODELS`,
  `LLM_JUDGE_FALLBACK_MODELS`, `LLM_RETRY_EMPTY`.

### 4. Фронтенд

Лаунчер `python run.py --frontend` сам поднимает и Vite. Вручную фронтенд
запускается так (бэкенд должен быть на `127.0.0.1:8000`; Vite-сервер
проксирует запросы `/chat`, `/student`, `/knowledge`, `/health`, `/api` на
него — `frontend/vite.config.js`):

```bash
cd frontend
npm install
npm run dev
```

Приложение откроется на http://localhost:5173.

### 4.1 Онбординг (карточка знакомства, E3)

При первом запуске (или пока профиль неполный) вместо формы темы
показывается **карточка знакомства** (`IntakeCard`): ученик вводит имя и
фамилию, тип («студент»/«школьник») и, если школьник, класс. Из
`ФИО|тип|класс` на клиенте детерминированно вычисляется `student_id`
(FNV-1a 32-bit, формат `stu_` + 8 hex — `frontend/src/identity.js`) и
сохраняется в localStorage под ключом `edututor_student` вместе с полями
профиля. Заполненный профиль уходит на бэкенд `POST /student/{id}/profile`
(fail-soft: при недоступном сервере запись лежит в localStorage и повторяется
при следующем запуске). Кнопка «Начать без карточки» пропускает онбординг —
занятие стартует анонимно под случайным id.

## HTTP API (кратко)

Базовый URL: `http://127.0.0.1:8000`. Полное описание — в
`adaptive_tutor/docs/api.md`.

| Метод и путь | Назначение |
|--------------|------------|
| `GET /health` | Статус сервиса, регион, число сессий и чанков RAG |
| `POST /chat` | Синхронный ответ репетитора одним JSON |
| `POST /chat/stream` | SSE-стрим событий агента (`agent.step`, `agent.tool`, `message`, `done`) |
| `GET /chat/history/{session_id}` | История сообщений сессии |
| `DELETE /chat/history/{session_id}` | Удаление сессии |
| `GET /knowledge` | Статус базы знаний (включена ли, число чанков, темы) |
| `POST /knowledge/provision` | Провижининг: поиск материалов по теме и индексация в RAG |
| `GET /student/{student_id}` | Профиль ученика: имя/тип/класс (карточка знакомства), темы, рекомендуемая тема |
| `POST /student/{student_id}/profile` | Сохранить профиль ученика из карточки знакомства (upsert; name ≥2 слов, learner_type из student/schoolchild) |
| `GET /student/{student_id}/sessions` | Сессии ученика |
| `GET /student/{student_id}/review` | SM-2: статистика карточек повторений + due-карточки |
| `GET /student/{student_id}/knowledge-graph` | Слой 2: статусы тем ученика, EMA-мастерство, статистика (dict `topics` + `stats`) |
| `GET /student/{student_id}/recommendations` | Рекомендации тем: слабые, пробелы пререквизитов, в работе |
| `GET /student/{student_id}/graph` | Граф источника `subject|grade` с мастерством ученика (каркас → фоновая LLM-сборка) |
| `GET /student/{student_id}/graph/{node}/related` | Соседи узла графа до глубины 2 |
| `GET /student/{student_id}/graph/{node}/wiki` | Статья узла графа (остаётся E2-заглушкой `wiki: null`; реальные статьи — в Knowledge Wiki, см. `GET /student/{id}/wiki`) |
| `GET /student/{student_id}/wiki` | Knowledge Wiki (E4): статьи-конспекты ученика — все предметы или `?subject=` |
| `GET /student/{student_id}/wiki/{subject}/{topic}` | Одна статья (человеческие имена в URL; поиск по slug); `404`, если нет |
| `POST /student/{student_id}/wiki/enrich` | Ленивое LLM-обогащение тела статьи по RAG-материалам темы |
| `DELETE /student/{student_id}/wiki/{subject}/{topic}` | Удалить статью ученика; `200`/`404` |
| `GET /student/{student_id}/export/csv` | Скачать CSV журнала ответов (`utf-8-sig`, Excel): `<student_id>_session_log.csv` |
| `GET /student/{student_id}/export/summary.csv` | Скачать CSV-сводку по сессиям: `<student_id>_summary.csv` |
| `GET /student/{student_id}/export/okf` | OKF-бандл графа источника `subject|grade` (манифест `{dir, conformant, errors, files}`) |

## Граф знаний (E2)

Трёхслойная модель: **Слой 2** — мастерство ученика в SQLite (`topics` профиля),
**Слой 1** — граф источника `subject|grade`, **Слой 3** — Knowledge Wiki
(конспекты с мастерством, реализованы в E4 — см. раздел «Knowledge Wiki … и
экспорт для учителя» ниже). Полные формы ответов — в `adaptive_tutor/docs/api.md`.

- **Мастерство и статусы (Слой 2)**. Тема получает статусы автоматически:
  `not_studied` (0 попыток) → `in_progress` (>0) → `mastered` (порог по попыткам
  и EMA-мастерству). Мастерство — EMA: `0.7·старое + 0.3·результат` с якорем
  0.5 на первом ответе; на каждый ответ копятся `attempts`/`correct`/`accuracy`
  и `weak_areas` (фидбек ошибок). Пороги: по умолчанию `mastered` требует
  `attempts >= 3` и `mastery >= 0.8`.
- **Граф источника (Слой 1)**. Строится по ключу `subject|grade` из RAG-сниппетов,
  которые накопил провижининг: LLM-онтолог (роль `fast`, temp 0.0, до 14 вершин)
  выделяет узлы/рёбра (`part_of` / `prerequisite` / `related`); при пустом или
  мусорном ответе LLM — детерминированный эвристический фолбэк по известным
  темам и маркерным заголовкам сниппетов. Результат кэшируется в
  **`adaptive_tutor/data/knowledge_graphs/`** (файл `<sha1>.json`; ключ включает
  схему/предмет/класс/объём сниппетов, поэтому рост базы пересобирает граф).
  Чтение не блокируется: сначала возвращается мгновенный каркас из тем, полная
  сборка идёт фоном (fire-and-forget), готовность сообщает SSE-событие
  `graph.ready`. Мастерство ученика накладывается на узлы по нормализованному
  заголовку (`_norm_topic` срезает «Урок/Параграф N …»); рёбра разово
  синхронизируются в `relations` тем ученика.
- **Гейт пререквизитов (advisory)**. При выборе темы сервер смотрит её
  `relations.prerequisite`: незакрытые (не изучены / не освоены) темы
  возвращаются SSE-событием `system` `kind="mastery.gate"` — фронтенд показывает
  баннер «стоит повторить …», но обучение не блокируется. В блок `adaptive`
  каждого хода добавлены ключи `topic_status` / `topic_mastery` / `topic_accuracy`.
- **UI**. Справа от чата — «Адаптивность» (`AdaptivePanel`), тепловая карта
  мастерства тем `MasteryWall` («Усвоение») и панель «Мои знания»
  (`StudentKGPanel`: статистика + список тем со статусами и кнопкой блица
  «Повторить (N)»). В центре над чатом — «Созвездие» (`KnowledgeGraphPanel`):
  canvas-визуализация графа с поиском, зумом, оверлеем мастерства узлов и
  деталями по клику (`related` + wiki-заглушка).
- **Конфигурация** (`.env`, префикс `TUTOR_`): `TUTOR_KNOWLEDGE_GRAPH_DIR`
  (каталог кэша графов, по умолчанию `data/knowledge_graphs`),
  `TUTOR_GRAPH_SCHEMA_VERSION`, `TUTOR_ONTOLOGY_MAX_VERTICES`,
  `TUTOR_MASTERY_MASTERED_THRESHOLD`, `TUTOR_MASTERY_MASTERED_ATTEMPTS`.

## Карточки для повторений (SM-2)

Автономная подсистема интервальных повторений по алгоритму SM-2:

- **Авто-добавление**: если ученик неверно ответил на вопрос квиза, сервер
  строит «answer record» из секрета последнего квиза и добавляет карточку в
  банк повторений (дедуп по тексту вопроса, кап банка — самые старые карточки
  вытесняются). Повторение интервалами планирует само ядро SM-2
  (`adaptive_tutor/src/review/sm2.py`).
- **Блиц «Повторить (N)»**: N = число «созревших» (due) карточек; кнопка
  запускает блиц через SSE-чат (`kind="review_request"`) — сервер выдаёт
  due-карточки как квизы и сам грейдит ответы без вызова агента: закрытые
  (с `options`) — сравнением с эталоном, открытые — коротким LLM-грейдером.
  По итогам блица подводится сводка «верно X из Y».
- **Конфигурация** (`.env`, префикс `TUTOR_`): `TUTOR_REVIEW_ENABLED`
  (вкл/выкл, по умолчанию `true`), `TUTOR_REVIEW_QUIZ_SIZE` (размер блица,
  по умолчанию 5), `TUTOR_REVIEW_BANK_MAX_CARDS` (кап банка на ученика,
  по умолчанию 200).
- **Где хранятся**: в том же SQLite-файле, что и профили, — таблица
  `review_cards` (`adaptive_tutor/data/students.db`). Счётчик due на каждый ход
  приходит в блоке `adaptive` (`review_due`).

Полное описание эндпоинта и SSE-протокола блица — в
`adaptive_tutor/docs/api.md`.

## Knowledge Wiki (конспекты с мастерством) и экспорт для учителя (E4)

- **Knowledge Wiki (Слой 3)**: персональные «конспекты» ученика — файлы OKF v0.2
  в `adaptive_tutor/data/knowledge_wiki/<student_id>/<subject>/<topic>.md`
  (slug-имена; в каталоге предмета дополнительно `_index.md`). На каждый
  оценённый ответ (`evaluation`/повторение) статья обновляется: EMA-мастерство
  `0.7·старое + 0.3·результат` с якорем 0.5, счётчики `attempts`/`correct`/
  производная `accuracy`, а при ошибке — заметка (`feedback` + контекст ответа,
  дедуп по фидбеку, кап 10). Статья создаётся только после первого ответа по
  теме; пустое тело лениво добивается LLM-конспектом по RAG-материалам
  (роль `fast`, ошибки не роняют чат).
- **Экспорт для учителя (CSV/OKF)**: журнал ответов хранится в таблице
  `session_records` того же SQLite (`data/students.db`), эндпоинты отдают CSV с
  BOM (`utf-8-sig`, Excel) — подробный журнал вопросов и сводку по сессиям, —
  а `/export/okf` собирает каталог-бандл OKF v0.2 в
  `data/okf/<student_id>/<subject>/` (`index.md`, `log.md`, `topics/*.md` узлов
  графа источника с мастерством тем) и возвращает манифест конформизма.
- **Конфигурация** (`.env`, префикс `TUTOR_`): `TUTOR_KNOWLEDGE_WIKI_DIR`
  (корень wiki, по умолчанию `data/knowledge_wiki`), `TUTOR_OKF_DIR` (корень
  OKF-бандлов, по умолчанию `data/okf`), `TUTOR_WIKI_ENABLED` (вкл/выкл Wiki,
  по умолчанию `true`), `TUTOR_WIKI_ENRICH_ENABLED` (ленивое LLM-обогащение,
  по умолчанию `true`). Формат OKF читает PyYAML (`PyYAML>=6.0`).

Полные формы ответов эндпоинтов — в `adaptive_tutor/docs/api.md`.

## Запуск тестов

Бэкенд (из каталога `adaptive_tutor`):

```bash
.venv\Scripts\python -m pytest tests/ -q
```

Фронтенд (из каталога `frontend`):

```bash
npm test      # vitest: юнит-тесты (jsdom)
npm run lint  # oxlint
npm run e2e   # Playwright: end-to-end сценарий на моках API (e2e/tutor.spec.js)
```

E2E-тест не требует запущенного бэкенда: ответы `/chat/stream`, `/student`,
`/chat/history` мокаются на уровне браузера.

## Тяжёлая зависимость: локальные эмбеддинги

`rag_search` и провижининг требуют пакет `sentence-transformers`, который
тянет за собой PyTorch (сотни мегабайт). Он используется только для локальных
эмбеддингов: по умолчанию `TUTOR_EMBEDDING_PROVIDER=local`, модель
`intfloat/multilingual-e5-small` (класс `LocalEmbedder`,
`src/rag/__init__.py`) скачивается один раз при первом обращении к RAG.

Обычный чат и `web_search` работают и без этой зависимости. Внешняя векторная
БД проекту не нужна — код использует `InMemoryVectorStore` (векторы живут в
памяти процесса), поэтому `qdrant-client` намеренно убран из
`pyproject.toml`/`requirements.txt`.

## Соответствие требованиям курса (OTUS)

| Требование курса | Как закрыто в проекте | Где в коде |
|------------------|-----------------------|-----------|
| Агент с реальной задачей; ReAct-цикл, где модель сама выбирает следующий шаг | Узел `planner` по контексту решает: вызвать инструмент или дать финальный ответ; выбор трассируется в `AgentStep.reason_summary` и логах | `src/agent/loop.py` (`plan`, `run_agent`), `src/agent/prompts.py` |
| Function-calling: инструменты со схемой и безопасным исполнением | `rag_search`/`web_search` описаны OpenAI-совместимыми JSON-схемами (`TOOL_SCHEMAS`); исполнение через `execute_tool` с таймаутом 15 c, retry ×2 и per-tool circuit breaker | `src/agent/tools.py` |
| RAG-память и поведение при пустом результате | `rag_search` по `InMemoryVectorStore`; при отсутствии релевантного материала — fallback на `web_search` или честный ответ; база наполняется провижинингом | `src/rag/__init__.py`, `src/rag/provisioning.py`, `src/agent/tools.py` |
| Оркестрация: ветвление, циклы, условия остановки | LangGraph `StateGraph`: `planner → tools → planner … → final`; условные рёбра `should_continue`; остановка по `terminated`, лимиту шагов и `IterationLimiter`; глобальный таймаут в `run_agent` | `src/agent/loop.py` (`build_graph`, `should_continue`) |
| Минимум 2 LLM-модели для разных ролей и 2 провайдера | Роли `planner`/`fast`/`judge`; регион RU → RouterAI, GLOBAL → OpenRouter | `src/llm/base.py`, `src/llm/router_ai.py`, `src/llm/openrouter.py` |
| Безопасность | `CircuitBreaker` (LLM-вызовы), `IterationLimiter`, `BudgetGuard`, `OutputValidator`; входные данные валидируются Pydantic-схемами API | `src/safety/__init__.py`, `src/api/server.py` |
| Наблюдаемость и корректный рендер формул | Структурированные JSONL-логи (`JsonlLogger`) с `trace_id` и маскированием секретов (`scrub`); системный промпт требует LaTeX (`$...$`/`$$...$$`), парность проверяет `OutputValidator`; фронтенд рендерит формулы через KaTeX | `src/observability/logger.py`, `src/agent/prompts.py`, `src/safety/__init__.py`, `frontend/src/components/Latex.jsx` |

## Документация

- `adaptive_tutor/README.md` — SOP-документ: процедуры агентного цикла,
  инструментов, моделей, безопасности, наблюдаемости.
- `adaptive_tutor/docs/architecture.md` — архитектура и диаграмма потока.
- `adaptive_tutor/docs/api.md` — описание HTTP API и SSE-протокола.
- `adaptive_tutor/docs/logging-example.md` — примеры JSONL-логов.
