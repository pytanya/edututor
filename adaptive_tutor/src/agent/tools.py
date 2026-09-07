"""Инструменты агента: Function Calling схемы + исполнение.

SOP (Standard Operation Procedure):
  1. Модель решает, какой инструмент вызвать, и возвращает tool_call.
  2. execute_tool(name, args) исполняет инструмент с таймаутом и обработкой ошибок.
  3. Результат сериализуется в JSON и вставляется обратно в контекст.
  4. На ошибку ставится флаг retry; после N ошибок инструмент отключается
     (circuit breaker на уровне инструмента).
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import settings
from ..rag import RAGEngine
from ..search.base import SearchRouter

MAX_TOOL_RESULT_CHARS = 4000
MAX_TOOL_RETRIES = 2
TOOL_TIMEOUT_SEC = 15.0

# Инструменты, внутри которых выполняется отдельный LLM-вызов, не укладываются в
# короткий TOOL_TIMEOUT_SEC: даём им полноценный llm_timeout_sec.
_LLM_TOOL_NAMES = frozenset({"generate_quiz"})


@dataclass
class ToolContext:
    rag: RAGEngine | None = None
    region: str = "GLOBAL"
    on_tool: Callable | None = None
    llm: Any | None = None
    model: str = ""
    # Учёт стоимости/наблюдаемость внутренних LLM-вызовов инструментов
    # (например, generate_quiz): прокидываются AgentRuntime при запуске графа.
    budget: Any | None = None
    logger: Any | None = None
    trace_id: str = ""


@dataclass
class ToolFailureTracker:
    """Per-tool circuit breaker: после MAX_TOOL_RETRIES подряд инструмент блокируется."""
    failures: dict[str, int] = field(default_factory=dict)
    disabled: set[str] = field(default_factory=set)

    def on_success(self, name: str):
        self.failures.pop(name, None)

    def on_failure(self, name: str):
        self.failures[name] = self.failures.get(name, 0) + 1
        if self.failures[name] >= MAX_TOOL_RETRIES:
            self.disabled.add(name)

    def is_disabled(self, name: str) -> bool:
        return name in self.disabled


# --- JSON-схемы для Function Calling (OpenAI-совместимые) ---

RAG_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "rag_search",
        "description": (
            "Искать по векторной базе знаний. Используй, когда нужно "
            "опереться на факты из изучаемого материала."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Семантический запрос"},
                "top_k": {"type": "integer", "description": "Кол-во результатов", "default": 5},
                "filters": {
                    "type": "object",
                    "description": "Фильтры по topic/grade",
                    "default": {},
                },
            },
            "required": ["query"],
        },
    },
}

WEB_SEARCH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Искать в интернете (Yandex/Tavily, fallback DuckDuckGo) текущую информацию, "
            "личности, события. Не путать с RAG-поиском по учебной базе."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос"},
                "max_results": {
                    "type": "integer",
                    "description": "Кол-во результатов",
                    "default": 5,
                },
                "language": {"type": "string", "enum": ["ru", "en"], "default": "ru"},
            },
            "required": ["query"],
        },
    },
}

GENERATE_QUIZ_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_quiz",
        "description": (
            "Сгенерировать короткий проверочный вопрос (квиз) по теме отдельным "
            "LLM-вызовом: возвращает готовую карточку {question, options, "
            "answer_type, _correct_answer, difficulty}. ВСЕГДА используй этот "
            "инструмент для проверки понимания (тип quiz) — не строй quiz JSON "
            "в основном ответе сам."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Тема, по которой нужен вопрос"},
                "difficulty": {
                    "type": "string",
                    "enum": ["easy", "medium", "hard"],
                    "description": "Целевая сложность (easy/medium/hard)",
                },
                "context_query": {
                    "type": "string",
                    "description": "Уточняющий запрос к базе знаний для фактов вопроса",
                },
            },
            "required": ["topic"],
        },
    },
}

TOOL_SCHEMAS = [RAG_TOOL_SCHEMA, WEB_SEARCH_TOOL_SCHEMA, GENERATE_QUIZ_TOOL_SCHEMA]


def _ok(data: Any) -> str:
    return json.dumps({"status": "ok", "data": data}, ensure_ascii=False)[:MAX_TOOL_RESULT_CHARS]


def _err(msg: str) -> str:
    return json.dumps({"status": "error", "error": msg}, ensure_ascii=False)


async def execute_tool(
    name: str, args: dict[str, Any], ctx: ToolContext, tracker: ToolFailureTracker
) -> str:
    """Исполняет инструмент с таймаутом, retry и обработкой ошибок."""
    if tracker.is_disabled(name):
        return _err(f"tool '{name}' временно отключён после серии ошибок")

    if ctx.on_tool:
        ctx.on_tool(name, args)

    timeout = (
        settings.llm_timeout_sec if name in _LLM_TOOL_NAMES else TOOL_TIMEOUT_SEC
    )
    for attempt in range(MAX_TOOL_RETRIES + 1):
        try:
            result = await asyncio.wait_for(_call_tool(name, args, ctx), timeout=timeout)
            tracker.on_success(name)
            return _ok(result)
        except TimeoutError:
            last = f"tool '{name}' таймаут (> {timeout}s)"
        except Exception as e:  # noqa: BLE001
            last = f"tool '{name}' error: {e}"

        # retry с короткой паузой
        await asyncio.sleep(0.3 * (attempt + 1))
        tracker.on_failure(name)

    return _err(last)


_QUIZ_PROMPT = """Ты — составитель коротких проверочных вопросов для учебной темы.
Составь ОДИН квиз по теме «{topic}»{ctx_block}.

СТРОГО верни ОДИН JSON-объект без текста вне него и без markdown-обёртки, вида:
{{"question": "...", "answer_type": "single"|"open", "options": [...],
"_correct_answer": "...", "difficulty": "easy|medium|hard"}}

Требования:
1. question — это ВСЕГДА прямой вопрос с «?» или начинающийся с вопросительного слова
   («Что…?», «Какой…?», «Почему…?», «Сколько…?»). В question НЕ должно быть правильного
   ответа: ни его формулировки, ни ключевого термина/числа/формулы ответа.
2. У вопроса ровно ОДИН верный ответ при ЛЮБОМ разумном толковании. Нельзя давать два
   варианта, каждый из которых по-своему верен (пример ловушки: в вопросе «какой элемент
   является второстепенным членом» варианты «Вечером» и «в парк» ОБА верны — так нельзя).
   Формулируй вопрос и дистракторы так, чтобы верным был только _correct_answer.
3. answer_type="single": ровно 4 варианта в options (1 верный + 3 дистрактора того же
   типа). Дистрактор обязан быть заведомо НЕВЕРНЫМ (не «тоже допустимым»), без дублей и
   одинаковых по смыслу. _correct_answer обязан ТОЧНО совпадать с текстом одного из options
   и быть единственным верным.
4. answer_type="open": options = [], _correct_answer — эталонный краткий ответ.
5. difficulty = {difficulty}.
Не раскрывай _correct_answer в question или options."""


def _escape_control_in_strings(text: str) -> str:
    """Чинит сырые переводы строк внутри строковых литералов JSON (LLM-ответ)."""
    out: list[str] = []
    in_str = False
    escaped = False
    for ch in text:
        if in_str:
            if escaped:
                out.append(ch)
                escaped = False
            elif ch == "\\":
                out.append(ch)
                escaped = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            elif ch in "\n\r\t\f\b":
                out.append({"\\n": "\\n", "\r": "\\r", "\t": "\\t", "\f": "\\f", "\b": "\\b"}[ch])
            else:
                out.append(ch)
        else:
            out.append(ch)
            if ch == '"':
                in_str = True
    return "".join(out)


def _parse_quiz_card(text: str | None) -> dict[str, Any] | None:
    """Достаёт первый JSON-объект из ответа генератора квиза и валидирует поля.

    Устойчив к markdown-обёртке и к сырым управляющим символам внутри строк.
    Возвращает None, если JSON не распознан или не хватает ключевых полей.
    """
    raw = (text or "").strip()
    fragment: str | None = None
    try:
        candidate = json.loads(raw)
        if isinstance(candidate, dict):
            fragment = raw
    except (json.JSONDecodeError, TypeError):
        pass
    if fragment is None:
        start = raw.find("{")
        if start >= 0:
            depth = 0
            in_str = False
            esc = False
            i = start
            while i < len(raw):
                ch = raw[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
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
                            fragment = raw[start : i + 1]
                            break
                i += 1
    if fragment is None:
        return None
    data: Any = None
    try:
        data = json.loads(fragment)
    except (json.JSONDecodeError, TypeError):
        try:
            data = json.loads(_escape_control_in_strings(fragment))
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("question"), str) or not data["question"].strip():
        return None
    if data.get("answer_type") not in ("single", "open"):
        return None
    if not isinstance(data.get("_correct_answer"), str) or not data["_correct_answer"].strip():
        return None
    return data


async def _generate_quiz(
    ctx: ToolContext, args: dict[str, Any], *, fix_hint: str = ""
) -> dict[str, Any]:
    """Отдельный LLM-вызов короткой карточки квиза (см. generate_quiz tool).

    Сначала ищет факты по теме в RAG (если движок подключён и что-то нашёл),
    затем просит модель собрать компактный quiz JSON в пределах
    settings.llm_quiz_max_tokens — оборванный ответ невозможен, payload короткий.
    ``fix_hint`` — причины отклонения предыдущего варианта (только reasons, без
    текста/ответа) для тихой регенерации.
    """
    if not ctx.llm:
        raise RuntimeError("LLM не подключён для generate_quiz")
    topic = str(args.get("topic") or "").strip()
    if not topic:
        raise RuntimeError("generate_quiz: не указана тема (topic)")
    difficulty = str(args.get("difficulty") or "medium").strip()
    if difficulty not in {"easy", "medium", "hard"}:
        difficulty = "medium"
    context_query = str(args.get("context_query") or topic).strip() or topic

    ctx_block = ""
    if ctx.rag is not None:
        try:
            results = ctx.rag.search(query=context_query, top_k=3, filters=None)
        except Exception:  # noqa: BLE001 — поиск не должен ронять генерацию квиза
            results = []
        if results:
            chunks = "\n\n".join(r.chunk.text for r in results[:3])[:2500]
            ctx_block = (
                "\n\nФакты из базы знаний (опирайся на них при составлении вопроса "
                f"и эталона):\n{chunks}"
            )

    prompt = _QUIZ_PROMPT.format(topic=topic, ctx_block=ctx_block, difficulty=difficulty)
    if fix_hint:
        prompt += (
            "\n\nПредыдущий вариант не прошёл контроль качества. Исправь его строго "
            f"по замечаниям:\n{fix_hint.strip()}"
        )
    model = ctx.model or "planner"
    resp = await ctx.llm.chat(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.4,
        max_tokens=settings.llm_quiz_max_tokens,
    )
    # Учёт стоимости и наблюдаемость: вызов generate_quiz обязан попадать в
    # бюджет сессии (BudgetGuard) и в JSONL-логи, иначе траты на квизы «не видны».
    usage = getattr(resp, "usage", None)
    cost_usd = resp.cost_usd if usage is not None else 0.0
    if ctx.budget is not None:
        ctx.budget.record(cost_usd)
    if ctx.logger is not None:
        ctx.logger.log(
            ctx.trace_id or "",
            "INFO",
            "quiz.gen",
            agent={"model": model},
            tool={"name": "generate_quiz", "topic": topic, "difficulty": difficulty},
            llm={
                "tokens": usage.to_dict() if usage is not None else {},
                "cost_usd": cost_usd,
            },
            status="ok",
        )
    card = _parse_quiz_card(resp.content)
    if card is None:
        raise RuntimeError("generate_quiz: не удалось разобрать карточку квиза")
    card.setdefault("difficulty", difficulty)
    return card


async def generate_quiz_card(
    ctx: ToolContext,
    *,
    topic: str,
    difficulty: str = "medium",
    context_query: str = "",
    fix_hint: str = "",
) -> dict[str, Any]:
    """Публичная точка генерации карточки квиза (для тихой регенерации в HTTP).

    Возвращает карточку {question, options, answer_type, _correct_answer,
    difficulty} либо поднимает исключение (fail-soft вызывающей стороной).
    ``fix_hint`` — причины отклонения предыдущего варианта.
    """
    return await _generate_quiz(
        ctx,
        {"topic": topic, "difficulty": difficulty, "context_query": context_query},
        fix_hint=fix_hint,
    )


async def _call_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> Any:
    if name == "rag_search":
        if not ctx.rag:
            raise RuntimeError("RAG engine не подключён")
        query = args.get("query")
        results = ctx.rag.search(
            query=query,
            top_k=int(args.get("top_k", 5)),
            filters=args.get("filters") or None,
        )
        if not results:
            # Пустая база/пустой результат: не «молчим» пустым массивом, а явно
            # направляем модель на web_search либо ответ из общих знаний.
            note = "База знаний пуста"
            if query and str(query).strip():
                note += f" по теме «{str(query).strip()}»"
            return [
                {
                    "note": True,
                    "text": note + ". Попробуйте web_search или объясните из общих знаний.",
                    "score": 0.0,
                    "metadata": {},
                }
            ]
        return [
            {
                "text": r.chunk.text,
                "score": round(r.score, 4),
                "metadata": r.chunk.metadata,
            }
            for r in results
        ]
    elif name == "web_search":
        results = await SearchRouter.search(
            query=args["query"],
            max_results=int(args.get("max_results", 5)),
        )
        return [
            {
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
            }
            for r in results
        ]
    elif name == "generate_quiz":
        return await _generate_quiz(ctx, args)
    raise ValueError(f"неизвестный инструмент: {name}")