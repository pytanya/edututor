"""JSON-кэш графов источника (E2, Слой 1).

Кэш оперирует словарём формата ``KnowledgeGraph.to_dict()``, а не объектом:
на диске лежит обезличенный контентный граф (спека §4.4). Файл ``<key>.json``,
где ключ — сокращённый SHA-1 от схемы/предмета/класса/объёма сниппетов.
"""

import hashlib
import json
import os
from pathlib import Path

from ..config import settings


def graph_cache_key(subject: str, grade: str, count: int, size: int) -> str:
    """Ключ кэша: схема + предмет/класс + кол-во тем + суммарный размер сниппетов.

    Схема и размер входят в ключ, чтобы граф пересобирался при росте базы и при
    изменении структуры (спека §4.4).
    """
    fingerprint = (
        f"v{settings.graph_schema_version}:{(subject or '').lower()}:"
        f"{grade or ''}:{count}:{size}"
    )
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]


def _graph_directory(graph_dir: str | None) -> Path:
    """Каталог кэша: явный аргумент или резолв настроек (resolved_knowledge_graph_dir)."""
    return Path(graph_dir) if graph_dir else Path(settings.resolved_knowledge_graph_dir)


def load_cached_graph(key: str, graph_dir: str | None = None) -> dict | None:
    """Читает граф из ``{graph_dir}/{key}.json``.

    Fail-soft: отсутствие файла, битый JSON или неверная структура (не dict) →
    ``None``; граф в этом случае пересобирается заново.
    """
    directory = _graph_directory(graph_dir)
    try:
        data = json.loads((directory / f"{key}.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def save_graph(key: str, graph_dict: dict, graph_dir: str | None = None) -> str:
    """Сохраняет словарь графа в ``{graph_dir}/{key}.json``.

    Создаёт каталог при необходимости; запись через временный файл + ``os.replace``
    (атомарная, чтобы частичный файл не прочитался как кэш). Возвращает путь.
    """
    directory = _graph_directory(graph_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{key}.json"
    tmp = directory / f".{key}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(graph_dict, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return str(path)
