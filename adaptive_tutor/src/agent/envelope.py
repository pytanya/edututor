"""Парсинг финального ответа агента в ContentEnvelope (с фолбэком)."""

import json
import re
from collections.abc import Iterator

from ..models.schemas import ContentEnvelope, ContentType

_HIDDEN_PAYLOAD_PREFIXES = ("_",)

# Типы конвертов для извлечения/спасения оборванного ответа.
_ENVELOPE_TYPES = "|".join(ContentType)
_TYPE_RE = re.compile(rf'"type"\s*:\s*"(?P<type>{_ENVELOPE_TYPES})"')
_TEXT_RE = re.compile(r'"text"\s*:\s*"')
_TOPIC_RE = re.compile(r'"topic"\s*:\s*"([^"]*)"')
_DIFFICULTY_RE = re.compile(r'"difficulty"\s*:\s*"(easy|medium|hard)"')

# Сырые управляющие символы внутри строки запрещены JSON, но LLM часто кладёт в
# ``"text"`` настоящие переводы строк. Такая строка валидна для сканера (баланс
# скобок не ломается), но не проходит ``json.loads`` — конверт терялся бы и ученику
# уходил сырой «словарь» с метаданными. Замена на экранирование чинит это.
_CONTROL_ESCAPES = {
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\f": "\\f",
    "\b": "\\b",
}


def _escape_control_in_strings(text: str) -> str:
    """Экранирует сырые управляющие символы только внутри строковых литералов.

    Проходит по JSON-фрагменту, отслеживая строки (``"..."``) и экранирование
    (``\\``), и заменяет недопустимые ``\\n``/``\\r``/``\\t`` внутри строки на
    их экранированные формы — после этого ``json.loads`` принимает фрагмент.
    Уже экранированные последовательности (``\\n`` двумя символами) не трогаются.
    """
    out: list[str] = []
    in_str = False
    escaped = False
    for ch in text:
        if in_str:
            if escaped:
                out.append(ch)
                escaped = False
            elif ch == "\\":
                out.append(ch)
                escaped = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            elif ch in _CONTROL_ESCAPES:
                out.append(_CONTROL_ESCAPES[ch])
            else:
                out.append(ch)
        else:
            out.append(ch)
            if ch == '"':
                in_str = True
    return "".join(out)


def _read_json_string_value(raw: str, quote_index: int) -> tuple[str, int]:
    """Значение строки JSON от открывающей кавычки (quote_index).

    Читает с учётом экранирования ``\\`` до незакрывающей ``"``. Если строка
    оборвана (модель упёрлась в max_tokens), возвращает всё до конца входа —
    значение ``unterminated=True`` сигнализируется end == len(raw).
    """
    out: list[str] = []
    i = quote_index + 1
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\":
            out.append(ch)
            if i + 1 < n:
                out.append(raw[i + 1])
                i += 2
                continue
            i += 1
            continue
        if ch == '"':
            return "".join(out), i
        out.append(ch)
        i += 1
    return "".join(out), n


def _trim_to_balanced_latex(text: str) -> str:
    """Обрезает текст так, чтобы не оставалось незакрытых ``$``/``$$``.

    Если обрыв пришёлся на середину формулы, хвост без закрывающего делимитера
    удаляется — иначе валидатор/рендер увидели бы несбалансированный LaTeX.
    """
    i = 0
    n = len(text)
    in_display = False
    in_inline = False
    last_good = 0
    while i < n:
        if text.startswith("$$", i):
            in_display = not in_display
            i += 2
        elif text[i] == "$":
            in_inline = not in_inline
            i += 1
        else:
            i += 1
        if not in_display and not in_inline:
            last_good = i
    if in_display or in_inline:
        return text[:last_good].rstrip()
    return text


def salvage_truncated_envelope(raw: str | None) -> ContentEnvelope | None:
    """Спасает theory-конверт из оборванного JSON-ответа модели.

    Когда модель упёрлась в max_tokens посреди JSON (finish_reason == "length"),
    ``parse_content_envelopes`` не находит ни одного целого объекта, и раньше
    ученику уходил сырой «словарь» с метаданными. Эта функция вынимает ``type``/
    ``text`` из недописанного конверта и возвращает theory-сообщение с тем, что
    модель успела сгенерировать (без хвоста незакрытой формулы).
    """
    raw = (raw or "").strip()
    if not raw.startswith("{"):
        return None
    type_m = _TYPE_RE.search(raw)
    text_m = _TEXT_RE.search(raw)
    if type_m is None or text_m is None:
        return None
    if type_m.group("type") != ContentType.THEORY.value:
        # Незакрытый quiz/practice показывать нельзя — вернём None, и вызывающий
        # код ответит вежливым отказом вместо сырого текста.
        return None
    text, _end = _read_json_string_value(raw, text_m.end() - 1)
    text = _trim_to_balanced_latex(text).strip()
    if not text:
        return None
    topic = _TOPIC_RE.search(raw)
    payload = {"topic": topic.group(1)} if topic is not None else {}
    diff_m = _DIFFICULTY_RE.search(raw)
    return ContentEnvelope(
        type=ContentType.THEORY,
        text=text,
        payload=payload,
        difficulty=diff_m.group(1) if diff_m is not None else "medium",
    )


def looks_like_truncated_envelope(raw: str | None) -> bool:
    """True, если ответ — недописанный JSON-конверт (а не обычная речь)."""
    raw = (raw or "").strip()
    return raw.startswith("{") and _TYPE_RE.search(raw) is not None


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
        data = _loads_lenient(fragment)
        if not isinstance(data, dict):
            continue
        try:
            envelopes.append(ContentEnvelope.model_validate(data))
        except Exception:  # noqa: BLE001 — невалидная схема -> пропускаем
            continue
    return envelopes


def _loads_lenient(fragment: str) -> dict | None:
    """``json.loads`` фрагмента с починкой сырых переводов строк внутри строк.

    Прямой парс (быстрый путь) почти всегда успешен; повтор с экранированием
    сырых управляющих символов спасает конверты, где модель вставила реальные
    ``\\n`` в ``"text"`` (невалидный JSON по спецификации).
    """
    try:
        data = json.loads(fragment)
    except (json.JSONDecodeError, TypeError):
        try:
            data = json.loads(_escape_control_in_strings(fragment))
        except (json.JSONDecodeError, TypeError):
            return None
    return data if isinstance(data, dict) else None


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
