"""Парсинг финального ответа агента в ContentEnvelope (с фолбэком)."""

import json
import re
from typing import Any

from ..models.schemas import ContentEnvelope, ContentType

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

_HIDDEN_PAYLOAD_PREFIXES = ("_",)


def sanitize_envelope(env: ContentEnvelope) -> ContentEnvelope:
    """Копия конверта без скрытых полей payload (_... и correct_answer)."""
    payload = {
        k: v for k, v in (env.payload or {}).items()
        if not (k.startswith(_HIDDEN_PAYLOAD_PREFIXES) or k == "correct_answer")
    }
    return env.model_copy(update={"payload": payload})


def _extract_json(text: str) -> dict[str, Any] | None:
    """Пытается получить dict из текста.

    Принимает: чистый JSON, ```json-обёртку, «json {…}» без обёртки (модели вроде
    deepseek-chat добавляют слово ``json`` перед объектом) и JSON внутри короткого
    текста (от первого '{' до последнего '}').
    """
    stripped = (text or "").strip()
    candidates: list[str] = []
    if stripped:
        candidates.append(stripped)
    match = _FENCE.search(stripped)
    if match:
        candidates.append(match.group(1).strip())
    # «json {…}» без фенса: если весь текст не является JSON, пробуем срез
    # от первого '{' до последнего '}' (устойчиво к пояснениям вокруг объекта).
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        fragment = stripped[start : end + 1]
        if fragment != stripped:
            candidates.append(fragment)
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def parse_content_envelope(raw: str) -> ContentEnvelope:
    """Парсит raw-ответ в ContentEnvelope; при ошибке — фолбэк theory с raw-текстом."""
    data = _extract_json(raw or "")
    if data is not None:
        try:
            return ContentEnvelope.model_validate(data)
        except Exception:  # noqa: BLE001 — невалидная схема -> фолбэк
            pass
    return ContentEnvelope(type=ContentType.THEORY, text=raw or "")
