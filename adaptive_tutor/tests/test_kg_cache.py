"""Тесты JSON-кэша графа источника (E2, Слой 1)."""

from pathlib import Path

from src.kg.cache import graph_cache_key, load_cached_graph, save_graph


def test_key_stable_and_sensitive_to_inputs(tmp_path):
    a = graph_cache_key("Физика", "7", 3, 1000)
    b = graph_cache_key("физика", "7", 3, 1000)     # lower-case одинаков
    assert a == b and len(a) == 16
    assert graph_cache_key("Физика", "7", 4, 1000) != a   # рост базы -> другой ключ
    assert graph_cache_key("Физика", "7", 3, 2000) != a   # рост размера -> другой ключ
    assert graph_cache_key("Физика", "8", 3, 1000) != a


def test_save_and_load_roundtrip(tmp_path):
    key = graph_cache_key("физика", "7", 1, 10)
    d = {"nodes": [{"id": "book:x", "title": "Учебник «x»", "type": "book"}], "edges": []}
    path = save_graph(key, d, graph_dir=str(tmp_path))
    assert Path(path).exists()
    assert load_cached_graph(key, str(tmp_path)) == d


def test_load_missing_and_corrupt_returns_none(tmp_path):
    assert load_cached_graph("deadbeef", str(tmp_path)) is None
    (tmp_path / "bad.json").write_text("{не json", encoding="utf-8")
    assert load_cached_graph("bad", str(tmp_path)) is None
