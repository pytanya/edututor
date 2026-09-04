"""Тесты чистого SM-2-ядра."""

import datetime

from src.review.sm2 import apply_sm2, card_id_for, is_due, now_iso


def _card(**overrides):
    base = {
        "card_id": "x", "due_at": now_iso(), "interval_days": 1.0,
        "ease": 2.5, "reps": 0, "lapses": 0,
    }
    base.update(overrides)
    return base


def test_card_id_stable_and_trimmed():
    assert card_id_for("  вопрос?  ") == card_id_for("вопрос?")
    assert len(card_id_for("вопрос")) == 16


def test_first_correct_interval_one_day():
    out = apply_sm2(_card(reps=0), True)
    assert out["reps"] == 1
    assert out["interval_days"] == 1.0
    assert out["ease"] == 2.5  # бонус при reps=1 равен 0.0
    assert out["lapses"] == 0


def test_second_correct_multiplies_interval():
    out = apply_sm2(_card(reps=1, interval_days=1.0, ease=2.5), True)
    assert out["reps"] == 2
    assert out["interval_days"] == 2.5
    assert out["ease"] == 2.55


def test_incorrect_resets_and_lapses():
    out = apply_sm2(_card(reps=5, interval_days=10.0, ease=2.5), False)
    assert out["reps"] == 0
    assert out["interval_days"] == 1.0
    assert out["lapses"] == 1
    assert out["ease"] == 2.3


def test_ease_min_clamp():
    out = apply_sm2(_card(reps=0, ease=1.3), False)
    assert out["ease"] == 1.3


def test_due_at_in_future_after_correct():
    out = apply_sm2(_card(reps=0), True)
    assert out["due_at"] > now_iso()


def test_is_due_empty_and_parse():
    assert is_due(_card(due_at=""))
    assert is_due(_card(due_at="не-дата"))
    future = (datetime.datetime.now() + datetime.timedelta(days=2)).isoformat()
    assert not is_due(_card(due_at=future))
