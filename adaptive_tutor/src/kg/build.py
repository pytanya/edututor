"""Сборка графа источника по RAG-сниппетам (E2, Слой 1).

В edututor нет загрузки учебников: граф строится по ключу ``subject|grade`` из
сниппетов, которые провижининг накопил по темам предмета/класса (§4.2):

- ``collect_snippets`` — группирует чанки по темам и собирает «хвосты» текстов
  для LLM-онтолога;
- ``build_topic_scaffold`` — мгновенный каркас из известных тем (для UI, пока
  полный граф не собран);
- ``build_or_load_graph`` — полный конвейер: кэш → LLM-онтология →
  эвристический фолбэк → сохранение (вызывается лениво по запросу графа);
- ``sync_relations_from_graph`` — переносит рёбра графа в отношения тем ученика
  по нормализованному совпадению title (§3.4).
"""

import re
from typing import Any

from ..config import settings
from ..rag import DocChunk
from .cache import graph_cache_key, load_cached_graph, save_graph
from .graph import NODE_COLORS, PART_OF, PREREQUISITE, RELATED
from .ontology import (
    build_heuristic_graph,
    build_ontology_graph,
    chat_ontology,
    ontology_prompt,
    parse_ontology,
)

# Префикс «Урок/Параграф N …» в заголовках узлов: срезается при матчинге (§3.4).
_PREFIX_RE = re.compile(
    r"^(?:урок|параграф|lesson|section|module|unit|тема|раздел)"
    r"\s*\d+[.:\s—–-]*\s*",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")

# Лимит текста сниппетов, отдаваемого LLM-онтологу (§4.2).
_MAX_TEXT = 12000


def _norm_topic(title: str) -> str:
    """Нормализованный ключ темы для матчинга title↔topic (§3.4).

    Срезает префикс-номер («Параграф 12: …» → «…»), приводит к нижнему
    регистру и сжимает пробелы.
    """
    return _WS_RE.sub(" ", _PREFIX_RE.sub("", title or "")).strip().lower()


def _root_key(subject: str, grade: str) -> str:
    """Компактный id корня: непустые части ``subject|grade`` (пробелы → _).

    Пустые предмет и класс дают фолбэк ``subject`` — единый id-сегмент.
    """
    parts = []
    for part in (subject, grade):
        clean = str(part or "").strip().replace(" ", "_")
        if clean:
            parts.append(clean)
    return "|".join(parts) or "subject"


def collect_snippets(
    chunks: list[DocChunk], per_topic: int = 8
) -> tuple[list[str], str, int, int]:
    """Группирует чанки по темам и собирает тексты сниппетов для LLM (§4.2).

    Темы — уникальные ``metadata["topic"]`` в порядке первого появления; по теме
    берутся последние ≤ ``per_topic`` текстов. Возвращает ``(topics, merged,
    count, size)``: ``count = len(topics)``, ``size`` — суммарная длина текстов
    ДО обрезки (саму обрезку до 12000 применяет ``build_or_load_graph``).
    Чанки без topic пропускаются.
    """
    by_topic: dict[str, list[str]] = {}
    order: list[str] = []
    for chunk in chunks:
        meta = chunk.metadata
        topic = meta.get("topic") if isinstance(meta, dict) else None
        if not topic:
            continue
        if topic not in by_topic:
            by_topic[topic] = []
            order.append(topic)
        by_topic[topic].append(chunk.text)
    topics = [topic for topic in order if by_topic[topic]]
    texts = [text for topic in topics for text in by_topic[topic][-per_topic:]]
    count = len(topics)
    size = sum(len(text) for text in texts)
    merged = "\n\n".join(texts)
    return topics, merged, count, size


def build_topic_scaffold(root: str, topics: list[str]) -> dict:
    """Быстрый каркас графа из известных тем (>=1 узел, без LLM).

    Словарь формата ``KnowledgeGraph.to_dict()``: корень ``book:{root}`` +
    узлы ``topic:{t}`` с ребром ``part_of`` к корню. Пустой список тем →
    один узел ``topic:{root}`` «Тема «{root}»».
    """
    book_id = f"book:{root}"
    nodes: list[dict[str, Any]] = [
        {
            "id": book_id,
            "title": f"Учебник «{root}»",
            "type": "book",
            "color": NODE_COLORS["book"],
        }
    ]
    edges: list[dict[str, str]] = []
    clean_topics = [str(topic).strip() for topic in topics if str(topic).strip()]
    if not clean_topics:
        topic_id = f"topic:{root}"
        nodes.append(
            {
                "id": topic_id,
                "title": f"Тема «{root}»",
                "type": "topic",
                "color": NODE_COLORS["topic"],
                "parent_id": book_id,
            }
        )
        edges.append({"source": book_id, "target": topic_id, "relation": PART_OF})
    for topic in clean_topics:
        topic_id = f"topic:{topic}"
        nodes.append(
            {
                "id": topic_id,
                "title": topic,
                "type": "topic",
                "color": NODE_COLORS["topic"],
                "parent_id": book_id,
            }
        )
        edges.append({"source": book_id, "target": topic_id, "relation": PART_OF})
    return {"nodes": nodes, "edges": edges}


async def build_or_load_graph(
    *,
    subject: str,
    grade: str,
    chunks: list[DocChunk],
    graph_dir: str,
    llm: Any,
    model: str,
) -> dict:
    """Полный конвейер сборки графа источника: кэш → LLM → эвристика (§4.2-4.4).

    Сниппеты собираются из чанков; ключ кэша считает схему/предмет/класс/объём
    (рост базы пересобирает граф). При попадании возвращается кэш; иначе —
    LLM-онтология по ``merged[:12000]``, при ``None`` или любом исключении —
    эвристический каркас ``build_heuristic_graph`` (гарантированно ≥1 узла).
    Вызов никогда не роняет: возвращается словарь графа.
    """
    topics, merged, count, size = collect_snippets(chunks)
    root = _root_key(subject, grade)
    key = graph_cache_key(subject, grade, count, size)
    try:
        cached = load_cached_graph(key, graph_dir)
        if cached is not None:
            return cached
        text = merged[:_MAX_TEXT]
        messages = ontology_prompt(text, root, settings.ontology_max_vertices)
        raw = await chat_ontology(llm, model, messages)
        kg = build_ontology_graph(text, root, parse_ontology(raw))
        if kg is None:
            kg = build_heuristic_graph(root, topics, merged)
        graph_dict = kg.to_dict()
        save_graph(key, graph_dict, graph_dir)
        return graph_dict
    except Exception:
        kg = build_heuristic_graph(root, topics, merged)
        return kg.to_dict()


def sync_relations_from_graph(
    rows: list[dict],
    nodes: list[dict],
    edges: list[dict],
) -> dict[str, dict[str, list[str]]]:
    """Переносит рёбра графа источника в отношения тем ученика (§3.4).

    Матчинг по нормализованному title (``_norm_topic``): узел графа отвечает
    теме строки, если их нормализованные заголовки совпали. Для рёбер
    ``prerequisite``/``related``, исходящих из совпавшего узла, тема-источник
    получает отношение к теме-цели. Возвращаются только темы хотя бы с одним
    отношением: ``{topic: {"prerequisite": [...], "related": [...]}}``.
    """
    node_id_by_title: dict[str, str] = {}
    for node in nodes:
        title = str(node.get("title") or "").strip()
        if title:
            node_id_by_title.setdefault(_norm_topic(title), str(node.get("id") or ""))
    topic_by_title: dict[str, str] = {}
    for row in rows:
        topic = str(row.get("topic") or "").strip()
        if topic:
            topic_by_title.setdefault(_norm_topic(topic), topic)
    node_to_topic: dict[str, str] = {}
    for norm_title, node_id in node_id_by_title.items():
        if norm_title in topic_by_title and node_id:
            node_to_topic[node_id] = topic_by_title[norm_title]

    out: dict[str, dict[str, list[str]]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        relation = str(edge.get("relation") or "")
        if relation not in (PREREQUISITE, RELATED):
            continue
        src_topic = node_to_topic.get(str(edge.get("source") or ""))
        tgt_topic = node_to_topic.get(str(edge.get("target") or ""))
        if not src_topic or not tgt_topic or src_topic == tgt_topic:
            continue
        record = out.setdefault(src_topic, {"prerequisite": [], "related": []})
        bucket = record[relation]
        if tgt_topic not in bucket:
            bucket.append(tgt_topic)
    return out
