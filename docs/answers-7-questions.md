# EduTutor — шпаргалка: ответы на 7 вопросов к проекту

## 1. Какую полезную задачу решает?
Адаптивный репетитор: ведёт диалог по теме и **сам подбирает сложность под ученика**.
- Теория/практика/подсказки — конверты `theory/practice/hint/quiz/evaluation` (`src/models/schemas.py`), формулы в LaTeX, рендер KaTeX.
- Квиз генерирует отдельный инструмент `generate_quiz` (короткий LLM-вызов, `src/agent/tools.py:112`).
- Ответы грейдятся сервером, при ошибке карточка уходит в SM-2-повторения.
- Сложность советует LinUCB contextual bandit перед каждым ходом, награда — корректность ответа (`src/api/server.py:1579,1811`, `src/student/linucb.py`).

## 2. Где агент сам выбирает действие?
Узел `planner` (`AgentRuntime.plan`, `src/agent/loop.py:123`): LLM возвращает `tool_calls` → `tools`, либо финальный ответ → `finalize`.
Выбор не скрыт: каждый шаг логируется с `reason_summary` и `trace_id` (`src/observability/logger.py:102`).
Текстовые вызовы инструментов (у провайдеров без нативных `tool_calls`) распознаются и исполняются штатно (`src/agent/loop.py:493`).

## 3. Когда и почему выбирается модель?
- По роли и региону — `get_models_for_region` (`src/llm/base.py:229`): planner/fast/judge. RU: qwen3.7-flash / qwen-2.5-7b-instruct / gemini-2.5-flash; GLOBAL: claude-sonnet-4 / gemini-2.5-flash / gpt-4.1-mini.
- В цикле: открытый circuit breaker / исчерпан бюджет → финализация без LLM (`loop.py:128,143`); недоступность ролевой модели → каскад на запасные модели и второй шлюз (`src/llm/cascade.py`); judge (Critic) — отдельно, `temperature=0` (`src/agent/critic.py:55`).
- Почему НЕ reasoning `deepseek-v4-flash-0731`: съедает `max_tokens` на скрытые «размышления» и возвращает пустой `content` (4/4 пустых прогона). Для строгого JSON-конверта нужна instruct-модель.

## 4. Какие инструменты вызывает модель?
Через function calling (схемы `TOOL_SCHEMAS`, `src/agent/tools.py:142`) — 3 инструмента:
- `rag_search` — векторная база знаний;
- `web_search` — интернет (Yandex/Tavily → DuckDuckGo);
- `generate_quiz` — карточка квиза; внутри делает **отдельный короткий LLM-вызов**, учитывается в бюджете и пишется в логи как `quiz.gen`.

Исполнение — `execute_tool` (`tools.py:153`): таймаут 15 c (для generate_quiz — llm_timeout), retry ×2, per-tool circuit breaker, результат ≤ 4000 симв.

## 5. Когда обращается к памяти?
По решению planner: промпт велит сначала решить, нужны ли факты из базы, и вызвать `rag_search` (`src/agent/prompts.py:9`); если контекста хватает — отвечает без поиска.
При пустом RAG — не пустой массив, а заметка «База пуста… Попробуйте web_search или объясните из общих знаний» (`src/agent/tools.py:400`) → модель идёт в веб-поиск или честно говорит о нехватке материала.

## 6. Где ветвление и остановка?
LangGraph `StateGraph` + условные рёбра `should_continue` (`build_graph`, `src/agent/loop.py:678`): `tool_calls` → `tools`, `terminated` → `END`, лимит шагов → принудительный `final`; ребро `tools → planner` замыкает цикл.
Остановка: `IterationLimiter` (счётчик, дефолт 6 шагов, `src/safety/__init__.py:67`), `max_agent_steps`, глобальный таймаут `asyncio.wait_for(150 c)` в `run_agent` (`loop.py:726`); при таймауте — корректный ответ, не падение.

## 7. Как доказать стабильность?
- **Логи**: JSONL, один ход → один `trace_id`; события `agent.step/agent.tool/quiz.gen/final.truncated/bandit.*`; секреты маскируются `scrub` (`src/observability/logger.py:55`). Пример — `adaptive_tutor/docs/logging-example.md`.
- **Защита**: `CircuitBreaker`, `IterationLimiter`, `BudgetGuard`, `OutputValidator` (парность `$`/`$$`).
- **Метрики с порогами** (README): judge ≥ 7/10, квизы с первой попытки ≥ 90%, успешность инструментов ≥ 95%, без обрыва JSON ≥ 95%, бюджет ≤ лимита.
- **Тесты**: backend pytest **405 passed**, frontend vitest **142 passed**, Playwright e2e на моках без бэкенда (`frontend/e2e/tutor.spec.js`).
