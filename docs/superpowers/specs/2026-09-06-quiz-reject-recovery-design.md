# Quiz.reject: восстановление вместо тупика — дизайн (дефект-фикс)

Дата: 2026-09-06. Воркспейс: `C:\otus\edututor`. Тип: дефект-фикс бэкенда
(восстановление после отклонения quiz серверным контролем качества). Коммит не
выполняется без явной просьбы (AGENTS.md).

## 1. Цель и место в продукте

Реальная русскоязычная сессия: ученик просит квиз («давай квиз», затем «другой
вопрос»). LLM выдаёт quiz-конверт, который **серверный guard отклоняет**
(`src/api/server.py:_guard_quiz_envelopes`, ~253–281), а сервер отвечает canned-
текстом:

> «Проверочный вопрос не прошёл контроль качества и отменён. Напишите «дай
> задание» или «другой вопрос» — я сформулирую его заново.»

(`_QUIZ_REJECT_TEXT`, `server.py:247–250`). Ученик повторяет «другой вопрос»,
модель снова выдаёт невалидный quiz, guard снова отклоняет → **бесконечный цикл
без выхода**. Воспроизведено вживую: 2 подряд `quiz.reject` в одной сессии.

Проблема не в правилах системного промпта (они верны, `prompts.py:32–46`), а в
отсутствии у сервера:
1. **причин** отклонения для модели (canned-текст — лишь запись в истории, модель
   не знает, что именно не так);
2. **ограниченной попытки восстановления** (модель не переспрашивается в этом же
   ходе);
3. **предохранителя цикла** (счётчик неудач подряд и блокировка quiz-режима).

## 2. Root-cause

- `quiz_problems` (`src/agent/quiz_guard.py:120–137`) находит причины, но
  `_guard_quiz_envelopes` их **только логирует** в JSONL `quiz.reject` и заменяет
  quiz на theory с текстом `_QUIZ_REJECT_TEXT` — причины не возвращаются, модель
  их не видит.
- Единственный вызов guard — `server.py:1526` (после `run_agent`). После замены
  ход завершается: `session.last_quiz` для «убитого» quiz не фиксируется, рука
  бандита не засчитывается (`server.py:1527–1537`), в историю уходит canned-текст
  (`server.py:1538–1550`).
- Отказ **приглашает продолжать**: canned-текст явно просит снова написать
  «другой вопрос» → следующий ход снова `run_agent` → снова невалидный quiz →
  снова отказ. Нет ни регенерации в этом ходе, ни предела попыток, ни смены
  формата.

## 3. Решения (согласовано)

Паттерн повторяет фикс «оборванного ответа» (`docs/superpowers/plans/2026-09-05
-fix-truncated-apology-defect.md`): **логируем → ОДИН ограниченный retry →
смягчаем фолбэк → только потом сдаёмся.**

1. **Причины наружу.** `_guard_quiz_envelopes` возвращает не только очищенный
   список конвертов, но и список отклонений
   `[{"reasons": [...], "text": env.text[:160], "answer_type": ...}]`.
   JSONL `quiz.reject` сохраняется и дополняется полем `streak`.
2. **Одна тихая регенерация** в обычном (не review/hint) ходе, когда отклонение
   есть и разрешено (лимит не исчерпан, не блок): ровно **один** дополнительный
   вызов planner (та же модель `runtime.models["planner"]`, без tools) с
   `context` + corrective system-сообщением. Повторный ответ guard-ится; если он
   содержит корректный quiz (или, по инструкции, practice) — он **заменяет**
   canned-theory, canned-текст ученику НЕ уходит, пишется JSONL `quiz.regen`.
   Ограничено 1 попыткой на ход; fail-soft при ошибке LLM.
3. **Streak + блок** для разрыва цикла: новые поля `ChatSession`
   `quiz_reject_streak` / `quiz_blocked`; переход — чистая функция
   `next_reject_state`. `_QUIZ_REJECT_TEXT` (провоцирующий «другой вопрос»)
   удаляется; вместо него — два нейтральных текста по состоянию сессии.
4. **Секрет не течёт.** Корректирующая инструкция строится только из строк
   `reasons`; текст/options/`_correct_answer` отклонённого quiz в промпт не
   попадают (иначе правильный ответ раскрылся бы ученику в следующем ходе).

## 4. Чистые хелперы recovery-политики: `src/agent/quiz_guard.py`

Модуль остаётся чистым (только `re`, `typing`; импортов `src.api` нет — он уже
импортируется HTTP-слоем, `server.py:263`). Docstring модуля расширяется: помимо
контроля качества — recovery-политика. Новый блок добавляется в конец файла
(после `quiz_problems`, `quiz_guard.py:137`).

Константы:
- `QUIZ_REJECT_CAP = 2` — модульная константа предела неудач подряд.
- `QUIZ_REJECT_RETRY_TEXT`, `QUIZ_BLOCKED_TEXT`, `QUIZ_BLOCKED_NOTE` — строки
  (точный текст в §7).
- `_QUIZ_REGEN_HEAD` — шаблон corrective-инструкции с `{reasons}`.

Функции:
- `quiz_reject_text(blocked: bool) -> str` — текст theory-замены отклонённого
  quiz в зависимости от режима сессии (retry-вариант при `blocked=False`,
  blocked-вариант при `blocked=True`).
- `build_regen_instruction(rejected: list[dict]) -> str` — corrective-инструкция
  для регенерации; берёт **только** строки `reasons` из записей отклонений,
  дедуплицирует, пустой список → «структурная невалидность».
- `next_reject_state(streak: int, blocked: bool, *, recovered: bool)
  -> tuple[int, bool]` — чистый переход счётчика по итогам хода:
  - `recovered=True` → `(0, False)` (ход доставил контент без невосстановленного
    отклонения: quiz пропущен guard, либо выдан practice/theory/evaluation);
  - `recovered=False` → `(streak + 1, (streak + 1) >= QUIZ_REJECT_CAP)`.
  Инвариант: `quiz_blocked == (quiz_reject_streak >= QUIZ_REJECT_CAP)`.
  Блок «включается со следующего хода»: на 2-й неудаче streak достигает CAP и
  `blocked=True` устанавливается в конце хода — 3-й ход уже заблокирован.

## 5. Состояние сессии: `src/api/session_store.py`

В dataclass `ChatSession` (`session_store.py:23–46`), рядом с transient-полями
bandit (`session_store.py:42–44`), добавить:

```python
    bandit_features: list = field(default_factory=list)
    quiz_reject_streak: int = 0
    quiz_blocked: bool = False
```

Поля живут только в памяти (время жизни сессии), не персистятся. Меняет их
`_run_chat` напрямую (как `session.bandit_arm`). В историю сообщений состояния
не пишется: системные заметки добавляются в `context` **per-turn**
(как сегодня совет бандита, `server.py:1440`), а не как роль истории (роли только
`user`/`assistant`, `_VALID_ROLES`, `session_store.py:20`).

## 6. Поток в `src/api/server.py`

Обычный (не review/hint) ход, ветка «после `run_agent`». Review-режим уходит
раньше (`server.py:1316–1348`), hint-ход имеет собственный служебный контекст
(`server.py:1382–1411`) — recovery их не касается. Точки правки:

**P1 (до `run_agent`, после `server.py:1414`).** В ветке `else` (обычный ход)
после `context = ...to_llm_context(...)`:

```python
        if session.quiz_blocked:
            context.append({"role": "system", "content": QUIZ_BLOCKED_NOTE})
```

Заметка видна модели в ЭТОМ ходе. Серверный грейд активного квиза
(`server.py:1465–1488`) при заблокированной сессии невозможен (блок ставится
только на ходах без выжившего quiz → `last_quiz` нет).

**P2 (guard, `server.py:1523–1526`).** Новая сигнатура
`_guard_quiz_envelopes(app, trace_id, envelopes, fallback_text, streak=None) ->
tuple[list[ContentEnvelope], list[dict]]`. Удаляется `_QUIZ_REJECT_TEXT`
(`server.py:247–250`). Замена отклонённого quiz — theory с переданным
`fallback_text`; причины копятся в `rejected`. JSONL `quiz.reject` сохраняется,
добавляется `streak`. Вызов:

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

**P3 (одна регенерация).** Между guard и `envelope = envelopes[-1]` (до
`server.py:1527`, т.е. до фиксации `last_quiz`/руки бандита):

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
                    type=("quiz" if any(
                        env.type.value == "quiz" for env in regen_out
                    ) else "practice"),
                    streak=session.quiz_reject_streak + 1,
                )
            else:
                JsonlLogger(settings.log_file).log(
                    trace_id, "INFO", "quiz.regen", status="failed",
                    streak=session.quiz_reject_streak + 1,
                )
```

Новый async-хелпер в `server.py` (после `_guard_quiz_envelopes`):

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

Сигнатура LLM-вызова совпадает с `src/agent/loop.py` `_retry_truncated`
(`loop.py:377–382`); `runtime.llm` — planner-клиент, модель —
`runtime.models.get("planner", "")` (`loop.py:117`).

**P4 (блок).** Если `session.quiz_blocked` и `rejected` непуст (модель вопреки
заметке дала quiz и guard его отклонил): плейсхолдеры-замены (текст ==
`fallback_text`) убираются, если остался другой контент; иначе остаётся одна
короткая директива `fallback_text` (НЕ старый canned-цикл):

```python
    if session.quiz_blocked and rejected:
        others = [env for env in envelopes if env.text != fallback_text]
        envelopes = others if others else [
            ContentEnvelope(type="theory", text=fallback_text)
        ]
```

**P5 (переход состояния, после записи истории `server.py:1538–1550`, до ветки
обновления мастерства `server.py:1552`), только не на hint-ходе:**

```python
    if not hint_request:
        session.quiz_reject_streak, session.quiz_blocked = next_reject_state(
            session.quiz_reject_streak,
            session.quiz_blocked,
            recovered=not rejected,
        )
```

Ход с выжившим quiz (в т.ч. после регенерации), практикой, теорией или
evaluation → `rejected == []` → сброс `(0, False)`. Невосстановленное отклонение
→ streak+1, при CAP — блок со следующего хода. Существующие шаги `last_quiz`
(`server.py:1528–1530`) и фиксации руки бандита (`server.py:1531–1537`) не
меняются и работают уже по итоговому `envelopes`.

## 7. Тексты ответа (точные формулировки и условия)

| Константа | Условие показа | Текст |
|---|---|---|
| `QUIZ_REJECT_RETRY_TEXT` | guard отклонил quiz в незаблокированном ходе (start-of-turn `quiz_blocked=False`; в т.ч. после неудачной регенерации) | «Проверочный вопрос не прошёл контроль качества и отменён. Попробуйте ещё раз или попросите задание.» |
| `QUIZ_BLOCKED_TEXT` | `session.quiz_blocked=True` (streak ≥ CAP): fallback-замена и, если нет другого контента, единственная директива хода | «Проверочные вопросы в этой сессии временно отключены — несколько попыток не прошли контроль качества. Перейдём к заданиям: напишите «дай задание», и я предложу упражнение.» |
| `QUIZ_BLOCKED_NOTE` | системная заметка в `context` каждого обычного хода при `session.quiz_blocked` | «Проверочные вопросы (quiz) в этой сессии отклоняются контролем качества. В ЭТОМ ходе НЕ выдавай quiz — дай practice-задание или короткое theory-объяснение.» |

Retry-вариант допустим на 1–2 попытке (краткое «попробуйте ещё раз или попросите
задание» — согласовано); старый canned-цикл («Напишите „дай задание“ или „другой
вопрос“ — я сформулирую его заново») полностью удаляется. Ни один текст не
содержит «другой вопрос»/«сформулирую его заново». `_correct_answer` ни в одном
тексте нет.

## 8. Наблюдаемость

`JsonlLogger.log(trace_id, level, event, **fields)` (`src/observability/logger.py:91`),
файл `logs/agent.jsonl`:

- `quiz.reject` (существующее, расширено): `reasons`, `text` (префикс ≤160),
  `answer_type`, `streak` — новое поле = значение streak, к которому привёл бы ход,
  если отклонение не восстановлено (`session.quiz_reject_streak + 1`).
- `quiz.regen` (новое): `status` ∈ {`ok`, `failed`, `llm_error`, `empty`,
  `parse_error`}; при `ok` дополнительно `type` (`quiz`|`practice`); `streak`.
  Ровно одно событие на попытку регенерации (максимум 1 на ход).

## 9. Тесты

- Юнит (новый `tests/test_quiz_recover.py`): константы и выбор текста
  (`quiz_reject_text` в обоих режимах; отсутствие старого loop-текста);
  `build_regen_instruction` (только reasons, дедуп, fallback при пустых);
  `next_reject_state` (reset при recovered; 0→1, 1→2=CAP/блок, блок сохраняется).
- `tests/test_session_store.py`: дефолты новых полей `ChatSession`.
- `tests/test_quiz_guard.py` (API-уровень, стиль `_FixedQuizLLM`/`_runtime`/
  `_post`, 131–198): тексты retry/blocked и рост streak по ходам.
- `tests/test_api.py` (API-уровень, стиль `FakeQuizLLM`/`FakeQuizThenEvalLLM`,
  510–666), новые фейки `QueueQuizLLM`:
  (a) невалидный quiz → ровно 1 регенерация, ученик получает валидный quiz,
  JSONL: `quiz.reject` + `quiz.regen` ok, canned-текста нет, `last_quiz`
  зафиксирован;
  (b) регенерация тоже невалидна → нейтральный retry-текст,
  `quiz_reject_streak == 1`, JSONL `quiz.regen` failed;
  (c) две неудачи → streak=2/CAP → 3-й ход с «другой вопрос»: третий quiz НЕ
  выдаётся, ответ = `QUIZ_BLOCKED_TEXT` (без loop-фразы), заметка «НЕ выдавай
  quiz» ушла модели, `quiz.regen` больше нет; затем practice → сброс блока;
  (d) happy-path валидный quiz не меняется: без регенерации, `last_quiz`
  выставлен, streak=0.
- Полный набор: `tests` зелёный (387 + новые), ruff чист.

## 10. Объём вне изменений

- Правила quiz в `SYSTEM_PROMPT` (`prompts.py:32–46`) не меняются (остаются
  авторитетными; corrective-инструкция лишь пересказывает два главных класса
  ошибок: не-вопросительная формулировка и раскрытие ответа в text/options).
- Никакого детерминированного авторства quiz; нет изменений review/blitz, hint,
  серверного грейда активного квиза (`server.py:1465–1488`) и LinUCB-логики
  (фиксация руки — только за выживший quiz/practice).
- Фронтенд и SSE/JSON-форматы не меняются. Новых Settings не добавляется
  (CAP — модульная константа). Коммитов нет без явной просьбы.

## 11. Что осталось вне данного этапа

- Метрика «доля успешных регенераций» и дашборд по `quiz.regen` — отдельно.
- «Следующая» регенерация поверх правил (например, явная смена answer_type при
  повторных однотипных отказах) — возможное развитие.
- Live-smoke с реальным LLM (ключи в `.env`, `python run.py --frontend`) —
  первым шагом следующей сессии по AGENTS.md.
