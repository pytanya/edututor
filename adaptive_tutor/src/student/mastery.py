"""Чистое ядро мастерства: EMA, статусы и проверка освоенности темы.

Модуль не знает про sqlite/LLM (спека E2 §2/§3.3) и переиспользуется в E4.
"""

from __future__ import annotations

EMA_ALPHA = 0.7
EMA_WEIGHT = 0.3
MASTERY_ANCHOR = 0.5


def apply_mastery(old_mastery: float, correct: bool, prior_attempts: int) -> float:
    """EMA мастерства: 0.7*старое + 0.3*результат. Якорь 0.5 на первом ответе."""
    old = MASTERY_ANCHOR if prior_attempts <= 0 else float(old_mastery)
    score01 = 1.0 if correct else 0.0
    return round(EMA_ALPHA * old + EMA_WEIGHT * score01, 4)


def derive_status(
    attempts: int,
    mastery: float,
    threshold: float = 0.8,
    min_attempts: int = 3,
) -> str:
    """Статус темы: not_studied / in_progress / mastered."""
    if attempts == 0:
        return "not_studied"
    if attempts >= min_attempts and mastery >= threshold:
        return "mastered"
    if attempts > 0:
        return "in_progress"
    return "not_studied"


def is_mastered(
    attempts: int,
    mastery: float,
    status: str,
    threshold: float = 0.8,
    min_attempts: int = 3,
) -> bool:
    """Освоена ли тема: явный статус или порог по попыткам и мастерству."""
    if status == "mastered":
        return True
    return attempts >= min_attempts and mastery >= threshold
