# Adaptive Tutor — SOP (Standard Operation Procedure)

Документ фиксирует **процедуры** работы агентного цикла: какие узлы выполняются,
как маршрутизируются инструменты, что происходит при ошибках и лимитах.

---

## 1. Агентный цикл (ReAct на LangGraph)

Рабочее состояние агента — `AgentGraphState` (`src/models/schemas.py`):
`messages`, `tool_calls`, `rag_context`, `web_results`, `student_profile`,
`difficulty`, `steps`, `paused`/`terminated`.

Цикл состоит из трёх узлов и маршрутизации:

```
START -> planner -> (cond_edge) -> tools -> planner -> ... -> final -> END
```

1. **planner** — LLM-модель получает `messages` (+ необязательно RAG-контекст)
   и решает, что делать:
   - вызвать **инструмент** (`rag_search` / `web_search`), или
   - сформировать **финальный ответ** (без tool_calls).
2. **tools** — исполняет запрошенные инструменты через `execute_tool` с
   таймаутом и retry. Результат кладётся в `tools_result` и возвращается планнеру.
3. **cond_edge** (`should_continue`) — решает, продолжить цикл или завершить:
   - если `tool_calls` непуст → `tools`;
   - если шагов больше `max_agent_steps` → принудительно `final`;
   - если `terminated` → `END`.

Защита от бесконечного цикла: **жёсткая квота шагов** (`max_agent_steps`, по
умолчанию 6) и **глобальный таймаут** (`max_agent_time_sec`, по умолчанию 150 c),
применяемый в `run_agent` через `asyncio.wait_for`.

---

## 2. Инструменты (Function Calling)

Инструменты объявлены как OpenAI-совместимые JSON-схемы в `src/agent/tools.py`
(`TOOL_SCHEMAS`). Модель получает схемы, возвращает `tool_call`, а исполнением
занимается `execute_tool(name, args, ctx, tracker)`.

| Инструмент | Sourcess | Назначение |
|------------|----------|-------------|
| `rag_search` | векторная база (Qdrant/in-memory) | факты из учебного материала |
| `web_search` | Yandex (RU) / Tavily (Global) → DDG fallback | актуальная интернет-информация |

Схема `rag_search`:
```json
{
  "type": "function",
  "function": {
    "name": "rag_search",
    "description": "Поиск по векторной базе знаний …",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {"type": "string"},
        "top_k": {"type": "integer", "default": 5},
        "filters": {"type": "object", "default": {}}
      },
      "required": ["query"]
    }
  }
}
```

Процедура исполнения инструмента (в `execute_tool`):
1. Проверить, не отключён ли инструмент circuit breaker'ом инструмента
   (`ToolFailureTracker`).
2. Выполнить вызов под таймаутом `TOOL_TIMEOUT_SEC` (15 c).
3. На успех — сохранить результат (ограничен `MAX_TOOL_RESULT_CHARS` = 4000).
4. На ошибку/таймаут — записать `failures`, повторить (до `MAX_TOOL_RETRIES` = 2).
5. При серии ошибок — занести в `disabled` (инструмент временно недоступен).

Результат инструмента сериализуется в JSON вида
`{"status": "ok"|"error", "data": ..., "error": ...}`.

---

## 3. Региональное маршрутизация

Управляется ENV `TUTOR_REGION` (`RU` | `GLOBAL`):

| Регион | LLM-агрегатор | Токен-счётчик | Поисковый engine (primary → fallback) |
|--------|---------------|---------------|----------------------------------------|
| RU     | RouterAI      | `RouterAITokenCounter` (₽ → USD) | Yandex → DuckDuckGo |
| GLOBAL | OpenRouter    | `OpenRouterTokenCounter` (credits ≈ USD) | Tavily → DuckDuckGo |

Фабрика `LLMClientFactory.get_client(region)` подключает нужный клиент
и его `TokenCounter`. `SearchRouter.get_engines(region)` возвращает упорядоченный
список поисковиков; `SearchRouter.search()` перебирает их с fallback.

---

## 4. Защита от лимитов (в агентном цикле)

В узле `planner` перед вызовом LLM проверяются:
- **Circuit breaker** (`runtime.circuit_breaker.is_open()`) — при открытом
  состоянии берём fast-модель/принудительно финализируем.
- **Бюджет/лимиты** (`runtime.budget.exceeded(model)`) — при исчерпании
  принудительно финализируем.

При LLM-ошибке — фиксируется failure в circuit breaker, цикл завершается
с корректным сообщением, а не падает.

---

## 5. Наблюдаемость

Каждый узел записывает `AgentStep` с полями:
`action`, `reason_summary` (краткое объяснение выбора БЕЗ скрытого reasoning),
`model`, `tool`, `arguments`, `status`, `duration_ms`.

Эти данные передаются в `runtime.logger(state, step)` для записи в структурированный
JSON-лог с уникальным `trace_id` (см. Шаг 4).

---

## 6. Модели по ролям

`runtime.models` — карта ролевых моделей (из `LLMClientFactory.get_models_for_region`):

- `planner` — планирование/принятие решений (агенций, выбор действий);
- `fast` — быстрые задачи (fallback/короткие вызовы);
- `judge` — оценка качества ответа (используется на шаге оценки, см. Шаг 3 дальше).

Каждая роль может быть задана отдельной моделью; это удовлетворяет требованию
«минимум 2 модели для разных задач».

---

## 7. Наблюдаемость (JSON-логи с trace_id)

Логирование — строго структурированный JSONL (`src/observability/logger.py`).
Один запрос → один `trace_id` (UUID hex), который прокидывается через
`TraceContext` и декоратор восстанавливает его автоматически.

Декоратор `@log_trace(logger, event)`:
```python
from ..observability.logger import log_trace

@log_trace(logger, "agent.plan")
async def plan(self, state): ...
```
Он записывает `event`, `status`, `duration_ms`, а при исключении — `error`
(уровень ERROR) без разглашения чувствительных данных.

Что логируем (через `JsonlLogger.step/llm_call/finished`):
- `timestamp`, `trace_id`, `level`, `event`;
- `agent.{action, reason_summary, model}` — краткое объяснение выбора БЕЗ CoT;
- `tool.{name, arguments}`, `status`, `sources`, `tokens`, `cost_usd`,
  `duration_ms`, `session_id`, `loop_invariants`.

Что НЕ логируем: скрытый chain-of-thought, секреты/API-ключи (`scrub` → `***`),
PII, тяжёлые промпты.

Пример записи — в `docs/logging-example.md`.

---

## 8. Безопасность (`src/safety/`)

### Circuit Breaker
`CircuitBreaker(threshold, cooldown_sec)` — состояния CLOSED / OPEN / HALF_OPEN:
- `record_failure()` — на `threshold`-й ошибке → OPEN;
- `is_open()` — возвращает True до истечения `cooldown`, затем HALF_OPEN;
- пробный успех → CLOSED, ошибка → OPEN.

Подключён в `AgentRuntime.plan`: при открытом состоянии LLM не вызывается,
агент финализируется безопасной заглушкой.

### Лимит итераций (защита от бесконечного цикла)
`IterationLimiter(max_iterations)` — жёсткий счётчик шагов. `tick()` возвращает
`False` после `max_iterations`; `stopped` блокирует дальнейшие планирования.
В графе учёт через `should_continue(state, limiter)` + квота из `max_agent_steps`
и глобальный таймаут `max_agent_time_sec` в `run_agent`.

### Бюджет/лимиты
`BudgetGuard(max_cost_usd, max_calls)` — блокирует цикл при превышении стоимости
или числа LLM-вызовов за сессию. `record(cost)` фиксирует траты; `exceeded(model)`
проверяет лимит перед очередным планированием.

### Валидация итогового ответа
`OutputValidator.validate(answer)` — применяется в узле `finalize`:
- непустой ответ;
- сбалансированность LaTeX-формул (парность `$`/`$$`) — критично для
  рендера формул на фронтенде.

При провале — ответ помечается `[требуется переформулировка]` и логируется
причина.

---

## 9. Ответы на 7 вопросов из методички курса

### Вопрос 1. Какую полезную задачу решает агент?

**Adaptive Tutor** помогает ученику осваивать тему в диалоге, персонализируя
сложность под его уровень знаний (зона ближайшего развития, подход DeepTutor).

**Вход** → **обработка** → **результат**:
- **Вход**: текстовый вопрос ученика по теме (математика/физика и т.п.).
- **Обработка**: агентный цикл (ReAct) — модель планирует, при необходимости
  обращается к векторной базе знаний (`rag_search`) или веб-поиску
  (`web_search`), формирует ответ.
- **Результат**: структурированный ответ с LaTeX-формулами (`$...$` inline,
  `$$...$$` block), корректно отдаваемый MathJax/KaTeX на фронтенде, плюс
  метаданные (стоимость, токены, источники).

Автоматическая смена сложности (LinUCB-бандит + scaffolding) упоминается в
`src/adaptive/` и подключается по мере развития профиля ученика.

---

### Вопрос 2. Где агент сам выбирает следующее действие?

**Узел `planner`** в `src/agent/loop.py`. Модель получает `messages` (+
необязательно RAG-контекст) и сама решает:
- вызвать инструмент `rag_search` / `web_search` (возвращает `tool_calls`),
- либо сформировать финальный ответ (без `tool_calls`).

Решение принимается языковой моделью по её усмотрению, но **обязательно
видно в коде и логах**: выбранный инструмент записывается в `AgentStep` с
полем `reason_summary` (см. `JsonlLogger.step`). Ответ «модель сама
разберётся» — недостаточен; здесь решение трассируется через `trace_id`.

---

### Вопрос 3. Когда и почему выбирается каждая модель?

Ролевые модели задаются в `LLMClientFactory.get_models_for_region()`:
`{"planner": ..., "fast": ..., "judge": ...}`.

- **planner** — планирование и принятие решений (основной цикл). Выбирается
  всегда, когда нужно решить, какой инструмент вызвать и как структурировать
  ответ.
- **fast** — быстрые задачи: fallback при открытом circuit breaker, короткие
  переформулировки, эвристики.
- **judge** — LLM-асессор качества ответа (оценка по метрикам, `src/judge.py`
  в референсе). Используется на этапе оценки, а не в цикле.

Выбор модели **внутри цикла** происходит в `planner` через
`state.current_model` и `runtime.models`. Минимум две разные модели для
разных задач — реализовано.

---

### Вопрос 4. Какой инструмент вызывает модель через function calling?

Модель вызывает **два инструмента** через OpenAI-совместимые JSON-схемы
(`src/agent/tools.py`, `TOOL_SCHEMAS`):

| Инструмент | Действие | Назначение |
|------------|----------|------------|
| `rag_search` | семантический поиск по векторной базе | факты из учебного материала |
| `web_search` | веб-поиск через `SearchRouter` | актуальная интернет-информация |

Исполняет их `execute_tool(name, args, ctx, tracker)` — с таймаутом 15 с,
retry ×2, перинструментным circuit breaker (`ToolFailureTracker`), результатом
ограниченным `MAX_TOOL_RESULT_CHARS`.

SOP для инструментов описан в разделе 2 этого README (когда вызывать,
как обрабатывать результат/ошибки, какие ограничения).

---

### Вопрос 5. Когда агент обращается к памяти и что делать, если ничего не найдено? + Формулы

**Обращение к памяти (retrieval).** В `src/agent/prompts.py` (системный промпт)
агенту прямо предписано: *«Сначала реши, нужно ли уточнить факты из базы; при
необходимости вызови инструмент»*. Модель решает сама, нужен ли поиск; если
контекст уже достаточен — финализирует без обращения к памяти.

**Что если ничего релевантного не найдено?** По SOP инструмента:
- `tool status=error` фиксируется в `ToolFailureTracker` (возможно повторение);
- при серии ошибок инструмент временно отключается;
- агент переключается на **fallback-источник**: `web_search` (актуальный
  интернет вместо базы знаний), либо честно сообщает ученику, что материала
  недостаточно и предлагает смежную тему.

**Формулы (дополнительно к вопросу).** В системный промпт агента зашито
правило рендера, чтобы модель не «забывала» LaTeX:

> *Математические формулы ОБЯЗАТЕЛЬНО оформляй в строгом LaTeX: inline
> формулы в `$...$`, а отдельные формулы в блоки `$$...$$`, чтобы
> фронтенд-рендерер MathJax/KaTeX мог их отрисовать.*

Дополнительно `OutputValidator` в узле `finalize` проверяет парность `$`/`$$`
— несбалансированные формулы помечаются и переформулируются.

---

### Вопрос 6. Где находятся ветвление, повтор шага и условие остановки?

**Ветвление / condition edges** — в `build_graph()` (`src/agent/loop.py`) и
`should_continue()`:
```
planner → (cond) → tools → planner → ... → final → END
```
`should_continue` возвращает:
- `tools`, если есть `tool_calls` — **повтор шага** (цикл ReAct);
- `final`, если шагов больше `max_agent_steps` или `limiter.stopped` —
  **принудительная остановка**;
- `stop`, если `terminated` — завершение.

**Повтор шага** реализован ребром `tools → planner` и отдельной проверкой
`IterationLimiter.tick()` (защита от бесконечного цикла), **таймаут**
`max_agent_time_sec` применяется в `run_agent` через `asyncio.wait_for`.

---

### Вопрос 7. Как доказать, что система работает правильно, стабильно и безопасно?

**Метрики успеха** (см. Шаг 3, раздел оценки):
- корректность финального ответа (LLM-ассессор `judge`, порог ≥ 7/10);
- доля успешных вызовов инструментов (`status=ok / total`);
- стоимость и токены на сессию (контроль `BudgetGuard`);
- доля валидных ответов по `OutputValidator`.

**Стабильность** доказана тестами (`tests/`, 25 passed):
- агентный цикл выбирает инструмент → исполняет → финализирует;
- `should_continue` покрывает tools/final/stop;
- превышение шагов форсирует final;
- обработка ошибки/таймаута инструмента.

**Наблюдаемость** (раздел 7): каждая трассировка имеет уникальный `trace_id`;
логируется выбор действия, `reason_summary`, модель, инструмент, статус,
токены, стоимость, длительность, источники. Это позволяет **доказать
поведение по логам**.

**Безопасность** (раздел 8): circuit breaker, лимит итераций, бюджет,
валидация результата, `scrub` маскирует секреты в логах.

**Пример запроса и результата** приведён в `docs/logging-example.md` и
README (раздел запуска), что отвечает требованию «форма сдачи».

---

## 10. Knowledge Wiki и экспорт для учителя (E4)

### Knowledge Wiki (Слой 3)

Персональные конспекты ученика хранятся как markdown-файлы OKF v0.2 на диске:

```
data/knowledge_wiki/<student_id>/<slug(subject)>/<slug(topic)>.md
data/knowledge_wiki/<student_id>/<slug(subject)>/_index.md   # индекс предмета
```

- **Формат файла**: YAML-frontmatter + `# title` + тело. Ключи в порядке спеки:
  `okf_version: "0.2"`, `type: Topic`, `title`, `topic`, `subject`, `grade`,
  `curriculum`, `mastery` (EMA `0.7·старое + 0.3·результат`, якорь 0.5),
  `accuracy` (производная `correct/attempts`), `attempts`, `correct`,
  `last_studied`, затем опционально `section_number`, `weak_areas`,
  `relations`, `notes` (заметки об ошибках: `{date, feedback, question?,
  student_answer?, correct_answer?}`; дедуп по `feedback[:180]`, кап 10),
  `concepts`, `source`. Slug: юникод-символы сохраняются, пробелы/`/`/`\` → `-`,
  пусто → `topic`.
- **Жизненный цикл**: статья создаётся/обновляется на каждый оценённый ответ
  (`evaluation`, повторения) через `apply_record`; без ответа по теме статья не
  создаётся. Пустое тело лениво добивается LLM-конспектом (`wiki/enrich.py`,
  роль `fast`, temp 0.3, max_tokens 400, контекст ≤ 4000 симв.) только при
  провижиненных RAG-материалах; ошибки обогащения глотаются (чат не падает).
  Удаление статьи пересоздаёт `_index.md`.
- **Флаги конфигурации** (`.env`, префикс `TUTOR_`):
  `TUTOR_KNOWLEDGE_WIKI_DIR` (`data/knowledge_wiki`), `TUTOR_OKF_DIR`
  (`data/okf`), `TUTOR_WIKI_ENABLED` (`true`), `TUTOR_WIKI_ENRICH_ENABLED`
  (`true`). Формат frontmatter читается PyYAML (`PyYAML>=6.0`).

### Экспорт для учителя

Журнал ответов — SQLite-таблица `session_records` (`data/students.db`), колонки:
`student_id`, `record_id` (`rec_<uuid12>`), `session_id`, `ts`, `subject`,
`topic`, `question_id` (ответы блица — `review:<card_id>`), `question`,
`options` (JSON-список или `NULL`), `answer_type`, `difficulty`,
`student_answer`, `correct`, `feedback`, `score01`. Окно выдачи — последние
`limit` (по умолчанию 500) записей.

CSV-эндпоинты отдают текст в `utf-8-sig` (BOM для Excel) c
`Content-Disposition: attachment`:

- `GET /student/{id}/export/csv` — журнал вопросов, колонки: `timestamp`,
  `session_id`, `subject`, `topic`, `question_id`, `question`, `options`
  (списки через `" | "`), `answer_type`, `difficulty`, `student_answer`,
  `score01`, `correct` (0/1), `feedback`. Файл `<student_id>_session_log.csv`.
- `GET /student/{id}/export/summary.csv` — одна строка на сессию: `session_id`,
  `subject`, `topic` (`" | "`), `started_at`, `ended_at`, `questions`,
  `correct`, `accuracy`, `mastered_topics`. Файл `<student_id>_summary.csv`.
- `GET /student/{id}/export/okf?subject=&grade=` — собирает OKF-бандл в
  `data/okf/<student_id>/<slug(subject)>/` (`index.md`, `log.md`,
  `topics/<slug(title)>.md` для не-book узлов с мастерством/relations в
  frontmatter) и возвращает манифест `{dir, conformant, errors, files}`.

Сборка CSV/OKF — чистые функции (`src/export/`), сбои БД/RAG дают пустые
данные/каркас, но не `500`. Наблюдаемость: JSONL-события `wiki.updated`,
`wiki.note`, `export.csv`, `export.summary`, `export.okf`.

---

## 11. Запуск тестов

Из каталога `adaptive_tutor`:

```bash
.venv/Scripts/python.exe -m pytest tests/ -q   # юнит/API/интеграция
.venv/Scripts/ruff.exe check src/ tests/       # линт
```