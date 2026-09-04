# Backend: Typed Content + Student Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Расширить backend `adaptive_tutor`, чтобы агент возвращал типизированный JSON-конверт (theory/practice/hint/quiz/evaluation) и хранил персистентный профиль ученика (SQLite по student_id) для отображения адаптивности во фронтенде.

**Architecture:** Подход A «конверт в цикле» — системный промпт требует JSON-конверт как финальный ответ, узел `finalize` парсит/валидирует его (фолбэк theory), кладёт в `state.content_envelope`. Профиль — новый модуль `student/` (SQLite store + adaptive-логика); агент о нём не знает, сервер обновляет БД после evaluation-ходов. SSE `message` и `POST /chat` получают `envelope` + `adaptive`.

**Tech Stack:** Python 3.11+, FastAPI, LangGraph, Pydantic v2, sqlite3 (stdlib), pytest, ruff.

**Spec:** `C:\otus\edututor\docs\superpowers\specs\2026-09-03-frontend-design.md`

## Global Constraints

- Python >= 3.11. Все существующие тесты (124) остаются зелёными.
- `ruff check src/ tests/` — чисто (line-length 100).
- Проект **не в git-репозитории**: шаг «Commit» заменяется на «тесты + ruff». Никаких `git`-команд.
- Русские docstring/комментарии, async/await, Pydantic v2, `threading.Lock` для разделяемого состояния.
- `.env` не менять. В `.env.example` новые переменные добавлять.
- Агент не должен знать про SQLite: обновление профиля — только в `src/api/server.py`.

---

### Task 1: Модели конверта + парсер

**Files:**
- Modify: `src/models/schemas.py`
- Create: `src/agent/envelope.py`
- Create: `tests/test_envelope.py`

**Interfaces:**
- Consumes: Pydantic v2 (уже в проекте).
- Produces:
  - `schemas.ContentType(StrEnum)` cо значениями `THEORY/PRACTICE/HINT/QUIZ/EVALUATION`.
  - `schemas.ContentEnvelope(BaseModel)`: `v: int = 1`, `type: ContentType`, `text: str`, `payload: dict[str, Any] = {}`, `difficulty: Literal["easy","medium","hard"] = "medium"`, метод `model_dump()`.
  - `agent.envelope.parse_content_envelope(raw: str) -> ContentEnvelope` — извлекает JSON (в т.ч. из ``` ```json ``` ```), при любой ошибке возвращает `ContentEnvelope(type=THEORY, text=raw)`.

- [ ] **Step 1: Добавить модели в `src/models/schemas.py`**

В конец файла (после `LearningStyle`), перед `AgentGraphState`, добавить:

```python
class ContentType(StrEnum):
    """Тип образовательного контента в конверте ответа агента."""
    THEORY = "theory"
    PRACTICE = "practice"
    HINT = "hint"
    QUIZ = "quiz"
    EVALUATION = "evaluation"


class ContentEnvelope(BaseModel):
    """Типизированный ответ агента (Подход A, конверт в цикле)."""
    v: int = 1
    type: ContentType
    text: str = Field(description="Markdown с формулами $...$ / $$...$$")
    payload: dict[str, Any] = Field(default_factory=dict)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
```

`Any`, `Literal`, `StrEnum`, `BaseModel`, `Field` уже импортированы в файле.

- [ ] **Step 2: Создать `src/agent/envelope.py`**

```python
"""Парсинг финального ответа агента в ContentEnvelope (с фолбэком)."""

import json
import re
from typing import Any

from ..models.schemas import ContentEnvelope, ContentType

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Пытается получить dict из текста: чистый JSON или из markdown-обёртки."""
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    match = _FENCE.search(stripped)
    if match:
        candidates.append(match.group(1).strip())
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def parse_content_envelope(raw: str) -> ContentEnvelope:
    """Парсит raw-ответ в ContentEnvelope; при ошибке — фолбэк theory с raw-текстом."""
    data = _extract_json(raw or "")
    if data is not None:
        try:
            return ContentEnvelope.model_validate(data)
        except Exception:  # noqa: BLE001 — невалидная схема -> фолбэк
            pass
    return ContentEnvelope(type=ContentType.THEORY, text=raw or "")
```

- [ ] **Step 3: Создать `tests/test_envelope.py`**

```python
"""Тесты парсинга финального ответа в ContentEnvelope."""

from src.agent.envelope import parse_content_envelope
from src.models.schemas import ContentEnvelope, ContentType


def test_valid_json_object():
    raw = '{"type": "theory", "text": "Текст $$x^2$$", "payload": {"topic": "тест"}, "difficulty": "hard"}'
    env = parse_content_envelope(raw)
    assert env.type == ContentType.THEORY
    assert "x^2" in env.text
    assert env.payload["topic"] == "тест"
    assert env.difficulty == "hard"


def test_json_wrapped_in_code_fence():
    raw = 'Ответ:\n```json\n{"type": "quiz", "text": "Вопрос?", "payload": {"answer_type": "single", "options": ["a", "b"]}}\n```'
    env = parse_content_envelope(raw)
    assert env.type == ContentType.QUIZ
    assert env.payload["answer_type"] == "single"


def test_evaluation_payload_preserved():
    raw = '{"type": "evaluation", "text": "Верно", "payload": {"correct": true, "knowledge_delta": 0.3}}'
    env = parse_content_envelope(raw)
    assert env.type == ContentType.EVALUATION
    assert env.payload["correct"] is True
    assert env.payload["knowledge_delta"] == 0.3


def test_plain_text_falls_back_to_theory():
    env = parse_content_envelope("Простое объяснение без JSON")
    assert env.type == ContentType.THEORY
    assert env.text == "Простое объяснение без JSON"


def test_invalid_json_in_fence_falls_back():
    env = parse_content_envelope('```json\n{broken}\n```')
    assert env.type == ContentType.THEORY
    assert env.text == '```json\n{broken}\n```'


def test_empty_string_falls_back():
    env = parse_content_envelope("")
    assert env.type == ContentType.THEORY
    assert env.text == ""
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_envelope.py -q`
Expected: 6 PASS.

- [ ] **Step 5: Проверка ruff**

Run: `.venv/Scripts/ruff.exe check src/models/schemas.py src/agent/envelope.py tests/test_envelope.py`
Expected: All checks passed.

---

### Task 2: Промпт — JSON-конверт и hint

**Files:**
- Modify: `src/agent/prompts.py`
- Test: `tests/test_envelope.py` (добавить проверку промпта)

**Interfaces:**
- Consumes: `schemas` (не напрямую), существующий `SYSTEM_PROMPT`.
- Produces: обновлённый `SYSTEM_PROMPT`, который требует финальный ответ JSON-конвертом и описывает типы и `hint`.

- [ ] **Step 1: Обновить `SYSTEM_PROMPT` в `src/agent/prompts.py`**

Заменить весь файл на:

```python
"""Промпты для агентного цикла."""

from ..models.schemas import AgentGraphState, LearningStyle

SYSTEM_PROMPT = (
    "Ты — Adaptive Tutor, ИИ-преподаватель. Ведешь обучение ученика в "
    "диалоге, адаптируя сложность к его уровню (зона ближайшего развития).\n\n"
    "Твоя задача — помогать осваивать тему шаг за шагом, используя материалы "
    "своей базы знаний (инструмент rag_search) и, при необходимости, "
    "актуальные сведения из интернета (инструмент web_search).\n\n"
    "Особенности вывода:\n"
    "  - Математические формулы ОБЯЗАТЕЛЬНО оформляй в строгом LaTeX: inline "
    "    формулы в $...$ (например $x^2 + y^2 = r^2$), а отдельные формулы "
    "    — в блоки $$...$$ (например $$E = mc^2$$), чтобы фронтенд-рендерер "
    "    MathJax/KaTeX мог их отрисовать.\n"
    "Правила:\n"
    "  - Сначала реши, нужно ли уточнить факты из базы/интернета; при "
    "    необходимости вызови инструмент.\n"
    "  - Когда данных достаточно — сформулируй финальный ответ (без вызова инструментов).\n"
    "  - Если ученик просит подсказку — ответь типом hint (дай направление, "
    "    НЕ полное решение).\n"
    "  - НЕ раскрывай внутренние рассуждения. Кратко объясни выбранное "
    "    действие в поле reason_summary.\n\n"
    "Формат финального ответа (СТРОГО): верни ОДИН JSON-объект без текста вне "
    "него и без markdown-обёртки, вида:\n"
    '{"type": "theory|practice|hint|quiz|evaluation", "text": "содержимое с формулами", '
    '"payload": {...}, "difficulty": "easy|medium|hard"}\n'
    "Типы:\n"
    '- theory — объяснение; payload: {"topic": "..."}\n'
    '- practice — задача для ученика; payload: {"task_ref": "..."}\n'
    '- hint — подсказка к текущей задаче; payload: {"task_ref": "..."}\n'
    '- quiz — вопрос для проверки понимания; payload: {"answer_type": "single"|"open", '
    '"options": [...]} (НИКОГДА не включай правильный ответ в options/text)\n'
    '- evaluation — проверка ответа ученика; payload: {"correct": true|false, '
    '"feedback": "...", "knowledge_delta": -0.1|0.2|0.3|0.5}\n"
)

_ADAPTATION_RULES: dict[LearningStyle, str] = {
    LearningStyle.VISUAL: "Используй схемы, ASCII-диаграммы, табличные сравнения",
    LearningStyle.AUDITORY: "Объясняй словами, приводи примеры «на слух»",
    LearningStyle.KINESTHETIC: "Давай практические задания, пошаговые упражнения",
    LearningStyle.READING: "Объясняй текстом, приводи определения",
}


def _build_adaptation_block(state: AgentGraphState) -> str:
    """Формирует блок адаптации на основе состояния ученика."""
    parts: list[str] = []

    parts.append(_ADAPTATION_RULES[state.learning_style])

    if state.current_knowledge_level >= 0.8:
        parts.append("Уровень ВЫСОКИЙ. Углублённая теория, сложные задачи")
    elif state.current_knowledge_level <= 0.3:
        parts.append("Уровень НИЗКИЙ. Основы, много примеров")

    if state.fatigue_level >= 0.7:
        parts.append("Ученик УСТАЛ. Сократи ответ, дай практику")
    elif state.fatigue_level >= 0.4:
        parts.append("Начинает уставать. Чередуй теорию с упражнениями")

    return "\n".join(parts)


def build_messages(state: AgentGraphState) -> list[dict]:
    """Собирает список сообщений для LLM из состояния графа."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    adaptation = _build_adaptation_block(state)
    messages.append({"role": "system", "content": adaptation})

    messages.extend(state.messages)

    if state.rag_context:
        ctx = "\n\n".join(r["text"] for r in state.rag_context[:5])
        messages.append({"role": "user", "content": f"[Контекст из базы знаний]\n{ctx}"})
    return messages
```

- [ ] **Step 2: Добавить тест промпта в `tests/test_envelope.py`**

Добавить в конец файла:

```python
from src.agent.prompts import SYSTEM_PROMPT


def test_system_prompt_requires_json_envelope():
    assert "JSON-объект" in SYSTEM_PROMPT
    assert '"type"' in SYSTEM_PROMPT
    assert "knowledge_delta" in SYSTEM_PROMPT
```

- [ ] **Step 3: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_envelope.py -q`
Expected: 7 PASS.

- [ ] **Step 4: Проверка ruff**

Run: `.venv/Scripts/ruff.exe check src/agent/prompts.py tests/test_envelope.py`
Expected: All checks passed.

---

### Task 3: finalize — конверт в состояние + событие message

**Files:**
- Modify: `src/models/schemas.py` (поле в `AgentGraphState`)
- Modify: `src/agent/loop.py` (`finalize`, `run_agent` message-event)
- Test: `tests/test_typed_finalize.py` (новый)

**Interfaces:**
- Consumes: `schemas.ContentEnvelope`, `agent.envelope.parse_content_envelope` (Task 1).
- Produces:
  - `AgentGraphState.content_envelope: ContentEnvelope | None = None`.
  - `finalize` кладёт в возвращаемый dict `content_envelope` (model_dump) и нормализует `final_answer` в `envelope.text`.
  - `run_agent` в событии `message` добавляет `envelope` (dict или None).

- [ ] **Step 1: Добавить поле в `AgentGraphState`**

В `src/models/schemas.py` после `final_answer`:

```python
    final_answer: str | None = None
    content_envelope: ContentEnvelope | None = None
```

- [ ] **Step 2: Переписать `finalize` в `src/agent/loop.py`**

Заменить текущий метод `finalize` (строки ~242-264) на:

```python
    async def finalize(self, state: AgentGraphState) -> dict[str, Any]:
        """Парсит ответ в конверт, валидирует текст, прогоняет Critic."""
        raw = state.final_answer or "Не смог сформировать ответ."
        envelope = parse_content_envelope(raw)
        answer = envelope.text

        ok, err = self.validator.validate(answer)
        if not ok:
            answer = "[требуется переформулировка] " + answer
            self._emit_log(state, "final", f"валидация провалена: {err}")

        if self.critic and ok:
            critic_result = await self.critic.validate(
                answer,
                rag_context=state.rag_context if state.rag_context else None,
            )
            if not critic_result.passed:
                answer = critic_result.corrected_answer or "[критик] " + answer
                issues_str = "; ".join(critic_result.issues)
                self._emit_log(state, "critic", f"критик отклонил: {issues_str}")

        envelope.text = answer
        self._notify("agent.finalize", {"status": "ok" if ok else "error"})
        result: dict[str, Any] = {
            "final_answer": answer,
            "content_envelope": envelope.model_dump(),
            "terminated": True,
        }
        if not ok:
            result["error"] = err
        return result
```

В начало файла добавить импорт: `from .envelope import parse_content_envelope` (после `from .critic import Critic`).

- [ ] **Step 3: Обновить message-событие в `run_agent`**

В `src/agent/loop.py` в `run_agent`, где шлётся событие `message`, добавить поле envelope:

```python
    runtime._notify(
        "message",
        {
            "content": result.final_answer or "",
            "envelope": (
                result.content_envelope.model_dump()
                if result.content_envelope is not None
                else None
            ),
            "error": result.error,
            "session_id": session_id,
        },
    )
```

(Заменить существующий блок, который без `envelope`.)

- [ ] **Step 4: Создать `tests/test_typed_finalize.py`**

```python
"""Тесты: finalize парсит конверт, run_agent шлёт envelope в message."""

import pytest

from src.agent.critic import Critic
from src.agent.loop import AgentRuntime, run_agent
from src.agent.tools import ToolContext
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.models.schemas import AgentGraphState, ContentType


class _TC:
    def parse_usage(self, raw):
        return TokenUsage()

    def estimate(self, text):
        return 0


class EnvelopePlanner(LLMClient):
    """Возвращает JSON-конверт сразу (без инструментов)."""

    def __init__(self, content: str):
        super().__init__(token_counter=_TC())
        self._content = content

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None):
        return LLMResponse(
            content=self._content,
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


class ApproveJudge(LLMClient):
    def __init__(self):
        super().__init__(token_counter=_TC())

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None):
        return LLMResponse(
            content='{"passed": true, "issues": []}',
            model=model,
            usage=TokenUsage(1, 1),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


def _runtime(llm, events=None):
    return AgentRuntime(
        llm=llm,
        models={"planner": "p", "fast": "f", "judge": "j"},
        tool_context=ToolContext(region="GLOBAL"),
        critic=Critic(llm=ApproveJudge(), model="j"),
        on_event=(lambda ev, data: events.append((ev, data)) if events is not None else None),
    )


@pytest.mark.asyncio
async def test_finalize_parses_envelope():
    planner = EnvelopePlanner('{"type": "quiz", "text": "Вопрос?", "payload": {"answer_type": "single", "options": ["a"]}}')
    rt = _runtime(planner)
    state = AgentGraphState(messages=[], final_answer='{"type": "quiz", "text": "Вопрос?", "payload": {"answer_type": "single", "options": ["a"]}}')
    out = await rt.finalize(state)
    assert out["content_envelope"]["type"] == "quiz"
    assert out["final_answer"] == "Вопрос?"


@pytest.mark.asyncio
async def test_run_agent_message_event_has_envelope():
    events = []
    planner = EnvelopePlanner('{"type": "theory", "text": "Объяснение $$x$$", "payload": {}}')
    rt = _runtime(planner, events)
    result = await run_agent(rt, [{"role": "user", "content": "hi"}])
    assert result.content_envelope is not None
    assert result.content_envelope.type == ContentType.THEORY
    msg_events = [data for ev, data in events if ev == "message"]
    assert msg_events, "ожидали событие message"
    assert msg_events[-1]["envelope"]["type"] == "theory"


@pytest.mark.asyncio
async def test_plain_text_falls_back_to_theory_in_finalize():
    planner = EnvelopePlanner("Обычный текст без JSON")
    rt = _runtime(planner)
    result = await run_agent(rt, [{"role": "user", "content": "hi"}])
    assert result.content_envelope is not None
    assert result.content_envelope.type == ContentType.THEORY
    assert result.content_envelope.text == "Обычный текст без JSON"
```

- [ ] **Step 5: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_typed_finalize.py tests/test_agent.py tests/test_integration.py tests/test_critic.py -q`
Expected: все PASS (включая прежние).

- [ ] **Step 6: Проверка ruff**

Run: `.venv/Scripts/ruff.exe check src/models/schemas.py src/agent/loop.py tests/test_typed_finalize.py`
Expected: All checks passed.

---

### Task 4: История сессий — конверт в записях, чистый LLM-контекст

**Files:**
- Modify: `src/api/session_store.py`
- Test: `tests/test_session_store.py`

**Interfaces:**
- Consumes: существующий `SessionStore`.
- Produces:
  - `append_message(session_id, role, content, meta: dict | None = None)` — запись хранит `{role, content, **meta}`.
  - `list_messages` возвращает полные записи (с метой).
  - `to_llm_context` возвращает только `{role, content}` (обрезает meta).
  - `consecutive_assistant_kind(session_id, kind: str) -> int` — сколько подряд последних assistant-сообщений имеют `meta.kind == kind`.

- [ ] **Step 1: Обновить `src/api/session_store.py`**

Заменить `append_message` и `to_llm_context`:

```python
    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        meta: dict | None = None,
    ) -> None:
        """Добавляет сообщение в историю; meta (kind/envelope/adaptive) кладётся рядом."""
        if role not in _VALID_ROLES:
            raise ValueError(f"Недопустимая роль: {role!r}; ожидается user или assistant")
        now = time.time()
        with self._lock:
            session = self._live_session_locked(session_id, now)
            if session is None:
                raise ValueError(f"Сессия {session_id!r} не найдена или истекла")
            record: dict = {"role": role, "content": content}
            if meta:
                record.update(meta)
            session.messages.append(record)
            if len(session.messages) > MAX_MESSAGES:
                del session.messages[:-MAX_MESSAGES]
            session.updated_at = now

    def consecutive_assistant_kind(self, session_id: str, kind: str) -> int:
        """Число подряд идущих assistant-сообщений с meta.kind == kind (с конца)."""
        now = time.time()
        with self._lock:
            session = self._live_session_locked(session_id, now)
            if session is None:
                return 0
            session.updated_at = now
            messages = list(session.messages)
        count = 0
        for message in reversed(messages):
            if message.get("role") != "assistant":
                break
            if message.get("kind") == kind:
                count += 1
            else:
                break
        return count
```

И в `to_llm_context` заменить сбор контекста так, чтобы в результат попадали только role/content:

```python
        context = [{"role": m["role"], "content": m["content"]} for m in window]
```

(строка, которая сейчас делает `context = list(window)`.)

- [ ] **Step 2: Добавить тесты в `tests/test_session_store.py`**

```python
def test_append_message_with_meta_roundtrip():
    store = SessionStore()
    s = store.create()
    store.append_message(s.session_id, "user", "hi")
    store.append_message(s.session_id, "assistant", "Привет", meta={"kind": "theory", "envelope": {"type": "theory"}})
    msgs = store.list_messages(s.session_id)
    assert msgs[1]["kind"] == "theory"
    assert msgs[1]["envelope"]["type"] == "theory"


def test_to_llm_context_strips_meta():
    store = SessionStore()
    s = store.create()
    store.append_message(s.session_id, "user", "hi")
    store.append_message(s.session_id, "assistant", "Привет", meta={"kind": "theory", "envelope": {}})
    ctx = store.to_llm_context(s.session_id)
    assert ctx == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Привет"}]


def test_consecutive_assistant_kind():
    store = SessionStore()
    s = store.create()
    store.append_message(s.session_id, "user", "hi")
    store.append_message(s.session_id, "assistant", "h1", meta={"kind": "hint"})
    store.append_message(s.session_id, "assistant", "h2", meta={"kind": "hint"})
    assert store.consecutive_assistant_kind(s.session_id, "hint") == 2
    store.append_message(s.session_id, "assistant", "т", meta={"kind": "theory"})
    assert store.consecutive_assistant_kind(s.session_id, "hint") == 0
```

- [ ] **Step 3: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_session_store.py -q`
Expected: все PASS (старые + 3 новых).

- [ ] **Step 4: ruff**

Run: `.venv/Scripts/ruff.exe check src/api/session_store.py tests/test_session_store.py`
Expected: All checks passed.

---

### Task 5: SQLite student store + adaptive-логика

**Files:**
- Create: `src/student/__init__.py`
- Create: `src/student/store.py`
- Create: `src/student/adaptive.py`
- Create: `tests/test_student_store.py`
- Create: `tests/test_adaptive.py`

**Interfaces:**
- Consumes: `sqlite3` stdlib, `settings`.
- Produces:
  - `student.store.StudentStore(db_path: str)`:
    - `upsert_student(student_id: str) -> None`
    - `get_student(student_id: str) -> dict | None` (поля из students + list topics не включён)
    - `list_topics(student_id: str) -> list[dict]` — `{topic, level, attempts, correct, last_seen}`
    - `touch_topic(student_id, topic, level_delta=0.0, correct: bool | None = None) -> float` — обновляет уровень/счётчики, возвращает новый уровень
    - `register_session(student_id, session_id, topic) -> None`
    - `get_topic_level(student_id, topic) -> float` (default 0.5)
    - `close()`
  - `student.adaptive.apply_delta(level: float, delta: float) -> float` — clamp01.
  - `student.adaptive.overall_level(topics: list[dict]) -> float` — среднее; пусто → 0.5.
  - `student.adaptive.pick_recommendation(topics, next_from_model: str | None = None) -> str | None`
    — приоритет: `next_from_model`; иначе тема с минимальным `level` (не равным 0.5 если есть выбор); пусто → None.

- [ ] **Step 1: Создать `src/student/__init__.py`**

```python
"""Персистентный профиль ученика (SQLite) и адаптивная логика."""
```

- [ ] **Step 2: Создать `src/student/store.py`**

```python
"""SQLite-хранилище профилей учеников: студенты, темы, сессии.

Использует один connection + threading.Lock. Ничего не пишем в логи —
store не должен ронять чат: вызывающие оборачивают в try/except.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
  student_id TEXT PRIMARY KEY,
  name TEXT DEFAULT '',
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS topics (
  student_id TEXT,
  topic TEXT,
  level REAL DEFAULT 0.5,
  attempts INT DEFAULT 0,
  correct INT DEFAULT 0,
  last_seen REAL,
  PRIMARY KEY (student_id, topic)
);
CREATE TABLE IF NOT EXISTS sessions (
  student_id TEXT,
  session_id TEXT,
  topic TEXT,
  started_at REAL,
  ended_at REAL,
  UNIQUE (student_id, session_id)
);
"""


class StudentStore:
    """Потокобезопасное SQLite-хранилище профилей учеников."""

    def __init__(self, db_path: str):
        if db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def _exec(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def upsert_student(self, student_id: str) -> None:
        now = time.time()
        self._exec(
            "INSERT INTO students (student_id, created_at, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(student_id) DO UPDATE SET updated_at = excluded.updated_at",
            (student_id, now, now),
        )

    def get_student(self, student_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM students WHERE student_id = ?", (student_id,))
        return rows[0] if rows else None

    def list_topics(self, student_id: str) -> list[dict]:
        return self._rows(
            "SELECT topic, level, attempts, correct, last_seen "
            "FROM topics WHERE student_id = ? ORDER BY last_seen DESC",
            (student_id,),
        )

    def touch_topic(
        self,
        student_id: str,
        topic: str,
        level_delta: float = 0.0,
        correct: bool | None = None,
    ) -> float:
        from .adaptive import apply_delta

        rows = self._rows(
            "SELECT level, attempts, correct FROM topics WHERE student_id = ? AND topic = ?",
            (student_id, topic),
        )
        if rows:
            level = rows[0]["level"]
            attempts = rows[0]["attempts"] + 1
            correct_count = rows[0]["correct"] + (1 if correct else 0)
        else:
            level = 0.5
            attempts = 1
            correct_count = 1 if correct else 0
        new_level = apply_delta(level, level_delta)
        now = time.time()
        self._exec(
            "INSERT INTO topics (student_id, topic, level, attempts, correct, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(student_id, topic) DO UPDATE SET "
            "level = excluded.level, attempts = excluded.attempts, "
            "correct = excluded.correct, last_seen = excluded.last_seen",
            (student_id, topic, new_level, attempts, correct_count, now),
        )
        self.upsert_student(student_id)
        return new_level

    def register_session(self, student_id: str, session_id: str, topic: str) -> None:
        now = time.time()
        self._exec(
            "INSERT OR IGNORE INTO sessions (student_id, session_id, topic, started_at) "
            "VALUES (?, ?, ?, ?)",
            (student_id, session_id, topic, now),
        )

    def get_topic_level(self, student_id: str, topic: str) -> float:
        rows = self._rows(
            "SELECT level FROM topics WHERE student_id = ? AND topic = ?",
            (student_id, topic),
        )
        return rows[0]["level"] if rows else 0.5

    def close(self) -> None:
        with self._lock:
            self._conn.close()
```

- [ ] **Step 3: Создать `src/student/adaptive.py`**

```python
"""Адаптивная логика: обновление уровня, средний уровень, рекомендации."""

from __future__ import annotations

from typing import Any


def apply_delta(level: float, delta: float) -> float:
    """Сдвигает уровень на delta и ограничивает отрезком [0, 1]."""
    return max(0.0, min(1.0, level + delta))


def overall_level(topics: list[dict[str, Any]]) -> float:
    """Средний уровень по всем темам; при отсутствии тем — 0.5."""
    if not topics:
        return 0.5
    return sum(t.get("level", 0.5) for t in topics) / len(topics)


def pick_recommendation(
    topics: list[dict[str, Any]],
    next_from_model: str | None = None,
) -> str | None:
    """Рекомендация: сначала от модели, иначе тема с минимальным уровнем."""
    if next_from_model and next_from_model.strip():
        return next_from_model.strip()
    if not topics:
        return None
    weakest = min(topics, key=lambda t: t.get("level", 0.5))
    return weakest["topic"]
```

- [ ] **Step 4: Создать `tests/test_student_store.py`**

```python
"""Тесты SQLite-хранилища профилей учеников."""

import pytest

from src.student.store import StudentStore


@pytest.fixture
def store(tmp_path):
    s = StudentStore(str(tmp_path / "students.db"))
    yield s
    s.close()


def test_upsert_and_get_student(store):
    store.upsert_student("stu_1")
    row = store.get_student("stu_1")
    assert row is not None and row["student_id"] == "stu_1"
    assert store.get_student("stu_x") is None


def test_touch_topic_updates_level_and_attempts(store):
    store.touch_topic("stu_1", "интегралы", level_delta=0.3, correct=True)
    store.touch_topic("stu_1", "интегралы", level_delta=-0.1, correct=False)
    topics = store.list_topics("stu_1")
    assert len(topics) == 1
    assert topics[0]["attempts"] == 2
    assert topics[0]["correct"] == 1
    assert topics[0]["level"] == pytest.approx(0.7, abs=1e-6)


def test_topic_level_default_and_after_update(store):
    assert store.get_topic_level("stu_1", "нет темы") == 0.5
    store.touch_topic("stu_1", "производные", level_delta=0.5, correct=True)
    assert store.get_topic_level("stu_1", "производные") == pytest.approx(1.0)


def test_register_session(store):
    store.upsert_student("stu_1")
    store.register_session("stu_1", "ses_1", "тема")
    store.register_session("stu_1", "ses_1", "тема")  # idempotent
    rows = store.list_topics("stu_1")
    assert rows == []
```

- [ ] **Step 5: Создать `tests/test_adaptive.py`**

```python
"""Тесты адаптивной логики."""

from src.student.adaptive import apply_delta, overall_level, pick_recommendation


def test_apply_delta_clamps():
    assert apply_delta(0.8, 0.5) == 1.0
    assert apply_delta(0.2, -0.5) == 0.0
    assert apply_delta(0.5, 0.3) == pytest.approx(0.8)


def test_overall_level_empty_and_mean():
    import pytest

    assert overall_level([]) == 0.5
    topics = [{"level": 0.6}, {"level": 1.0}, {"level": 0.2}]
    assert overall_level(topics) == pytest.approx(0.6)


def test_pick_recommendation_prefers_model():
    topics = [{"topic": "a", "level": 0.2}, {"topic": "b", "level": 0.9}]
    assert pick_recommendation(topics, next_from_model="теорема виета") == "теорема виета"


def test_pick_recommendation_weakest_topic():
    topics = [{"topic": "a", "level": 0.9}, {"topic": "b", "level": 0.3}]
    assert pick_recommendation(topics) == "b"


def test_pick_recommendation_empty():
    assert pick_recommendation([]) is None
```

- [ ] **Step 6: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_student_store.py tests/test_adaptive.py -q`
Expected: 6 + 5 PASS.

- [ ] **Step 7: ruff**

Run: `.venv/Scripts/ruff.exe check src/student/ tests/test_student_store.py tests/test_adaptive.py`
Expected: All checks passed.

---

### Task 6: Config — путь к БД студентов

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Добавить поле в `src/config.py`**

В секцию после `# Эмбеддинги` (перед `# Безопасность`) добавить:

```python
    # Профиль ученика
    student_db_path: str = Field(
        default="data/students.db", description="Путь к SQLite-файлу профилей"
    )
```

- [ ] **Step 2: Добавить переменную в `.env.example`**

```text
# Профиль ученика (SQLite)
TUTOR_STUDENT_DB_PATH=data/students.db
```

- [ ] **Step 3: Проверка**

Run: `.venv/Scripts/python.exe -c "from src.config import settings; print(settings.student_db_path)"`
Expected: `data/students.db`

- [ ] **Step 4: ruff**

Run: `.venv/Scripts/ruff.exe check src/config.py`
Expected: All checks passed.

---

### Task 7: Сервер — student_id, kind, envelope/adaptive в ответах, /student

**Files:**
- Modify: `src/api/server.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `StudentStore`, `student.adaptive.*` (Task 5), `ContentEnvelope` (Task 1), `SessionStore.consecutive_assistant_kind` (Task 4), `parse_content_envelope` (Task 1).
- Produces:
  - `ChatRequest` + поля `student_id: str = ""`, `kind: Literal["message","hint_request"] = "message"`.
  - `ChatResponse` + поля `envelope: dict | None`, `adaptive: dict`.
  - SSE `message` data: `{content, envelope, adaptive, error, session_id}`.
  - `GET /student/{student_id}`.
  - `create_app(student_store: StudentStore | None = None, ...)`.

- [ ] **Step 1: Добавить поля в `ChatRequest` и `ChatResponse`**

В `ChatRequest` после `session_id`:

```python
    student_id: str = Field(default="", description="Идентификатор ученика (опц.)")
```

После `grade`:

```python
    kind: Literal["message", "hint_request"] = Field(
        default="message", description="hint_request — кнопка «Подсказка»"
    )
```

В `ChatResponse` после `reply`:

```python
    envelope: dict | None = Field(default=None, description="Конверт типизированного контента")
    adaptive: dict = Field(default_factory=dict, description="Адаптивный блок для панели")
```

Импорт в начало файла (в существующий блок `from ..models.schemas import AgentGraphState`):

```python
from ..models.schemas import AgentGraphState, ContentEnvelope
from ..student.adaptive import overall_level, pick_recommendation
from ..student.store import StudentStore
```

- [ ] **Step 2: Хелперы для adaptive-блока и id**

Добавить после `_to_response`:

```python
_HINT_SERVICE_TEXT = "[ученик просит подсказку к текущей задаче]"
_MAX_HINTS_IN_A_ROW = 2


def _gen_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _build_adaptive(
    store: StudentStore,
    student_id: str,
    topic: str,
    difficulty: str,
    next_from_model: str | None = None,
) -> dict:
    """Собирает adaptive-блок для ответа фронтенду."""
    topics = []
    if store is not None:
        store.upsert_student(student_id)
        topics = store.list_topics(student_id)
    topic_level = store.get_topic_level(student_id, topic) if store is not None and topic else 0.5
    rec = None
    if store is not None:
        rec = pick_recommendation(topics, next_from_model=next_from_model)
    return {
        "student_id": student_id,
        "current_knowledge_level": overall_level(topics),
        "topic": topic,
        "topic_level": topic_level,
        "attempts": next((t["attempts"] for t in topics if t["topic"] == topic), 0),
        "correct": next((t["correct"] for t in topics if t["topic"] == topic), 0),
        "difficulty": difficulty,
        "recommended_next": rec,
    }
```

- [ ] **Step 3: Переписать `_run_chat`**

Заменить тело `_run_chat` так, чтобы: резолвился `student_id`, поддерживался `kind`, обновлялся профиль после evaluation, возвращался envelope/adaptive. Новый код:

```python
async def _run_chat(
    app: FastAPI,
    body: ChatRequest,
    on_event: Callable[[str, dict], None] | None = None,
) -> tuple[AgentGraphState, str, str, ContentEnvelope | None, dict]:
    """Общий путь POST /chat и SSE.

    Возвращает (state, session_id, trace_id, envelope, adaptive).
    Пишет профиль в SQLite после evaluation-ходов; сбой БД не роняет чат.
    """
    store: StudentStore | None = app.state.student_store
    profile = body.student_profile.model_dump(exclude_none=True)
    session = app.state.sessions.get_or_create(
        session_id=body.session_id or None, student_profile=profile
    )
    session_id = session.session_id
    student_id = body.student_id or _gen_id("stu")

    if body.topic:
        await _provision_for(app, topic=body.topic, subject=body.subject, grade=body.grade)

    hint_request = body.kind == "hint_request"
    if hint_request:
        # Guard: не более _MAX_HINTS_IN_A_ROW подсказок подряд на одну задачу.
        if app.state.sessions.consecutive_assistant_kind(session_id, "hint") >= _MAX_HINTS_IN_A_ROW:
            env = ContentEnvelope(
                type="hint",
                text="Вы уже получили несколько подсказок — попробуйте решить задачу сами, "
                     "а если совсем сложно, задайте вопрос иначе.",
            )
            answer = env.text
            app.state.sessions.append_message(
                session_id, "assistant", answer, meta={"kind": env.type.value, "envelope": env.model_dump()}
            )
            adaptive = _build_adaptive(store, student_id, body.topic, "medium")
            state = AgentGraphState(messages=[], final_answer=answer, terminated=True)
            state.content_envelope = env
            return state, session_id, "", env, adaptive
        # Служебный контекст вместо обычного сообщения ученика
        context = app.state.sessions.to_llm_context(session_id)
        context.append({"role": "user", "content": _HINT_SERVICE_TEXT})
    else:
        _append_user_if_new(app.state.sessions, session_id, body.message)
        context = app.state.sessions.to_llm_context(session_id)

    runtime: AgentRuntime = app.state.runtime_factory()
    rag = app.state.rag_engine
    if rag is not None and getattr(runtime.tool_context, "rag", None) is None:
        runtime.tool_context.rag = rag
    if on_event is not None:
        runtime.on_event = on_event

    trace_id = JsonlLogger.new_trace_id()
    state = await run_agent(
        runtime,
        messages=context,
        session_id=session_id,
        student_profile=profile,
        trace_id=trace_id,
    )

    envelope = state.content_envelope
    reply = state.final_answer or ""
    if envelope is None:
        envelope = ContentEnvelope(type="theory", text=reply)
    if reply:
        app.state.sessions.append_message(
            session_id,
            "assistant",
            reply,
            meta={"kind": envelope.type.value, "envelope": envelope.model_dump()},
        )

    # Обновление профиля после evaluation-хода (сбой БД не роняет чат)
    if store is not None and envelope.type.value == "evaluation":
        payload = envelope.payload or {}
        topic = body.topic
        if topic:
            try:
                store.upsert_student(student_id)
                store.register_session(student_id, session_id, topic)
                store.touch_topic(
                    student_id,
                    topic,
                    level_delta=float(payload.get("knowledge_delta", 0.0)),
                    correct=bool(payload.get("correct", False)),
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[student] не удалось обновить профиль: {exc}")
    elif store is not None:
        try:
            store.upsert_student(student_id)
        except Exception:  # noqa: BLE001
            pass

    next_from_model = None
    if envelope.payload and envelope.payload.get("next_topic"):
        next_from_model = envelope.payload["next_topic"]
    adaptive = _build_adaptive(store, student_id, body.topic, state.difficulty, next_from_model)
    return state, session_id, trace_id, envelope, adaptive
```

Примечание: в hint-коротком замыкании используется локальная переменная `adaptive` до `state` — порядок не важен, оба возвращаются. `_append_user_if_new` и `to_llm_context` уже существуют.

- [ ] **Step 4: Переписать `_to_response`**

```python
def _to_response(
    state: AgentGraphState,
    trace_id: str,
    session_id: str,
    envelope: ContentEnvelope | None,
    adaptive: dict,
) -> ChatResponse:
    """Маппит состояние в стабильный ответ + конверт + adaptive."""
    reply = state.final_answer or (envelope.text if envelope else "")
    error = state.error
    if not reply:
        reply = FALLBACK_REPLY
        error = error or "агент не сформировал ответ"
    return ChatResponse(
        reply=reply,
        envelope=envelope.model_dump() if envelope is not None else None,
        adaptive=adaptive,
        error=error,
        trace_id=trace_id,
        session_id=session_id,
        difficulty=state.difficulty,
        steps=len(state.steps),
        terminated=bool(state.terminated),
    )
```

- [ ] **Step 5: Обновить `/chat` и `/chat/stream`**

В `chat()`:

```python
        try:
            state, session_id, trace_id, envelope, adaptive = await asyncio.wait_for(
                _run_chat(request.app, body),
                timeout=settings.max_agent_time_sec + 30,
            )
        except TimeoutError:
            return ChatResponse(
                reply=FALLBACK_REPLY,
                error="превышен таймаут агента",
                trace_id="",
                session_id=body.session_id,
                adaptive={"student_id": body.student_id, "recommended_next": None},
                terminated=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"внутренняя ошибка: {exc}") from exc
        return _to_response(state, trace_id=trace_id, session_id=session_id, envelope=envelope, adaptive=adaptive)
```

В `chat_stream`: внутри `run()` собрать результат и после него послать собственные `message`(с envelope+adaptive) и `done` (т.к. `run_agent`-овские message/done приходят без adaptive). Нужно игнорировать `message`/`done` от агента и слать свои. Реализация:

```python
        async def run() -> None:
            try:
                state, session_id, trace_id, envelope, adaptive = await _run_chat(
                    request.app, body, on_event=on_event
                )
            except Exception as exc:  # noqa: BLE001
                events.put_nowait(("error", {"message": str(exc)}))
                events.put_nowait(("done", {"session_id": body.session_id}))
                return
            content = state.final_answer or (envelope.text if envelope else "")
            events.put_nowait(
                (
                    "message",
                    {
                        "content": content,
                        "envelope": envelope.model_dump() if envelope is not None else None,
                        "adaptive": adaptive,
                        "error": state.error,
                        "session_id": session_id,
                    },
                )
            )
            events.put_nowait(
                ("done", {"session_id": session_id, "trace_id": trace_id, "steps": len(state.steps)})
            )
```

И фильтр в `on_event`, чтобы не дублировать message/done от агента:

```python
        def on_event(event: str, data: dict[str, Any]) -> None:
            if event in ("message", "done"):
                return  # финальные события шлём сами, с adaptive
            events.put_nowait((event, data))
```

- [ ] **Step 6: `/student` эндпоинт + DI в `create_app`**

Добавить параметр и state в `create_app`:

```python
def create_app(
    runtime_factory: RuntimeFactory | None = None,
    sessions: SessionStore | None = None,
    rag_engine: RAGEngine | None = None,
    provisioner: Provisioner | None = None,
    student_store: StudentStore | None = None,
) -> FastAPI:
```

После `app.state.knowledge_lock = threading.Lock()`:

```python
    app.state.student_store = student_store or StudentStore(settings.student_db_path)
```

Добавить роут перед `return app`:

```python
    @app.get("/student/{student_id}")
    def student_profile(student_id: str) -> dict[str, Any]:
        """Профиль ученика: темы, уровни, рекомендация."""
        store: StudentStore = app.state.student_store
        if store is None or store.get_student(student_id) is None:
            raise HTTPException(status_code=404, detail="студент не найден")
        topics = store.list_topics(student_id)
        return {
            "student_id": student_id,
            "topics": topics,
            "recommended_next": pick_recommendation(topics),
        }
```

- [ ] **Step 7: Обновить тесты `tests/test_api.py`**

В `_fake_runtime_factory` вернуть как есть. Заменить фикстуру, добавив tmp-стор:

```python
@pytest.fixture
def client(tmp_path):
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(runtime_factory=_fake_runtime_factory, student_store=store)
    with TestClient(app) as c:
        yield c
    store.close()
```

Добавить импорт `from src.student.store import StudentStore`.

Обновить `test_chat_valid_message`: вместо `_to_response`, ответ теперь содержит `envelope` и `adaptive`. Добавить проверки:

```python
    assert body["envelope"] is not None
    assert body["envelope"]["type"] in {"theory", "practice", "hint", "quiz", "evaluation"}
    assert "student_id" in body["adaptive"]
```

(существующие проверки `reply`, `error is None`, `session_id`, `difficulty`, `steps`, `terminated` остаются.)

Добавить тест SSE на наличие envelope/adaptive и тест hint_request-короткого замыкания:

```python
def test_chat_stream_message_has_envelope_and_adaptive(client):
    with client.stream("POST", "/chat/stream", json={"message": "Привет", "session_id": "sse-env"}) as resp:
        text = "".join(resp.iter_text())
    assert '"envelope"' in text
    assert '"adaptive"' in text


def test_student_endpoint(client):
    # после чата студент создаётся
    client.post("/chat", json={"message": "hello", "student_id": "stu_e2e"})
    resp = client.get("/student/stu_e2e")
    assert resp.status_code == 200
    assert resp.json()["student_id"] == "stu_e2e"
    assert resp.json()["topics"] == []
    assert client.get("/student/unknown").status_code == 404


def test_hint_request_does_not_append_user_message(client):
    client.post("/chat", json={"message": "как решить?", "session_id": "sess-h1"})
    client.post("/chat", json={"message": "подсказка", "kind": "hint_request", "session_id": "sess-h1"})
    hist = client.get("/chat/history/sess-h1").json()["messages"]
    roles = [m["role"] for m in hist]
    assert roles == ["user", "assistant", "assistant"]
    user_contents = [m["content"] for m in hist if m["role"] == "user"]
    assert "подсказка" not in user_contents
```

- [ ] **Step 8: Запустить тесты**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api.py tests/test_typed_finalize.py -q`
Expected: все PASS.

- [ ] **Step 9: ruff**

Run: `.venv/Scripts/ruff.exe check src/api/server.py tests/test_api.py`
Expected: All checks passed.

---

### Task 8: Полный прогон

- [ ] **Step 1: Полный тест-сьют**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: все тесты PASS (124 + новые).

- [ ] **Step 2: ruff по всему проекту**

Run: `.venv/Scripts/ruff.exe check src/ tests/`
Expected: All checks passed.

- [ ] **Step 3: Smoke импортов**

Run: `.venv/Scripts/python.exe -c "from src.api.server import app; from src.student.store import StudentStore; print('ok')"`
Expected: `ok`

---

## Self-Review

- **Spec coverage:** §1 конверт — Task 1/2/3; §2 профиль (SQLite, adaptive, /student) — Task 5/6/7; §3 API (student_id, kind, envelope/adaptive, history с meta, чистый LLM-контекст) — Task 3/4/7; §5 лимиты (≤2 подсказки) — Task 7 guard.
- **Плейсхолдеры:** нет TBD/TODO; весь код приведён.
- **Type consistency:** `ContentEnvelope`, `parse_content_envelope`, `StudentStore.*`, `SessionStore.append_message(..., meta)`, `consecutive_assistant_kind`, `_build_adaptive` — имена согласованы между задачами.
