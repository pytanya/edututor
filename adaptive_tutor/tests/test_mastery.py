"""Чистое ядро мастерства (E2 Task 2): EMA, статусы, проверка освоенности."""

from src.student.mastery import apply_mastery, derive_status, is_mastered


def test_three_correct_in_a_row_mastered():
    m = apply_mastery(0.0, True, 0)   # anchor: 0.5 -> 0.65
    assert m == 0.65
    m = apply_mastery(m, True, 1)     # 0.755
    assert m == 0.755
    m = apply_mastery(m, True, 2)     # 0.8285
    assert m == 0.8285
    assert derive_status(3, m) == "mastered"
    assert is_mastered(3, m, "mastered")


def test_two_correct_then_wrong_in_progress():
    m = apply_mastery(apply_mastery(0.0, True, 0), True, 1)
    m = apply_mastery(m, False, 2)    # 0.7*0.755 = 0.5285
    assert m == 0.5285
    assert derive_status(3, m) == "in_progress"
    assert not is_mastered(3, m, "in_progress")


def test_first_wrong_anchor():
    assert apply_mastery(0.0, False, 0) == 0.35   # 0.7*0.5 + 0.3*0


def test_statuses():
    assert derive_status(0, 0.0) == "not_studied"
    assert derive_status(1, 0.65) == "in_progress"
    assert derive_status(2, 0.5) == "in_progress"
