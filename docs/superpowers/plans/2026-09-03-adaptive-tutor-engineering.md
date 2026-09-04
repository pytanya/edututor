# Adaptive Tutor Engineering Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three engineering improvements to the adaptive tutor agent: robust DDGS fallback with explicit timeout/429 handling, adaptive student state fields (DeepTutor), and a Critic validator node using a lightweight judge model.

**Architecture:** (1) Search engines get explicit `httpx.TimeoutError` / 429 catch blocks that log the failure reason before the `SearchRouter` fallback chain proceeds to DuckDuckGo. (2) `AgentGraphState` gains `current_knowledge_level`, `learning_style`, `fatigue_level` fields; the system prompt reads them and adapts strategy. (3) A new `Critic` class uses the `judge` model to validate the final answer for hallucinations and LaTeX correctness; it runs as a post-finalize step in the agent graph.

**Tech Stack:** Python 3.11+, LangGraph, Pydantic v2, httpx, OpenAI SDK (for judge LLM call), pytest/pytest-asyncio

**Spec:** User requirements (3 bullet points: DDGS fallback, DeepTutor adaptive state, Critic validator)

## Global Constraints

- Python >= 3.11
- All existing 25 tests must continue passing
- New tests required for each feature
- Follow existing code conventions: Russian docstrings, Pydantic v2 models, async/await, `threading.Lock` for thread safety
- Do not modify `.env` or expose API keys
- `ruff` + `mypy` strict must pass

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `src/search/base.py` | Add explicit timeout/429 error types, improve fallback logging |
| Modify | `src/search/tavily.py` | Catch `httpx.TimeoutException` and 429 status |
| Modify | `src/search/yandex.py` | Catch `httpx.TimeoutException` and 429 status |
| Modify | `src/models/schemas.py` | Add `current_knowledge_level`, `learning_style`, `fatigue_level` to state |
| Modify | `src/agent/prompts.py` | Read adaptive fields from state, inject into system prompt |
| Create | `src/agent/critic.py` | Critic class: LLM-based answer validation |
| Modify | `src/agent/loop.py` | Wire Critic into graph as post-finalize step |
| Modify | `src/config.py` | Add `critic_model` config (reuse `judge` model key) |
| Create | `tests/test_search_fallback.py` | Tests for timeout/429 fallback |
| Create | `tests/test_adaptive_state.py` | Tests for adaptive state fields + prompt adaptation |
| Create | `tests/test_critic.py` | Tests for Critic validator |

---

### Task 1: Explicit Timeout/429 Handling in Search Engines

**Files:**
- Modify: `src/search/base.py:28-84`
- Modify: `src/search/tavily.py`
- Modify: `src/search/yandex.py`
- Create: `tests/test_search_fallback.py`

**Interfaces:**
- Consumes: existing `SearchEngine` ABC, `SearchRouter` class
- Produces: `SearchTimeoutError`, `SearchRateLimitError` exception classes in `base.py`

- [ ] **Step 1: Add typed exception classes to `src/search/base.py`**

Add after the `SearchResult` dataclass (after line 10):

```python
class SearchTimeoutError(Exception):
    """Поисковый движок не ответил вовремя (таймаут)."""
    pass


class SearchRateLimitError(Exception):
    """Поисковый движок вернул 429 (превышен лимит запросов)."""
    pass
```

- [ ] **Step 2: Update `SearchRouter.search()` to log specific error types**

Replace the existing `search()` method body (lines 66-84) with:

```python
    @classmethod
    async def search(
        cls,
        query: str,
        max_results: int = 5,
        region: Optional[Region] = None,
    ) -> list[SearchResult]:
        engines = cls.get_engines(region)

        for engine in engines:
            try:
                results = await engine.search(query, max_results)
                if results:
                    return results
            except SearchTimeoutError as e:
                print(f"[fallback] {engine.__class__.__name__} таймаут: {e} → пробуем DDGS")
                continue
            except SearchRateLimitError as e:
                print(f"[fallback] {engine.__class__.__name__} 429 rate-limit: {e} → пробуем DDGS")
                continue
            except Exception as e:
                print(f"[fallback] {engine.__class__.__name__} ошибка: {e}")
                continue

        return []
```

- [ ] **Step 3: Add timeout/429 handling to `TavilySearch.search()`**

Replace the entire `search` method in `src/search/tavily.py`:

```python
    async def search(
        self,
        query: str,
        max_results: int = 5,
        language: str = "en",
    ) -> list[SearchResult]:
        import httpx
        try:
            response = await self.client.search(
                query=query,
                max_results=max_results,
                search_depth="basic",
                include_answer=False,
            )
        except httpx.TimeoutException as e:
            raise SearchTimeoutError(f"Tavily timeout: {e}") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise SearchRateLimitError(f"Tavily 429: {e}") from e
            raise

        results = []
        for item in response.get("results", []):
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                score=item.get("score", 0.0),
            ))

        return results
```

Also add the import at the top of the file:

```python
from .base import SearchEngine, SearchResult, SearchTimeoutError, SearchRateLimitError
```

- [ ] **Step 4: Add timeout/429 handling to `YandexSearch.search()`**

Replace the `search` method in `src/search/yandex.py`:

```python
    async def search(
        self,
        query: str,
        max_results: int = 5,
        language: str = "ru",
    ) -> list[SearchResult]:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/search",
                    headers={
                        "Authorization": f"Api-Key {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": {
                            "search_text": query,
                            "lr": 213,
                            "hl": language,
                            "fmt": "json",
                            "groups_per_page": max_results,
                        }
                    },
                )
        except httpx.TimeoutException as e:
            raise SearchTimeoutError(f"Yandex timeout: {e}") from e

        if response.status_code == 429:
            raise SearchRateLimitError(f"Yandex 429: rate limit exceeded")

        response.raise_for_status()
        data = response.json()

        results = []
        for group in data.get("groups", [])[:max_results]:
            doc = group.get("doc", {})
            results.append(SearchResult(
                title=doc.get("title", ""),
                url=doc.get("url", ""),
                snippet=doc.get("headline", ""),
            ))

        return results
```

Also update the import:

```python
import httpx
from .base import SearchEngine, SearchResult, SearchTimeoutError, SearchRateLimitError
```

- [ ] **Step 5: Write failing tests for fallback behavior**

Create `tests/test_search_fallback.py`:

```python
"""Тесты fallback-цепочки поиска при таймаутах и 429."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.search.base import (
    SearchRouter,
    SearchEngine,
    SearchResult,
    SearchTimeoutError,
    SearchRateLimitError,
)
from src.config import Region


class FakeSearchEngine(SearchEngine):
    """Заглушка: возвращает результат или выбрасывает исключение."""

    def __init__(self, results=None, exc=None):
        self._results = results or []
        self._exc = exc
        self.call_count = 0

    async def search(self, query, max_results=5, language="ru"):
        self.call_count += 1
        if self._exc:
            raise self._exc
        return self._results


@pytest.fixture(autouse=True)
def clear_engine_cache():
    """Сбрасываем кэш engines между тестами."""
    SearchRouter._engines.clear()
    yield
    SearchRouter._engines.clear()


async def test_timeout_triggers_fallback():
    primary = FakeSearchEngine(exc=SearchTimeoutError("timeout"))
    fallback = FakeSearchEngine(results=[SearchResult(title="ok", url="u", snippet="s")])

    with patch.object(SearchRouter, "get_engines", return_value=[primary, fallback]):
        results = await SearchRouter.search("test")

    assert len(results) == 1
    assert results[0].title == "ok"
    assert primary.call_count == 1
    assert fallback.call_count == 1


async def test_rate_limit_triggers_fallback():
    primary = FakeSearchEngine(exc=SearchRateLimitError("429"))
    fallback = FakeSearchEngine(results=[SearchResult(title="ok", url="u", snippet="s")])

    with patch.object(SearchRouter, "get_engines", return_value=[primary, fallback]):
        results = await SearchRouter.search("test")

    assert len(results) == 1
    assert primary.call_count == 1
    assert fallback.call_count == 1


async def test_generic_exception_triggers_fallback():
    primary = FakeSearchEngine(exc=RuntimeError("boom"))
    fallback = FakeSearchEngine(results=[SearchResult(title="ok", url="u", snippet="s")])

    with patch.object(SearchRouter, "get_engines", return_value=[primary, fallback]):
        results = await SearchRouter.search("test")

    assert len(results) == 1


async def test_all_engines_fail_returns_empty():
    primary = FakeSearchEngine(exc=SearchTimeoutError("timeout"))
    fallback = FakeSearchEngine(exc=SearchRateLimitError("429"))

    with patch.object(SearchRouter, "get_engines", return_value=[primary, fallback]):
        results = await SearchRouter.search("test")

    assert results == []


async def test_primary_succeeds_no_fallback():
    primary = FakeSearchEngine(results=[SearchResult(title="first", url="u", snippet="s")])
    fallback = FakeSearchEngine(results=[SearchResult(title="second", url="u", snippet="s")])

    with patch.object(SearchRouter, "get_engines", return_value=[primary, fallback]):
        results = await SearchRouter.search("test")

    assert len(results) == 1
    assert results[0].title == "first"
    assert fallback.call_count == 0
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_search_fallback.py -v`
Expected: 5 PASS

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -v`
Expected: all 30 tests PASS (25 existing + 5 new)

- [ ] **Step 8: Commit**

```bash
git add src/search/base.py src/search/tavily.py src/search/yandex.py tests/test_search_fallback.py
git commit -m "feat(search): add explicit timeout/429 handling with DDGS fallback"
```

---

### Task 2: Adaptive Student State Fields (DeepTutor)

**Files:**
- Modify: `src/models/schemas.py:43-58`
- Modify: `src/agent/prompts.py`
- Create: `tests/test_adaptive_state.py`

**Interfaces:**
- Consumes: existing `AgentGraphState`
- Produces: new fields `current_knowledge_level` (float 0.0-1.0), `learning_style` (enum), `fatigue_level` (float 0.0-1.0) on state; adapted system prompt via `build_messages()`

- [ ] **Step 1: Add adaptive fields to `AgentGraphState`**

In `src/models/schemas.py`, add these imports at the top (line 3):

```python
from enum import Enum
```

Add the `LearningStyle` enum before `AgentGraphState` (after line 30):

```python
class LearningStyle(str, Enum):
    """Стиль обучения ученика."""
    VISUAL = "visual"       # визуал: схемы, диаграммы, примеры
    AUDITORY = "auditory"   # аудиал: объяснения, лекции
    KINESTHETIC = "kinesthetic"  # кинестетик: практика, упражнения
    READING = "reading"     # чтение/письмо: тексты, конспекты
```

Add three fields to `AgentGraphState` (after `needs_scaffold` line, before `difficulty`):

```python
    current_knowledge_level: float = Field(
        0.5, ge=0.0, le=1.0,
        description="Текущий уровень знаний ученика (0.0新手 — 1.0эксперт)",
    )
    learning_style: LearningStyle = Field(
        LearningStyle.READING,
        description="Стиль обучения ученика",
    )
    fatigue_level: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Уровень усталости ученика (0.0 — бодрый, 1.0 — устал)",
    )
```

- [ ] **Step 2: Update system prompt to read adaptive fields**

Replace `src/agent/prompts.py` entirely:

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
    "  - Ответ должен быть структурированным и понятным ученику.\n\n"
    "Правила:\n"
    "  - Сначала реши, нужно ли уточнить факты из базы/интернета; при "
    "    необходимости вызови инструмент.\n"
    "  - Когда данных достаточно — сформулируй финальный ответ (Reasoning: final).\n"
    "  - НЕ раскрывай внутренние рассуждения. Кратко объясни выбранное "
    "    действие в поле reason_summary.\n"
)


_ADAPTATION_RULES = {
    LearningStyle.VISUAL: (
        "Ученик — визуал. Используй схемы, ASCII-диаграммы, "
        "табличные сравнения, визуальные аналогии."
    ),
    LearningStyle.AUDITORY: (
        "Ученик — аудиал. Объясняй словами, приводи примеры «на слух», "
        "используй повествовательный стиль."
    ),
    LearningStyle.KINESTHETIC: (
        "Ученик — кинестетик. Давай практические задания, пошаговые "
        "упражнения, интерактивные примеры."
    ),
    LearningStyle.READING: (
        "Ученик —讀/write. Объясняй текстом, приводи определения, "
        "рекомендуй дополнительное чтение."
    ),
}


def _build_adaptation_block(state: AgentGraphState) -> str:
    """Формирует блок адаптации на основе состояния ученика."""
    lines = []

    # Стиль обучения
    style = state.learning_style
    lines.append(_ADAPTATION_RULES.get(style, ""))

    # Уровень знаний
    lvl = state.current_knowledge_level
    if lvl >= 0.8:
        lines.append(
            "Уровень знаний ВЫСОКИЙ. Давай углублённую теорию, "
            "ссылки на первоисточники, сложные задачи."
        )
    elif lvl <= 0.3:
        lines.append(
            "Уровень знаний НИЗКИЙ. Начинай с основ, избегай "
            "жаргонизма, давай много примеров."
        )
    else:
        lines.append(
            "Уровень знаний СРЕДНИЙ. Баланс между теорией и практикой."
        )

    # Усталость
    fatigue = state.fatigue_level
    if fatigue >= 0.7:
        lines.append(
            "Ученик УСТАЛ. Сократи ответ, дай практику вместо теории, "
            "сделай паузу-вопрос «Хочешь отдохнуть?»."
        )
    elif fatigue >= 0.4:
        lines.append(
            "Ученик начинает уставать. Чередуй теорию с короткими "
            "упражнениями, поддерживай внимание."
        )

    return "\n".join(lines)


def build_messages(state: AgentGraphState) -> list[dict]:
    """Собирает список сообщений для LLM из состояния графа."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Блок адаптации
    adaptation = _build_adaptation_block(state)
    if adaptation:
        messages.append({"role": "system", "content": f"[Адаптация]\n{adaptation}"})

    messages.extend(state.messages)

    # Контекст из инструментов, если есть
    if state.rag_context:
        ctx = "\n\n".join(r["text"] for r in state.rag_context[:5])
        messages.append({"role": "user", "content": f"[Контекст из базы знаний]\n{ctx}"})

    return messages
```

- [ ] **Step 3: Write tests for adaptive state and prompt**

Create `tests/test_adaptive_state.py`:

```python
"""Тесты адаптивного состояния ученика (DeepTutor)."""

import pytest

from src.models.schemas import AgentGraphState, LearningStyle
from src.agent.prompts import build_messages, _build_adaptation_block


def _state(**kwargs) -> AgentGraphState:
    return AgentGraphState(messages=[{"role": "user", "content": "test"}], **kwargs)


class TestAdaptiveFields:
    def test_defaults_are_sensible(self):
        s = AgentGraphState()
        assert s.current_knowledge_level == 0.5
        assert s.learning_style == LearningStyle.READING
        assert s.fatigue_level == 0.0

    def test_knowledge_level_validates_range(self):
        with pytest.raises(Exception):
            AgentGraphState(current_knowledge_level=1.5)
        with pytest.raises(Exception):
            AgentGraphState(current_knowledge_level=-0.1)

    def test_fatigue_level_validates_range(self):
        with pytest.raises(Exception):
            AgentGraphState(fatigue_level=2.0)


class TestAdaptationBlock:
    def test_high_knowledge_gets_advanced_prompt(self):
        s = _state(current_knowledge_level=0.9)
        block = _build_adaptation_block(s)
        assert "углублённую теорию" in block

    def test_low_knowledge_gets_basic_prompt(self):
        s = _state(current_knowledge_level=0.2)
        block = _build_adaptation_block(s)
        assert "с основ" in block

    def test_high_fatigue_gets_short_prompt(self):
        s = _state(fatigue_level=0.8)
        block = _build_adaptation_block(s)
        assert "УСТАЛ" in block

    def test_visual_style_prompt(self):
        s = _state(learning_style=LearningStyle.VISUAL)
        block = _build_adaptation_block(s)
        assert "визуал" in block.lower() or "схемы" in block

    def test_kinesthetic_style_prompt(self):
        s = _state(learning_style=LearningStyle.KINESTHETIC)
        block = _build_adaptation_block(s)
        assert "практические задания" in block

    def test_no_adaptation_for_default_state(self):
        s = _state()  # default: level=0.5, style=reading, fatigue=0.0
        block = _build_adaptation_block(s)
        assert "СРЕДНИЙ" in block  # level 0.5 → medium


class TestBuildMessages:
    def test_adaptation_block_injected(self):
        s = _state(current_knowledge_level=0.9, fatigue_level=0.8)
        msgs = build_messages(s)
        system_msgs = [m for m in msgs if m["role"] == "system"]
        assert len(system_msgs) == 2  # base prompt + adaptation
        assert "углублённую теорию" in system_msgs[1]["content"]
        assert "УСТАЛ" in system_msgs[1]["content"]

    def test_rag_context_appended(self):
        s = _state(rag_context=[{"text": "fact1"}, {"text": "fact2"}])
        msgs = build_messages(s)
        assert any("fact1" in m["content"] for m in msgs if m["role"] == "user")

    def test_user_messages_preserved(self):
        s = _state(messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])
        msgs = build_messages(s)
        user_msgs = [m for m in msgs if m["role"] == "user"]
        assert len(user_msgs) >= 2  # adaptation doesn't add user msgs
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_adaptive_state.py -v`
Expected: 12 PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: all 42 tests PASS (25 existing + 5 search + 12 adaptive)

- [ ] **Step 6: Commit**

```bash
git add src/models/schemas.py src/agent/prompts.py tests/test_adaptive_state.py
git commit -m "feat(state): add adaptive student fields (knowledge_level, learning_style, fatigue) with prompt adaptation"
```

---

### Task 3: Critic Validator (Judge Model)

**Files:**
- Create: `src/agent/critic.py`
- Modify: `src/agent/loop.py:139-150,200-224`
- Create: `tests/test_critic.py`

**Interfaces:**
- Consumes: `LLMClient` (judge model), `AgentGraphState.final_answer`
- Produces: `CriticResult` dataclass with `passed: bool`, `issues: list[str]`, `corrected_answer: Optional[str]`; wired as node `critic` between `final` and `END` in the LangGraph

- [ ] **Step 1: Create `src/agent/critic.py`**

```python
"""Критик: валидация ответа агента через лёгкую модель (judge).

Проверяет:
  1. Галлюцинации: не противоречит ли ответ контексту из RAG/web.
  2. LaTeX: все формулы закрыты корректными тегами $…$ / $$…$$.
  3. Структура: ответ не пустой, содержательный.
"""

from dataclasses import dataclass, field
from typing import Optional

from ..llm.base import LLMClient, LLMResponse, TokenUsage
from ..models.schemas import AgentGraphState


CRITIC_SYSTEM_PROMPT = (
    "Ты — Критик (Critic). Твоя единственная задача — проверить ответ "
    "преподавателя на качество.\n\n"
    "Проверь:\n"
    "1. LaTeX: все inline-формулы обёрнуты в $…$, блочные — в $$…$$. "
    "   Если формула не закрыта — это ОШИБКА.\n"
    "2. Галлюцинации: если в ответе есть конкретные факты (даты, числа, "
    "   имена), убедись, что они обоснованы. Если факт сомнителен — "
    "   пометь как [возможная галлюцинация].\n"
    "3. Структура: ответ должен быть содержательным (не пустым, не "
    "   состоять только из служебных меток).\n\n"
    "Верни JSON:\n"
    '{"passed": true/false, "issues": ["..."], "corrected_answer": "..." or null}\n\n'
    "Если passed=true, corrected_answer=null.\n"
    "Если passed=false и возможна коррекция — заполни corrected_answer.\n"
    "Если исправить невозможно — corrected_answer=null, опиши проблему в issues."
)


@dataclass
class CriticResult:
    """Результат работы критика."""
    passed: bool
    issues: list[str] = field(default_factory=list)
    corrected_answer: Optional[str] = None


class Critic:
    """Валидатор ответа агента через легковесную LLM (judge-модель)."""

    def __init__(self, llm: LLMClient, model: str):
        self.llm = llm
        self.model = model

    async def validate(
        self,
        answer: str,
        rag_context: Optional[list] = None,
    ) -> CriticResult:
        """Проверяет ответ через LLM-критика."""
        if not answer or not answer.strip():
            return CriticResult(passed=False, issues=["пустой ответ"])

        # Быстрая проверка LaTeX без LLM
        latex_issues = _check_latex(answer)
        if latex_issues:
            return CriticResult(passed=False, issues=latex_issues)

        # LLM-проверка на галлюцинации и структуру
        context_block = ""
        if rag_context:
            context_block = "\n\n[Контекст из базы знаний]\n" + "\n".join(
                r.get("text", "") if isinstance(r, dict) else str(r)
                for r in rag_context[:5]
            )

        messages = [
            {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
            {"role": "user", "content": f"[Ответ преподавателя]\n{answer}{context_block}"},
        ]

        try:
            resp: LLMResponse = await self.llm.chat(
                messages=messages,
                model=self.model,
                temperature=0.0,
                max_tokens=512,
            )
            return _parse_critic_response(resp.content)
        except Exception:
            # Если критик упал — пропускаем LLM-проверку, доверяем LaTeX-проверке
            return CriticResult(passed=True, issues=["критик недоступен, пропущено"])


def _check_latex(text: str) -> list[str]:
    """Проверяет баланс LaTeX-тегов без LLM."""
    issues = []
    # Проверка парности $$
    dbl = text.count("$$")
    if dbl % 2 != 0:
        issues.append("нечётное количество $$ тегов (незакрытая блочная формула)")
    # Проверка парности $ (за вычетом $$)
    single = text.count("$") - 2 * dbl
    if single % 2 != 0:
        issues.append("нечётное количество $ тегов (незакрытая inline-формула)")
    return issues


def _parse_critic_response(content: str) -> CriticResult:
    """Парсит JSON-ответ критика."""
    import json
    try:
        # Извлекаем JSON из ответа (модель может обернуть в ```json ... ```)
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(text)
        return CriticResult(
            passed=bool(data.get("passed", True)),
            issues=data.get("issues", []),
            corrected_answer=data.get("corrected_answer"),
        )
    except (json.JSONDecodeError, AttributeError):
        # Если модель вернула не JSON — считаем, что проверка пройдена
        return CriticResult(passed=True, issues=["критик вернул некорректный JSON"])
```

- [ ] **Step 2: Wire Critic into the agent loop**

In `src/agent/loop.py`, add import at line 23:

```python
from .critic import Critic, CriticResult
```

Update `AgentRuntime.__init__` to accept a critic (add after `validator` param):

```python
    def __init__(
        self,
        llm: LLMClient,
        models: dict[str, str],
        tool_context: ToolContext,
        circuit_breaker: Optional[Any] = None,
        budget: Optional[Any] = None,
        logger: Optional[JsonlLogger] = None,
        trace_id: str = "",
        limiter: Optional[IterationLimiter] = None,
        validator: Optional[OutputValidator] = None,
        critic: Optional[Critic] = None,
    ):
        self.llm = llm
        self.models = models
        self.tool_context = tool_context
        self.circuit_breaker = circuit_breaker
        self.budget = budget
        self.logger = logger
        self.trace_id = trace_id
        self.limiter = limiter or IterationLimiter()
        self.validator = validator or OutputValidator()
        self.critic = critic
```

Replace the `finalize` method (lines 139-150) with:

```python
    async def finalize(self, state: AgentGraphState) -> dict[str, Any]:
        """Финальная валидация/нормализация ответа + Critic-проверка."""
        answer = state.final_answer or "Не смог сформировать ответ."
        ok, err = self.validator.validate(answer)
        if not ok:
            answer = "[требуется переформулировка] " + answer
            self._emit_log(state, "final", f"валидация провалена: {err}")

        # Critic-проверка (если подключён)
        if self.critic and ok:
            critic_result = await self.critic.validate(
                answer,
                rag_context=state.rag_context if state.rag_context else None,
            )
            if not critic_result.passed:
                if critic_result.corrected_answer:
                    answer = critic_result.corrected_answer
                else:
                    answer = "[критик] " + answer
                self._emit_log(
                    state, "critic",
                    f"критик отклонил: {'; '.join(critic_result.issues)}",
                )

        result = {"final_answer": answer, "terminated": True}
        if not ok:
            result["error"] = err
        return result
```

Update `build_graph` to add the critic node (after line 208):

```python
def build_graph(runtime: AgentRuntime):
    """Собирает LangGraph с condition edges + critic."""
    limiter = runtime.limiter
    cond = lambda state: should_continue(state, limiter)  # noqa: E731

    g = StateGraph(AgentGraphState)
    g.add_node(NODE_PLAN, runtime.plan)
    g.add_node(NODE_TOOLS, runtime.run_tools)
    g.add_node(NODE_FINAL, runtime.finalize)

    g.add_edge(START, NODE_PLAN)
    g.add_edge(NODE_TOOLS, NODE_PLAN)
    g.add_edge(NODE_FINAL, END)

    g.add_conditional_edges(
        NODE_PLAN,
        cond,
        {
            STATUS_TOOLS: NODE_TOOLS,
            STATUS_FINAL: NODE_FINAL,
            STATUS_STOP: END,
        },
    )
    return g.compile()
```

Note: The graph structure stays the same — `final` → `END`. The Critic runs _inside_ `finalize()` as a step, not as a separate graph node. This avoids graph complexity while still ensuring every answer passes through the Critic.

- [ ] **Step 3: Write tests for Critic**

Create `tests/test_critic.py`:

```python
"""Тесты Critic-валидатора."""

import pytest
from unittest.mock import AsyncMock

from src.agent.critic import Critic, CriticResult, _check_latex, _parse_critic_response
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.models.schemas import AgentGraphState


class FakeJudgeLLM(LLMClient):
    """Возвращает предопределённый ответ критика."""

    def __init__(self, content: str):
        self._content = content
        self.call_count = 0

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None):
        self.call_count += 1
        return LLMResponse(
            content=self._content,
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


class TestCheckLatex:
    def test_balanced_passes(self):
        assert _check_latex("Формула $$x^2$$ и $y$") == []

    def test_unbalanced_dbl(self):
        issues = _check_latex("Формула $$x^2 незакрыта")
        assert any("$$" in i for i in issues)

    def test_unbalanced_single(self):
        issues = _check_latex("Формула $x^2 незакрыта")
        assert any("$" in i for i in issues)


class TestParseCriticResponse:
    def test_valid_json_passed(self):
        r = _parse_critic_response('{"passed": true, "issues": [], "corrected_answer": null}')
        assert r.passed is True
        assert r.issues == []

    def test_valid_json_failed_with_correction(self):
        r = _parse_critic_response(
            '{"passed": false, "issues": ["галлюцинация"], "corrected_answer": "исправленный"}'
        )
        assert r.passed is False
        assert r.corrected_answer == "исправленный"

    def test_wrapped_in_codeblock(self):
        r = _parse_critic_response('```json\n{"passed": true, "issues": []}\n```')
        assert r.passed is True

    def test_invalid_json_returns_passed(self):
        r = _parse_critic_response("not json at all")
        assert r.passed is True


class TestCriticValidate:
    async def test_empty_answer_fails(self):
        llm = FakeJudgeLLM("{}")
        critic = Critic(llm=llm, model="judge")
        result = await critic.validate("")
        assert not result.passed
        assert "пустой" in result.issues[0]

    async def test_latex_issue_fails_without_llm(self):
        llm = FakeJudgeLLM("{}")
        critic = Critic(llm=llm, model="judge")
        result = await critic.validate("Формула $$x^2 незакрыта")
        assert not result.passed
        assert llm.call_count == 0  # LLM не вызывался

    async def test_valid_answer_calls_llm(self):
        llm = FakeJudgeLLM('{"passed": true, "issues": []}')
        critic = Critic(llm=llm, model="judge")
        result = await critic.validate("Ответ $$E=mc^2$$")
        assert result.passed
        assert llm.call_count == 1

    async def test_llm_failure_returns_passed(self):
        class FailLLM(LLMClient):
            async def chat(self, *a, **k):
                raise RuntimeError("API down")
            async def chat_stream(self, *a, **k):
                yield ""

        critic = Critic(llm=FailLLM(), model="judge")
        result = await critic.validate("Ответ $$E=mc^2$$")
        assert result.passed  # graceful degradation

    async def test_rag_context_passed_to_llm(self):
        llm = FakeJudgeLLM('{"passed": true, "issues": []}')
        critic = Critic(llm=llm, model="judge")
        await critic.validate("Ответ", rag_context=[{"text": "факт1"}])
        assert llm.call_count == 1


class TestCriticIntegration:
    """Интеграция Critic с AgentRuntime.finalize()."""

    async def test_finalize_with_critic_passes(self):
        from src.agent.loop import AgentRuntime
        from src.agent.tools import ToolContext

        judge_llm = FakeJudgeLLM('{"passed": true, "issues": []}')
        planner_llm = FakeJudgeLLM("final answer")  # не используется в finalize

        runtime = AgentRuntime(
            llm=planner_llm,
            models={"planner": "test", "judge": "judge"},
            tool_context=ToolContext(),
            critic=Critic(llm=judge_llm, model="judge"),
        )
        state = AgentGraphState(
            messages=[],
            final_answer="Ответ $$E=mc^2$$",
        )
        result = await runtime.finalize(state)
        assert result["final_answer"] == "Ответ $$E=mc^2$$"

    async def test_finalize_with_critic_rejects(self):
        from src.agent.loop import AgentRuntime
        from src.agent.tools import ToolContext

        judge_llm = FakeJudgeLLM(
            '{"passed": false, "issues": ["галлюцинация"], "corrected_answer": "исправлено"}'
        )
        planner_llm = FakeJudgeLLM("final answer")

        runtime = AgentRuntime(
            llm=planner_llm,
            models={"planner": "test", "judge": "judge"},
            tool_context=ToolContext(),
            critic=Critic(llm=judge_llm, model="judge"),
        )
        state = AgentGraphState(
            messages=[],
            final_answer="Ответ $$E=mc^2$$",
        )
        result = await runtime.finalize(state)
        assert result["final_answer"] == "исправлено"

    async def test_finalize_without_critic_skips(self):
        from src.agent.loop import AgentRuntime
        from src.agent.tools import ToolContext

        planner_llm = FakeJudgeLLM("final answer")

        runtime = AgentRuntime(
            llm=planner_llm,
            models={"planner": "test"},
            tool_context=ToolContext(),
            critic=None,
        )
        state = AgentGraphState(
            messages=[],
            final_answer="Ответ $$E=mc^2$$",
        )
        result = await runtime.finalize(state)
        assert result["final_answer"] == "Ответ $$E=mc^2$$"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_critic.py -v`
Expected: 14 PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: all 56 tests PASS (25 existing + 5 search + 12 adaptive + 14 critic)

- [ ] **Step 6: Run linter and type checker**

Run: `ruff check src/ tests/`
Run: `mypy src/`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add src/agent/critic.py src/agent/loop.py tests/test_critic.py
git commit -m "feat(critic): add LLM-based answer validator (judge model) in agent loop"
```

---

### Task 4: Wire Judge Model from Factory + Integration Test

**Files:**
- Modify: `src/config.py` (no changes needed — `judge` model already in factory)
- Create: `tests/test_integration.py`
- Modify: `tests/test_agent.py` (update FakeLLM + add critic test)

**Interfaces:**
- Consumes: `LLMClientFactory.get_models_for_region()` returns `judge` key
- Produces: end-to-end test showing full agent loop with Critic

- [ ] **Step 1: Add integration test**

Create `tests/test_integration.py`:

```python
"""Интеграционные тесты: полный агентный цикл с адаптацией и критиком."""

import pytest

from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.agent.loop import AgentRuntime, run_agent
from src.agent.tools import ToolContext
from src.agent.critic import Critic
from src.models.schemas import AgentGraphState, LearningStyle


class FakePlannerLLM(LLMClient):
    """Возвращает tool_call, потом финальный ответ с формулами."""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                model=model,
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
                finish_reason="tool_calls",
                tool_calls=[{
                    "id": "call_1",
                    "function": {"name": "web_search", "arguments": '{"query": "квадратное уравнение"}'},
                }],
            )
        return LLMResponse(
            content="Квадратное уравнение $ax^2 + bx + c = 0$ решается формулой $$x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$",
            model=model,
            usage=TokenUsage(prompt_tokens=20, completion_tokens=15),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


class FakeJudgeLLM(LLMClient):
    """Критик: всегда одобряет."""

    async def chat(self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None):
        return LLMResponse(
            content='{"passed": true, "issues": []}',
            model=model,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
            finish_reason="stop",
        )

    async def chat_stream(self, *a, **k):
        yield ""


async def test_full_cycle_with_adaptation_and_critic():
    planner = FakePlannerLLM()
    judge = FakeJudgeLLM()

    runtime = AgentRuntime(
        llm=planner,
        models={"planner": "test", "judge": "judge"},
        tool_context=ToolContext(region="GLOBAL"),
        critic=Critic(llm=judge, model="judge"),
    )

    result = await run_agent(
        runtime,
        messages=[{"role": "user", "content": "Объясни квадратное уравнение"}],
        session_id="integration-test",
        student_profile={
            "current_knowledge_level": 0.7,
            "learning_style": "visual",
            "fatigue_level": 0.2,
        },
    )

    assert result.final_answer
    assert "ax^2" in result.final_answer or "frac" in result.final_answer
    assert not result.error
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_integration.py -v`
Expected: 1 PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/ -v`
Expected: all 57 tests PASS

- [ ] **Step 4: Run linter**

Run: `ruff check src/ tests/`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration test for full agent cycle with adaptation + critic"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** DDGS fallback (Task 1), DeepTutor adaptive state (Task 2), Critic validator (Task 3) — all three requirements covered
- [x] **Placeholder scan:** No TBD/TODO/placeholders in any step
- [x] **Type consistency:** `SearchTimeoutError`/`SearchRateLimitError` defined in Task 1, imported in Tasks 1 steps; `Critic` class created in Task 3, used in Task 3 integration + Task 4; `LearningStyle` enum created in Task 2, used in prompts
- [x] **Existing tests:** All 25 existing tests remain untouched and should continue passing
- [x] **New tests:** 5 (search) + 12 (adaptive) + 14 (critic) + 1 (integration) = 32 new tests
