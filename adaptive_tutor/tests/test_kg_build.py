"""Тесты сборки графа источника: сниппеты, каркас тем, sync relations (E2 Task 7)."""

import asyncio

from src.kg.build import (
    build_or_load_graph,
    build_topic_scaffold,
    collect_snippets,
    sync_relations_from_graph,
)
from src.rag import DocChunk


def _chunk(topic, text):
    return DocChunk(
        id=f"c{hash(text)}",
        text=text,
        metadata={"topic": topic, "subject": "физика", "grade": "7"},
    )


class StubLLM:
    """Инъецируемый async-клиент: возвращает фиксированный ответ или бросает."""

    def __init__(self, reply=""):
        self._reply = reply
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def test_collect_snippets_groups_and_counts():
    chunks = [
        _chunk("сила", "текст А1"),
        _chunk("скорость", "текст Б1"),
        _chunk("сила", "текст А2"),
    ]
    topics, merged, count, size = collect_snippets(chunks)
    assert topics == ["сила", "скорость"] and count == 2
    assert size == len("текст А1") + len("текст Б1") + len("текст А2")
    assert "текст А2" in merged


def test_collect_snippets_per_topic_cap():
    chunks = [_chunk("т", f"текст {i}") for i in range(12)]
    _, merged, count, _ = collect_snippets(chunks, per_topic=8)
    assert count == 1 and merged.count("текст") == 8


def test_build_topic_scaffold():
    d = build_topic_scaffold("физика|7", ["Сила", "Скорость"])
    ids = {n["id"] for n in d["nodes"]}
    assert "book:физика|7" in ids and {"topic:Сила", "topic:Скорость"} <= ids
    assert len(d["edges"]) == 2
    single = build_topic_scaffold("x", [])
    assert len(single["nodes"]) >= 1 and single["nodes"][-1]["type"] == "topic"


def test_sync_relations_normalized_title_match():
    rows = [{"topic": "Атмосфера"}, {"topic": "Погода"}]
    nodes = [
        {"id": "sec:x:12", "title": "Параграф 12: Атмосфера"},
        {"id": "sec:x:13", "title": "Параграф 13: Погода"},
    ]
    edges = [{"source": "sec:x:12", "target": "sec:x:13", "relation": "prerequisite"}]
    out = sync_relations_from_graph(rows, nodes, edges)
    assert out["Атмосфера"]["prerequisite"] == ["Погода"]


def test_build_or_load_graph_llm_error_falls_back(tmp_path):
    llm = StubLLM(RuntimeError("нет сети"))
    d = asyncio.run(
        build_or_load_graph(
            subject="физика",
            grade="7",
            chunks=[_chunk("сила", "Сила — векторная величина")],
            graph_dir=str(tmp_path),
            llm=llm,
            model="fast",
        )
    )
    assert d["nodes"][0]["id"] == "book:физика|7"
    assert len(d["nodes"]) >= 2


def test_build_or_load_graph_cached_not_rebuilt(tmp_path):
    payload = (
        '{"nodes":[{"id":"c1","title":"Сила тяжести","type":"topic","section":""}],'
        '"edges":[]}'
    )
    llm = StubLLM(payload)
    chunks = [_chunk("сила", "Сила тяжести притягивает тела к Земле.")]
    kwargs = {
        "subject": "Физика",
        "grade": "7",
        "graph_dir": str(tmp_path),
        "llm": llm,
        "model": "fast",
    }
    first = asyncio.run(build_or_load_graph(chunks=chunks, **kwargs))
    assert llm.calls == 1
    assert any(n["title"] == "Сила тяжести" for n in first["nodes"])
    second = asyncio.run(build_or_load_graph(chunks=chunks, **kwargs))
    assert second == first and llm.calls == 1
