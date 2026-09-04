"""LLM-онтология и эвристический фолбэк графа знаний (E2, Слой 1).

Модуль строит граф источника двумя путями:
- ``build_ontology_graph`` — по JSON LLM (вершины/рёбра решает модель);
  мусорный/пустой ответ → ``None``;
- ``build_heuristic_graph`` — детерминированный каркас по известным темам
  предмета и маркерным заголовкам сниппетов (фолбэк, гарантированно ≥1 узла).
"""

import json
import re
from typing import Any

from ..config import settings
from .graph import PART_OF, PREREQUISITE, RELATED, KnowledgeGraph
from .junk import (
    _WEB_NOISE,
    _is_paragraph_number,
    _is_ui_element,
    _is_url_like,
    clean_title,
    is_junk_topic,
)

# Допустимые типы вершин и рёбра LLM-онтологии (остальное нормализуем/отбрасываем)
_ONTOLOGY_TYPES = {"topic", "concept", "section", "lesson"}
_ONTOLOGY_RELATIONS = {PART_OF, PREREQUISITE, RELATED}

# Маркерные заголовки в сниппетах веб-конспектов (как в референсе _web_headings)
_WEB_H1_RE = re.compile(r"^#{1,3}\s+(.+)$")
_WEB_NUM_RE = re.compile(r"^\s*\d{1,2}[.)]\s+(.{2,90})$")

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def ontology_prompt(text: str, source: str, max_vertices: int) -> list[dict]:
    """Системный+пользовательский промпт для LLM-онтолога (спека §4.3)."""
    system = (
        "Ты — онтолог образовательного агента EduTutor. По фрагменту учебного материала "
        "построй граф знаний: ВЕРШИНЫ — темы и ключевые понятия из текста, РЁБРА — "
        "смысловые связи между ними. "
        "Решения, что станет вершинами и рёбрами, принимаешь ТЫ по содержанию. "
        "Правила: "
        "1) Только понятия, реально присутствующие в тексте; не выдумывай. "
        f"2) Не больше {max_vertices} вершин; название 1-4 слова. "
        "3) ПРЕДПОЧИТАЙ ПОНЯТИЙНЫЕ узлы (термины и концепции). НЕ включай структурные узлы: "
        "номера уроков/классов («7 класс», «Урок 5»), рубрики, оглавление. "
        "4) relation только: part_of (входит в), prerequisite (опирается на), related (связан). "
        "5) Для каждой вершины укажи section — §N/параграф из текста (если есть, иначе пусто). "
        'Верни строго JSON: {"nodes":[{"id":"c1","title":"...","type":"topic|concept",'
        '"section":""}], "edges":[{"source":"c1","target":"c2","relation":"part_of"}]}'
    )
    user = f"Источник: {source}\n\nТекст материала:\n{text[:12000]}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def extract_section_number(section: str) -> str:
    """«§12», «Параграф 5» → «12» (для RAG-фильтра по section_number)."""
    m = re.search(r"(\d{1,3})", section or "")
    return m.group(1) if m else ""


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Пытается получить dict из текста (локальная копия envelope._extract_json).

    Принимает чистый JSON, ```json-обёртку и JSON внутри короткого текста
    (от первого '{' до последнего '}').
    """
    stripped = (text or "").strip()
    candidates: list[str] = []
    if stripped:
        candidates.append(stripped)
    match = _FENCE.search(stripped)
    if match:
        candidates.append(match.group(1).strip())
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


def parse_ontology(raw: str) -> dict[str, Any]:
    """Извлекает JSON-объект онтологии; мусор/не-JSON → {}."""
    data = _extract_json_object(raw)
    return data if data is not None else {}


def build_ontology_graph(text: str, source: str, raw_data: dict[str, Any]) -> KnowledgeGraph | None:
    """Строит граф из LLM-онтологии; None — модель не дала ни одной вершины.

    Валидация по §4.3: типы вне {topic,concept,section,lesson} → concept,
    relation вне {part_of,prerequisite,related} → related; дубли (lower title),
    заголовки < 2 символов и junk отбрасываются. Корень book:{source} добавляется
    всегда, узлы подвешиваются ребром part_of к корню.
    """
    kg = KnowledgeGraph()
    root_id = f"book:{source}"
    kg.add_topic(root_id, f"Учебник «{source}»", node_type="book")
    node_ids = {root_id}
    seen_titles: set[str] = set()
    added_nodes: list[str] = []
    raw_nodes = raw_data.get("nodes") if isinstance(raw_data.get("nodes"), list) else []
    for n in raw_nodes:
        if not isinstance(n, dict):
            continue
        title = clean_title(str(n.get("title") or ""))
        if not title or len(title) < 2:
            continue
        if is_junk_topic(title):
            continue
        key = title.lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)
        nid = str(n.get("id") or f"concept:{source}:{len(added_nodes)}")
        ntype = str(n.get("type") or "concept")
        if ntype not in _ONTOLOGY_TYPES:
            ntype = "concept"
        section = extract_section_number(str(n.get("section") or ""))
        kg.add_topic(nid, title, node_type=ntype, section_number=section or None, parent_id=root_id)
        node_ids.add(nid)
        added_nodes.append(nid)
        if len(added_nodes) >= settings.ontology_max_vertices:
            break
    if not added_nodes:
        return None

    raw_edges = raw_data.get("edges") if isinstance(raw_data.get("edges"), list) else []
    for e in raw_edges:
        if not isinstance(e, dict):
            continue
        src, tgt = str(e.get("source") or ""), str(e.get("target") or "")
        if src not in node_ids or tgt not in node_ids or src == tgt:
            continue
        rel = str(e.get("relation") or RELATED)
        if rel not in _ONTOLOGY_RELATIONS:
            rel = RELATED
        kg.add_edge(src, tgt, rel)

    return kg


def build_heuristic_graph(root: str, topics: list[str], snippet_text: str) -> KnowledgeGraph:
    """Эвристический каркас графа по известным темам и сниппетам (§4.3).

    Иерархия: корень book → известные темы предмета (topic) → маркерные
    markdown/нумерованные заголовки сниппетов (topic) с junk-фильтрами,
    лимит 30 узлов; если узлов нет — деградация до одного узла-темы.
    Гарантированно возвращает граф с ≥1 узлом.
    """
    kg = KnowledgeGraph()
    root_id = f"book:{root}"
    kg.add_topic(root_id, f"Учебник «{root}»", node_type="book")
    total = 0

    for t in topics:
        t = str(t).strip()
        if not t:
            continue
        nid = f"topic:{t}"
        kg.add_topic(nid, t, node_type="topic", parent_id=root_id)
        if nid in kg.graph:
            kg.add_edge(root_id, nid, PART_OF)
            total += 1

    seen: set[str] = set()
    heading_ids: list[str] = []
    for line in snippet_text.splitlines():
        line = line.strip()
        m = _WEB_H1_RE.match(line)
        title: str | None = None
        if m:
            title = m.group(1).strip()
        else:
            mn = _WEB_NUM_RE.match(line)
            if mn:
                title = mn.group(1).strip()
        if not title or len(title) < 3:
            continue
        if _is_url_like(title):
            continue
        title_clean = clean_title(re.sub(r"^[\d.]+\s*", "", title))
        if not title_clean or title_clean.lower() in _WEB_NOISE:
            continue
        if _is_url_like(title_clean):
            continue
        if _is_ui_element(title_clean):
            continue
        if _is_paragraph_number(title_clean):
            continue
        if is_junk_topic(title_clean):
            continue
        key = title_clean.lower()
        if key in seen:
            continue
        seen.add(key)
        nid = f"sec:{root}:web:{len(heading_ids)}"
        kg.add_topic(nid, title_clean, node_type="topic", parent_id=root_id)
        kg.add_edge(root_id, nid, PART_OF)
        heading_ids.append(nid)
        total += 1
        if len(heading_ids) >= 30:  # лимит узлов — не даём «лепнине» завалить граф
            break

    if total == 0:
        nid = f"topic:{root}"
        kg.add_topic(nid, f"Тема «{root}»", node_type="topic", parent_id=root_id)
        kg.add_edge(root_id, nid, PART_OF)
    return kg


async def chat_ontology(llm: Any, model: str, messages: list[dict]) -> str:
    """LLM-вызов онтологии (роль fast): temperature=0.0, max_tokens=900.

    llm — инъецируемый async-клиент с методом ``chat(messages, model=...)``
    (для тестов — заглушка); любой Exception → "" (дальше heuristic-фолбэк).
    """
    try:
        resp = await llm.chat(messages=messages, model=model, temperature=0.0, max_tokens=900)
    except Exception:
        return ""
    if isinstance(resp, str):
        return resp
    content = getattr(resp, "content", None)
    return content or ""
