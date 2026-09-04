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

from ..rag import RAGEngine
from ..search.base import SearchRouter

MAX_TOOL_RESULT_CHARS = 4000
MAX_TOOL_RETRIES = 2
TOOL_TIMEOUT_SEC = 15.0


@dataclass
class ToolContext:
    rag: RAGEngine | None = None
    region: str = "GLOBAL"
    on_tool: Callable | None = None


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

TOOL_SCHEMAS = [RAG_TOOL_SCHEMA, WEB_SEARCH_TOOL_SCHEMA]


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

    for attempt in range(MAX_TOOL_RETRIES + 1):
        try:
            result = await asyncio.wait_for(_call_tool(name, args, ctx), timeout=TOOL_TIMEOUT_SEC)
            tracker.on_success(name)
            return _ok(result)
        except TimeoutError:
            last = f"tool '{name}' таймаут (> {TOOL_TIMEOUT_SEC}s)"
        except Exception as e:  # noqa: BLE001
            last = f"tool '{name}' error: {e}"

        # retry с короткой паузой
        await asyncio.sleep(0.3 * (attempt + 1))
        tracker.on_failure(name)

    return _err(last)


async def _call_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> Any:
    if name == "rag_search":
        if not ctx.rag:
            raise RuntimeError("RAG engine не подключён")
        results = ctx.rag.search(
            query=args["query"],
            top_k=int(args.get("top_k", 5)),
            filters=args.get("filters") or None,
        )
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
    raise ValueError(f"неизвестный инструмент: {name}")