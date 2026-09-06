"""Тесты парсинга финального ответа в ContentEnvelope."""

from src.agent.envelope import (
    looks_like_truncated_envelope,
    parse_content_envelope,
    parse_content_envelopes,
    salvage_truncated_envelope,
)
from src.agent.prompts import SYSTEM_PROMPT
from src.models.schemas import ContentType


def test_valid_json_object():
    raw = (
        '{"type": "theory", "text": "Текст $$x^2$$", "payload": {"topic": "тест"}, '
        '"difficulty": "hard"}'
    )
    env = parse_content_envelope(raw)
    assert env.type == ContentType.THEORY
    assert "x^2" in env.text
    assert env.payload["topic"] == "тест"
    assert env.difficulty == "hard"


def test_json_wrapped_in_code_fence():
    raw = (
        "Ответ:\n```json\n"
        '{"type": "quiz", "text": "Вопрос?", "payload": {"answer_type": "single", '
        '"options": ["a", "b"]}}'
        "\n```"
    )
    env = parse_content_envelope(raw)
    assert env.type == ContentType.QUIZ
    assert env.payload["answer_type"] == "single"


def test_json_prefixed_with_json_word_without_fence():
    """deepseek-chat отвечает «json {…}» без markdown-обёртки — должны парсить."""
    raw = (
        'json {"type": "practice", "text": "Задача", '
        '"payload": {"task_ref": "x"}, "difficulty": "medium"}'
    )
    env = parse_content_envelope(raw)
    assert env.type == ContentType.PRACTICE
    assert env.text == "Задача"
    assert env.payload["task_ref"] == "x"


def test_json_surrounded_by_plain_text():
    """JSON-конверт внутри короткого текста извлекается целиком."""
    raw = (
        "Вот ответ: "
        '{"type": "quiz", "text": "Вопрос?", '
        '"payload": {"answer_type": "single", "options": ["a", "b"]}}'
        " Проверьте его."
    )
    env = parse_content_envelope(raw)
    assert env.type == ContentType.QUIZ
    assert env.text == "Вопрос?"


def test_evaluation_payload_preserved():
    raw = (
        '{"type": "evaluation", "text": "Верно", '
        '"payload": {"correct": true, "knowledge_delta": 0.3}}'
    )
    env = parse_content_envelope(raw)
    assert env.type == ContentType.EVALUATION
    assert env.payload["correct"] is True
    assert env.payload["knowledge_delta"] == 0.3


def test_plain_text_falls_back_to_theory():
    env = parse_content_envelope("Простое объяснение без JSON")
    assert env.type == ContentType.THEORY
    assert env.text == "Простое объяснение без JSON"


def test_raw_newline_inside_json_string_is_repaired():
    """Модель вставляет в ``"text"`` настоящий перевод строки — невалидный JSON.

    Раньше конверт терялся, и ученик видел в чате сырой «словарь» с метаданными
    вместо урока. Теперь управляющие символы внутри строк экранируются.
    """
    raw = (
        '{"type": "theory", "text": "Сила тяжести — это сила.\\n\\n'
        'Масса тела измеряется в кг.", "payload": {"topic": "Сила тяжести"}, '
        '"difficulty": "medium"}'
    ).replace("\\n\\n", "\n\n")
    env = parse_content_envelope(raw)
    assert env.type == ContentType.THEORY
    assert env.text == "Сила тяжести — это сила.\n\nМасса тела измеряется в кг."
    assert env.payload["topic"] == "Сила тяжести"


def test_raw_newline_in_second_of_two_envelopes():
    raw = (
        '{"type": "theory", "text": "Объяснение.", '
        '"payload": {"topic": "т"}, "difficulty": "easy"}\n\n'
        '{"type": "practice", "text": "Задача\\nс условием в несколько строк.", '
        '"payload": {"task_ref": "x"}, "difficulty": "medium"}'
    ).replace("\\nс", "\nс")
    envelopes = parse_content_envelopes(raw)
    assert [e.type.value for e in envelopes] == ["theory", "practice"]
    assert envelopes[1].text == "Задача\nс условием в несколько строк."


def test_escaped_newline_inside_string_is_not_double_escaped():
    raw = (
        '{"type": "theory", "text": "Строка с \\\\n экранированием.", '
        '"payload": {"topic": "т"}, "difficulty": "medium"}'
    )
    env = parse_content_envelope(raw)
    assert env.type == ContentType.THEORY
    assert env.text == "Строка с \\n экранированием."


def test_raw_crlf_inside_json_string_is_repaired():
    raw = (
        '{"type": "theory", "text": "Строка один\\r\\nСтрока два", '
        '"payload": {"topic": "т"}, "difficulty": "easy"}'
    ).replace("\\r\\n", "\r\n")
    env = parse_content_envelope(raw)
    assert env.type == ContentType.THEORY
    assert env.text == "Строка один\r\nСтрока два"


def test_truncated_json_is_not_parsed_but_looks_like_envelope():
    """Модель упёрлась в max_tokens посреди JSON — целого конверта нет."""
    raw = (
        '{"type": "theory", "text": "Рациональные числа — это числа. '
        'Формула: $$F = m \\cdot g$$, а дальше текст обрывается на '
    )
    assert parse_content_envelopes(raw) == []
    assert looks_like_truncated_envelope(raw) is True


def test_salvage_truncated_theory_returns_clean_text():
    raw = (
        '{"type": "theory", "text": "Рациональные числа — это числа. '
        'Формула: $$F = m \\cdot g$$. И начало незакрытой формулы: $$E = mc'
    )
    env = salvage_truncated_envelope(raw)
    assert env is not None
    assert env.type == ContentType.THEORY
    assert env.text.startswith("Рациональные числа")
    assert "$$E = mc" not in env.text  # хвост незакрытой формулы срезан
    assert "type" not in env.text and "{""" not in env.text


def test_salvage_keeps_topic_when_payload_was_written():
    raw = (
        '{"type": "theory", "text": "Полный урок про рациональные числа.", '
        '"payload": {"topic": "Рациональные числа"}, "difficulty": "med'
    )
    env = salvage_truncated_envelope(raw)
    assert env is not None
    assert env.text == "Полный урок про рациональные числа."
    assert env.payload.get("topic") == "Рациональные числа"
    assert env.difficulty == "medium"  # оборванное difficulty -> дефолт


def test_salvage_truncated_quiz_returns_none():
    raw = '{"type": "quiz", "text": "Вопрос для ученика, который оборвался'
    assert looks_like_truncated_envelope(raw) is True
    assert salvage_truncated_envelope(raw) is None


def test_salvage_truncated_quiz_with_text_as_theory_hint():
    """Обрыв quiz с уже сформированным text: с include_quiz_hint — theory-подсказка.

    Варианты ответов/``_correct_answer`` ещё не написаны, поэтому частичный вопрос
    безопасно показать ученику как подсказку, а не прятать за отказом.
    """
    raw = '{"type": "quiz", "text": "Сколько будет $5 + (-3)$?", "payload": {"answ'
    assert salvage_truncated_envelope(raw) is None  # по умолчанию секреты не утекают
    env = salvage_truncated_envelope(raw, include_quiz_hint=True)
    assert env is not None
    assert env.type == ContentType.THEORY
    assert env.text.startswith("Сколько будет $5 + (-3)$?")


def test_salvage_truncated_practice_with_text_as_theory_hint():
    raw = '{"type": "practice", "text": "Реши пример: $5 + (-3) = ?$", "payload": {"task'
    assert salvage_truncated_envelope(raw) is None
    env = salvage_truncated_envelope(raw, include_quiz_hint=True)
    assert env is not None
    assert env.type == ContentType.THEORY
    assert env.text == "Реши пример: $5 + (-3) = ?$"


def test_salvage_quiz_without_text_returns_none_even_with_hint():
    """Обрыв ДО появления поля ``"text"``: подсказать нечего — None и с hint."""
    raw = '{"type": "quiz", "payload": {"answer_type": "single", "options": ["a", "b"]'
    assert looks_like_truncated_envelope(raw) is True
    assert salvage_truncated_envelope(raw, include_quiz_hint=True) is None


def test_salvage_practice_without_text_returns_none_even_with_hint():
    raw = '{"type": "practice", "payload": {"task_ref": "x"}, "difficulty": "med'
    assert salvage_truncated_envelope(raw) is None
    assert salvage_truncated_envelope(raw, include_quiz_hint=True) is None


def test_salvage_ignores_plain_text():
    assert salvage_truncated_envelope("Обычный текст без JSON") is None
    assert looks_like_truncated_envelope("Обычный текст") is False


def test_two_consecutive_json_objects_parse_to_two():
    """Модель вернула theory + practice подряд — парсим оба, а не «словарь»."""
    raw = (
        '{"type": "theory", "text": "Физические явления — это изменения.", '
        '"payload": {"topic": "Физические явления"}, "difficulty": "easy"}\n\n'
        '{"type": "practice", "text": "Приведите пример явления.", '
        '"payload": {"task_ref": "daily"}, "difficulty": "medium"}'
    )
    envelopes = parse_content_envelopes(raw)
    assert [e.type.value for e in envelopes] == ["theory", "practice"]
    assert envelopes[0].payload["topic"] == "Физические явления"
    assert envelopes[1].payload["task_ref"] == "daily"
    assert envelopes[1].difficulty == "medium"


def test_parse_content_envelope_takes_first_of_two():
    raw = (
        '{"type": "theory", "text": "Объяснение.", '
        '"payload": {"topic": "т"}, "difficulty": "easy"}\n\n'
        '{"type": "practice", "text": "Задание.", '
        '"payload": {"task_ref": "x"}, "difficulty": "medium"}'
    )
    env = parse_content_envelope(raw)
    assert env.type == ContentType.THEORY
    assert env.text == "Объяснение."


def test_two_objects_with_latex_braces_and_code_fence():
    raw = (
        "```json\n"
        '{"type": "theory", "text": "Формула $$x = \\\\frac{-b \\\\pm \\\\sqrt{D}}{2a}$$.", '
        '"payload": {"topic": "кв"}, "difficulty": "medium"}\n'
        "\n```\n"
        '{"type": "quiz", "text": "Чему равен D?", '
        '"payload": {"answer_type": "single", "options": ["1", "2"], "_correct_answer": "1"}, '
        '"difficulty": "hard"}'
    )
    envelopes = parse_content_envelopes(raw)
    assert [e.type.value for e in envelopes] == ["theory", "quiz"]
    assert "\\frac" in envelopes[0].text
    assert envelopes[1].payload["answer_type"] == "single"


def test_no_json_still_falls_back_to_theory_text():
    raw = "Просто текст без фигурных скобок."
    assert parse_content_envelopes(raw) == []
    env = parse_content_envelope(raw)
    assert env.type == ContentType.THEORY
    assert env.text == raw


def test_invalid_json_in_fence_falls_back():
    env = parse_content_envelope('```json\n{broken}\n```')
    assert env.type == ContentType.THEORY
    assert env.text == '```json\n{broken}\n```'


def test_empty_string_falls_back():
    env = parse_content_envelope("")
    assert env.type == ContentType.THEORY
    assert env.text == ""


def test_system_prompt_requires_json_envelope():
    assert "JSON-объект" in SYSTEM_PROMPT
    assert '"type"' in SYSTEM_PROMPT
    assert "knowledge_delta" in SYSTEM_PROMPT


def test_prompt_mentions_hidden_correct_answer():
    assert "_correct_answer" in SYSTEM_PROMPT
