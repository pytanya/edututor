# Пример JSON-лога (агентный цикл)

Каждая запись — одна JSON-строка. Декоратор `@log_trace` и методы
`JsonlLogger.step/llm_call/finished` гарантируют наличие `trace_id`,
`timestamp`, `event`, `status` и метрик.

## Шаг планирования (agent.step)

```json
{
  "timestamp": "2026-09-03T12:00:01.482193Z",
  "level": "INFO",
  "trace_id": "1f3a9c2e5b0f4d8a9c2e5b0f4d8a9c2e",
  "event": "agent.step",
  "agent": {
    "action": "tool",
    "reason_summary": "rag_search",
    "model": "anthropic/claude-sonnet-4"
  },
  "tool": {
    "name": "rag_search",
    "arguments": {
      "query": "квадратичная функция",
      "top_k": 5,
      "filters": {"topic": "algebra"}
    }
  },
  "status": "ok",
  "duration_ms": 320,
  "session_id": "sess-12345",
  "loop_invariants": {"difficulty": "medium"}
}
```

## Вызов LLM (agent.llm)

```json
{
  "timestamp": "2026-09-03T12:00:01.805101Z",
  "level": "INFO",
  "trace_id": "1f3a9c2e5b0f4d8a9c2e5b0f4d8a9c2e",
  "event": "agent.llm",
  "agent": {"model": "anthropic/claude-sonnet-4"},
  "llm": {
    "tokens": {"prompt_tokens": 1284, "completion_tokens": 96, "total_tokens": 1380},
    "cost_usd": 0.00124
  },
  "status": "ok",
  "duration_ms": 515,
  "sources": ["https://tavily.com/result/1", "local_rag:chunk_42"]
}
```

## Завершение (agent.finished)

```json
{
  "timestamp": "2026-09-03T12:00:02.110482Z",
  "level": "INFO",
  "trace_id": "1f3a9c2e5b0f4d8a9c2e5b0f4d8a9c2e",
  "event": "agent.finished",
  "session_id": "sess-12345",
  "status": "ok",
  "steps": 2,
  "duration_ms": 0
}
```

## Ошибка (уровень ERROR)

```json
{
  "timestamp": "2026-09-03T12:00:03.120482Z",
  "level": "ERROR",
  "trace_id": "1f3a9c2e5b0f4d8a9c2e5b0f4d8a9c2e",
  "event": "agent.plan",
  "status": "error",
  "error": "LLM error: Connection timed out",
  "duration_ms": 15000
}
```

## Что НЕ логируется

Скрытый chain-of-thought, секреты/API-ключи (маскируются `scrub` → `***`),
PII, тяжёлые промпты и полные системные системные промпты.