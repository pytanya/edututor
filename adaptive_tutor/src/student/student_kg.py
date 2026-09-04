"""Чистые payload-хелперы Слоя 2 (без sqlite): статистика и ответы API.

Модуль переиспользуется эндпоинтами ``/student/{id}/knowledge-graph``,
``/recommendations`` и ``/graph``: не знает про SQLite/LLM (спека E2 §5).
Строки тем нормализуются в фиксированный словарь ``row_payload``,
``knowledge_graph_payload`` собирает точную форму ответа, ``stats_of`` —
сводные счётчики статусов.
"""

from __future__ import annotations

from typing import Any

from .mastery import derive_status, is_mastered


def accuracy(attempts: int, correct: int) -> float:
    """Доля верных ответов ``correct/attempts`` (0.0 при отсутствии попыток)."""
    if not attempts:
        return 0.0
    return round(correct / attempts, 4)


def row_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Нормализует строку темы в payload Слоя 2 (поля фиксированы, §5).

    Устойчив к отсутствующим полям (например, синтезированные строки
    рекомендаций без weak_areas/relations/last_seen).
    """
    attempts = int(row.get("attempts") or 0)
    correct = int(row.get("correct") or 0)
    mastery = float(row.get("mastery") or 0.0)
    status = row.get("status") or derive_status(attempts, mastery)
    raw_accuracy = row.get("accuracy")
    if isinstance(raw_accuracy, (int, float)):
        acc = float(raw_accuracy)
    else:
        acc = accuracy(attempts, correct)
    weak_areas = row.get("weak_areas")
    if not isinstance(weak_areas, list):
        weak_areas = []
    relations = row.get("relations")
    if not isinstance(relations, dict):
        relations = {}
    relations = {
        "prerequisite": list(relations.get("prerequisite") or []),
        "related": list(relations.get("related") or []),
    }
    return {
        "topic": str(row.get("topic") or ""),
        "subject": str(row.get("subject") or ""),
        "status": status,
        "mastery": mastery,
        "attempts": attempts,
        "correct": correct,
        "accuracy": acc,
        "weak_areas": weak_areas,
        "last_seen": row.get("last_seen"),
        "relations": relations,
    }


def stats_of(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Сводные счётчики: mastered по ``is_mastered``, остальные по ``status``."""
    mastered = sum(
        1
        for r in rows
        if is_mastered(
            int(r.get("attempts") or 0),
            float(r.get("mastery") or 0.0),
            str(r.get("status") or ""),
        )
    )
    in_progress = sum(1 for r in rows if r.get("status") == "in_progress")
    not_studied = sum(1 for r in rows if r.get("status") == "not_studied")
    return {
        "mastered": mastered,
        "in_progress": in_progress,
        "not_studied": not_studied,
        "total": len(rows),
    }


def knowledge_graph_payload(
    student_id: str, subject: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Точная форма ``GET /knowledge-graph`` (спека §5).

    ``topics`` — словарь ``{topic: row_payload}``; у пустого списка строк всё
    равно отдаются ``stats`` из нулей.
    """
    payloads = [row_payload(r) for r in rows]
    return {
        "student_id": student_id,
        "subject": subject,
        "topics": {p["topic"]: p for p in payloads},
        "stats": stats_of(payloads),
    }
