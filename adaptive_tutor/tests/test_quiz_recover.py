"""Юнит-тесты чистой recovery-политики quiz (тексты, инструкция, streak)."""

from src.agent.quiz_guard import (
    QUIZ_BLOCKED_NOTE,
    QUIZ_BLOCKED_TEXT,
    QUIZ_REJECT_CAP,
    QUIZ_REJECT_RETRY_TEXT,
    build_regen_instruction,
    next_reject_state,
    quiz_reject_text,
)


def test_quiz_reject_cap_is_two():
    assert QUIZ_REJECT_CAP == 2


def test_quiz_reject_text_retry_variant():
    text = quiz_reject_text(blocked=False)
    assert text == QUIZ_REJECT_RETRY_TEXT
    # старый canned-цикл («другой вопрос») в текстах отсутствует
    assert "другой вопрос" not in text
    assert "сформулирую его заново" not in text


def test_quiz_reject_text_blocked_variant():
    text = quiz_reject_text(blocked=True)
    assert text == QUIZ_BLOCKED_TEXT
    assert "другой вопрос" not in text


def test_build_regen_instruction_uses_only_reasons():
    instruction = build_regen_instruction([
        {
            "reasons": [
                "текст не является вопросом (нет «?» и нет вопросительного слова)",
                "правильный ответ раскрыт в тексте вопроса",
            ],
            "text": "Мерой инертности тела является его масса.",
            "answer_type": "single",
        }
    ])
    assert instruction.startswith("Твой предыдущий quiz-конверт отклонён")
    assert "не является вопросом" in instruction
    assert "раскрыт в тексте вопроса" in instruction
    # текст/секрет отклонённого квиза в промпт регенерации не попадают
    assert "масса" not in instruction.lower()


def test_build_regen_instruction_dedups_and_fallback():
    one = build_regen_instruction([{"reasons": ["причина A", "причина A"]}])
    assert one.count("причина A") == 1
    empty = build_regen_instruction(
        [{"reasons": [], "text": "x", "answer_type": None}]
    )
    assert "структурная невалидность" in empty


def test_next_reject_state_resets_on_recovered():
    assert next_reject_state(streak=1, blocked=True, recovered=True) == (0, False)
    assert next_reject_state(streak=0, blocked=False, recovered=True) == (0, False)


def test_next_reject_state_increments_and_blocks_at_cap():
    # 0->1 (не блок), 1->2 (=CAP, блок), 2->3 (блок сохраняется)
    assert next_reject_state(0, False, recovered=False) == (1, False)
    assert next_reject_state(1, False, recovered=False) == (2, True)
    assert next_reject_state(2, True, recovered=False) == (3, True)


def test_blocked_note_tells_model_to_skip_quiz():
    assert "НЕ выдавай quiz" in QUIZ_BLOCKED_NOTE
