"""SM-2 (SuperMemo-2): чистые функции без sqlite.

Формулы и константы — 1:1 с референсом project_work (src/review.py).
"""

import datetime
import hashlib

MIN_EASE = 1.3
INITIAL_EASE = 2.5
INITIAL_INTERVAL = 1.0


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def card_id_for(question: str) -> str:
    """Стабильный ID карточки по тексту вопроса (дедуп)."""
    return hashlib.sha256((question or "").strip().encode("utf-8")).hexdigest()[:16]


def is_due(card: dict) -> bool:
    """Due = нет due_at / непарсится / наступило."""
    if not card.get("due_at"):
        return True
    due = parse_iso(str(card["due_at"]))
    return due is None or due <= datetime.datetime.now()


def apply_sm2(card: dict, correct: bool) -> dict:
    """Применяет SM-2 к SM-2-полям карточки и возвращает обновлённые поля.

    Не мутирует входной dict. Вызывающий сохраняет результат в БД.
    """
    reps = int(card.get("reps", 0))
    interval = float(card.get("interval_days", INITIAL_INTERVAL))
    ease = float(card.get("ease", INITIAL_EASE))
    lapses = int(card.get("lapses", 0))
    now = datetime.datetime.now()
    if correct:
        reps += 1
        interval = INITIAL_INTERVAL if reps == 1 else round(interval * ease, 1)
        ease = max(MIN_EASE, round(ease + (0.1 - max(0, 3 - reps) * 0.05), 2))
    else:
        reps = 0
        interval = INITIAL_INTERVAL
        lapses += 1
        ease = max(MIN_EASE, round(ease - 0.2, 2))
    due_at = (now + datetime.timedelta(days=interval)).isoformat(timespec="seconds")
    return {
        "reps": reps,
        "interval_days": interval,
        "ease": ease,
        "lapses": lapses,
        "last_reviewed": now_iso(),
        "due_at": due_at,
    }
