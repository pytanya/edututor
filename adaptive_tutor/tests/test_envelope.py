"""Тесты парсинга финального ответа в ContentEnvelope."""

from src.agent.envelope import parse_content_envelope
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
