# Quiz.reject-цикл — восстановление вместо тупика — план реализации

> Статус: выполнено (2026-09-06). Tasks 1–5 закрыты; backend 401 pytest PASS, ruff чист.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Разорвать бесконечный цикл «ученик просит квиз → модель выдаёт
невалидный quiz → guard отклоняет → canned-текст «напишите "другой вопрос"» → всё
повторяется». Сервер получает причины отклонения, делает ОДНУ тихую
регенерацию quiz в том же ходе, а после двух неудач подряд переводит сессию в
режим «без quiz» (блок) до первого корректного хода. Ученик всегда получает либо
рабочий quiz/practice, либо нейтральный ответ без приглашения в бесконечный
переспрос.

**Architecture:**

```
run_agent (обычный ход)
      │  envelopes
      ▼
_guard_quiz_envelopes(app, trace_id, envelopes, fallback_text, streak)
      │  (чистый список, rejected[ {reasons, text, answer_type} ])
      ▼
recovery? (не review/hint, не blocked, streak < CAP=2)
      │ да
      ▼
await _regen_quiz(runtime, context, rejected, trace_id)  → один вызов planner
      │  повторный ответ снова guard-ится
      ├─ корректен (quiz|practice) → envelopes = повторный вывод, rejected = []
      └─ не корректен/ошибка  → envelopes = прежний fallback (rejected непуст)
      ▼
blocked? rejected непуст → убрать плейсхолдеры; если ничего не осталось —
      короткая директива QUIZ_BLOCKED_TEXT (без «другой вопрос»)
      ▼
last_quiz / рука бандита / история  (без изменений, работают по итоговым envelopes)
      ▼
конец хода: quiz_reject_streak, quiz_blocked = next_reject_state(...,
      recovered=not rejected)
```

Чистая политика (тексты, инструкция, переход счётчика) живёт в
`src/agent/quiz_guard.py` (модуль остаётся без I/O); HTTP-слой (`server.py`)
только вызывает её. Состояние — два transient-поля `ChatSession`.

**Tech Stack:** Python 3.11, FastAPI (`src/api/server.py`), Pydantic
(`ContentEnvelope`, `src/models/schemas.py`), in-memory `SessionStore`
(`src/api/session_store.py`), JSONL-наблюдаемость (`JsonlLogger`,
`src/observability/logger.py`). Тесты: pytest (asyncio_mode=auto, ruff-конфиг:
`line-length=100`, select E,F,I,N,UP,B,A,SIM — из `adaptive_tutor/pyproject.toml`).
Запуск — из `adaptive_tutor/`:
`.venv/Scripts/python.exe -m pytest <file> -q` и
`.venv/Scripts/python.exe -m ruff check src tests`.

**Spec:** `docs/superpowers/specs/2026-09-06-quiz-reject-recovery-design.md`.

## Global Constraints

- **Никаких коммитов** без явной просьбы (AGENTS.md). Каждый Task завершается
  проверкой (запуск тестов + ruff), а не коммитом.
- Код на каждую задачу идёт по TDD: сначала падающий тест, затем реализация.
- `_correct_answer`/options/text отклонённого quiz НЕ попадают в промпт
  регенерации и в тексты ответа (инструкция строится только из `reasons`).
- Правила quiz в `SYSTEM_PROMPT` (`prompts.py:32–46`) не редактируются.
- Recovery не трогает review-режим (`server.py:1316–1348`), hint-запросы
  (`server.py:1382–1411`), серверный грейд активного квиза
  (`server.py:1465–1488`) и LinUCB (фиксация руки — только за выживший
  quiz/practice, `server.py:1531–1537`).
- Новых Settings нет: `QUIZ_REJECT_CAP = 2` — модульная константа.
- Фронтенд/SSE/JSON-форматы не меняются.

---

### Task 1: Чистые хелперы recovery-политики + юнит-тесты

Files:
- Modify `adaptive_tutor/src/agent/quiz_guard.py` — расширить docstring модуля и
  добавить блок констант/функций в конец файла (после `quiz_problems`,
  `quiz_guard.py:137`).
- Create `adaptive_tutor/tests/test_quiz_recover.py`.

Interfaces:
- Consumes: ничего (модуль остаётся чистым: только `re`/`typing`; в
  `tests/test_quiz_recover.py` — только `src.agent.quiz_guard`).
- Produces:
  - `QUIZ_REJECT_CAP: int = 2`;
  - `QUIZ_REJECT_RETRY_TEXT`, `QUIZ_BLOCKED_TEXT`, `QUIZ_BLOCKED_NOTE: str`;
  - `quiz_reject_text(blocked: bool) -> str`;
  - `build_regen_instruction(rejected: list[dict]) -> str`;
  - `next_reject_state(streak: int, blocked: bool, *, recovered: bool)
    -> tuple[int, bool]`.

- [x] **Step 1 (TDD):** Создать `tests/test_quiz_recover.py` с падающими
  юнит-тестами на пока не существующие символы:

```python
"""Юнит-тесты чистой recovery-политики quiz (тексты, инструкция, streak)."""

from src.agent.quiz_guard import (
    QUIZ_BLOCKED_NOTE,
    QUIZ_BLOCKED_TEXT,
    QUIZ_REJECT_CAP,
    QUIZ_REJECT_RETRY_TEXT,
    build_regen_instruction,
    next_reject_state,
    quiz_reject_text,
)


def test_quiz_reject_cap_is_two():
    assert QUIZ_REJECT_CAP == 2


def test_quiz_reject_text_retry_variant():
    text = quiz_reject_text(blocked=False)
    assert text == QUIZ_REJECT_RETRY_TEXT
    # старый canned-цикл («другой вопрос») в текстах отсутствует
    assert "другой вопрос" not in text
    assert "сформулирую его заново" not in text


def test_quiz_reject_text_blocked_variant():
    text = quiz_reject_text(blocked=True)
    assert text == QUIZ_BLOCKED_TEXT
    assert "другой вопрос" not in text


def test_build_regen_instruction_uses_only_reasons():
    instruction = build_regen_instruction([
        {
            "reasons": [
                "текст не является вопросом (нет «?» и нет вопросительного слова)",
                "правильный ответ раскрыт в тексте вопроса",
            ],
            "text": "Мерой инертности тела является его масса.",
            "answer_type": "single",
        }
    ])
    assert instruction.startswith("Твой предыдущий quiz-конверт отклонён")
    assert "не является вопросом" in instruction
    assert "раскрыт в тексте вопроса" in instruction
    # текст/секрет отклонённого квиза в промпт регенерации не попадают
    assert "масса" not in instruction.lower()


def test_build_regen_instruction_dedups_and_fallback():
    one = build_regen_instruction([{"reasons": ["причина A", "причина A"]}])
    assert one.count("причина A") == 1
    empty = build_regen_instruction(
        [{"reasons": [], "text": "x", "answer_type": None}]
    )
    assert "структурная невалидность" in empty


def test_next_reject_state_resets_on_recovered():
    assert next_reject_state(streak=1, blocked=True, recovered=True) == (0, False)
    assert next_reject_state(streak=0, blocked=False, recovered=True) == (0, False)


def test_next_reject_state_increments_and_blocks_at_cap():
    # 0->1 (не блок), 1->2 (=CAP, блок), 2->3 (блок сохраняется)
    assert next_reject_state(0, False, recovered=False) == (1, False)
    assert next_reject_state(1, False, recovered=False) == (2, True)
    assert next_reject_state(2, True, recovered=False) == (3, True)


def test_blocked_note_tells_model_to_skip_quiz():
    assert "НЕ выдавай quiz" in QUIZ_BLOCKED_NOTE
```

Проверка (ожидаемо красная — символов ещё нет):
```bash
.venv/Scripts/python.exe -m pytest tests/test_quiz_recover.py -q
```

- [x] **Step 2:** Реализовать. В `src/agent/quiz_guard.py` заменить docstring
  (строки 1–10) на:

```python
"""Серверный контроль качества quiz и recovery-политика после отклонения.

Модель генерирует квиз свободным текстом, поэтому возможны вопросы-утверждения,
в которых уже назван правильный ответ («Мерой инертности тела является его
масса.» + вариант «Масса»), дубли вариантов, эталон не из options и т.п.

Проверка — чистая функция ``quiz_problems``: возвращает список причин отклонения;
пустой список означает, что квиз структурно корректен и не раскрывает ответ.
Используется в HTTP-слое (``src/api/server.py``) перед выдачей квиза ученику.

Recovery-политика (тоже чистые хелперы): guard возвращает причины, HTTP-слой
делает ОДНУ тихую регенерацию; при неудаче счётчик ``quiz_reject_streak`` растёт,
а после ``QUIZ_REJECT_CAP`` неудач подряд quiz-режим блокируется до первого
корректного хода. Переход счётчика — ``next_reject_state``; тексты замены —
``quiz_reject_text`` / ``build_regen_instruction``.
"""
```

Затем в конец файла (после `quiz_problems`, `quiz_guard.py:137`) добавить:

```python
# --- Recovery после отклонения quiz (чистая политика для HTTP-слоя) ---------

QUIZ_REJECT_CAP = 2

QUIZ_REJECT_RETRY_TEXT = (
    "Проверочный вопрос не прошёл контроль качества и отменён. "
    "Попробуйте ещё раз или попросите задание."
)

QUIZ_BLOCKED_TEXT = (
    "Проверочные вопросы в этой сессии временно отключены — несколько попыток "
    "не прошли контроль качества. Перейдём к заданиям: напишите «дай задание», "
    "и я предложу упражнение."
)

QUIZ_BLOCKED_NOTE = (
    "Проверочные вопросы (quiz) в этой сессии отклоняются контролем качества. "
    "В ЭТОМ ходе НЕ выдавай quiz — дай practice-задание или короткое "
    "theory-объяснение."
)

_QUIZ_REGEN_HEAD = (
    "Твой предыдущий quiz-конверт отклонён серверным контролем качества "
    "по причинам: {reasons}. Сформулируй ЗАНОВО ОДИН корректный quiz-конверт "
    "строго по схеме: text — это вопрос с «?» либо предложение с вопросительного "
    "слова; правильный ответ НЕ раскрывать ни в text, ни в options; "
    'answer_type "single" — ровно 4 варианта, _correct_answer — ровно один из '
    'options; answer_type "open" — без вариантов. Если не уверен в корректном '
    "quiz — верни practice-задание."
)


def quiz_reject_text(blocked: bool) -> str:
    """Текст theory-замены отклонённого quiz в зависимости от режима сессии.

    blocked=False (streak < CAP): вежливая нейтральная просьба повторить или
    попросить задание. blocked=True: короткая директива сменить формат на
    задания — без бесконечных приглашений «другой вопрос».
    """
    return QUIZ_BLOCKED_TEXT if blocked else QUIZ_REJECT_RETRY_TEXT


def build_regen_instruction(rejected: list[dict]) -> str:
    """Корректирующая инструкция для одной регенерации quiz.

    Принимает список отклонений вида {"reasons": [...], "text": ...,
    "answer_type": ...}. Использует ТОЛЬКО строки reasons: текст и ответ
    отклонённого quiz в промпт не попадают (иначе ученику раскрылся бы
    _correct_answer).
    """
    seen: list[str] = []
    for record in rejected:
        for reason in record.get("reasons") or []:
            reason = str(reason).strip()
            if reason and reason not in seen:
                seen.append(reason)
    reasons = "; ".join(seen) if seen else "структурная невалидность"
    return _QUIZ_REGEN_HEAD.format(reasons=reasons)


def next_reject_state(
    streak: int, blocked: bool, *, recovered: bool
) -> tuple[int, bool]:
    """Переход счётчика отклонений quiz по итогам хода (чистая функция).

    recovered=True — ход закончился контентом без невосстановленного отклонения
    (guard пропустил quiz, либо выдан practice/theory/evaluation): сброс в
    (0, False).
    recovered=False — ход закончился отклонённым quiz без восстановления:
    streak+1; quiz_blocked=True при streak >= QUIZ_REJECT_CAP — со следующего
    хода регенерация выключена и модели уходит QUIZ_BLOCKED_NOTE.
    """
    if recovered:
        return 0, False
    next_streak = streak + 1
    return next_streak, next_streak >= QUIZ_REJECT_CAP
```

Проверка (зелёная):
```bash
.venv/Scripts/python.exe -m pytest tests/test_quiz_recover.py -q
```

- [x] **Step 3:** Стиль.
```bash
.venv/Scripts/python.exe -m ruff check src tests
```
- [x] **Acceptance:** `tests/test_quiz_recover.py` зелёный (8 тестов); ruff чист;
  `quiz_guard.py` не импортирует `src.api` (модуль остался чистым).

### Task 2: Поля `ChatSession` + guard возвращает причины + нейтральные тексты и streak на сервере

Files:
- Modify `adaptive_tutor/src/api/session_store.py` — два поля dataclass.
- Modify `adaptive_tutor/src/api/server.py` — импорт, удаление
  `_QUIZ_REJECT_TEXT`, новая сигнатура `_guard_quiz_envelopes`, вызов в
  `_run_chat`, заметка `QUIZ_BLOCKED_NOTE` в `context`, переход состояния в
  конце хода.
- Modify `adaptive_tutor/tests/test_session_store.py` — тест дефолтов полей.
- Modify `adaptive_tutor/tests/test_quiz_guard.py` — import констант + API-тест
  роста streak/нейтральных текстов.

Interfaces:
- Consumes: из `src.agent.quiz_guard`: `quiz_reject_text`, `QUIZ_BLOCKED_NOTE`,
  `next_reject_state`, `quiz_problems`.
- Produces: `_guard_quiz_envelopes(app, trace_id, envelopes, fallback_text,
  streak=None) -> tuple[list[ContentEnvelope], list[dict]]`; запись отклонения
  `{"reasons": list[str], "text": str, "answer_type": str|None}`; JSONL
  `quiz.reject` с полем `streak`; поля `ChatSession.quiz_reject_streak` /
  `quiz_blocked`.

- [x] **Step 1 (TDD):** В `tests/test_session_store.py` (в конец файла) добавить:

```python
def test_chat_session_quiz_reject_defaults() -> None:
    """ChatSession по умолчанию не имеет неудач quiz и не заблокирована."""
    session = SessionStore().create()
    assert session.quiz_reject_streak == 0
    assert session.quiz_blocked is False
```

В `tests/test_quiz_guard.py` обновить импорт (строка 10) на:

```python
from src.agent.quiz_guard import (
    QUIZ_BLOCKED_TEXT,
    QUIZ_REJECT_RETRY_TEXT,
    leak_reasons,
    quiz_problems,
    structural_issues,
)
```

и в конец файла добавить помощника и тест (многоходовой, один `TestClient`):

```python
def _post_msg(client, message, session="ses_q"):
    resp = client.post(
        "/chat",
        json={
            "message": message,
            "session_id": session,
            "student_id": "stu_q",
            "topic": "инерция",
            "subject": "физика",
        },
    )
    assert resp.status_code == 200
    return resp.json()


def test_reject_streak_increments_and_text_is_neutral(monkeypatch, tmp_path):
    """Отклонённый quiz: streak растёт, тексты нейтральные, без loop-фразы."""
    monkeypatch.setattr(settings, "log_file", str(tmp_path / "tutor.jsonl"))
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(runtime_factory=lambda: _runtime(leaky=True), student_store=store)
    app.state.rag_engine = None
    app.state.provisioner = None
    with TestClient(app) as c:
        body1 = _post_msg(c, "давай квиз")
        session = c.app.state.sessions.get("ses_q")
        assert session.quiz_reject_streak == 1
        assert session.quiz_blocked is False
        assert body1["reply"] == QUIZ_REJECT_RETRY_TEXT

        body2 = _post_msg(c, "другой вопрос")
        session = c.app.state.sessions.get("ses_q")
        assert session.quiz_reject_streak == 2
        assert session.quiz_blocked is True
        assert body2["reply"] == QUIZ_REJECT_RETRY_TEXT

        body3 = _post_msg(c, "другой вопрос")
        session = c.app.state.sessions.get("ses_q")
        assert session.quiz_reject_streak == 3
        assert session.quiz_blocked is True
        assert body3["reply"] == QUIZ_BLOCKED_TEXT
        assert "другой вопрос" not in body3["reply"]
        assert "сформулирую его заново" not in body3["reply"]
    store.close()
```

Проверка (красная — поля/констант/поведения ещё нет):
```bash
.venv/Scripts/python.exe -m pytest tests/test_session_store.py tests/test_quiz_guard.py -q
```

- [x] **Step 2:** В `src/api/session_store.py`, в dataclass `ChatSession`
  (`session_store.py:23–46`), после `bandit_features` (строка 44) добавить два
  поля:

```python
    quiz_reject_streak: int = 0
    quiz_blocked: bool = False
```

- [x] **Step 3:** В `src/api/server.py`:
  - после строки `from ..agent.loop import AgentRuntime, run_agent` (строка 32)
    добавить импорт:

```python
from ..agent.quiz_guard import (
    QUIZ_BLOCKED_NOTE,
    next_reject_state,
    quiz_problems,
    quiz_reject_text,
)
```

  - удалить константу `_QUIZ_REJECT_TEXT` (строки 247–250);
  - внутри `_guard_quiz_envelopes` удалить локальный импорт
    `from ..agent.quiz_guard import quiz_problems` (строка 263) — теперь он на
    уровне модуля;
  - заменить сигнатуру и тело `_guard_quiz_envelopes` (строки 253–281) на:

```python
def _guard_quiz_envelopes(
    app: FastAPI,
    trace_id: str,
    envelopes: list[ContentEnvelope],
    fallback_text: str,
    streak: int | None = None,
) -> tuple[list[ContentEnvelope], list[dict]]:
    """Серверный контроль quiz-конвертов перед выдачей ученику.

    Квиз, в котором раскрыт ответ (или битая структура — дубли вариантов,
    эталон не из options и т.п.), заменяется нейтральным theory-сообщением с
    текстом ``fallback_text`` (выбирает вызывающий по состоянию сессии). Ученик
    не получает заведомо некачественный/негрейдуемый вопрос, а secret
    ``last_quiz`` для него не фиксируется.

    Возвращает кортеж (чистый список, список отклонений). Элемент отклонения:
    {"reasons": [...], "text": env.text[:160], "answer_type": ...}. Причины
    пишутся в JSONL ``quiz.reject`` (с числом неудач подряд ``streak``).
    """
    out: list[ContentEnvelope] = []
    rejected: list[dict] = []
    for env in envelopes:
        if env.type.value != "quiz":
            out.append(env)
            continue
        problems = quiz_problems(env)
        if not problems:
            out.append(env)
            continue
        JsonlLogger(settings.log_file).log(
            trace_id, "INFO", "quiz.reject",
            reasons=problems,
            text=(env.text or "")[:160],
            answer_type=(env.payload or {}).get("answer_type"),
            streak=streak,
        )
        rejected.append(
            {
                "reasons": list(problems),
                "text": (env.text or "")[:160],
                "answer_type": (env.payload or {}).get("answer_type"),
            }
        )
        out.append(ContentEnvelope(type="theory", text=fallback_text))
    return out, rejected
```

- [x] **Step 4:** В `_run_chat` (обычная ветка): сразу после
  `context = app.state.sessions.to_llm_context(session_id)` (строка 1414)
  добавить заметку для заблокированной сессии:

```python
        if session.quiz_blocked:
            context.append({"role": "system", "content": QUIZ_BLOCKED_NOTE})
```

- [x] **Step 5:** Заменить вызов guard в `_run_chat` (строки 1523–1526) на:

```python
    fallback_text = quiz_reject_text(blocked=session.quiz_blocked)
    envelopes, rejected = _guard_quiz_envelopes(
        app,
        trace_id,
        envelopes,
        fallback_text=fallback_text,
        streak=session.quiz_reject_streak + 1,
    )
```

- [x] **Step 6:** После цикла записи истории (после строки 1550, перед веткой
  обновления мастерства со строки 1552) добавить переход состояния (не на
  hint-ходе):

```python
    if not hint_request:
        session.quiz_reject_streak, session.quiz_blocked = next_reject_state(
            session.quiz_reject_streak,
            session.quiz_blocked,
            recovered=not rejected,
        )
```

- [x] **Step 7:** Проверка (зелёная):
```bash
.venv/Scripts/python.exe -m pytest tests/test_session_store.py tests/test_quiz_guard.py -q
.venv/Scripts/python.exe -m ruff check src tests
```
- [x] **Acceptance:** Существующие тесты guard (`test_server_rejects_leaky_quiz`,
  `test_server_keeps_valid_quiz`) зелёные; новый многоходовой тест подтверждает
  рост streak 1→2→3, блок на 2-й неудаче и нейтральные тексты без loop-фразы;
  ruff чист.

### Task 3: Регенерация quiz (один вызов planner) + серверная обвязка + JSONL `quiz.regen`

Files:
- Modify `adaptive_tutor/src/api/server.py` — расширить импорт из
  `..agent.quiz_guard`, добавить async-хелпер `_regen_quiz`, добавить блок
  recovery и блок «blocked» между guard и `envelope = envelopes[-1]`.

Interfaces:
- Consumes: `build_regen_instruction`, `QUIZ_REJECT_CAP` (из
  `..agent.quiz_guard`); `runtime.llm.chat` / `runtime.models["planner"]`;
  `parse_content_envelopes` (уже импортирован, `server.py:31`); `rejected`
  (из Task 2), `context`, `review_mode`, `hint_request`, `session`.
- Produces: `_regen_quiz(runtime: AgentRuntime, context: list[dict],
  rejected: list[dict], trace_id: str) -> list[ContentEnvelope] | None`;
  JSONL `quiz.regen` (status ok/failed/llm_error/empty/parse_error);
  замена `envelopes` на повторный корректный вывод.

- [x] **Step 1:** Расширить импорт в `server.py` (строки после
  `from ..agent.loop import AgentRuntime, run_agent`) до:

```python
from ..agent.quiz_guard import (
    QUIZ_BLOCKED_NOTE,
    QUIZ_REJECT_CAP,
    build_regen_instruction,
    next_reject_state,
    quiz_problems,
    quiz_reject_text,
)
```

- [x] **Step 2:** Сразу после `_guard_quiz_envelopes` (после возврата кортежа,
  ~строка 300) добавить async-хелпер:

```python
async def _regen_quiz(
    runtime: AgentRuntime,
    context: list[dict],
    rejected: list[dict],
    trace_id: str,
) -> list[ContentEnvelope] | None:
    """Одна тихая регенерация quiz после отклонения серверным guard.

    Один вызов planner (та же модель, без tools) с corrective-инструкцией по
    причинам отклонения. Возвращает распарсенные конверты повторного ответа
    либо None (LLM error / пусто / нераспознанный JSON) — fail-soft.
    Секрет отклонённого квиза (_correct_answer/options/text) в промпт не
    попадает: только строки reasons.
    """
    model = runtime.models.get("planner", "")
    instruction = build_regen_instruction(rejected)
    messages = [*context, {"role": "system", "content": instruction}]
    log = JsonlLogger(settings.log_file)
    try:
        resp = await runtime.llm.chat(
            messages=messages,
            model=model,
            temperature=0.4,
            max_tokens=settings.llm_max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft
        log.log(trace_id, "WARNING", "quiz.regen", status="llm_error",
                error=str(exc)[:300])
        return None
    if runtime.budget and resp.usage:
        runtime.budget.record(resp.cost_usd)
    reply = (resp.content or "").strip()
    if not reply:
        log.log(trace_id, "WARNING", "quiz.regen", status="empty")
        return None
    parsed = parse_content_envelopes(reply)
    if not parsed:
        log.log(trace_id, "WARNING", "quiz.regen", status="parse_error")
        return None
    return parsed
```

- [x] **Step 3:** В `_run_chat` сразу после нового вызова guard (Step 5 из
  Task 2) и ДО строки `envelope = envelopes[-1] if envelopes else None`
  (строка 1527) вставить блок recovery и блок blocked:

```python
    if (
        rejected
        and not review_mode
        and not hint_request
        and not session.quiz_blocked
        and session.quiz_reject_streak < QUIZ_REJECT_CAP
    ):
        regen_envs = await _regen_quiz(runtime, context, rejected, trace_id)
        if regen_envs:
            regen_out, regen_rejected = _guard_quiz_envelopes(
                app, trace_id, regen_envs,
                fallback_text=fallback_text,
                streak=session.quiz_reject_streak + 1,
            )
            if not regen_rejected and any(
                env.type.value in {"quiz", "practice"} for env in regen_out
            ):
                envelopes = regen_out
                rejected = []
                JsonlLogger(settings.log_file).log(
                    trace_id, "INFO", "quiz.regen", status="ok",
                    type=(
                        "quiz" if any(
                            env.type.value == "quiz" for env in regen_out
                        ) else "practice"
                    ),
                    streak=session.quiz_reject_streak + 1,
                )
            else:
                JsonlLogger(settings.log_file).log(
                    trace_id, "INFO", "quiz.regen", status="failed",
                    streak=session.quiz_reject_streak + 1,
                )

    if session.quiz_blocked and rejected:
        others = [env for env in envelopes if env.text != fallback_text]
        envelopes = others if others else [
            ContentEnvelope(type="theory", text=fallback_text)
        ]
```

- [x] **Step 4:** Полный прогон затрагиваемых файлов (регрессии нет — старые
  тесты guard не проверяют число LLM-вызовов и не содержат старый loop-текст):
```bash
.venv/Scripts/python.exe -m pytest tests/test_quiz_recover.py tests/test_session_store.py tests/test_quiz_guard.py tests/test_api.py -q
.venv/Scripts/python.exe -m ruff check src tests
```
- [x] **Acceptance:** Все перечисленные файлы зелёные; ruff чист; новых событий
  `quiz.regen` не возникает в happy-path (валидный quiz guard пропускает), блок
  recovery не срабатывает на review/hint-ходах.

### Task 4: API-интеграционные тесты восстановления (test_api.py)

Files:
- Modify `adaptive_tutor/tests/test_api.py` — import констант, фейк
  `QueueQuizLLM`, помощники, четыре сценария (a)–(d).

Interfaces:
- Consumes: `/chat` endpoint; `_BAD_QUIZ`/`_GOOD_QUIZ`/`_PRACTICE` (JSON-строки);
  `QueueQuizLLM` (счётчик вызовов + `last_messages`); поля сессии из Task 2;
  `quiz.regen`/`quiz.reject` из JSONL (логгер через `settings.log_file`).
- Produces: тесты сценариев регенерации, streak/блока и happy-path.

- [x] **Step 1 (TDD):** В `tests/test_api.py` после строки
  `from src.agent.loop import AgentRuntime` добавить импорт:

```python
from src.agent.quiz_guard import QUIZ_BLOCKED_TEXT, QUIZ_REJECT_RETRY_TEXT
```

(порядок `..critic` < `..loop` < `..quiz_guard` < `..tools` — для isort).

В конец файла добавить константы-конверты, фейк и помощников:

```python
# --- Quiz.reject: восстановление вместо тупика (2026-09-06) -----------------

_BAD_QUIZ = (
    '{"type": "quiz", "text": "Мерой инертности тела является его масса.", '
    '"payload": {"answer_type": "single", "options": ["Сила", "Масса", "Энергия", '
    '"Импульс"], "_correct_answer": "Масса"}, "difficulty": "medium"}'
)
_GOOD_QUIZ = (
    '{"type": "quiz", "text": "Что является мерой инертности тела?", '
    '"payload": {"answer_type": "single", "options": ["Сила", "Масса", "Энергия", '
    '"Импульс"], "_correct_answer": "Масса"}, "difficulty": "medium"}'
)
_PRACTICE = (
    '{"type": "practice", "text": "Приведите пример тела, сохраняющего скорость.", '
    '"payload": {"task_ref": "daily"}, "difficulty": "medium"}'
)


class QueueQuizLLM(LLMClient):
    """Планировщик с очередью ответов; при переполнении повторяет последний."""

    def __init__(self, *contents: str):
        self.queue = list(contents)
        self.calls = 0
        self.last_messages: list[dict] = []

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024,
        tools=None, tool_choice=None
    ):
        self.calls += 1
        self.last_messages = list(messages)
        content = self.queue[min(self.calls - 1, len(self.queue) - 1)]
        return LLMResponse(
            content=content,
            model=model,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


def _recovery_app(tmp_path, fake, monkeypatch):
    """FastAPI app на QueueQuizLLM (без критика) с JSONL во временный файл."""
    monkeypatch.setattr(settings, "log_file", str(tmp_path / "tutor.jsonl"))
    app = create_app(
        runtime_factory=lambda: AgentRuntime(
            llm=fake,
            models={"planner": "test", "fast": "test", "judge": "test"},
            tool_context=ToolContext(region="GLOBAL"),
            critic=None,
        ),
        student_store=StudentStore(str(tmp_path / "students.db")),
    )
    app.state.rag_engine = None
    app.state.provisioner = None
    return app


def _recover_post(client, message, session="recover-s1", student="stu_rec",
                  topic="инерция"):
    resp = client.post("/chat", json={
        "message": message, "session_id": session, "student_id": student,
        "topic": topic, "subject": "физика",
    })
    assert resp.status_code == 200
    return resp.json()
```

Затем сами сценарии:

```python
def test_quiz_rejected_then_regenerated_once(tmp_path, monkeypatch):
    """Невалидный quiz -> ровно одна регенерация; ученик получает валидный quiz."""
    fake = QueueQuizLLM(_BAD_QUIZ, _GOOD_QUIZ)
    app = _recovery_app(tmp_path, fake, monkeypatch)
    with TestClient(app) as c:
        body = _recover_post(c, "давай квиз")
        session = c.app.state.sessions.get("recover-s1")
        log = (tmp_path / "tutor.jsonl").read_text(encoding="utf-8")
    assert body["envelope"]["type"] == "quiz"
    assert "Что является мерой инертности тела?" in body["reply"]
    assert "контроль качества" not in body["reply"]
    assert fake.calls == 2  # 1 planner + ровно 1 регенерация
    joined = "\n".join(m.get("content", "") for m in fake.last_messages)
    assert "Твой предыдущий quiz-конверт отклонён" in joined
    assert session.last_quiz is not None
    assert session.quiz_reject_streak == 0
    assert session.quiz_blocked is False
    assert log.count('"event": "quiz.reject"') == 1
    regen_lines = [ln for ln in log.splitlines() if '"quiz.regen"' in ln]
    assert len(regen_lines) == 1 and '"status": "ok"' in regen_lines[0]


def test_quiz_regen_failure_shows_neutral_fallback_and_streak(tmp_path, monkeypatch):
    """Регенерация тоже невалидна -> нейтральный retry-текст, streak=1."""
    fake = QueueQuizLLM(_BAD_QUIZ, _BAD_QUIZ)
    app = _recovery_app(tmp_path, fake, monkeypatch)
    with TestClient(app) as c:
        body = _recover_post(c, "давай квиз")
        session = c.app.state.sessions.get("recover-s1")
        log = (tmp_path / "tutor.jsonl").read_text(encoding="utf-8")
    assert body["envelope"]["type"] == "theory"
    assert body["reply"] == QUIZ_REJECT_RETRY_TEXT
    assert "другой вопрос" not in body["reply"]
    assert "сформулирую его заново" not in body["reply"]
    assert fake.calls == 2
    assert session.quiz_reject_streak == 1
    assert session.quiz_blocked is False
    assert log.count('"event": "quiz.reject"') == 2  # оригинал + регенерация
    regen_lines = [ln for ln in log.splitlines() if '"quiz.regen"' in ln]
    assert len(regen_lines) == 1 and '"status": "failed"' in regen_lines[0]


def test_quiz_blocked_after_two_failures_and_practice_unblocks(
    tmp_path, monkeypatch
):
    """Две неудачи -> блок; 3-й quiz не выдаётся; корректная practice снимает блок."""
    fake = QueueQuizLLM(
        _BAD_QUIZ, _BAD_QUIZ,  # ход 1: planner + регенерация
        _BAD_QUIZ, _BAD_QUIZ,  # ход 2: planner + регенерация
        _BAD_QUIZ,             # ход 3: только planner (блок, без регенерации)
        _PRACTICE,             # ход 4: practice снимает блок
    )
    app = _recovery_app(tmp_path, fake, monkeypatch)
    with TestClient(app) as c:
        body1 = _recover_post(c, "давай квиз")
        session = c.app.state.sessions.get("recover-s1")
        assert session.quiz_reject_streak == 1
        assert session.quiz_blocked is False
        assert body1["reply"] == QUIZ_REJECT_RETRY_TEXT

        body2 = _recover_post(c, "другой вопрос")
        session = c.app.state.sessions.get("recover-s1")
        assert session.quiz_reject_streak == 2
        assert session.quiz_blocked is True
        assert body2["reply"] == QUIZ_REJECT_RETRY_TEXT

        body3 = _recover_post(c, "другой вопрос")
        session = c.app.state.sessions.get("recover-s1")
        assert session.quiz_reject_streak == 3
        assert session.quiz_blocked is True
        assert body3["envelope"]["type"] == "theory"
        assert body3["reply"] == QUIZ_BLOCKED_TEXT
        assert "другой вопрос" not in body3["reply"]
        joined = "\n".join(m.get("content", "") for m in fake.last_messages)
        assert "НЕ выдавай quiz" in joined

        body4 = _recover_post(c, "дай задание")
        session = c.app.state.sessions.get("recover-s1")
        assert body4["envelope"]["type"] == "practice"
        assert session.quiz_reject_streak == 0
        assert session.quiz_blocked is False
        assert fake.calls == 6
    log = (tmp_path / "tutor.jsonl").read_text(encoding="utf-8")
    assert log.count('"event": "quiz.regen"') == 2  # только ходы 1 и 2


def test_valid_quiz_happy_path_unchanged(tmp_path, monkeypatch):
    """Валидный quiz: без регенерации, last_quiz выставлен, streak не растёт."""
    fake = QueueQuizLLM(_GOOD_QUIZ)
    app = _recovery_app(tmp_path, fake, monkeypatch)
    with TestClient(app) as c:
        body = _recover_post(c, "дай задание", session="recover-d1",
                             student="stu_rec", topic="инерция")
        session = c.app.state.sessions.get("recover-d1")
        log = (tmp_path / "tutor.jsonl").read_text(encoding="utf-8")
    assert body["envelope"]["type"] == "quiz"
    assert fake.calls == 1
    assert session.last_quiz is not None
    assert session.quiz_reject_streak == 0
    assert session.quiz_blocked is False
    assert '"event": "quiz.regen"' not in log
```

Проверка (красная до реализации Task 3, зелёная после):
```bash
.venv/Scripts/python.exe -m pytest tests/test_api.py -q
```
- [x] **Step 2:** Стиль.
```bash
.venv/Scripts/python.exe -m ruff check src tests
```
- [x] **Acceptance:** Четыре сценария зелёные: (a) 1 регенерация и валидный quiz
  без canned-текста; (b) retry-текст и streak=1; (c) блок после CAP и снятие
  блока практикой (без третьей регенерации, без loop-фразы); (d) happy-path без
  изменений (вызовов planner ровно 1, `last_quiz` выставлен).

### Task 5: Полный прогон + ruff + live-smoke (опционально)

Files:
- Modify: нет (только проверка).
- Test: весь backend `adaptive_tutor/tests`.

- [x] **Step 1:** Полный набор из `adaptive_tutor/`:
```bash
.venv/Scripts/python.exe -m pytest tests -q
.venv/Scripts/python.exe -m ruff check src tests
```
Ожидание: 387 существующих + новые (Task 1: 8; Task 2: +2; Task 4: +4) PASS;
ruff чист.
- [x] **Step 2 (live-smoke, опционально, требует ключи в `.env`):**
  `python run.py --frontend`, реальная сессия: «давай квиз» → если модель даёт
  невалидный quiz, в `logs/agent.jsonl` видны `quiz.reject` + `quiz.regen`;
  после двух неудач подряд — блок (заметка модели «НЕ выдавай quiz»), после
  запроса «дай задание» — practice. Цикл «другой вопрос» прекращается.
- [x] **Acceptance:** Весь backend-набор зелёный; ruff чист; live-smoke (если
  выполнялся) подтверждает отсутствие тупикового цикла в журнале.

---

**Что осталось** (после завершения Tasks 1–5): прогнать live-smoke с настоящим
LLM (первый шаг следующей сессии по AGENTS.md — реальные ключи, `run.py
--frontend`), затем «Этап 5+» из gap-аудита. Закоммитить изменения — только по
явной просьбе.

---

## Бэклог (низкий приоритет)

- [ ] **LLM-самопроверка уникальности ответа карточки квиза** (обнаружено на
  live-smoke 2026-09-07). Guard ловит дубли/утечку ответа, но не семантическую
  «двухответность»: вопрос «какой элемент — второстепенный член» с вариантами
  «Вечером» и «в парк» (оба — обстоятельства) допускает два верных ответа, а
  грейдер считает верным только `_correct_answer`. Детерминированно не отсечь.
  Идея: после генерации карточки (`generate_quiz`, `tools.py`) короткий
  LLM-чекаут (та же fast-модель) возвращает `{ok, correct_index}` — единственный
  верный вариант и заведомо неверные дистракторы; при `ok=false` — одна тихая
  перегенерация с причиной. Стоимость: +1 вызов (~768 токенов, ~$0.0001) и
  +3–8 с к каждому single-квизу. Промпт `_QUIZ_PROMPT` уже ужесточён
  (ровно один верный ответ) — это снижает частоту, но не гарантирует. Опция:
  включить за флагом `TUTOR_*` (например, `quiz_selfcheck_enabled`).
