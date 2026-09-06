# Scaffolding subtasks: лестница подсказок — дизайн (Этап 5, optional)

Дата: 2026-09-06. Воркспейс: `C:\otus\edututor`. Тип: новое под-системное изменение
бэкенда + минимальная UI-обвязка поверх существующего hint-потока. Коммит не
выполняется без явной просьбы.

## 1. Цель и место в продукте

Gap-аудит (`docs/superpowers/2026-09-04-gap-audit-edututor-vs-reference.md`)
декларирует scaffolding-концепцию reference-проекта как «Этап 5, optional».
Концепция reference: когда задача сложна для ученика, тьютор (1) декомпозирует её
на короткую последовательность подзадач-шагов, (2) сопровождает каждый шаг
«лестницей» подсказок от общего направления до почти-решения и (3) снимает
поддержку по мере роста мастерства.

В нашей архитектуре сегодня:

- Есть конверт `hint` + серверный флоу `hint_request` с капом «2 подсказки подряд
  на задачу» (`src/api/server.py:344-345` `_MAX_HINTS_IN_A_ROW = 2`,
  `_HINT_SERVICE_TEXT`; guard `server.py:1442-1468`). Каждая подсказка
  **генерируется моделью заново** (`server.py:1469-1471` подставляет служебный
  текст в контекст, дальше `run_agent`, `server.py:1562`).
- Есть тип конверта `practice`, оценка ответа через конверт `evaluation`
  (`server.py:1682+`: answer record → mastery `store.apply_result`, SM-2
  review-карточка, LinUCB `bandit.update`, журнал, Wiki).
- Есть серверное мастерство тем per (student, topic): `accuracy`/`mastery`/
  `status` (`src/student/store.py:410-440`, `apply_result` `store.py:442-495`),
  пререквизиты (`store.py:672-687`), порог освоения `is_mastered` (attempts>=3,
  mastery>=0.8) (`src/student/mastery.py:36-46`).
- Есть LinUCB-советник сложности: перед ходом сервер добавляет модельный совет,
  при выдаче `quiz`/`practice` фиксирует «сыгранную руку» (`server.py:1482-1514`,
  `server.py:1649-1655`), на `evaluation` обновляет бандита (`server.py:1709-1743`).
- Фронтенд рендерит конверты по `envelope.type` (`frontend/src/components/Chat.jsx:56-64`);
  `practice` — `PracticeBlock.jsx` (текст + кнопка «Подсказка»), `hint` — `HintBlock.jsx`.

Reference-реализация и её инвокация (для педагогики и формы данных, НЕ для
переноса хранилища):

- `C:\otus\project_work\src\scaffold.py:33-70` — `hint_for(question,
  correct_answer, context, level, llm_call=None)`: два уровня (1=направление через
  ключевые слова, 2=«Начни так: …» первые слова эталона), rule-based fallback без
  LLM. Нам важна идея «уровни нарастающей конкретики», не её сигнатура.
- `C:\otus\project_work\src\evaluation.py:296-343` — лестница включается при
  НЕВЕРНОМ ответе: первый промах → hint level 1, повторный → level 2
  (`st.hint_level`, `retry_question_id`), после исчерпания и при наличии у карточки
  `subtasks` — разбор по шагам (`st.subtask_queue`, вопрос НЕ финализируется).
- `C:\otus\project_work\src\graph.py:2590-2646` — `subtask_node`: шаги задаются
  по одному как мини-вопросы, после ответа на все шаги исходный вопрос
  перезадаётся (`question_id + "-b"`).
- `C:\otus\project_work\src\tutor.py:315,406-409` — подзадачи **авторятся
  моделью** при генерации задания (`"subtasks": [...]`, до 3 штук).
- `C:\otus\project_work\src\states.py:141-145` — состояние в per-student JSON
  `TutorState` (`hint_level`, `subtask_queue`, …). **Этого мы НЕ переносим**:
  у нас SQLite mastery + in-memory `ChatSession` + конверты.

**Отличие нашего MVP от reference**: в reference scaffolding — это реакция на
неверный ответ (retry-цикл). У нас его место занимает *упреждающая* поддержка для
сложной практики у слабой темы: шаги видны как чек-лист сразу, а лестница
подсказок вытягивается кнопкой «Подсказка» без перегенерации моделью. Retry-цикл
«ошибся → та же задача с лестницей» вынесен за MVP (см. §13).

## 2. Ключевые решения (согласовано)

1. **Один конверт `practice`, расширенный payload** — НОВЫЙ тип конверта
   `scaffold` НЕ вводится (почему — §4.1).
2. **Лестница хранится на сервере per-сессии**, модель авторует её ОДИН раз
   при выдаче практики; каждый последующий `hint_request` обслуживается
   сервером детерминированно, без вызова LLM.
3. **Секреты лестницы — в underscore-поле** `payload._scaffold_hints`
   (санитизация уже вырезает `_...` и `correct_answer`,
   `src/agent/envelope.py:9,168-174`); клиент и история получают только видимый
   чек-лист `payload.scaffold_steps`.
4. **Решение «когда декомпозировать» — гибрид**: сигналы (слабая тема, hard от
   LinUCB, явная просьба) собирает и докладывает модели СЕРВЕР (у модели нет
   доступа к store), а окончательное решение и формулировки шагов — за моделью.
   Снятие поддержки (fade) — серверный чистый предикат на пороге мастерства.
5. **Оценка и mastery не меняются**: ответ на scaffold-практику идёт обычным
   путём `evaluation` → record → SM-2/LinUCB/mastery/wiki; scaffold-состояние
   при этом сбрасывается.
6. **UI-минимум**: чек-лист рисует существующий `PracticeBlock` (по
   `payload.scaffold_steps`), подсказки — существующий `HintBlock` отдельными
   сообщениями; новых блоков нет.

## 3. Терминология

- **Scaffold-практика** — `practice`-конверт с валидным scaffold-payload.
- **Шаг (subtask/checkpoint)** — элемент декомпозиции из `scaffold_steps`
  (короткая инструкция «что сделать на этапе N»).
- **Лестница шага** — ровно 3 строки `_scaffold_hints[step]`: level 0
  «направление» → level 1 «конкретнее» → level 2 «почти решение».
- **Позиция лестницы** — пара `(step, level)`; серверная модель продвижения:
  сначала исчерпываются 3 уровня текущего шага, затем переход к следующему шагу
  (`next_ladder`). Упрощение UX задокументировано в §11.
- **Fade** — автоматическое прекращение *совета* декомпозировать, когда тема
  переходит в `mastered` (`src/student/mastery.py:36-46`).

## 4. Контракт payload и API

### 4.1. Почему НЕ новый тип `scaffold`

Введение `ContentType.SCAFFOLD` (`src/models/schemas.py:54-70`) потребовало бы
правок во всех точках, где тип участвует: `prompts.py` (схема JSON),
`envelope.py` (`_ENVELOPE_TYPES` строится из `ContentType`,
`envelope.py:12-13`), `salvage_truncated_envelope`, `quiz_guard`, серверные guard
и bandit-разметка «руки играются на quiz/practice» (`server.py:1653-1655`),
фронтенд-диспатч (`Chat.jsx:56-64`). При этом семантика — «практика, которую
ученик решает с поддержкой», а не новый тип контента. Поэтому: **reuse
`practice` + payload-флаг структуры**. Все существующие механизмы (гвард
`practice` не проверяет — `src/agent/quiz_guard.py` валидирует только quiz;
bandit; append; рендер) работают без изменений.

### 4.2. Формат от модели (один раз, при выдаче практики)

Модель возвращает ОДИН `practice`-конверт:

```json
{
  "type": "practice",
  "text": "Условие задачи (без решения)",
  "difficulty": "hard",
  "payload": {
    "task_ref": "…",
    "scaffold_steps": [
      "Выпиши дано и искомое.",
      "Составь уравнение по условию.",
      "Реши уравнение и проверь корни."
    ],
    "_scaffold_hints": [
      ["О чём говорится в условии? Раздели величины.", "Введи обозначения для неизвестного…", "Пусть x — неизвестное…"],
      ["Какие величины связаны?", "Свяжи их знаком равенства…", "Получится x + 5 = 12."],
      ["Какие шаги остались после составления уравнения?", "Перенеси числа, приведи подобные…", "x = 7 — проверь подстановкой."]
    ]
  }
}
```

Правила валидации (сервер, чистый предикат `normalize_scaffold_payload`):
- `scaffold_steps` — список из **2–4** непустых строк;
- `_scaffold_hints` — параллельный список той же длины; каждый элемент — список
  из **ровно 3** непустых строк (направление → конкретнее → почти решение);
- любое отклонение → payload **не является** scaffold-практикой: сервер удаляет
  оба ключа из payload (обычная практика), логирует `scaffold.reject`.

**Секреты**: level 2 («почти решение») для всех шагов не должен покидать сервер.
`payload._scaffold_hints` — underscore-ключ верхнего уровня → `sanitize_envelope`
(`envelope.py:168-174`) вырезает его и из истории, и из ответа клиенту.
Видимый чек-лист `scaffold_steps` (не секрет, это и есть поддержка) остаётся в
санитизированном payload → `PracticeBlock` рендерит его.

### 4.3. Открытые API-форматы

Никаких новых REST/SSE-эндпоинтов и событий нет. `hint_request` в активном
scaffold обслуживается внутри `POST /chat` и `/chat/stream` тем же ранним
возвратом, что и существующий guard-отказ (`server.py:1442-1468`), и уходит
клиенту как обычный `message`-фрейм с `envelope.type = "hint"`.

## 5. Чистый модуль `src/student/scaffold.py`

Чистые функции без I/O (стиль `src/student/linucb.py`, `src/student/mastery.py`),
не знают про sqlite/LLM/FastAPI.

Константы (module-level, стиль кодовой базы — как `_MAX_HINTS_IN_A_ROW`):
- `MIN_STEPS = 2`, `MAX_STEPS = 4`, `HINTS_PER_STEP = 3`;
- `WEAK_ACCURACY = 0.6`, `WEAK_MIN_ATTEMPTS = 2`;
- `HINT_LEVEL_LABELS = ("направление", "конкретнее", "почти решение")`;
- `SCAFFOLD_ADVICE_TEXT` — текст system-совета для модели (см. §6.2);
- `STEP_REQUEST_PHRASES` — триггеры явной просьбы «разбей на шаги».

Функции (сигнатуры зафиксированы и используются во всех задачах плана):

- `normalize_scaffold_payload(payload: dict) -> tuple[list[str], list[list[str]]] | None`
  — валидирует контракт §4.2; возвращает (очищенные шаги, скрытые лестницы) или
  None.
- `asked_for_steps(text: str) -> bool` — эвристика явной просьбы
  («разбей/разбейте», «по шагам», «пошагово», «декомпозиц», …) в последнем
  сообщении ученика.
- `should_scaffold(topic: dict | None, *, bandit_hard: bool = False,
  requested: bool = False) -> bool` — триггер+fade (§6.1).
- `next_ladder(current: int, level: int, n_steps: int) -> tuple[int, int] | None`
  — следующая позиция `(step, level)` или None (лестница исчерпана). level — индекс
  последней ВЫДАННОЙ подсказки в текущем шаге (−1 = ещё не выдавали).
- `format_hint_text(hint: str, step: int, level: int, n_steps: int,
  step_title: str) -> str` — текст подсказки для сообщения
  («Шаг N из M. <title> — <label>: <hint>»).

## 6. Поток в `src/api/server.py`

### 6.1. Триггер и fade (где живёт решение и почему)

Триггер декомпозиции — три источника:

1. **Слабая тема**: `topic.attempts >= WEAK_MIN_ATTEMPTS` и
   `topic.accuracy < WEAK_ACCURACY` (mastery-строки доступны серверу,
   `store.get_topic`, `store.py:588-595`).
2. **Hard от LinUCB**: в этом ходе советник выбрал `arm == 2` (`arm_difficulty
   == "hard"`, `server.py:414-423`, `server.py:1482-1514`).
3. **Явная просьба** «разбей на шаги» — `asked_for_steps(body.message)`.

Fade: тема `mastered` (`is_mastered`: attempts>=3 и mastery>=0.8 либо
status=="mastered", `mastery.py:36-46`) → автоматический совет больше не
добавляется (поддержка «снята»). Явная просьба ученика при этом уважается
(`requested=True` побеждает fade — уважение автономии).

**Решение живёт в чистом предикате** `should_scaffold` (юнит-тестируется), а
**вызывает его сервер** и добавляет в контекст хода system-совет `SCAFFOLD_ADVICE_TEXT`
рядом с bandit-советом (`server.py:1504`). Формулировки шагов и финальное решение
«давать ли scaffold» — за моделью (советник, не диктатор; та же философия, что у
LinUCB, `server.py:420-422`). Это обосновано тем, что у модели нет доступа к
`StudentStore`, а декомпозицию качественно формулирует только генеративная модель.

### 6.2. Совет модели (вставка в ветку обычного хода)

В `else`-ветке обычного хода (`server.py:1472-1514`), после bandit-блока, до
`run_agent` (`server.py:1562`):

```python
    # Scaffolding (Этап 5): совет модели про пошаговую поддержку (не диктат).
    if (
        not hint_request
        and store is not None
        and student_id
        and body.topic.strip()
        and session.bandit_arm is None
    ):
        try:
            from src.student.scaffold import (
                SCAFFOLD_ADVICE_TEXT,
                asked_for_steps,
                should_scaffold,
            )

            tp = store.get_topic(student_id, body.topic) or {}
            hard = session_bandit is not None and int(session_bandit[0]) == 2
            requested = asked_for_steps(body.message)
            if should_scaffold(tp, bandit_hard=hard, requested=requested):
                context.append({"role": "system", "content": SCAFFOLD_ADVICE_TEXT})
                JsonlLogger(settings.log_file).log(
                    "", "INFO", "scaffold.advice",
                    topic=body.topic, bandit_hard=hard, requested=requested,
                )
        except Exception as exc:  # noqa: BLE001 — совет не должен ронять чат
            print(f"[scaffold] совет не сформирован: {exc}")
```

`SCAFFOLD_ADVICE_TEXT` (≈ короткая версия схемы, полная схема — в системном
промпте §7):

```
[Поддержка: тема даётся ученику трудно. Если сейчас уместна практика — можешь
сопроводить её пошаговой поддержкой: payload.scaffold_steps (2–4 чекпоинта) и
payload._scaffold_hints (по 3 подсказки на шаг: направление → конкретнее →
почти решение). Решение за тобой.]
```

### 6.3. Установка scaffold в сессию (после `run_agent`, до санитизации)

Точка вставки — после фиксации bandit-руки (`server.py:1649-1655`), ДО
`safe_envs = [sanitize_envelope(...)]` (`server.py:1656`). Устанавливается по
ПОСЛЕДНЕМУ конверту хода (`envelope = envelopes[-1]`, `server.py:1645`):

```python
    # Scaffolding (Этап 5): практика с пошаговой поддержкой. Скрытую лестницу
    # (_scaffold_hints) храним в сессии; из конверта/истории её вычистит sanitize.
    if (
        envelope is not None
        and envelope.type.value == "practice"
        and not hint_request
        and not review_mode
    ):
        if _install_scaffold(session, envelope):
            JsonlLogger(settings.log_file).log(
                trace_id, "INFO", "scaffold.install",
                topic=body.topic, steps=len(session.scaffold_steps),
            )
        elif session.scaffold_steps:
            _clear_scaffold(session)
    elif not hint_request and session.scaffold_steps:
        _clear_scaffold(session)
```

`_install_scaffold(session, envelope) -> bool`: вызывает
`normalize_scaffold_payload(envelope.payload)`; при None удаляет
`scaffold_steps`/`_scaffold_hints` из payload (обычная практика, лог
`scaffold.reject`) и возвращает False; при успехе заполняет
`session.scaffold_task/steps/hints/current/level` и возвращает True.

### 6.4. Обслуживание `hint_request` (детерминированная лестница)

Ветка `if hint_request:` (`server.py:1442`) — ВСТАВКА ДО существующего guard на
`_MAX_HINTS_IN_A_ROW` (scaffold расширяет кап своей лестницей; старый кап=2
остаётся для обычных задач). Реализация — модульные хелперы `_clear_scaffold`,
`_install_scaffold`, `_scaffold_anchored`, `_scaffold_ladder_reply` (возвращает
ранний ответ в стиле guard); точный код — в плане (Task 4); семантика:

1. `session.scaffold_steps` непуст и `_scaffold_anchored(...)` (последнее
   assistant-сообщение, игнорируя `hint`/`theory`, — `practice` с текстом,
   равным `session.scaffold_task`) → лестница активна.
2. `pos = next_ladder(scaffold_current, scaffold_level, len(steps))`.
3. `pos is None` → лестница исчерпана: сообщение-отказ
   `_SCAFFOLD_EXHAUSTED_TEXT` («…соберите решение по шагам и ответьте»),
   конверт `hint`, ранний возврат в стиле guard (`server.py:1449-1468`).
4. Иначе `scaffold_current, scaffold_level = pos`;
   `text = format_hint_text(hints[step][level], step, level, n, title)`;
   конверт `hint`, append assistant-сообщения с meta `kind=hint`, JSONL
   `scaffold.hint`, ранний возврат.

Модель в этих ходах НЕ вызывается (лестница не перегенерируется).

### 6.5. Сброс (lifecycle) и связь с оценкой

Состояние сбрасывается (`_clear_scaffold`):
1. в начале каждого обычного хода `kind=message` (`else`-ветка, до
   `_append_user_if_new`, `server.py:1472-1474`) — т.е. при ответе ученика,
   смене темы или уходе в сторону;
2. если после `run_agent` последний конверт — `practice` БЕЗ scaffold или
   иной тип (§6.3 `elif`).

Оценка ответа на scaffold-практику идёт БЕЗ изменений: `evaluation` → record →
`store.apply_result` (mastery), LinUCB `bandit.update`, SM-2 review-карточка (для
открытых практик `correct_answer` пуст → карточка не создаётся — это
существующее поведение практик, `server.py:1760-1781`), журнал, Wiki
(`server.py:1682-1815`). К моменту обработки `evaluation` scaffold уже сброшен
п.1 — двойная очистка безопасна.

Поля `ChatSession` (dataclass `src/api/session_store.py:23-48`, в конец группы
после `bandit_features`):

```python
    # Scaffolding subtasks (Этап 5): состояние активной лестницы подсказок.
    scaffold_task: str = ""      # текст scaffold-практики (якорь проверки)
    scaffold_steps: list = field(default_factory=list)   # list[str] чек-лист
    scaffold_hints: list = field(default_factory=list)   # list[list[str]] (скрыто)
    scaffold_current: int = 0    # текущий шаг лестницы
    scaffold_level: int = -1     # последняя выданная подсказка шага (-1 = нет)
```

Активность scaffold = `bool(scaffold_steps)` (и параллельность
`scaffold_hints`). Значения живут только в памяти (время жизни сессии, TTL
`SessionStore`, `session_store.py:79-82`); в SQLite ничего нового не пишется.

## 7. Интеграция с промптами (`src/agent/prompts.py`)

Расширить системный промпт (`prompts.py:5-49`). В блок типов, в буллет `practice`
(сейчас `prompts.py:30`), добавить описание scaffold-payload:

```
- practice — задача для ученика; payload: {"task_ref": "..."}.
  Пошаговая поддержка (scaffolding) для трудной темы/сложной задачи/просьбы
  «разбей на шаги»: верни practice с payload:
  {"scaffold_steps": ["<чекпоинт 1>", ...],          // 2–4 шага в порядке решения
   "_scaffold_hints": [["<направление>", "<конкретнее>", "<почти решение>"], ...]}
  — по ровно 3 строки на каждый шаг, от общего направления к почти готовому
  решению. scaffold_steps ученик видит сразу (чек-лист), _scaffold_hints сервер
  отдаёт по одной на кнопку «Подсказка». В text — ТОЛЬКО условие задачи; не
  дублируй шаги/подсказки/ответ в text и в остальные поля payload.
```

Ограничение: существующие sanity-тесты промпта проверяют только наличие маркеров
(`tests/test_envelope.py:273-280`), поэтому добавление текста безопасно; в план
включён тест на появление маркеров `scaffold_steps`/`_scaffold_hints`.

## 8. UI (`frontend/`)

Решение MVP — **без новых блоков** (вариант «лёгкий» из постановки):

- `PracticeBlock.jsx` (сейчас: label + текст + кнопка «Подсказка»): при
  непустом `Array.isArray(payload.scaffold_steps)` рендерить нумерованный
  чек-лист `<ol className="scaffold-steps">` под текстом задачи и добавить класс
  `scaffold` к блоку (label «Задача (по шагам)»). Кнопка «Подсказка»
  (`onSend('', 'hint_request')`) остаётся — каждая подсказка приходит отдельным
  сообщением.
- `Chat.jsx` НЕ меняется: диспатч `practice` → `PracticeBlock` уже есть
  (`Chat.jsx:58`), детерминированные подсказки — обычные `hint`-конверты
  (`HintBlock.jsx`).
- `frontend/src/index.css`: стили `.block.practice.scaffold` и `.scaffold-steps`
  рядом с блоком стилей контента (`index.css:398-475`), используя токены
  `--green`, `--ink-soft`, `--line` (`index.css:7-29`).
- Тесты: `blocks.test.jsx` (рендер чек-листа, label, кнопка «Подсказка» не
  ломается).

## 9. Конфигурация

Новых env-переменных НЕТ (в отличие от LinUCB, где был kill-switch): константы
уровней/порогов — module-level в `src/student/scaffold.py`. При необходимости
полный kill-switch — будущее (§13).

## 10. Наблюдаемость

JSONL (`logs/agent.jsonl`, тот же `JsonlLogger`):
- `scaffold.advice` — {topic, bandit_hard, requested} (сработал совет модели);
- `scaffold.install` — {topic, steps};
- `scaffold.reject` — {reason} (невалидный scaffold-payload → обычная практика);
- `scaffold.hint` — {session_id, step, level, steps};
- (лестница исчерпана видна как повторные `scaffold.hint` без level-подъёма —
  отдельное событие не требуется).

## 11. Известные ограничения (фиксируются в MVP)

- Scaffold-состояние — in-memory (`ChatSession`, TTL); после истечения сессии
  скрытая лестница теряется. В истории/при перезагрузке остаётся только видимый
  чек-лист (`scaffold_steps` в payload сообщения) — продолжить лестницу после
  потери сессии нельзя.
- Нет UI «шаг выполнен»/возврата назад: лестница идёт лексикографически
  (3 уровня шага N, затем шаг N+1). Ученик, застрявший на шаге 3, получит
  подсказки шага 1, пока не «пройдёт» его лестницу — упрощение; полноценный
  интерактивный пошаговый режим (как `subtask_node` reference) — вне MVP.
- Упреждающая поддержка vs reference-реакция: ретрай-цикл «неверно → та же
  задача с подсказками» не реализуется (см. §13).
- Промежуточный вопрос/смена темы (любой `kind=message`) сбрасывает лестницу;
  «повтори условие» в середине лестницы невозможен без потери прогресса.
- Scaffold-практика оценивается как целое; отдельные шаги в mastery/SM-2 не
  учитываются (нет «кредита за шаги»).
- Модель может выдать scaffold и без совета сервера (финальное решение за ней);
  качество «почти решение» контролируется только промптом.

## 12. MVP scope

- Один тип поддержки: `practice` + `payload.scaffold_steps` (2–4) +
  `_scaffold_hints` (3/шаг), серверная лестница, deterministic `hint_request`.
- Триггер: слабая тема / LinUCB-hard / явная просьба; совет — system-сообщение
  модели; модель авторует scaffold.
- Fade: `should_scaffold` возвращает False для `mastered` тем (кроме явной
  просьбы).
- Lifecycle полей сессии (§6.5), очистка при ответе/новой задаче.
- Оценка/mastery/SM-2/LinUCB после scaffold-ответа — без изменений.
- UI: чек-лист в `PracticeBlock` + стили + тесты.
- Тесты: чистые юниты `scaffold.py`; интеграция через HTTP с фейковым рантаймом
  (установка, детерминированные подсказки без LLM, исчерпание, сброс, отсутствие
  утечки `_scaffold_hints`); frontend-рендер чек-листа.

## 13. Future / out-of-scope

- Интерактивный пошаговый режим: ответ на каждый шаг, продвижение и переспрос
  исходной задачи (аналог `subtask_node`, `graph.py:2590-2646`).
- Retry-цикл «неверный ответ на задачу → включение лестницы на той же задаче»
  (аналог `evaluation.py:296-343`): потребует отдельной серверной
  задачи-«открытого задания» с состоянием попыток.
- UI «шаг готов / вернуться к шагу»; прогресс-бар лестницы; кнопка «ещё
  подсказка» на самой подсказке.
- Персистентность scaffold-состояния (SQLite-колонка, переживание сессий/TTL),
  kill-switch env `TUTOR_SCAFFOLD_ENABLED`.
- Декомпозиция через отдельный дешёвый LLM-вызов (а не в payload практики);
  правило «лестница обрывается при успешном ответе» в mastery-метриках.
