"""Агентный цикл на LangGraph: ReAct с condition edges, tools, лимитами.

Сценарий:
  - planner: LLM решает, вызвать ли инструмент или выдать финальный ответ.
  - tools: исполняет выбранные инструменты (RAG / web-search) с таймаутом/retry.
  - cond_edge: если модель запросила более N шагов или превышен бюджет/время —
    замыкаем доступ к инструментам и принуждаем финализировать.
"""

import asyncio
import json
import re
import time
from collections.abc import Callable
from typing import Any

# LangGraph
from langgraph.graph import END, START, StateGraph

from ..config import settings
from ..llm.base import LLMClient, LLMResponse
from ..models.schemas import (
    AgentGraphState,
    AgentStep,
    ContentEnvelope,
    ContentType,
    LearningStyle,
    ToolCall,
)
from ..observability.logger import JsonlLogger, TraceContext
from ..safety import IterationLimiter, OutputValidator
from .critic import Critic
from .envelope import parse_content_envelopes
from .prompts import build_messages
from .tools import TOOL_SCHEMAS, ToolContext, execute_tool

NODE_PLAN = "planner"
NODE_TOOLS = "tools"
NODE_FINAL = "final"

# Статусы для cond_edge
STATUS_TOOLS = "tools"
STATUS_FINAL = "final"
STATUS_STOP = "stop"

# Лимиты на размер контекста, возвращаемого инструментами
_MAX_CONTEXT_SNIPPETS = 10  # максимум сниппетов RAG в rag_context
_SNIPPET_CHARS = 600  # максимум символов одного сниппета
_MAX_OBSERVATION_ITEMS = 8  # сколько позиций показывать модели в наблюдении


class AgentRuntime:
    """Dependencies для агентного цикла."""

    def __init__(
        self,
        llm: LLMClient,
        models: dict[str, str],
        tool_context: ToolContext,
        circuit_breaker: Any | None = None,
        budget: Any | None = None,
        logger: JsonlLogger | None = None,
        trace_id: str = "",
        limiter: IterationLimiter | None = None,
        validator: OutputValidator | None = None,
        critic: Critic | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ):
        self.llm = llm
        self.models = models  # {"planner": "...", "fast": "..."}
        self.tool_context = tool_context
        self.circuit_breaker = circuit_breaker
        self.budget = budget
        self.logger = logger
        self.trace_id = trace_id
        self.limiter = limiter or IterationLimiter()
        self.validator = validator or OutputValidator()
        self.critic = critic
        self.on_event = on_event

    def _log(self, state: AgentGraphState, step: AgentStep):
        if self.logger:
            self.logger.step(state, step, self.trace_id)

    def _notify(self, event: str, data: dict) -> None:
        if not self.on_event:
            return
        try:  # noqa: SIM105
            self.on_event(event, data)
        except Exception:
            pass  # наблюдатель не должен ронять агента

    async def plan(self, state: AgentGraphState) -> dict[str, Any]:
        """Планирование: LLM выбирает действие (tool_call или финал)."""
        start = time.time()
        model = self.models.get(state.current_model, self.models.get("planner", ""))

        if self.circuit_breaker and self.circuit_breaker.is_open():
            self._emit_log(state, "final", "circuit breaker open")
            self._notify(
                "agent.step",
                {
                    "action": "final",
                    "model": model,
                    "tool": None,
                    "status": "error",
                    "reason": "circuit_breaker",
                    "step": len(state.steps),
                },
            )
            return _force_final(state, "circuit breaker open: используется fast-модель")

        if self.budget and self.budget.exceeded(state.current_model):
            self._emit_log(state, "final", "бюджет исчерпан")
            self._notify(
                "agent.step",
                {
                    "action": "final",
                    "model": model,
                    "tool": None,
                    "status": "error",
                    "reason": "budget",
                    "step": len(state.steps),
                },
            )
            return _force_final(state, "бюджет сессии исчерпан")

        messages = build_messages(state)
        try:
            resp: LLMResponse = await self.llm.chat(
                messages=messages,
                model=model,
                tools=TOOL_SCHEMAS,
                temperature=0.4,
                max_tokens=1024,
            )
        except Exception as e:  # noqa: BLE001
            if self.circuit_breaker:
                self.circuit_breaker.record_failure()
            self._notify(
                "agent.step",
                {
                    "action": "final",
                    "model": model,
                    "tool": None,
                    "status": "error",
                    "reason": "llm_error",
                    "step": len(state.steps),
                },
            )
            return _force_final(state, f"LLM error: {e}")

        if self.circuit_breaker:
            self.circuit_breaker.record_success()

        if self.budget and resp.usage:
            self.budget.record(resp.cost_usd)

        # Маршрут
        duration_ms = int((time.time() - start) * 1000)
        tool_calls = [
            ToolCall(name=tc["function"]["name"], arguments=_parse_args(tc))
            for tc in resp.tool_calls
        ]
        if not tool_calls:
            # Некоторые модели/провайдеры не умеют нативные tool_calls и «вызывают»
            # инструмент текстом («Функция rag_search json {...}»). Это НЕ финальный
            # ответ — распознаём и исполняем инструмент штатно.
            tool_calls = _textual_tool_calls(resp.content)

        if tool_calls:
            first = tool_calls[0]
            step = _step(state, "tool", resp, first.name, first.arguments, duration_ms)
            self._emit_log(state, step.action, first.name)
            self._notify(
                "agent.step",
                {
                    "action": "tool",
                    "model": resp.model,
                    "tool": first.name,
                    "status": "ok",
                    "step": len(state.steps),
                },
            )
            return {
                "tool_calls": tool_calls,
                "current_model": state.current_model,
                "steps": [*state.steps, step],
            }

        # Нет tool_calls => финальный ответ. НЕ выставляем terminated: маршрутизация
        # должна увести planner -> final, где выполняется finalize (валидация + Critic).
        step = _step(state, "final", resp, None, None, duration_ms)
        self._emit_log(state, "final", "финальный ответ")
        self._notify(
            "agent.step",
            {
                "action": "final",
                "model": resp.model,
                "tool": None,
                "status": "ok",
                "step": len(state.steps),
            },
        )
        return {
            "final_answer": resp.content,
            "steps": [*state.steps, step],
        }

    def _emit_log(self, state: AgentGraphState, action: str, reason: str):
        if not self.logger:
            return
        step = AgentStep(
            action=action,
            reason_summary=reason,
            model=self.models.get(state.current_model, ""),
            status="ok",
            duration_ms=0,
        )
        self.logger.step(state, step, self.trace_id)

    async def run_tools(self, state: AgentGraphState) -> dict[str, Any]:
        """Исполняет инструменты и возвращает результат модели.

        Результат каждого инструмента добавляется в разговор как user-сообщение
        (OpenAI-совместимые провайдеры отвергают role:"tool" без предшествующего
        assistant tool_calls), а содержимое rag_search/web_search копируется в
        rag_context / web_results для последующего критик-чека.
        """
        tracker = _TrackerShim()
        results: dict[str, Any] = {}
        rag_context = list(state.rag_context)
        web_results = list(state.web_results)
        observations: list[dict[str, str]] = []

        for call in state.tool_calls:
            out = await execute_tool(call.name, call.arguments, self.tool_context, tracker)
            results[call.name] = out
            parsed = _safe_loads(out)
            status = "ok" if parsed.get("status") == "ok" else "error"
            self._notify("agent.tool", {"name": call.name, "status": status})
            _merge_tool_output(call.name, out, rag_context, web_results)
            observations.append({"role": "user", "content": _observation_text(call.name, out)})

        # LangGraph-состояние без редукторов: сообщения и контекст перезаписываются,
        # поэтому возвращаем ПОЛНЫЕ списки (предыдущие сообщения не теряем).
        return {
            "messages": [*state.messages, *observations],
            "tools_result": results,
            "tool_calls": [],
            "rag_context": rag_context,
            "web_results": web_results,
        }

    async def finalize(self, state: AgentGraphState) -> dict[str, Any]:
        """Парсит ответ в конверт(ы), валидирует текст, прогоняет Critic.

        Модель иногда отвечает НЕСКОЛЬКИМИ JSON-конвертами подряд (например,
        theory + practice): первый обрабатывается штатно (валидатор + Critic),
        остальные сохраняются как есть в ``content_envelopes`` — HTTP-слой
        превращает каждый в отдельное сообщение-блок чата.
        """
        raw = state.final_answer or "Не смог сформировать ответ."
        all_envs = parse_content_envelopes(raw)
        envelope = (
            all_envs[0]
            if all_envs
            else ContentEnvelope(type=ContentType.THEORY, text=raw)
        )
        answer = envelope.text

        # Страховка: модель «вызвала» инструмент текстом и не дошла до ответа
        # (envelope не распознан — fallback theory с исходным текстом). Такой
        # служебный мусор ученику не показываем.
        if (
            _textual_tool_calls(raw)
            and envelope.type.value == "theory"
            and envelope.text == raw
        ):
            answer = (
                "Извините, я не смог подготовить ответ — мой внутренний помощник "
                "не завершил поиск материала. Сформулируйте вопрос ещё раз."
            )
            envelope.text = answer
            self._emit_log(
                state, "final", "модель не дала финального ответа: текстовый вызов инструмента"
            )
            return {
                "final_answer": answer,
                "content_envelope": envelope.model_dump(),
                "content_envelopes": [envelope.model_dump()],
                "terminated": True,
                "error": "не сформирован финальный ответ",
            }

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
                if critic_result.corrected_answer:
                    answer = critic_result.corrected_answer
                issues_str = "; ".join(critic_result.issues)
                self._emit_log(state, "critic", f"критик отклонил: {issues_str}")

        envelope.text = answer
        envelopes = [envelope, *all_envs[1:]]
        self._notify("agent.finalize", {"status": "ok" if ok else "error"})
        result: dict[str, Any] = {
            "final_answer": answer,
            "content_envelope": envelope.model_dump(),
            "content_envelopes": [env.model_dump() for env in envelopes],
            "terminated": True,
        }
        if not ok:
            result["error"] = err
        return result


class _TrackerShim:
    """Обёртка, сохраняющая интерфейс ToolFailureTracker без disable на графе."""

    def is_disabled(self, name: str) -> bool:
        return False

    def on_success(self, name: str): pass

    def on_failure(self, name: str): pass


def _parse_args(tc: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(tc["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        return {}


_TEXTUAL_TOOL_RE = re.compile(r"(rag_search|web_search)", re.IGNORECASE)
# Сколько символов после имени инструмента искать открывающую скобку JSON.
_TEXTUAL_TOOL_MAX_TAIL = 80


def _read_json_object(text: str, start: int) -> tuple[dict[str, Any] | None, int]:
    """Читает сбалансированный JSON-объект от `start` (с учётом строк/экранов)."""
    depth = 0
    in_str = False
    escaped = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1]), i + 1
                    except json.JSONDecodeError:
                        return None, i + 1
        i += 1
    return None, i


def _textual_tool_calls(content: str) -> list[ToolCall]:
    """Распознаёт вызов инструмента, записанный моделью текстом, а не tool_calls.

    Боевой пример: «…материалов.Функцияrag_search json {"query": "броуновское
    движение"}». Такой текст НЕ является ответом ученику: провайдер не умеет
    нативные tool_calls, и модель описывает вызов словами. Распознаём имя
    инструмента + JSON-аргументы рядом (с обязательным полем "query"), чтобы
    цикл исполнил инструмент штатно.

    Возвращает [] для обычного текста/валидного финального ответа.
    """
    if not content:
        return []
    calls: list[ToolCall] = []
    for match in _TEXTUAL_TOOL_RE.finditer(content):
        tail = content[match.end() : match.end() + _TEXTUAL_TOOL_MAX_TAIL]
        start = tail.find("{")
        if start < 0:
            continue
        args, _ = _read_json_object(content, match.end() + start)
        if args is None:
            continue
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            continue
        calls.append(ToolCall(name=match.group(1).lower(), arguments=args))
    return calls


def _step(
    state: AgentGraphState,
    action: str,
    resp: LLMResponse,
    tool: str | None,
    args: Any | None,
    duration_ms: int = 0,
) -> AgentStep:
    return AgentStep(
        action=action,
        reason_summary=tool or "готовлю ответ",
        model=resp.model,
        tool=tool,
        arguments=args,
        status="ok",
        duration_ms=duration_ms,
    )


def _force_final(state: AgentGraphState, reason: str) -> dict[str, Any]:
    return {"final_answer": f"[ограничение] {reason}", "terminated": True, "error": reason}


def _safe_loads(text: str) -> dict[str, Any]:
    """Безопасно разбирает JSON-строку результата инструмента."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _truncate(text: str, limit: int = _SNIPPET_CHARS) -> str:
    return text if len(text) <= limit else text[:limit]


def _format_context_item(item: Any) -> str:
    """Форматирует одну позицию результата инструмента для чтения моделью."""
    if not isinstance(item, dict):
        return _truncate(str(item))
    if "text" in item:
        score = item.get("score")
        prefix = f"[score={score}] " if score is not None else ""
        return prefix + _truncate(str(item["text"]))
    parts = [p for p in (item.get("title"), item.get("url"), item.get("snippet")) if p]
    if parts:
        return _truncate(" — ".join(str(p) for p in parts))
    return _truncate(json.dumps(item, ensure_ascii=False))


def _observation_text(name: str, output: str) -> str:
    """Собирает читаемый текст наблюдения за результатом инструмента."""
    parsed = _safe_loads(output)
    if not parsed:
        return f"[Результат инструмента {name}]\n{output}"
    if parsed.get("status") == "error":
        error = parsed.get("error") or "неизвестная ошибка"
        return f"[Результат инструмента {name}]\nОшибка: {error}"

    data = parsed.get("data")
    lines = [f"[Результат инструмента {name}]"]
    if not data:
        lines.append("Пустой результат.")
    elif isinstance(data, list):
        for index, item in enumerate(data[:_MAX_OBSERVATION_ITEMS], start=1):
            lines.append(f"{index}. {_format_context_item(item)}")
        rest = len(data) - _MAX_OBSERVATION_ITEMS
        if rest > 0:
            lines.append(f"... и ещё {rest}.")
    else:
        lines.append(_truncate(str(data)))
    return "\n".join(lines)


def _merge_tool_output(
    name: str,
    output: str,
    rag_context: list[Any],
    web_results: list[Any],
) -> None:
    """Копирует содержимое инструмента в rag_context / web_results."""
    parsed = _safe_loads(output)
    if parsed.get("status") != "ok":
        return
    data = parsed.get("data")
    if not isinstance(data, list):
        return

    if name == "rag_search":
        for item in data:
            if not isinstance(item, dict) or not item.get("text"):
                continue
            rag_context.append(
                {
                    "text": _truncate(str(item["text"])),
                    "score": item.get("score"),
                    "metadata": item.get("metadata"),
                }
            )
        del rag_context[_MAX_CONTEXT_SNIPPETS:]
    elif name == "web_search":
        for item in data:
            if not isinstance(item, dict):
                continue
            web_results.append({k: item.get(k) for k in ("title", "url", "snippet")})


def _clamp01(value: float) -> float:
    """Ограничивает значение отрезком [0, 1]."""
    return max(0.0, min(1.0, value))


def _adaptive_fields(profile: dict[str, Any] | None) -> dict[str, Any]:
    """Переносит ключи student_profile на типизированные поля состояния."""
    profile = profile or {}
    try:
        knowledge = _clamp01(float(profile.get("current_knowledge_level")))
    except (TypeError, ValueError):
        knowledge = 0.5
    try:
        fatigue = _clamp01(float(profile.get("fatigue_level")))
    except (TypeError, ValueError):
        fatigue = 0.0
    try:
        style = LearningStyle(str(profile.get("learning_style", "")).lower())
    except (ValueError, TypeError):
        style = LearningStyle.READING
    return {
        "current_knowledge_level": knowledge,
        "learning_style": style,
        "fatigue_level": fatigue,
    }


def should_continue(state: AgentGraphState, limiter: IterationLimiter | None = None) -> str:
    """Condition edge: продолжать цикл или завершить."""
    if state.terminated:
        return STATUS_STOP
    limiter = limiter or IterationLimiter()
    if limiter.stopped or len(state.steps) >= settings.max_agent_steps:
        return STATUS_FINAL  # принудительная финализация
    if state.tool_calls:
        return STATUS_TOOLS
    return STATUS_FINAL


def build_graph(runtime: AgentRuntime):
    """Собирает LangGraph с condition edges."""
    limiter = runtime.limiter
    cond = lambda state: should_continue(state, limiter)  # noqa: E731

    g = StateGraph(AgentGraphState)
    g.add_node(NODE_PLAN, runtime.plan)
    g.add_node(NODE_TOOLS, runtime.run_tools)
    g.add_node(NODE_FINAL, runtime.finalize)

    g.add_edge(START, NODE_PLAN)
    g.add_edge(NODE_TOOLS, NODE_PLAN)  # после инструментов снова планируем

    # Ветвление от planner
    g.add_conditional_edges(
        NODE_PLAN,
        cond,
        {
            STATUS_TOOLS: NODE_TOOLS,
            STATUS_FINAL: NODE_FINAL,
            STATUS_STOP: END,
        },
    )
    g.add_edge(NODE_FINAL, END)
    return g.compile()


async def run_agent(
    runtime: AgentRuntime,
    messages: list[dict],
    session_id: str = "",
    student_profile: dict | None = None,
    trace_id: str = "",
) -> AgentGraphState:
    """Точка входа: запускает агентный цикл с лимитами времени."""
    runtime.trace_id = trace_id or TraceContext.current() or JsonlLogger.new_trace_id()
    graph = build_graph(runtime)
    profile = student_profile or {}
    initial = AgentGraphState(
        session_id=session_id,
        messages=messages,
        student_profile=profile,
        **_adaptive_fields(profile),
    )
    try:
        with TraceContext(runtime.trace_id):
            out = await asyncio.wait_for(
                graph.ainvoke(initial),
                timeout=settings.max_agent_time_sec,
            )
        result = AgentGraphState(**out) if isinstance(out, dict) else out
    except TimeoutError:
        initial.terminated = True
        initial.error = "превышен таймаут агента"
        initial.final_answer = "[ограничение] превышено время ответа."
        result = initial
    if runtime.logger:
        runtime.logger.log(
            runtime.trace_id, "INFO", "agent.finished",
            session_id=session_id,
            status="error" if result.error else "ok",
            steps=len(result.steps),
            duration_ms=0,
        )
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
    runtime._notify(
        "done",
        {
            "session_id": session_id,
            "trace_id": runtime.trace_id,
            "steps": len(result.steps),
        },
    )
    return result