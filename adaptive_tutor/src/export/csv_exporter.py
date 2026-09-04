"""Экспорт для учителя: CSV журнала вопросов и сводки сессий.

UTF-8 с BOM добавляет HTTP-эндпоинт (encode("utf-8-sig")) — Excel открывает
корректно. Сборка — чистые функции; rows — списки dict-строк.
"""

from __future__ import annotations

import csv
import datetime
import io
from collections.abc import Iterable
from typing import Any

QUESTION_COLUMNS = [
    "timestamp", "session_id", "subject", "topic", "question_id", "question",
    "options", "answer_type", "difficulty", "student_answer", "score01",
    "correct", "feedback",
]

SUMMARY_COLUMNS = [
    "session_id", "subject", "topic", "started_at", "ended_at",
    "questions", "correct", "accuracy", "mastered_topics",
]


def iso_ts(ts: Any) -> str:
    """ISO-метка (секунды) из unix-ts; нечисловое -> ''."""
    try:
        return datetime.datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
    except Exception:
        return ""


def _text(value: Any) -> Any:
    return "" if value is None else value


def _row_texts(row: dict[str, Any], columns: list[str]) -> list[Any]:
    out: list[Any] = []
    for col in columns:
        value = row.get(col)
        if col == "timestamp" or col in ("started_at", "ended_at"):
            value = iso_ts(value)
        elif isinstance(value, list):
            value = " | ".join(str(v) for v in value)
        elif isinstance(value, bool):
            value = int(value)
        out.append(_text(value))
    return out


def _csv_text(columns: list[str], rows: Iterable[dict[str, Any]]) -> str:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(_row_texts(row, columns))
    return buf.getvalue()


def questions_csv(rows: Iterable[dict[str, Any]]) -> str:
    """CSV по строкам журнала (порядок колонок — QUESTION_COLUMNS)."""
    return _csv_text(QUESTION_COLUMNS, rows)


def summary_csv(rows: Iterable[dict[str, Any]]) -> str:
    """CSV сводки по сессиям (SUMMARY_COLUMNS)."""
    return _csv_text(SUMMARY_COLUMNS, rows)
