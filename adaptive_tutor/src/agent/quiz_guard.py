"""Серверный контроль качества quiz и recovery-политика после отклонения.

Модель генерирует квиз свободным текстом, поэтому возможны вопросы-утверждения,
в которых уже назван правильный ответ («Мерой инертности тела является его
масса.» + вариант «Масса»), дубли вариантов, эталон не из options и т.п.

Проверка — чистая функция ``quiz_problems``: возвращает список причин отклонения;
пустой список означает, что квиз структурно корректен и не раскрывает ответ.
Используется в HTTP-слое (``src/api/server.py``) перед выдачей квиза ученику.

Recovery-политика (тоже чистые хелперы): guard возвращает причины, HTTP-слой
делает ОДНУ тихую регенерацию; при неудаче счётчик ``quiz_reject_streak`` растёт,
а после ``QUIZ_REJECT_CAP`` неудач подряд quiz-режим блокируется до первого
корректного хода. Переход счётчика — ``next_reject_state``; тексты замены —
``quiz_reject_text`` / ``build_regen_instruction``.
"""

from __future__ import annotations

import re
from typing import Any

_WS = re.compile(r"\s+")
_DOLLAR = re.compile(r"\$+")
_WORD = re.compile(r"[^\W_]+")

_MIN_OPTIONS = 2
_MIN_PHRASE = 3

# Начала, типичные для вопросительной формулировки (в т.ч. повелительные «укажи…»).
_QUESTION_STARTS = (
    "как", "что", "чем", "чему", "почему", "зачем", "кто", "какой", "какая",
    "какие", "какое", "каких", "каком", "каким", "который", "которая", "где",
    "когда", "куда", "откуда", "сколько", "верно", "укажи", "укажите", "выбери",
    "выберите", "назови", "назовите", "перечисли", "определи", "сопоставь",
    "соотнеси", "найди", "заполни", "дополни", "объясни", "дай", "дайте",
    "реши", "решите", "какова", "каков",
)


def _norm(text: Any) -> str:
    """Нормализация: без $, нижний регистр, схлопнутые пробелы."""
    s = _DOLLAR.sub("", str(text or ""))
    s = s.casefold()
    return _WS.sub(" ", s).strip()


def _no_spaces(text: str) -> str:
    return _WS.sub("", text)


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text)


def _looks_like_question(text: str) -> bool:
    """Стем похож на вопрос: заканчивается «?» или начинается с вопросительного слова."""
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith("?"):
        return True
    head = _tokens(_norm(t))
    return bool(head and head[0] in _QUESTION_STARTS)


def structural_issues(text: str, payload: dict[str, Any]) -> list[str]:
    """Структурные проблемы (грейдинг/повторение были бы невозможны или сломаны)."""
    issues: list[str] = []
    if not (text or "").strip():
        issues.append("пустой текст вопроса")
    # Текст вопроса обязан быть вопросом: «?» или вопросительное слово/обращение.
    if (text or "").strip() and not _looks_like_question(text):
        issues.append(
            "текст не является вопросом (нет «?» и нет вопросительного слова)"
        )
    answer_type = payload.get("answer_type")
    if answer_type not in ("single", "open"):
        issues.append(f"неизвестный answer_type: {answer_type!r}")
        return issues
    options = payload.get("options")
    if answer_type == "single":
        if not isinstance(options, list) or len(options) < _MIN_OPTIONS:
            issues.append(f"мало вариантов ответа (нужно не меньше {_MIN_OPTIONS})")
            return issues
        norm_opts = [_norm(o) for o in options]
        if len(norm_opts) != len(set(norm_opts)):
            issues.append("варианты ответа дублируются")
        correct = payload.get("_correct_answer")
        if not correct or not str(correct).strip():
            issues.append("нет эталонного ответа (_correct_answer)")
        else:
            norm_correct = _norm(str(correct))
            if sum(1 for o in norm_opts if o == norm_correct) != 1:
                issues.append("эталонный ответ не совпадает ровно с одним вариантом")
    return issues


def leak_reasons(text: str, options: list[Any]) -> list[str]:
    """«Утечка» ответа: вариант дословно присутствует в тексте вопроса.

    Срабатываем только на стемы-утверждения (без «?» и вопросительного слова) —
    именно так модель выдают ответ в вопросе («Мерой инертности тела является его
    масса.» + вариант «Масса»). Вопросительные стемы не трогаем: в них дословное
    упоминание варианта обычно легитимно («Что из этого верно: A или B?») и
    неоднозначно для автоматической проверки.
    """
    reasons: list[str] = []
    text = str(text or "").strip()
    if not text:
        return reasons
    if _looks_like_question(text):
        return reasons
    norm_t = _norm(text)
    nospace_t = _no_spaces(norm_t)
    for opt in options or []:
        phrase = _norm(opt)
        if len(phrase) < _MIN_PHRASE:
            continue
        if phrase in norm_t or _no_spaces(phrase) in nospace_t:
            reasons.append("правильный ответ раскрыт в тексте вопроса")
            break
    return reasons


def quiz_problems(envelope: Any) -> list[str]:
    """Причины отклонения quiz-конверта; [] — квиз можно отдавать ученику.

    Принимает ``ContentEnvelope`` либо dict-конверт (для удобства тестов).
    """
    if hasattr(envelope, "payload"):
        payload = envelope.payload or {}
        text = envelope.text or ""
    else:
        payload = (envelope or {}).get("payload") or {}
        text = (envelope or {}).get("text") or ""
    if not isinstance(payload, dict):
        payload = {}
    problems = structural_issues(str(text), payload)
    if payload.get("answer_type") == "single":
        options = payload.get("options")
        problems += leak_reasons(str(text), options if isinstance(options, list) else [])
    return problems


# --- Recovery после отклонения quiz (чистая политика для HTTP-слоя) ---------

QUIZ_REJECT_CAP = 2

QUIZ_REJECT_RETRY_TEXT = (
    "Проверочный вопрос не прошёл контроль качества и отменён. "
    "Попробуйте ещё раз или попросите задание."
)

QUIZ_BLOCKED_TEXT = (
    "Проверочные вопросы в этой сессии временно отключены — несколько попыток "
    "не прошли контроль качества. Перейдём к заданиям: напишите «дай задание», "
    "и я предложу упражнение."
)

QUIZ_BLOCKED_NOTE = (
    "Проверочные вопросы (quiz) в этой сессии отклоняются контролем качества. "
    "В ЭТОМ ходе НЕ выдавай quiz — дай practice-задание или короткое "
    "theory-объяснение."
)

_QUIZ_REGEN_HEAD = (
    "Твой предыдущий quiz-конверт отклонён серверным контролем качества "
    "по причинам: {reasons}. Сформулируй ЗАНОВО ОДИН корректный quiz-конверт "
    "строго по схеме: text — это вопрос с «?» либо предложение с вопросительного "
    "слова; правильный ответ НЕ раскрывать ни в text, ни в options; "
    'answer_type "single" — ровно 4 варианта, _correct_answer — ровно один из '
    'options; answer_type "open" — без вариантов. Если не уверен в корректном '
    "quiz — верни practice-задание."
)


def quiz_reject_text(blocked: bool) -> str:
    """Текст theory-замены отклонённого quiz в зависимости от режима сессии.

    blocked=False (streak < CAP): вежливая нейтральная просьба повторить или
    попросить задание. blocked=True: короткая директива сменить формат на
    задания — без бесконечных приглашений «другой вопрос».
    """
    return QUIZ_BLOCKED_TEXT if blocked else QUIZ_REJECT_RETRY_TEXT


def build_regen_instruction(rejected: list[dict]) -> str:
    """Корректирующая инструкция для одной регенерации quiz.

    Принимает список отклонений вида {"reasons": [...], "text": ...,
    "answer_type": ...}. Использует ТОЛЬКО строки reasons: текст и ответ
    отклонённого quiz в промпт не попадают (иначе ученику раскрылся бы
    _correct_answer).
    """
    seen: list[str] = []
    for record in rejected:
        for reason in record.get("reasons") or []:
            reason = str(reason).strip()
            if reason and reason not in seen:
                seen.append(reason)
    reasons = "; ".join(seen) if seen else "структурная невалидность"
    return _QUIZ_REGEN_HEAD.format(reasons=reasons)


def next_reject_state(
    streak: int, blocked: bool, *, recovered: bool
) -> tuple[int, bool]:
    """Переход счётчика отклонений quiz по итогам хода (чистая функция).

    recovered=True — ход закончился контентом без невосстановленного отклонения
    (guard пропустил quiz, либо выдан practice/theory/evaluation): сброс в
    (0, False).
    recovered=False — ход закончился отклонённым quiz без восстановления:
    streak+1; quiz_blocked=True при streak >= QUIZ_REJECT_CAP — со следующего
    хода регенерация выключена и модели уходит QUIZ_BLOCKED_NOTE.
    """
    if recovered:
        return 0, False
    next_streak = streak + 1
    return next_streak, next_streak >= QUIZ_REJECT_CAP
