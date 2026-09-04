"""Тесты junk-фильтров и LLM/эвристической онтологии графа (E2 Task 5)."""

from src.kg.ontology import (
    build_heuristic_graph,
    build_ontology_graph,
    extract_section_number,
    ontology_prompt,
    parse_ontology,
)

VALID = '{"nodes":[{"id":"c1","title":"Сила тяжести","type":"concept","section":"§12"},' \
        '{"id":"c2","title":"G=mg","type":"concept","section":"§12"}],' \
        '"edges":[{"source":"c1","target":"c2","relation":"part_of"}]}'


def test_prompt_has_rules_and_limit():
    msgs = ontology_prompt("текст", "физ7", 14)
    assert msgs[0]["role"] == "system" and "Не больше 14 вершин" in msgs[0]["content"]
    assert "12000" not in msgs[0]["content"] and "Текст материала:" in msgs[1]["content"]


def test_build_ontology_valid():
    kg = build_ontology_graph("текст", "физ7", parse_ontology(VALID))
    d = kg.to_dict()
    assert d["nodes"][0]["id"] == "book:физ7"
    assert any(n["title"] == "Сила тяжести" for n in d["nodes"])
    assert d["nodes"][1]["section_number"] == "12"


def test_build_ontology_invalid_types_and_dup_dropped():
    raw = parse_ontology('{"nodes":[{"id":"a","title":"Сила","type":"бред","section":""},'
                         '{"id":"b","title":"Сила","type":"topic","section":""},'
                         '{"id":"c","title":"Улучшить свой запрос","type":"topic","section":""},'
                         '{"id":"d","title":"x","type":"topic","section":""}],"edges":[]}')
    kg = build_ontology_graph("текст", "s", raw)
    titles = [n["title"] for n in kg.to_dict()["nodes"]]
    assert titles == ["Учебник «s»", "Сила"]            # дедуп по lower, junk/короткий выброшен


def test_build_ontology_none_when_no_nodes():
    assert build_ontology_graph("т", "s", {"nodes": [], "edges": []}) is None


def test_build_ontology_edges_unknown_relation_related():
    raw = parse_ontology('{"nodes":[{"id":"a","title":"A B","type":"topic","section":""},'
                         '{"id":"b","title":"C D","type":"topic","section":""}],'
                         '"edges":[{"source":"a","target":"b","relation":"xyz"}]}')
    kg = build_ontology_graph("т", "s", raw)
    assert kg.to_dict()["edges"][0]["relation"] == "related"


def test_junk_filters():
    from src.kg.junk import (
        _is_paragraph_number,
        _is_ui_element,
        _is_url_like,
        clean_title,
        is_junk_topic,
    )
    assert is_junk_topic("Улучшить свой запрос") and is_junk_topic("похожие запросы")
    assert not is_junk_topic("Сила тяжести")
    assert _is_paragraph_number("§ 58") and _is_paragraph_number("12.")
    assert _is_url_like("https://example.com/x") and _is_url_like("www.ya.ru")
    assert _is_ui_element("Скачать:") or _is_ui_element("Скачать")
    assert clean_title("  Сила  — ") == "Сила  — ".strip(" *#-–—")  # без mojibake не трогает


def test_heuristic_fallback_and_degenerate():
    kg = build_heuristic_graph("физ7", ["Сила", "Скорость"], "## Ускорение\nтекст\n## Ускорение")
    d = kg.to_dict()
    assert d["nodes"][0]["id"] == "book:физ7"
    topics = {n["title"] for n in d["nodes"] if n["type"] == "topic"}
    assert {"Сила", "Скорость", "Ускорение"} <= topics
    single = build_heuristic_graph("x", [], "")
    assert single.to_dict()["nodes"][-1]["title"] == "Тема «x»"


def test_extract_section_number():
    assert extract_section_number("§12") == "12"
    assert extract_section_number("Параграф 5") == "5"
    assert extract_section_number("") == ""
