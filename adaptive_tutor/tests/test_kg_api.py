"""API-тесты E2 Task 9: /knowledge-graph, /recommendations, /graph, /related, /wiki."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.agent.critic import Critic
from src.agent.loop import AgentRuntime
from src.agent.tools import ToolContext
from src.api.server import create_app
from src.config import settings
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.rag import InMemoryVectorStore, RAGEngine
from src.student.store import StudentStore
from src.wiki.models import WikiArticle
from src.wiki.store import KnowledgeWiki

# LLM-онтология для фоновой сборки: те же topic-узлы, что даёт каркас.
_ONTOLOGY_REPLY = (
    '{"nodes":[{"id":"topic:Сила","title":"Сила","type":"topic","section":"12"},'
    '{"id":"topic:Скорость","title":"Скорость","type":"topic","section":""}],'
    '"edges":[{"source":"topic:Сила","target":"topic:Скорость",'
    '"relation":"related"}]}'
)
_THEORY_REPLY = '{"type":"theory","text":"Готово."}'


class _FakeEmbedder:
    """Эмбеддер-заглушка: нулевые векторы (graph не требует поиска по смыслу)."""

    def embed(self, texts):
        return [[0.0, 0.0, 0.0] for _ in texts]


class _TC:
    def parse_usage(self, raw):
        return TokenUsage()

    def estimate(self, text):
        return 0


class _SmartLLM(LLMClient):
    """Планировщик/онтолог: на промпт онтолога — JSON графа, иначе теория."""

    def __init__(self):
        super().__init__(token_counter=_TC())

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        system = messages[0].get("content", "") if messages else ""
        content = _ONTOLOGY_REPLY if "онтолог" in system else _THEORY_REPLY
        return LLMResponse(content=content, model=model,
                           usage=TokenUsage(1, 1), finish_reason="stop")

    async def chat_stream(self, *a, **k):
        yield ""


class _Judge(LLMClient):
    def __init__(self):
        super().__init__(token_counter=_TC())

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        return LLMResponse(content='{"passed": true, "issues": []}', model=model,
                           usage=TokenUsage(1, 1), finish_reason="stop")

    async def chat_stream(self, *a, **k):
        yield ""


def _runtime_factory():
    return AgentRuntime(
        llm=_SmartLLM(),
        models={"planner": "p", "fast": "f", "judge": "j"},
        tool_context=ToolContext(region="GLOBAL"),
        critic=Critic(llm=_Judge(), model="j"),
    )


def _ok():
    return SimpleNamespace(correct=True, feedback="")


def _bad():
    return SimpleNamespace(correct=False, feedback="неверно")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient: tmp sqlite + ручной RAGEngine с чанками физика/7."""
    monkeypatch.setattr(settings, "knowledge_graph_dir", str(tmp_path / "graphs"))
    store = StudentStore(str(tmp_path / "students.db"))
    rag = RAGEngine(store=InMemoryVectorStore(), embedder=_FakeEmbedder())
    rag.ingest(
        ["Сила — векторная величина.\n## Равнодействующая",
         "Скорость — мера движения тела.\n## Ускорение"],
        metadatas=[
            {"topic": "Сила", "subject": "физика", "grade": "7"},
            {"topic": "Скорость", "subject": "физика", "grade": "7"},
        ],
    )
    app = create_app(runtime_factory=_runtime_factory, student_store=store, rag_engine=rag)
    with TestClient(app) as c:
        yield c
    store.close()


def test_knowledge_graph_endpoint_empty_and_filled(client):
    empty = client.get("/student/stu_1/knowledge-graph").json()
    assert empty["student_id"] == "stu_1"
    assert empty["topics"] == {}
    assert empty["stats"] == {"mastered": 0, "in_progress": 0, "not_studied": 0, "total": 0}

    store: StudentStore = client.app.state.student_store
    for _ in range(3):
        store.apply_result("stu_1", "тема-трижды", "физика", _ok())

    filled = client.get(
        "/student/stu_1/knowledge-graph", params={"subject": "физика"}
    ).json()
    assert filled["stats"] == {"mastered": 1, "in_progress": 0, "not_studied": 0, "total": 1}
    topic = filled["topics"]["тема-трижды"]
    assert topic["mastery"] == pytest.approx(0.8285)
    assert topic["attempts"] == 3
    assert topic["accuracy"] == pytest.approx(1.0)
    assert topic["relations"] == {"prerequisite": [], "related": []}


def test_recommendations_endpoint(client):
    store: StudentStore = client.app.state.student_store
    store.apply_result("stu_2", "слабая", "физика", _bad())
    store.apply_result("stu_2", "слабая", "физика", _bad())      # acc 0.0/2 -> weak
    store.apply_result("stu_2", "текущая", "физика", _ok())
    store.set_relations("stu_2", "текущая", {"prerequisite": ["предыдущая"]})

    body = client.get(
        "/student/stu_2/recommendations",
        params={"current_topic": "текущая", "subject": "физика", "limit": 10},
    ).json()
    topics = [r["topic"] for r in body["recommendations"]]
    assert topics[0] == "слабая"               # слабые темы идут первыми
    assert "предыдущая" in topics              # gap пререквизита включён
    assert body["prerequisite_gaps"] == ["предыдущая"]
    assert [r["topic"] for r in body["weak_topics"]] == ["слабая"]


def test_graph_scaffold_and_overlay(client):
    first = client.get(
        "/student/stu_g/graph", params={"subject": "физика", "grade": "7"}
    )
    assert first.status_code == 200
    body = first.json()
    assert body["root"] == "book:физика|7"
    ids = {n["id"] for n in body["nodes"]}
    assert {"topic:Сила", "topic:Скорость"} <= ids
    assert body["active_topic"] is None
    assert body["stats"] == {"nodes": len(body["nodes"]), "edges": len(body["edges"])}
    node0 = next(n for n in body["nodes"] if n["id"] == "topic:Сила")
    assert "mastery" not in node0

    store: StudentStore = client.app.state.student_store
    store.apply_result("stu_g", "Сила", "физика", _ok())

    again = client.get(
        "/student/stu_g/graph", params={"subject": "физика", "grade": "7"}
    ).json()
    node = next(n for n in again["nodes"] if n["id"] == "topic:Сила")
    assert {"mastery", "attempts", "correct", "accuracy", "status"} <= set(node)
    assert node["attempts"] == 1
    assert node["status"] == "in_progress"


def test_graph_related_and_wiki(client):
    params = {"subject": "физика", "grade": "7"}
    rel = client.get("/student/stu_r/graph/topic:Сила/related", params=params)
    assert rel.status_code == 200
    body = rel.json()
    assert body["node"]["id"] == "topic:Сила"
    assert isinstance(body["related"], list)

    assert client.get(
        "/student/stu_r/graph/нет_такого/related", params=params
    ).json() == {"node": None, "related": []}

    wiki = client.get("/student/stu_r/graph/topic:Сила/wiki", params=params)
    assert wiki.status_code == 200
    wb = wiki.json()
    assert wb["node"]["id"] == "topic:Сила"
    assert wb["wiki"] is None

    assert client.get(
        "/student/stu_r/graph/нет_такого/wiki", params=params
    ).status_code == 404


def test_graph_wiki_returns_real_article(client, tmp_path):
    """E4 §5: при статье ученика по теме узла /graph/{node}/wiki отдаёт её, не null."""
    wiki = KnowledgeWiki(str(tmp_path / "wiki"), student_id="stu_w")
    art = WikiArticle(
        subject="физика", topic="Сила", mastery=0.65, attempts=1, correct=1,
        body="Сила — векторная величина.",
    )
    wiki.upsert(art)

    resp = client.get(
        "/student/stu_w/graph/topic:Сила/wiki",
        params={"subject": "физика", "grade": "7"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["node"]["id"] == "topic:Сила"
    assert body["wiki"] is not None
    assert body["wiki"]["topic"] == "Сила"
    assert body["wiki"]["subject"] == "физика"
    assert body["wiki"]["mastery"] == 0.65
    assert body["wiki"]["attempts"] == 1
    assert body["wiki"]["body"] == "Сила — векторная величина."


def test_graph_ready_event_drained(client):
    app = client.app
    app.state.graph_ready_events["физика|7"] = [
        {"root": "book:физика|7", "stats": {"nodes": 2, "edges": 1}}
    ]
    base = {
        "message": "продолжим", "session_id": "ses_gr", "student_id": "stu_gr",
        "subject": "физика", "grade": "7",
    }
    with client.stream("POST", "/chat/stream", json=base) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())

    assert "event: graph.ready" in text
    assert '"root": "book:физика|7"' in text
    assert '"stats": {"nodes": 2, "edges": 1}' in text

    with client.stream("POST", "/chat/stream", json=base) as resp:
        second = "".join(resp.iter_text())
    assert "event: graph.ready" not in second
