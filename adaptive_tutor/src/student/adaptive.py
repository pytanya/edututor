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
