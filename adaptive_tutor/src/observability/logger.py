"""Структурированное JSON-логирование с уникальным trace_id.

Формат записи (одна JSON-строка на событие):
{
  "timestamp": "2026-09-03T12:00:00.123456Z",
  "level": "INFO",
  "trace_id": "...",
  "event": "agent.step",
  "agent": { ... },
  "llm": { ... },
  "tool": { ... },
  "status": "ok",
  "sources": [],
  "tokens": {...},
  "cost_usd": 0.0012,
  "duration_ms": 320,
  "session_id": "..."
}

НЕ логируются: скрытый chain-of-thought, секреты/ключи, PII, тяжёлые промпты.
"""

import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

try:
    from ..models.schemas import AgentGraphState, AgentStep
except ImportError:  # pragma: no cover
    AgentStep = None  # type: ignore
    AgentGraphState = None  # type: ignore

try:
    import asyncio

    def asyncio_iscoroutinefunction(fn: Callable) -> bool:
        return asyncio.iscoroutinefunction(fn)
except Exception:  # pragma: no cover
    def asyncio_iscoroutinefunction(fn: Callable) -> bool:  # type: ignore
        return False

# Поля, которые запрещено логировать ни при каких обстоятельствах.
_SENSITIVE_KEYS = {
    "api_key", "secret", "password", "token", "authorization",
    "openrouter_api_key", "routerai_api_key", "yandex_api_key", "tavily_api_key",
}


def scrub(value: Any) -> Any:
    """Маскирует чувствительные поля рекурсивно (без изменения исходных данных)."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            low = str(k).lower()
            sensitive = any(s in low for s in ("key", "secret", "token", "password"))
            if low in _SENSITIVE_KEYS or sensitive:
                out[k] = "***"
            else:
                out[k] = scrub(v)
        return out
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


class JsonlLogger:
    """Thread-safe JSONL-логгер с trace_id."""

    def __init__(self, file_path: str, level: int = logging.INFO):
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        self._path = file_path
        self._level = level
        self._lock = threading.Lock()

    @classmethod
    def new_trace_id(cls) -> str:
        return uuid.uuid4().hex

    def _write(self, record: dict[str, Any]):
        record["timestamp"] = _now()
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock, open(self._path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def log(self, trace_id: str, level: str, event: str, **fields: Any):
        if _level_value(level) < self._level:
            return
        record = {
            "level": level.upper(),
            "trace_id": trace_id,
            "event": event,
            **scrub(fields),
        }
        self._write(record)

    def step(self, state: "AgentGraphState", step: "AgentStep", trace_id: str):
        """Логирует один шаг агентного цикла (без скрытого reasoning)."""
        self.log(
            trace_id,
            "INFO",
            "agent.step",
            agent={
                "action": step.action,
                "reason_summary": step.reason_summary,  # краткое объяснение, НЕ CoT
                "model": step.model,
            },
            tool={
                "name": step.tool,
                "arguments": scrub(step.arguments) if step.arguments else None,
            },
            status=step.status,
            duration_ms=step.duration_ms,
            session_id=state.session_id,
            loop_invariants={"difficulty": state.difficulty},
        )

    def llm_call(
        self,
        trace_id: str,
        model: str,
        status: str,
        tokens: dict[str, int],
        cost_usd: float,
        duration_ms: int,
        sources: list[str],
    ):
        self.log(
            trace_id,
            "INFO",
            "agent.llm",
            agent={"model": model},
            llm={"tokens": tokens, "cost_usd": cost_usd},
            status=status,
            duration_ms=duration_ms,
            sources=sources,
        )


def _now() -> str:
    micros = int(time.time() * 1e6) % 1_000_000
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{micros:06d}Z"


def _level_value(level: str) -> int:
    return {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}.get(level.upper(), 20)


class TraceContext:
    """Контекст trace_id: корректная вложенность через contextvar/thread-local."""

    _local = threading.local()

    def __init__(self, trace_id: str | None = None):
        self.trace_id = trace_id or JsonlLogger.new_trace_id()

    def __enter__(self):
        self._prev = getattr(self._local, "trace_id", None)
        self._local.trace_id = self.trace_id
        return self

    def __exit__(self, *exc):
        if self._prev is None:
            delattr(self._local, "trace_id")
        else:
            self._local.trace_id = self._prev

    @classmethod
    def current(cls) -> str:
        return getattr(cls._local, "trace_id", "")


def log_trace(logger: JsonlLogger, event: str, level: str = "INFO"):
    """Декоратор: делает JSON-запись о вызове функции с trace_id и временем.

    Использование:
        @log_trace(logger, "agent.plan")
        async def plan(self, state): ...
    """
    def deco(fn: Callable) -> Callable:
        if asyncio_iscoroutinefunction(fn):

            async def async_wrapper(*args, **kwargs):
                trace_id = TraceContext.current() or JsonlLogger.new_trace_id()
                start = time.time()
                try:
                    result = await fn(*args, **kwargs)
                    ms = int((time.time() - start) * 1000)
                    logger.log(trace_id, level, event, status="ok", duration_ms=ms)
                    return result
                except Exception as e:  # noqa: BLE001
                    ms = int((time.time() - start) * 1000)
                    logger.log(
                        trace_id, "ERROR", event,
                        status="error",
                        error=str(e)[:500],
                        duration_ms=ms,
                    )
                    raise

            return async_wrapper

        def sync_wrapper(*args, **kwargs):
            trace_id = TraceContext.current() or JsonlLogger.new_trace_id()
            start = time.time()
            try:
                result = fn(*args, **kwargs)
                ms = int((time.time() - start) * 1000)
                logger.log(trace_id, level, event, status="ok", duration_ms=ms)
                return result
            except Exception as e:  # noqa: BLE001
                ms = int((time.time() - start) * 1000)
                logger.log(
                    trace_id, "ERROR", event,
                    status="error", error=str(e)[:500],
                    duration_ms=ms,
                )
                raise

        return async_wrapper if asyncio_iscoroutinefunction(fn) else sync_wrapper

    return deco


# Обратная совместимость/удобство импорта
__all__ = ["JsonlLogger", "TraceContext", "log_trace", "scrub", "asdict"]