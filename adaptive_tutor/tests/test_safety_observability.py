"""Тесты наблюдаемости (JSON-логи) и безопасности."""

import json
from pathlib import Path

import pytest

from src.observability.logger import JsonlLogger, TraceContext, log_trace, scrub
from src.safety import BudgetGuard, CircuitBreaker, IterationLimiter, OutputValidator


def _read_lines(path: str) -> list[str]:
    """Читает строки JSONL-файла."""
    return Path(path).read_text(encoding="utf-8").strip().splitlines()


def _last_record(path: str) -> dict:
    """Возвращает последнюю запись JSONL-файла."""
    return json.loads(_read_lines(path)[-1])

# --- Наблюдаемость ---

def test_scrub_masks_sensitive_keys_recursively():
    data = {
        "api_key": "sk-secret-123",
        "config": {"routerai_api_key": "abc"},
        "safe": "visible",
        "nested": {"token": "xyz"},
    }
    out = scrub(data)
    assert out["api_key"] == "***"
    assert out["config"]["routerai_api_key"] == "***"
    assert out["nested"]["token"] == "***"
    assert out["safe"] == "visible"


def test_jsonl_logger_appends_valid_json(tmp_path):
    log = JsonlLogger(str(tmp_path / "agent.jsonl"))
    tid = "trace_abc"
    log.log(tid, "INFO", "agent.step", agent={"action": "tool"}, status="ok")
    lines = _read_lines(log._path)
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["trace_id"] == tid
    assert rec["event"] == "agent.step"
    assert rec["level"] == "INFO"
    assert "timestamp" in rec


def test_trace_context_sets_and_clears():
    tid = "my-trace"
    with TraceContext(tid):
        assert TraceContext.current() == tid
    assert TraceContext.current() == ""


def test_log_trace_decorator_sync(tmp_path):
    log = JsonlLogger(str(tmp_path / "agent.jsonl"))

    @log_trace(log, "test.op")
    def op(x: int) -> int:
        return x * 2

    with TraceContext("deco_trace"):
        assert op(21) == 42

    rec = _last_record(log._path)
    assert rec["event"] == "test.op"
    assert rec["status"] == "ok"
    assert "duration_ms" in rec


def test_log_trace_decorator_captures_error(tmp_path):
    log = JsonlLogger(str(tmp_path / "agent.jsonl"))

    @log_trace(log, "test.fail")
    def boom():
        raise RuntimeError("kaboom")

    with TraceContext("deco_err"), pytest.raises(RuntimeError):
        boom()

    rec = _last_record(log._path)
    assert rec["status"] == "error"
    assert "kaboom" in rec["error"]


# --- Безопасность ---

def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(threshold=3, cooldown_sec=60)
    assert not cb.is_open()
    cb.record_failure()
    cb.record_failure()
    assert not cb.is_open()  # 2 < 3
    cb.record_failure()  # -> 3, открывается
    assert cb.is_open()


def test_circuit_breaker_recovers_after_cooldown():
    cb = CircuitBreaker(threshold=1, cooldown_sec=1)
    cb.record_failure()
    assert cb.is_open()
    # simulate time passing (cooldown 1s)
    cb._opened_at -= 2
    assert not cb.is_open()  # HALF_OPEN
    cb.record_success()
    assert not cb.is_open() and cb.state == CircuitBreaker.CLOSED


def test_iteration_limiter_stops():
    lim = IterationLimiter(max_iterations=3)
    assert lim.tick()
    assert lim.tick()
    assert lim.tick()
    assert not lim.tick()  # 3-й исчерпал
    assert lim.stopped
    lim.reset()
    assert lim.tick()


def test_budget_guard_limits_calls_and_cost():
    bg = BudgetGuard(max_cost_usd=1.0, max_calls=3)
    assert bg.allowed()
    bg.record(0.4)
    assert bg.allowed()
    bg.record(0.6)  # суммарно 1.0 — больше нельзя
    assert not bg.allowed(0.01)  # cost-limit
    # отдельно: лимит вызовов
    bg2 = BudgetGuard(max_cost_usd=100.0, max_calls=2)
    bg2.record()
    bg2.record()
    assert bg2.exceeded()


def test_output_validator_rejects_unbalanced_latex():
    v = OutputValidator()
    ok, _ = v.validate("Ответ с $$x^2$$ формулой")
    assert ok
    ok, err = v.validate("Формула $$x^2 не закрыта")
    assert not ok and "LaTeX" in err


def test_output_validator_rejects_empty():
    v = OutputValidator()
    ok, err = v.validate("   ")
    assert not ok and "пустой" in err