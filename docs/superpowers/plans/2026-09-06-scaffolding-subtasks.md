# Scaffolding subtasks: лестница подсказок — план реализации (Этап 5, optional)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Внедрить MVP-поддержку «scaffolding subtasks»: трудная `practice`
выдаётся моделью с пошаговым чек-листом (`payload.scaffold_steps`) и скрытой
лестницей подсказок (`payload._scaffold_hints`, по 3 уровня на шаг). Лестница
хранится сервером per-сессии и выдаётся по `hint_request` детерминированно (без
перегенерации моделью); при ответе/новой задаче состояние сбрасывается; при
освоении темы авто-совет декомпозировать прекращается (fade). Оценка/mastery/
SM-2/LinUCB после ответа не меняются.

**Architecture:** Чистый модуль `src/student/scaffold.py` (константы, валидация
payload, триггер+fade, продвижение лестницы) + 5 полей `ChatSession`
(in-memory) + точки в `src/api/server.py`: (1) system-совет модели перед
`run_agent`, (2) установка лестницы в сессию после `run_agent` (до санитизации),
(3) сброс при обычном ходе, (4) детерминированный `hint_request` в активном
scaffold. Промпт описывает формат payload. Фронтенд: чек-лист рендерит
существующий `PracticeBlock`, подсказки — существующий `HintBlock`.

**Tech Stack:** Python 3.11+, FastAPI, pytest, ruff; React 19 + vitest + RTL +
oxlint.

**Spec:** `docs/superpowers/specs/2026-09-06-scaffolding-subtasks-design.md`

## Global Constraints

- НОВЫЙ тип конверта НЕ вводится: reuse `practice` + payload.
- `_scaffold_hints` — скрытый underscore-ключ верхнего уровня payload; клиенту и
  в историю он не попадает (существующий `sanitize_envelope`,
  `src/agent/envelope.py:168-174`). В тестах проверяем отсутствие утечки.
- Обычный hint-кап `_MAX_HINTS_IN_A_ROW = 2` и review-режим НЕ ломаются:
  scaffold перехватывает `hint_request` ТОЛЬКО при активной и «заякоренной»
  лестнице; во всех остальных случаях поведение прежнее.
- Все обращения к scaffold fail-soft: `try/except` не должен ронять чат.
- Запуск тестов из `adaptive_tutor/`:
  `.venv/Scripts/python.exe -m pytest tests/<file> -q`; стиль:
  `.venv/Scripts/python.exe -m ruff check src tests -q`.
  Фронтенд из `frontend/`: `npm test -- <файл>` / `npm test` и `npm run lint`.
- Коммиты не делать без явной просьбы (AGENTS.md).

---

### Task 1: Чистый модуль `src/student/scaffold.py` + `tests/test_scaffold.py`

**Files:**
- Create: `src/student/scaffold.py`
- Create: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: только стандартная библиотека; `is_mastered` из `src.student.mastery`
  (ленивый импорт внутри функции, как `store.py:674`).
- Produces (используются в Task 2/4):
  - `MIN_STEPS = 2`, `MAX_STEPS = 4`, `HINTS_PER_STEP = 3`,
    `WEAK_ACCURACY = 0.6`, `WEAK_MIN_ATTEMPTS = 2`;
  - `HINT_LEVEL_LABELS = ("направление", "конкретнее", "почти решение")`;
  - `SCAFFOLD_ADVICE_TEXT` (совет для модели);
  - `STEP_REQUEST_PHRASES` (триггеры «разбей на шаги»);
  - `normalize_scaffold_payload(payload: dict) -> tuple[list[str], list[list[str]]] | None`;
  - `asked_for_steps(text: str) -> bool`;
  - `should_scaffold(topic: dict | None, *, bandit_hard: bool = False,
    requested: bool = False) -> bool`;
  - `next_ladder(current: int, level: int, n_steps: int) -> tuple[int, int] | None`;
  - `format_hint_text(hint: str, step: int, level: int, n_steps: int,
    step_title: str) -> str`.

- [ ] **Step 1: падающий тест** — создать `tests/test_scaffold.py`:

```python
"""Юнит-тесты scaffolding subtasks (модуль src/student/scaffold.py)."""

from src.student.scaffold import (
    HINTS_PER_STEP,
    MIN_STEPS,
    asked_for_steps,
    format_hint_text,
    next_ladder,
    normalize_scaffold_payload,
    should_scaffold,
)

VALID_STEPS = ["Найдите x^2", "Найдите x", "Проверьте корни"]
VALID_HINTS = [
    ["h11", "h12", "h13"],
    ["h21", "h22", "h23"],
    ["h31", "h32", "h33"],
]


def test_normalize_valid_payload():
    payload = {"scaffold_steps": VALID_STEPS, "_scaffold_hints": VALID_HINTS}
    steps, hints = normalize_scaffold_payload(payload)
    assert steps == VALID_STEPS
    assert hints == VALID_HINTS


def test_normalize_trims_and_requires_min_max_steps():
    ok = normalize_scaffold_payload({
        "scaffold_steps": ["  a  ", "b", "c"],
        "_scaffold_hints": [["1", "2", "3"]] * 3,
    })
    assert ok is not None
    assert ok[0] == ["a", "b", "c"]
    assert normalize_scaffold_payload({"scaffold_steps": ["a"], "_scaffold_hints": [["1", "2", "3"]]}) is None
    too_many = ["a%d" % i for i in range(MIN_STEPS + 4)]
    assert normalize_scaffold_payload({"scaffold_steps": too_many, "_scaffold_hints": [["1", "2", "3"]] * len(too_many)}) is None


def test_normalize_rejects_shape_mismatch():
    # разное число шагов и групп подсказок
    payload = {
        "scaffold_steps": VALID_STEPS,
        "_scaffold_hints": VALID_HINTS[:2],
    }
    assert normalize_scaffold_payload(payload) is None
    # строка лестницы не из 3 уровней
    payload = {
        "scaffold_steps": VALID_STEPS,
        "_scaffold_hints": [["1", "2"], ["3", "4", "5"], ["6", "7", "8"]],
    }
    assert normalize_scaffold_payload(payload) is None
    # пустой текст шага/подсказки
    payload = {
        "scaffold_steps": VALID_STEPS,
        "_scaffold_hints": [["", "2", "3"], ["4", "5", "6"], ["7", "8", "9"]],
    }
    assert normalize_scaffold_payload(payload) is None
    # отсутствие ключей / не списки
    assert normalize_scaffold_payload({}) is None
    assert normalize_scaffold_payload({"scaffold_steps": VALID_STEPS}) is None


def test_asked_for_steps_phrases():
    assert asked_for_steps("Разбей на шаги эту задачу")
    assert asked_for_steps("реши по шагам")
    assert asked_for_steps("подскажи план решения")
    assert not asked_for_steps("Дай пример")
    assert not asked_for_steps("")
    assert not asked_for_steps(None)


def test_should_scaffold_triggers_and_fades():
    # неизвестная тема: только hard/явная просьба
    assert should_scaffold(None) is False
    assert should_scaffold(None, bandit_hard=True) is True
    # освоенная тема выключает авто-поддержку (fade)
    mastered = {"attempts": 3, "mastery": 0.9, "status": "mastered", "accuracy": 1.0}
    assert should_scaffold(mastered, bandit_hard=True) is False
    assert should_scaffold(mastered, requested=True) is True
    # слабая тема: attempts>=2 и accuracy<0.6
    weak = {"attempts": 2, "mastery": 0.3, "status": "in_progress", "accuracy": 0.4}
    assert should_scaffold(weak) is True
    # сильная тема
    strong = {"attempts": 5, "mastery": 0.75, "status": "in_progress", "accuracy": 0.9}
    assert should_scaffold(strong) is False
    assert should_scaffold(strong, bandit_hard=True) is True


def test_next_ladder_progression_and_exhaustion():
    assert HINTS_PER_STEP == 3
    assert next_ladder(0, -1, 3) == (0, 0)
    assert next_ladder(0, 0, 3) == (0, 1)
    assert next_ladder(0, 1, 3) == (0, 2)
    assert next_ladder(0, 2, 3) == (1, 0)
    assert next_ladder(1, 2, 3) == (2, 0)
    assert next_ladder(2, 2, 3) is None
    assert next_ladder(0, -1, 1) == (0, 0)
    assert next_ladder(0, 2, 1) is None


def test_format_hint_text():
    text = format_hint_text("x=7", step=0, level=2, n_steps=3, step_title="Найдите x")
    assert "Шаг 1 из 3" in text
    assert "Найдите x" in text
    assert "почти решение" in text
    assert text.endswith("x=7")
```

- [ ] **Step 2:** Запустить — убедиться, что падает:

```bash
.venv/Scripts/python.exe -m pytest tests/test_scaffold.py -q
```

Expected: FAIL (`ModuleNotFoundError: No module named 'src.student.scaffold'`).

- [ ] **Step 3:** Реализовать `src/student/scaffold.py`:

```python
"""Scaffolding subtasks (Этап 5): лестница подсказок для трудных практик.

Чистые функции без I/O (стиль src/student/linucb.py, mastery.py): валидация
payload scaffold-практики, триггер+fade по мастерству темы, продвижение по
лестнице подсказок и форматирование текста подсказки. Модуль не знает про
sqlite/LLM/FastAPI.
"""

from __future__ import annotations

from typing import Any

MIN_STEPS = 2
MAX_STEPS = 4
HINTS_PER_STEP = 3
WEAK_ACCURACY = 0.6
WEAK_MIN_ATTEMPTS = 2

HINT_LEVEL_LABELS = ("направление", "конкретнее", "почти решение")

SCAFFOLD_ADVICE_TEXT = (
    "[Поддержка: тема даётся ученику трудно. Если сейчас уместна практика — "
    "можешь сопроводить её пошаговой поддержкой: payload.scaffold_steps (2–4 "
    "чекпоинта) и payload._scaffold_hints (по 3 подсказки на шаг: направление → "
    "конкретнее → почти решение). Решение за тобой.]"
)

STEP_REQUEST_PHRASES = (
    "разбей", "разбейте", "по шагам", "пошагово", "по шажочкам",
    "шагами", "декомпозиц", "план решения", "плана решения",
)


def normalize_scaffold_payload(
    payload: dict[str, Any] | None,
) -> tuple[list[str], list[list[str]]] | None:
    """Валидирует scaffold-payload практики (спека §4.2).

    Требования: ``scaffold_steps`` — 2..4 непустых строки; ``_scaffold_hints`` —
    параллельный список той же длины, каждый элемент — ровно HINTS_PER_STEP
    непустых строк. Возвращает (очищенные шаги, скрытые лестницы) либо None.
    """
    if not isinstance(payload, dict):
        return None
    raw_steps = payload.get("scaffold_steps")
    raw_hints = payload.get("_scaffold_hints")
    if not isinstance(raw_steps, list) or not isinstance(raw_hints, list):
        return None
    if not (MIN_STEPS <= len(raw_steps) <= MAX_STEPS):
        return None
    if len(raw_steps) != len(raw_hints):
        return None
    steps: list[str] = []
    hints: list[list[str]] = []
    for step, ladder in zip(raw_steps, raw_hints, strict=True):
        title = str(step).strip() if step is not None else ""
        if not title:
            return None
        if not isinstance(ladder, list) or len(ladder) != HINTS_PER_STEP:
            return None
        cleaned = [str(h).strip() if h is not None else "" for h in ladder]
        if any(not h for h in cleaned):
            return None
        steps.append(title)
        hints.append(cleaned)
    return steps, hints


def asked_for_steps(text: str | None) -> bool:
    """Эвристика явной просьбы «разбей на шаги» в тексте ученика."""
    norm = (text or "").casefold()
    return any(phrase in norm for phrase in STEP_REQUEST_PHRASES)


def should_scaffold(
    topic: dict[str, Any] | None,
    *,
    bandit_hard: bool = False,
    requested: bool = False,
) -> bool:
    """Триггер декомпозиции и fade (спека §6.1).

    True при явной просьбе (всегда), LinUCB-hard, либо слабой теме
    (attempts >= 2 и accuracy < 0.6). Освоенная тема (is_mastered) отключает
    авто-поддержку, но явная просьба побеждает fade.
    """
    if requested:
        return True
    if topic is None:
        return bandit_hard
    from .mastery import is_mastered

    mastered = is_mastered(
        int(topic.get("attempts") or 0),
        float(topic.get("mastery") or 0.0),
        str(topic.get("status") or ""),
    )
    if mastered:
        return False
    if bandit_hard:
        return True
    attempts = int(topic.get("attempts") or 0)
    accuracy = float(topic.get("accuracy") or 0.0)
    return attempts >= WEAK_MIN_ATTEMPTS and accuracy < WEAK_ACCURACY


def next_ladder(current: int, level: int, n_steps: int) -> tuple[int, int] | None:
    """Следующая позиция лестницы (step, level) либо None (лестница исчерпана).

    level — индекс последней ВЫДАННОЙ подсказки текущего шага (−1 = ещё не
    выдавали). Сначала исчерпываются HINTS_PER_STEP уровней шага, затем
    переход к следующему шагу.
    """
    n = int(n_steps)
    if n <= 0:
        return None
    cur = max(0, min(int(current), n - 1))
    lvl = max(-1, min(int(level), HINTS_PER_STEP - 1))
    pos = cur * HINTS_PER_STEP + (lvl + 1)
    if pos >= n * HINTS_PER_STEP:
        return None
    return pos // HINTS_PER_STEP, pos % HINTS_PER_STEP


def format_hint_text(
    hint: str,
    step: int,
    level: int,
    n_steps: int,
    step_title: str,
) -> str:
    """Текст подсказки: «Шаг N из M. <title> — <label>: <hint>»."""
    lvl = max(0, min(int(level), len(HINT_LEVEL_LABELS) - 1))
    label = HINT_LEVEL_LABELS[lvl]
    return (
        f"Шаг {int(step) + 1} из {int(n_steps)}. "
        f"{(step_title or '').strip()} — {label}: {hint}"
    )
```

- [ ] **Step 4:** Прогнать тесты Task 1:

```bash
.venv/Scripts/python.exe -m pytest tests/test_scaffold.py -q
```

Expected: PASS.

- [ ] **Step 5:** Ruff:

```bash
.venv/Scripts/python.exe -m ruff check src/student/scaffold.py tests/test_scaffold.py
```

Expected: All checks passed.

---

### Task 2: Поля `ChatSession` для активной лестницы

**Files:**
- Modify: `src/api/session_store.py:23-48` (dataclass `ChatSession`)
- Test: добавить функцию в `tests/test_scaffold.py`

**Interfaces:**
- Produces: `ChatSession.scaffold_task: str = ""`,
  `ChatSession.scaffold_steps: list = field(default_factory=list)`,
  `ChatSession.scaffold_hints: list = field(default_factory=list)`,
  `ChatSession.scaffold_current: int = 0`,
  `ChatSession.scaffold_level: int = -1`.

- [ ] **Step 1: падающий тест** — дописать в `tests/test_scaffold.py`:

```python
def test_chat_session_scaffold_fields_defaults_and_roundtrip():
    from src.api.session_store import SessionStore

    store = SessionStore()
    session = store.create(student_id="stu_1", topic="квадратные уравнения")
    assert session.scaffold_task == ""
    assert session.scaffold_steps == []
    assert session.scaffold_hints == []
    assert session.scaffold_current == 0
    assert session.scaffold_level == -1

    session.scaffold_steps = ["a", "b"]
    session.scaffold_current = 1
    got = store.get(session.session_id)
    assert got.scaffold_steps == ["a", "b"]
    assert got.scaffold_current == 1
```

- [ ] **Step 2:** Запустить — убедиться, что падает:

```bash
.venv/Scripts/python.exe -m pytest tests/test_scaffold.py::test_chat_session_scaffold_fields_defaults_and_roundtrip -q
```

Expected: FAIL (`AttributeError: 'ChatSession' object has no attribute
'scaffold_task'`).

- [ ] **Step 3:** Добавить поля в dataclass `ChatSession` (в конец, после
  `bandit_features`, `src/api/session_store.py:44`):

```python
    # Scaffolding subtasks (Этап 5): состояние активной лестницы подсказок.
    scaffold_task: str = ""      # текст scaffold-практики (якорь проверки)
    scaffold_steps: list = field(default_factory=list)   # list[str] чек-лист
    scaffold_hints: list = field(default_factory=list)   # list[list[str]] (скрыто)
    scaffold_current: int = 0    # текущий шаг лестницы
    scaffold_level: int = -1     # последняя выданная подсказка шага (-1 = нет)
```

- [ ] **Step 4:** Прогнать тест:

```bash
.venv/Scripts/python.exe -m pytest tests/test_scaffold.py -q
```

Expected: PASS.

---

### Task 3: Промпт — формат scaffold-payload практики

**Files:**
- Modify: `src/agent/prompts.py` (буллет `practice`, строка 30)
- Test: добавить функцию в `tests/test_scaffold.py`

**Interfaces:**
- Produces: системный промпт описывает `payload.scaffold_steps`/`_scaffold_hints`.

- [ ] **Step 1: падающий тест** — дописать в `tests/test_scaffold.py`:

```python
def test_system_prompt_mentions_scaffold_payload():
    from src.agent.prompts import SYSTEM_PROMPT

    assert "scaffold_steps" in SYSTEM_PROMPT
    assert "_scaffold_hints" in SYSTEM_PROMPT
    assert "почти решение" in SYSTEM_PROMPT
```

Запустить:

```bash
.venv/Scripts/python.exe -m pytest tests/test_scaffold.py::test_system_prompt_mentions_scaffold_payload -q
```

Expected: FAIL (маркеров нет в промпте).

- [ ] **Step 2:** В `src/agent/prompts.py` расширить буллет `practice`
  (сейчас `SYSTEM_PROMPT` заканчивает буллет строкой `'- practice — задача для
  ученика; payload: {"task_ref": "..."}\n'`, `prompts.py:30`) — заменить на:

```python
    "- practice — задача для ученика; payload: {\"task_ref\": \"...\"}.\n"
    "  Пошаговая поддержка (scaffolding) для трудной темы/сложной задачи/просьбы "
    "«разбей на шаги»: верни practice с payload: {\"scaffold_steps\": "
    "[\"<чекпоинт 1>\", ...], \"_scaffold_hints\": [[\"<направление>\", "
    "\"<конкретнее>\", \"<почти решение>\"], ...]} — 2–4 чекпоинта в порядке "
    "решения и по ровно 3 строки подсказок на каждый, от общего направления к "
    "почти готовому решению. scaffold_steps ученик видит сразу (чек-лист); "
    "_scaffold_hints сервер отдаёт по одной на кнопку «Подсказка». В text — "
    "ТОЛЬКО условие задачи: не дублируй в text/прочих полях шаги, подсказки и "
    "ответ.\n"
```

- [ ] **Step 3:** Прогнать тест + убедиться, что sanity-тесты промпта не сломаны:

```bash
.venv/Scripts/python.exe -m pytest tests/test_scaffold.py tests/test_envelope.py::test_system_prompt_requires_json_envelope tests/test_adaptive_state.py -q
```

Expected: PASS.

- [ ] **Step 4:** Ruff:

```bash
.venv/Scripts/python.exe -m ruff check src/agent/prompts.py tests/test_scaffold.py
```

Expected: All checks passed.

---

### Task 4: Сервер — установка/сброс/совет и детерминированный `hint_request`

**Files:**
- Modify: `src/api/server.py`
- Create: `tests/test_scaffold_api.py`

**Interfaces:**
- Consumes: `normalize_scaffold_payload`, `asked_for_steps`, `should_scaffold`,
  `SCAFFOLD_ADVICE_TEXT`, `next_ladder`, `format_hint_text` (Task 1), поля
  `ChatSession.scaffold_*` (Task 2).
- Produces:
  - `_SCAFFOLD_EXHAUSTED_TEXT` (module const, рядом с `_HINT_SERVICE_TEXT`,
    `server.py:344-345`);
  - `_clear_scaffold(session: ChatSession) -> None`;
  - `_install_scaffold(session: ChatSession, envelope: ContentEnvelope) -> bool`;
  - `_scaffold_anchored(sessions: SessionStore, session_id: str,
    session: ChatSession) -> bool`;
  - `_scaffold_ladder_reply(app: FastAPI, session: ChatSession, session_id: str,
    student_id: str, body: ChatRequest)
    -> tuple[AgentGraphState, str, str, ContentEnvelope, dict, list[dict]]`;
  - поведение: совет-нота в контексте хода; установка лестницы по последнему
    `practice`-конверту; сброс при обычном ходе/новой задаче; перехват
    `hint_request` без вызова модели; JSONL `scaffold.advice/install/hint`.

- [ ] **Step 1: падающий интеграционный тест** — создать
  `tests/test_scaffold_api.py`:

```python
"""Интеграционные тесты scaffolding subtasks через HTTP (фейковый рантайм)."""

import time

from fastapi.testclient import TestClient

from src.agent.critic import Critic
from src.agent.loop import AgentRuntime
from src.agent.tools import ToolContext
from src.api.server import create_app
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.student.store import StudentStore

SCAFFOLD_PRACTICE = (
    '{"type": "practice", "text": "Решите уравнение x^2=16.", '
    '"difficulty": "hard", '
    '"payload": {"task_ref": "t1", '
    '"scaffold_steps": ["Найдите x^2", "Найдите x", "Проверьте корни"], '
    '"_scaffold_hints": [["h11", "h12", "h13"], ["h21", "h22", "h23"], '
    '["h31", "h32", "h33"]]}}'
)
EVALUATION_OK = (
    '{"type": "evaluation", "text": "Верно!", '
    '"payload": {"correct": true, "feedback": "ok"}, "difficulty": "medium"}'
)
PLAIN_PRACTICE = (
    '{"type": "practice", "text": "Решите уравнение x^2=16.", '
    '"difficulty": "medium", "payload": {"task_ref": "t2"}}'
)


class _Judge(LLMClient):
    async def chat(self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None):
        return LLMResponse(
            content='{"passed": true, "issues": []}', model=model,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3), finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


class FakeScaffoldLLM(LLMClient):
    """Вызов 1: scaffold-практика. Далее: evaluation. Запоминает последний контекст."""

    def __init__(self):
        self.calls = 0
        self.last_messages = []

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None):
        self.calls += 1
        self.last_messages = list(messages)
        content = SCAFFOLD_PRACTICE if self.calls == 1 else EVALUATION_OK
        return LLMResponse(
            content=content, model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


class FakePlainLLM(LLMClient):
    """Вызов 1: обычная practice. Далее: короткий текст (модельная подсказка)."""

    def __init__(self):
        self.calls = 0
        self.last_messages = []

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None):
        self.calls += 1
        self.last_messages = list(messages)
        content = PLAIN_PRACTICE if self.calls == 1 else "Попробуйте выразить x."
        return LLMResponse(
            content=content, model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


def _runtime(fake):
    return AgentRuntime(
        llm=fake,
        models={"planner": "test", "fast": "test", "judge": "test"},
        tool_context=ToolContext(region="GLOBAL"),
        critic=Critic(llm=_Judge(), model="judge"),
    )


def _chat_body(session_id, message="", kind="message", topic="квадратные уравнения", student_id="stu_s"):
    return {
        "message": message,
        "kind": kind,
        "session_id": session_id,
        "student_id": student_id,
        "topic": topic,
        "subject": "алгебра",
    }


def test_scaffold_install_hides_hints_and_sets_session(tmp_path):
    fake = FakeScaffoldLLM()
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(runtime_factory=lambda: _runtime(fake), student_store=store)
    with TestClient(app) as c:
        resp = c.post("/chat", json=_chat_body("scaf-s1", message="Дай задание"))
        assert resp.status_code == 200
        env = resp.json()["envelope"]
        assert env["type"] == "practice"
        assert env["payload"]["scaffold_steps"] == ["Найдите x^2", "Найдите x", "Проверьте корни"]
        assert "_scaffold_hints" not in env["payload"]

        session = app.state.sessions.get("scaf-s1")
        expected_hints = [
            ["h11", "h12", "h13"],
            ["h21", "h22", "h23"],
            ["h31", "h32", "h33"],
        ]
        assert session.scaffold_steps == [
            "Найдите x^2", "Найдите x", "Проверьте корни",
        ]
        assert session.scaffold_hints == expected_hints
        assert session.scaffold_task == "Решите уравнение x^2=16."
        assert (session.scaffold_current, session.scaffold_level) == (0, -1)

        # в истории тоже нет скрытой лестницы
        hist = c.get("/chat/history/scaf-s1").json()["messages"]
        last = hist[-1]
        assert "_scaffold_hints" not in (last.get("envelope") or {}).get("payload", {})
    store.close()


def test_scaffold_hint_deterministic_without_llm(tmp_path):
    fake = FakeScaffoldLLM()
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(runtime_factory=lambda: _runtime(fake), student_store=store)
    with TestClient(app) as c:
        c.post("/chat", json=_chat_body("scaf-h1", message="Дай задание"))
        calls_before = fake.calls

        resp = c.post("/chat", json=_chat_body("scaf-h1", kind="hint_request"))
        assert resp.status_code == 200
        env = resp.json()["envelope"]
        assert env["type"] == "hint"
        assert "Шаг 1 из 3" in env["text"]
        assert "направление" in env["text"]
        assert "h11" in env["text"]
        # модель не вызывалась (лестница из сессии, не перегенерация)
        assert fake.calls == calls_before
        session = app.state.sessions.get("scaf-h1")
        assert (session.scaffold_current, session.scaffold_level) == (0, 0)
    store.close()


def test_scaffold_ladder_advances_then_exhausts(tmp_path):
    fake = FakeScaffoldLLM()
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(runtime_factory=lambda: _runtime(fake), student_store=store)
    with TestClient(app) as c:
        c.post("/chat", json=_chat_body("scaf-l1", message="Дай задание"))
        expected = [
            (1, "направление", "h11"),
            (1, "конкретнее", "h12"),
            (1, "почти решение", "h13"),
            (2, "направление", "h21"),
            (2, "конкретнее", "h22"),
            (2, "почти решение", "h23"),
            (3, "направление", "h31"),
            (3, "конкретнее", "h32"),
            (3, "почти решение", "h33"),
        ]
        calls_before = fake.calls
        for step, label, hint in expected:
            resp = c.post("/chat", json=_chat_body("scaf-l1", kind="hint_request"))
            env = resp.json()["envelope"]
            assert env["type"] == "hint"
            assert f"Шаг {step} из 3" in env["text"]
            assert label in env["text"]
            assert hint in env["text"]
        # лестница исчерпана — детерминированный отказ, модель не вызывается
        resp = c.post("/chat", json=_chat_body("scaf-l1", kind="hint_request"))
        env = resp.json()["envelope"]
        assert env["type"] == "hint"
        assert "закончились" in env["text"]
        assert fake.calls == calls_before
    store.close()


def test_scaffold_cleared_on_answer_and_evaluation(tmp_path):
    fake = FakeScaffoldLLM()
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(runtime_factory=lambda: _runtime(fake), student_store=store)
    with TestClient(app) as c:
        c.post("/chat", json=_chat_body("scaf-c1", message="Дай задание"))
        c.post("/chat", json=_chat_body("scaf-c1", kind="hint_request"))
        assert app.state.sessions.get("scaf-c1").scaffold_steps

        resp = c.post("/chat", json=_chat_body("scaf-c1", message="Ответ: 4 и -4"))
        assert resp.json()["envelope"]["type"] == "evaluation"
        session = app.state.sessions.get("scaf-c1")
        assert session.scaffold_steps == []
        assert session.scaffold_task == ""
        # после сброса hint_request снова идёт в модель (капа/флоу не сломаны):
        # run_agent может делать >1 вызова LLM, поэтому проверяем только факт вызова
        calls_before = fake.calls
        c.post("/chat", json=_chat_body("scaf-c1", kind="hint_request"))
        assert fake.calls > calls_before
    store.close()


def test_scaffold_malformed_payload_falls_back_to_plain_practice(tmp_path):
    class FakeBadLLM(FakeScaffoldLLM):
        async def chat(self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None):
            self.calls += 1
            self.last_messages = list(messages)
            bad = (
                '{"type": "practice", "text": "Решите x^2=16.", "difficulty": "hard", '
                '"payload": {"scaffold_steps": ["один"], '
                '"_scaffold_hints": [["a", "b", "c"], ["d", "e", "f"]]}}'
            )
            return LLMResponse(
                content=bad, model=model,
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
                finish_reason="stop",
            )

    fake = FakeBadLLM()
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(runtime_factory=lambda: _runtime(fake), student_store=store)
    with TestClient(app) as c:
        resp = c.post("/chat", json=_chat_body("scaf-b1", message="Дай задание"))
        env = resp.json()["envelope"]
        assert env["type"] == "practice"
        assert "scaffold_steps" not in env["payload"]
        assert "_scaffold_hints" not in env["payload"]
        assert app.state.sessions.get("scaf-b1").scaffold_steps == []
    store.close()


def test_scaffold_advice_added_for_weak_topic(tmp_path):
    fake = FakeScaffoldLLM()
    store = StudentStore(str(tmp_path / "students.db"))
    store.upsert_student("stu_weak")
    store._exec(
        "INSERT INTO topics (student_id, topic, subject, level, mastery, status, "
        "attempts, correct, weak_areas, relations, last_seen) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("stu_weak", "квадратные уравнения", "алгебра", 0.3, 0.3, "in_progress",
         2, 0, "[]", "{}", time.time()),
    )
    app = create_app(runtime_factory=lambda: _runtime(fake), student_store=store)
    with TestClient(app) as c:
        c.post("/chat", json=_chat_body("scaf-a1", message="Дай задание",
                                        student_id="stu_weak"))
        joined = "\n".join(
            m.get("content", "") for m in fake.last_messages if m.get("role") == "system"
        )
        assert "Поддержка" in joined
    store.close()


def test_plain_practice_hint_still_goes_through_model(tmp_path):
    fake = FakePlainLLM()
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(runtime_factory=lambda: _runtime(fake), student_store=store)
    with TestClient(app) as c:
        c.post("/chat", json=_chat_body("scaf-p1", message="Дай задание"))
        assert app.state.sessions.get("scaf-p1").scaffold_steps == []
        calls_before = fake.calls
        resp = c.post("/chat", json=_chat_body("scaf-p1", kind="hint_request"))
        assert resp.status_code == 200
        # обычный модельный hint-поток: модель вызвана (run_agent может звать
        # LLM >1 раза за ход, поэтому точное число не проверяем)
        assert fake.calls > calls_before
    store.close()
```

- [ ] **Step 2:** Запустить — убедиться, что падает:

```bash
.venv/Scripts/python.exe -m pytest tests/test_scaffold_api.py -q
```

Expected: FAIL (AttributeError: scaffold поля / конверт не санитизирован и т.п.).

- [ ] **Step 3:** Импорт в `src/api/server.py` (в блок импортов `..student`,
  рядом с `server.py:61-64`):

```python
from ..student.scaffold import normalize_scaffold_payload
```

- [ ] **Step 4:** Константа текста отказа рядом с `_HINT_SERVICE_TEXT`
  (`server.py:344-345`):

```python
_HINT_SERVICE_TEXT = "[ученик просит подсказку к текущей задаче]"
_MAX_HINTS_IN_A_ROW = 2
_SCAFFOLD_EXHAUSTED_TEXT = (
    "Подсказки к заданию закончились — соберите решение по шагам и ответьте. "
    "Если совсем сложно, переформулируйте вопрос."
)
```

- [ ] **Step 5:** Хелперы после `_latest_assistant_kind` (`server.py:1026-1036`):

```python
def _clear_scaffold(session: ChatSession) -> None:
    """Сбрасывает активную лестницу подсказок сессии."""
    session.scaffold_task = ""
    session.scaffold_steps = []
    session.scaffold_hints = []
    session.scaffold_current = 0
    session.scaffold_level = -1


def _install_scaffold(session: ChatSession, envelope: ContentEnvelope) -> bool:
    """Сохраняет лестницу scaffold-практики в сессию.

    При невалидном payload оба ключа удаляются из envelope.payload (обычная
    практика) и возвращается False. Скрытую лестницу из санитизированной
    копии вырежет sanitize_envelope в вызывающем коде.
    """
    if envelope.type.value != "practice":
        return False
    payload = envelope.payload or {}
    parsed = normalize_scaffold_payload(payload)
    if parsed is None:
        payload.pop("scaffold_steps", None)
        payload.pop("_scaffold_hints", None)
        _clear_scaffold(session)
        JsonlLogger(settings.log_file).log(
            "", "INFO", "scaffold.reject", reason="invalid_scaffold_payload"
        )
        return False
    steps, hints = parsed
    session.scaffold_task = envelope.text or ""
    session.scaffold_steps = steps
    session.scaffold_hints = hints
    session.scaffold_current = 0
    session.scaffold_level = -1
    return True


def _scaffold_anchored(
    sessions: SessionStore, session_id: str, session: ChatSession
) -> bool:
    """Лестница привязана к последней выданной практике (не осиротела).

    Идём по assistant-сообщениям с конца, пропуская подсказки и теорию:
    первая значимая запись должна быть practice с текстом scaffold_task.
    """
    if not session.scaffold_task:
        return False
    for message in reversed(sessions.list_messages(session_id)):
        if message.get("role") != "assistant":
            continue
        kind = message.get("kind")
        if kind in ("hint", "theory"):
            continue
        if kind == "practice":
            return (message.get("content") or "") == session.scaffold_task
        return False
    return False


def _scaffold_ladder_reply(
    app: FastAPI,
    session: ChatSession,
    session_id: str,
    student_id: str,
    body: ChatRequest,
) -> tuple[AgentGraphState, str, str, ContentEnvelope, dict, list[dict]]:
    """Детерминированная подсказка активной лестницы (или отказ по исчерпании).

    Ранний возврат в стиле guard на _MAX_HINTS_IN_A_ROW (server.py:1449-1468):
    модель не вызывается. Исчерпание — next_ladder вернул None.
    """
    from src.student.scaffold import format_hint_text, next_ladder

    n_steps = len(session.scaffold_steps)
    pos = next_ladder(session.scaffold_current, session.scaffold_level, n_steps)
    if pos is None:
        env = ContentEnvelope(type="hint", text=_SCAFFOLD_EXHAUSTED_TEXT)
        answer = env.text
    else:
        step, level = pos
        session.scaffold_current = step
        session.scaffold_level = level
        text = format_hint_text(
            session.scaffold_hints[step][level],
            step=step,
            level=level,
            n_steps=n_steps,
            step_title=session.scaffold_steps[step],
        )
        env = ContentEnvelope(type="hint", text=text)
        answer = env.text
        JsonlLogger(settings.log_file).log(
            "", "INFO", "scaffold.hint",
            session_id=session_id, step=step + 1, level=level + 1, steps=n_steps,
        )
    app.state.sessions.append_message(
        session_id,
        "assistant",
        answer,
        meta={"kind": env.type.value, "envelope": env.model_dump()},
    )
    adaptive = _build_adaptive(
        app.state.student_store, student_id, body.topic, "medium",
        subject=body.subject,
    )
    state = AgentGraphState(messages=[], final_answer=answer, terminated=True)
    state.content_envelope = env
    return state, session_id, "", env, adaptive, [
        {"content": answer, "envelope": sanitize_envelope(env).model_dump()}
    ]
```

- [ ] **Step 6:** В `_run_chat` в ветке `if hint_request:` (`server.py:1442`)
  ДО существующего guard (комментарий `# Guard: не более ...`, `server.py:1444`)
  вставить перехват активной лестницы:

```python
    hint_request = body.kind == "hint_request"
    if hint_request:
        # Scaffolding (Этап 5): активная лестница — обслуживаем детерминированно,
        # без вызова модели (лестница сохранена при выдаче практики).
        if session.scaffold_steps and _scaffold_anchored(
            app.state.sessions, session_id, session
        ):
            return _scaffold_ladder_reply(app, session, session_id, student_id, body)
        # Guard: не более _MAX_HINTS_IN_A_ROW подсказок подряд на одну задачу.
```

- [ ] **Step 7:** В `else`-ветке обычного хода, первой строкой тела (`server.py:1472`,
  перед `_append_user_if_new`), добавить сброс лестницы:

```python
    else:
        # Scaffolding (Этап 5): обычный ход (ответ/новая задача) снимает лестницу.
        _clear_scaffold(session)
        _append_user_if_new(app.state.sessions, session_id, body.message)
```

- [ ] **Step 8:** После bandit-блока (`server.py:1514`, конец `try/except` совета)
  добавить совет модели про scaffold (до `runtime = ...`, `server.py:1516`):

```python
        # Scaffolding (Этап 5): совет модели про пошаговую поддержку (не диктат).
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

(Блок вставляется в конец `else`-ветки, где `context` уже построен и
`session_bandit` определён на `server.py:1440`.)

- [ ] **Step 9:** Установка лестницы после фиксации bandit-руки, ДО санитизации
  (`server.py:1649-1656`), сразу после блока
  `session.bandit_arm, session.bandit_topic, session.bandit_features = session_bandit`:

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

- [ ] **Step 10:** Прогнать интеграционные тесты:

```bash
.venv/Scripts/python.exe -m pytest tests/test_scaffold_api.py -q
```

Expected: PASS.

- [ ] **Step 11:** Прогнать связанные существующие тесты (hint-флоу, bandit,
  review) + ruff:

```bash
.venv/Scripts/python.exe -m pytest tests/test_api.py tests/test_scaffold_api.py tests/test_scaffold.py -q
.venv/Scripts/python.exe -m ruff check src/api/server.py src/student/scaffold.py tests/test_scaffold_api.py tests/test_scaffold.py
```

Expected: PASS / All checks passed.

---

### Task 5: Фронтенд — чек-лист в `PracticeBlock`

**Files:**
- Modify: `frontend/src/components/PracticeBlock.jsx`
- Modify: `frontend/src/components/blocks.test.jsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `envelope.payload.scaffold_steps: string[] | undefined`.
- Produces: `PracticeBlock` рендерит нумерованный `<ol className="scaffold-steps">`
  и меняет label на «Задача (по шагам)», когда `scaffold_steps` непуст.

- [ ] **Step 1: падающий тест** — в `blocks.test.jsx` добавить (после
  существующего блока про `PracticeBlock`, `blocks.test.jsx:23-28`):

```jsx
const scaffoldPractice = {
  type: 'practice',
  text: 'Решите уравнение x² = 16',
  payload: {
    scaffold_steps: ['Найдите x²', 'Найдите x', 'Проверьте корни'],
  },
}

it('PracticeBlock renders scaffold checklist and keeps hint button', async () => {
  const send = vi.fn()
  render(<PracticeBlock envelope={scaffoldPractice} onSend={send} />)
  expect(screen.getByText(/по шагам/i)).toBeInTheDocument()
  expect(screen.getByText('Найдите x²')).toBeInTheDocument()
  expect(screen.getByText('Найдите x')).toBeInTheDocument()
  expect(screen.getByText('Проверьте корни')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /Подсказка/ }))
  expect(send).toHaveBeenCalledWith('', 'hint_request')
})

it('PracticeBlock without scaffold_steps stays plain', () => {
  render(<PracticeBlock envelope={practice} onSend={() => {}} />)
  expect(screen.queryByText('Найдите x²')).not.toBeInTheDocument()
  expect(screen.queryByText(/по шагам/i)).not.toBeInTheDocument()
})
```

Запустить:

```bash
npm test -- src/components/blocks.test.jsx
```

Expected: FAIL (чек-лист не рендерится).

- [ ] **Step 2:** Заменить содержимое `PracticeBlock.jsx`:

```jsx
import Latex, { InlineText } from './Latex'

export default function PracticeBlock({ envelope, onSend }) {
  const steps = Array.isArray(envelope?.payload?.scaffold_steps)
    ? envelope.payload.scaffold_steps
    : []
  const scaffolded = steps.length > 0
  return (
    <div className={`block practice${scaffolded ? ' scaffold' : ''}`}>
      <div className="block-label">Задача{scaffolded ? ' (по шагам)' : ''}</div>
      <Latex text={envelope?.text || ''} />
      {scaffolded && (
        <ol className="scaffold-steps">
          {steps.map((step, i) => (
            <li key={`${i}-${step}`} className="scaffold-step">
              <span className="scaffold-step-num">{i + 1}.</span>
              <InlineText text={step} />
            </li>
          ))}
        </ol>
      )}
      <button className="btn hint" onClick={() => onSend('', 'hint_request')}>💡 Подсказка</button>
    </div>
  )
}
```

- [ ] **Step 3:** В `index.css` после `.block.practice { ... }` (строка 452)
  добавить стили:

```css
.block.practice.scaffold {
  border-left-color: var(--green-strong);
}

/* Scaffolding subtasks (Этап 5): чек-лист шагов трудной практики */
.scaffold-steps {
  display: grid;
  gap: 6px;
  margin: 10px 0 4px;
  padding: 0;
  list-style: none;
}

.scaffold-step {
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 6px 10px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 10px;
  color: var(--ink-soft);
  font-size: 14px;
}

.scaffold-step-num {
  flex: none;
  min-width: 18px;
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--green);
}
```

- [ ] **Step 4:** Прогнать тесты и lint:

```bash
npm test -- src/components/blocks.test.jsx
npm test
npm run lint
```

Expected: PASS / PASS / lint clean.

---

### Task 6: Полный прогон, docs и «Что осталось»

**Files:**
- (документация уже создана: `docs/superpowers/specs/2026-09-06-scaffolding-subtasks-design.md`,
  настоящий план)
- Опционально: `README.md`/`adaptive_tutor/docs/api.md` — строка про scaffolding.

- [ ] **Step 1:** Полный прогон бэкенда и lint:

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
.venv/Scripts/python.exe -m ruff check src tests -q
```

Expected: PASS (401 базовых + ~14 новых) / All checks passed.

- [ ] **Step 2:** Полный прогон фронтенда:

```bash
npm test
npm run lint
```

Expected: PASS (104 базовых + 2 новых) / lint clean.

- [ ] **Step 3:** Smoke против живого сервера (опционально, по желанию
  пользователя, настоящий LLM): после рестарта uvicorn два хода через
  `/chat/stream` — «дай сложное задание по <слабая тема>» → «подсказка» →
  проверить в `logs/agent.jsonl` события `scaffold.advice`/`scaffold.install`/
  `scaffold.hint`.

- [ ] **Step 4:** Сообщить «Что осталось» (см. AGENTS.md): невыполненные пункты
  плана, live-smoke с настоящим LLM, и следующий рекомендуемый шаг.
