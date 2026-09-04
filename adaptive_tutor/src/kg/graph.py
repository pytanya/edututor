"""Слой 1: граф знаний источника (обёртка над networkx DiGraph).

Класс `KnowledgeGraph` хранит сеть узлов/рёбер учебного источника
(книга/раздел/урок/тема/понятие) и предоставляет сериализацию в JSON-словари.
"""

import networkx as nx

from .junk import _is_url_like, is_junk_topic

PART_OF = "part_of"
PREREQUISITE = "prerequisite"
RELATED = "related"

NODE_COLORS = {
    "book": "#F4A261",
    "lesson": "#64DFDF",
    "section": "#B388FF",
    "topic": "#69F0AE",
    "default": "#FF8A80",
}

_NODE_FIELDS = ("id", "title", "type", "color")
_OPTIONAL_NODE_FIELDS = ("section_number", "parent_id")


class KnowledgeGraph:
    """Граф знаний учебного источника на базе networkx DiGraph."""

    def __init__(self) -> None:
        self.graph = nx.DiGraph()

    def add_topic(
        self,
        node_id: str,
        title: str,
        node_type: str = "topic",
        section_number: str | None = None,
        parent_id: str | None = None,
        allow_url: bool = False,
        **attrs: object,
    ) -> None:
        """Добавляет узел темы/раздела/урока/книги.

        URL-заголовки и поисковый/UI-мусор в граф не попадают: URL-подобные
        названия отсекаются всегда (кроме `allow_url=True` — намеренно создаваемый
        узел-источник), мусорные темы — для всех типов кроме структурных
        (`book`/`lesson`).
        """
        if not allow_url and _is_url_like(title):
            return
        if node_type not in ("book", "lesson") and is_junk_topic(title):
            return
        data: dict[str, object] = dict(attrs)
        data.update(
            {
                "id": node_id,
                "title": title,
                "type": node_type,
                "color": NODE_COLORS.get(node_type, NODE_COLORS["default"]),
            }
        )
        if section_number is not None:
            data["section_number"] = section_number
        if parent_id is not None:
            data["parent_id"] = parent_id
        self.graph.add_node(node_id, **data)

    def add_edge(self, source: str, target: str, relation: str = RELATED) -> None:
        """Добавляет ребро между существующими различными узлами."""
        if source == target:
            return
        if source not in self.graph or target not in self.graph:
            return
        self.graph.add_edge(source, target, relation=relation)

    def neighbors(self, node_id: str, max_depth: int = 2) -> list[dict[str, str | int]]:
        """DFS по исходящим рёбрам: записи рёбер до глубины max_depth."""
        if node_id not in self.graph:
            return []
        result: list[dict[str, str | int]] = []
        visited = {node_id}
        stack: list[tuple[str, int]] = [(node_id, 0)]
        while stack:
            current, depth = stack.pop()
            if depth >= max_depth:
                continue
            source = self.graph.nodes[current]
            for target in self.graph.successors(current):
                if target in visited:
                    continue
                visited.add(target)
                node = self.graph.nodes[target]
                edge = self.graph.edges[current, target]
                result.append(
                    {
                        "source": current,
                        "source_title": source.get("title", ""),
                        "relation": edge.get("relation", RELATED),
                        "target": target,
                        "target_title": node.get("title", ""),
                        "target_type": node.get("type", ""),
                        "depth": depth + 1,
                    }
                )
                stack.append((target, depth + 1))
        return result

    def to_dict(self) -> dict[str, list[dict[str, object]]]:
        """Сериализация: whitelist-атрибуты узлов и список рёбер."""
        nodes: list[dict[str, object]] = []
        for node_id in self.graph.nodes:
            data = self.graph.nodes[node_id]
            item = {field: data.get(field) for field in _NODE_FIELDS}
            for field in _OPTIONAL_NODE_FIELDS:
                value = data.get(field)
                if value is not None:
                    item[field] = value
            nodes.append(item)
        edges = [
            {
                "source": source,
                "target": target,
                "relation": edge.get("relation", RELATED),
            }
            for source, target, edge in self.graph.edges(data=True)
        ]
        return {"nodes": nodes, "edges": edges}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "KnowledgeGraph":
        """Восстанавливает граф из словаря, созданного `to_dict`."""
        kg = cls()
        for node in data.get("nodes", []):
            if not isinstance(node, dict):
                continue
            kg.add_topic(
                str(node.get("id", "")),
                str(node.get("title", "")),
                node_type=str(node.get("type", "topic")),
                section_number=node.get("section_number"),
                parent_id=node.get("parent_id"),
            )
        for edge in data.get("edges", []):
            if not isinstance(edge, dict):
                continue
            kg.add_edge(
                str(edge.get("source", "")),
                str(edge.get("target", "")),
                str(edge.get("relation", RELATED)),
            )
        return kg

    def stats(self) -> dict[str, int]:
        """Количество узлов и рёбер графа."""
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
        }
