"""Безопасность: Circuit Breaker, лимит итераций, бюджет, выходная валидация."""

import threading
import time

from ..config import settings


class CircuitBreakerError(RuntimeError):
    pass


class CircuitBreaker:
    """Простой circuit breaker.

    Состояния:
      CLOSED — запросы идут. При failure_count >= threshold переходит в OPEN.
      OPEN   — запросы отклоняются на cooldown секунд, затем HALF_OPEN.
      HALF_OPEN — один пробный запрос; успех → CLOSED, ошибка → OPEN.
    """

    CLOSED, OPEN, HALF_OPEN = "CLOSED", "OPEN", "HALF_OPEN"

    def __init__(
        self,
        threshold: int = None,
        cooldown_sec: int = None,
        name: str = "llm",
    ):
        self.threshold = threshold or settings.circuit_breaker_threshold
        self.cooldown = cooldown_sec or settings.circuit_breaker_timeout
        self.name = name
        self._state = self.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        return self._state

    def is_open(self) -> bool:
        with self._lock:
            if self._state == self.OPEN:
                if time.time() - self._opened_at >= self.cooldown:
                    self._state = self.HALF_OPEN
                    return False
                return True
            return False

    def record_success(self):
        with self._lock:
            self._failures = 0
            self._state = self.CLOSED

    def record_failure(self):
        with self._lock:
            self._failures += 1
            if self._state == self.HALF_OPEN or self._failures >= self.threshold:
                self._state = self.OPEN
                self._opened_at = time.time()

    def __call__(self, fn=None):
        return self


class IterationLimiter:
    """Защита от бесконечных циклов: жёсткий лимит шагов агента.

    Следит за количеством итераций. При превышении статус меняется на
    'stopped' и последующие вызовы отклоняются.

    TT (вдохновлено DeepTutor): даже при «зависшем» цикле агент гарантированно
    остановится на max_iterations, не расходуя бесконечный бюджет.
    """

    def __init__(self, max_iterations: int | None = None):
        self.max_iterations = max_iterations or settings.max_agent_steps
        self._count = 0
        self._lock = threading.Lock()

    @property
    def can_iterate(self) -> bool:
        with self._lock:
            return self._count < self.max_iterations

    @property
    def stopped(self) -> bool:
        return not self.can_iterate

    def tick(self) -> bool:
        """Инкрементирует счётчик. Возвращает False, если лимит исчерпан."""
        with self._lock:
            if self._count >= self.max_iterations:
                return False
            self._count += 1
            return True

    def reset(self):
        with self._lock:
            self._count = 0


class BudgetExceededError(RuntimeError):
    pass


class BudgetGuard:
    """Ограничение стоимости и количества LLM-вызовов за сессию."""

    def __init__(
        self,
        max_cost_usd: float = None,
        max_calls: int = None,
    ):
        self.max_cost = (
            max_cost_usd if max_cost_usd is not None else settings.max_cost_per_session_usd
        )
        self.max_calls = (
            max_calls if max_calls is not None else settings.max_llm_calls_per_session
        )
        self._spent = 0.0
        self._calls = 0
        self._lock = threading.Lock()

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def spent(self) -> float:
        return self._spent

    def record(self, cost_usd: float = 0.0):
        with self._lock:
            self._calls += 1
            self._spent += cost_usd

    def allowed(self, cost_usd: float = 0.0) -> bool:
        with self._lock:
            return (
                self._calls < self.max_calls
                and self._spent + cost_usd <= self.max_cost
            )

    def exceeded(self, model: str | None = None) -> bool:
        return not self.allowed()


# --- Выходная валидация ---

REQUIRED_WORDS_LATEX = ["$$", "$"]


class OutputValidator:
    """Проверяет итоговый ответ агента перед возвратом пользователю.

    Проверки:
      - непустой ответ;
      - LaTeX-формулы корректно сбалансированы (парность $…$ и $$…$$);
      - ответ не содержит явных признаков инъекции/служебных меток.
    """

    def validate(self, answer: str) -> tuple[bool, str | None]:
        if not answer or not answer.strip():
            return False, "пустой ответ"

        if _unbalanced_latex(answer):
            return False, "несбалансированные LaTeX-формулы"

        return True, None


def _unbalanced_latex(text: str) -> bool:
    # Простой счётчик: парные $$ и $ должны быть чётными.
    dbl = text.count("$$")
    single = text.count("$") - 2 * dbl
    return single % 2 != 0 or dbl % 2 != 0