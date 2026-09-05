"""Парсинг финального ответа агента в ContentEnvelope (с фолбэком)."""

import json
from collections.abc import Iterator

from ..models.schemas import ContentEnvelope, ContentType

_HIDDEN_PAYLOAD_PREFIXES = ("_",)


def sanitize_envelope(env: ContentEnvelope) -> ContentEnvelope:
    """Копия конверта без скрытых полей payload (_... и correct_answer)."""
    payload = {
        k: v for k, v in (env.payload or {}).items()
        if not (k.startswith(_HIDDEN_PAYLOAD_PREFIXES) or k == "correct_answer")
    }
    return env.model_copy(update={"payload": payload})


def _iter_json_objects(text: str) -> Iterator[str]:
    """Перечисляет фрагменты top-level JSON-объектов из текста по порядку.

    Сканер понимает строки и экранирование, поэтому вложенные ``{``/``}`` внутри
    ``"..."`` (например, LaTeX в тексте конверта) не ломают балансировку.
    Позволяет извлечь НЕСКОЛЬКО JSON-конвертов, идущих подряд в одном ответе
    модели (например, ``theory`` + ``practice`` через пустую строку).
    """
    i = 0
    n = len(text)
    while i < n:
        start = text.find("{", i)
        if start == -1:
            return
        depth = 0
        in_str = False
        esc = False
        j = start
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        yield text[start : j + 1]
                        i = j + 1
                        break
            j += 1
        else:
            return


def parse_content_envelopes(raw: str) -> list[ContentEnvelope]:
    """Все валидные JSON-конверты из ответа модели (их может быть несколько).

    Модель иногда отвечает несколькими конвертами подряд (теория + задание):
    каждый такой объект парсится отдельно. Невалидные фрагменты пропускаются.
    """
    envelopes: list[ContentEnvelope] = []
    for fragment in _iter_json_objects(raw or ""):
        try:
            data = json.loads(fragment)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        try:
            envelopes.append(ContentEnvelope.model_validate(data))
        except Exception:  # noqa: BLE001 — невалидная схема -> пропускаем
            continue
    return envelopes


def parse_content_envelope(raw: str) -> ContentEnvelope:
    """Парсит raw-ответ в первый валидный ContentEnvelope; иначе — theory.

    ``parse_content_envelopes`` сохраняет все конверты, а эта функция отдаёт
    первый (для single-envelope семантики агентного цикла); при отсутствии
    JSON-конверта — фолбэк theory с raw-текстом.
    """
    envelopes = parse_content_envelopes(raw)
    if envelopes:
        return envelopes[0]
    return ContentEnvelope(type=ContentType.THEORY, text=raw or "")
