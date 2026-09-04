"""Тесты обёртки графа знаний на networkx (E2 Task 4)."""

from src.kg.graph import PART_OF, PREREQUISITE, RELATED, KnowledgeGraph


def test_add_root_and_topic():
    kg = KnowledgeGraph()
    kg.add_topic("book:физ7", "Учебник «физ7»", node_type="book")
    kg.add_topic("topic:интегралы", "интегралы", node_type="topic", parent_id="book:физ7")
    kg.add_edge("book:физ7", "topic:интегралы", PART_OF)
    d = kg.to_dict()
    assert d["nodes"][0]["type"] == "book"
    assert d["nodes"][1]["parent_id"] == "book:физ7"
    assert d["edges"][0]["relation"] == "part_of"


def test_add_topic_filters_junk_and_url():
    kg = KnowledgeGraph()
    kg.add_topic("book:x", "Учебник «x»", node_type="book")
    kg.add_topic("t1", "Улучшить свой запрос", node_type="topic")   # junk
    kg.add_topic("t2", "https://example.com/foo", node_type="topic")  # url
    kg.add_topic("t3", "слабые темы", node_type="topic")
    assert set(n["id"] for n in kg.to_dict()["nodes"]) == {"book:x", "t3"}
    assert kg.add_edge("нет", "book:x", PART_OF) is None or "нет" not in kg.graph


def test_from_dict_roundtrip():
    kg = KnowledgeGraph()
    kg.add_topic("n1", "A", "topic")
    kg.add_topic("n2", "B", "concept")
    kg.add_edge("n1", "n2", PREREQUISITE)
    kg2 = KnowledgeGraph.from_dict(kg.to_dict())
    assert kg2.to_dict() == kg.to_dict()
    assert kg2.stats() == {"nodes": 2, "edges": 1}


def test_neighbors_depth_2():
    kg = KnowledgeGraph()
    for i, t in enumerate(["R", "A", "B", "C"]):
        kg.add_topic(f"n{i}", t, "topic")
    kg.add_edge("n0", "n1", RELATED)
    kg.add_edge("n1", "n2", PART_OF)
    kg.add_edge("n2", "n3", RELATED)
    rel = kg.neighbors("n0", max_depth=2)
    assert {r["target"] for r in rel} == {"n1", "n2"}
    assert all("target_title" in r and "relation" in r for r in rel)
