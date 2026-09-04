"""Модели Knowledge Wiki (E4 Task 2), хранилище (Task 3), обогащение (Task 4)
и API-эндпоинты + хук в чате (Task 7)."""

import pytest
from fastapi.testclient import TestClient

from src.agent.critic import Critic
from src.agent.loop import AgentRuntime
from src.agent.tools import ToolContext
from src.api.server import create_app
from src.config import settings
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.student.store import StudentStore
from src.wiki.enrich import build_messages, enrich_body
from src.wiki.models import WikiArticle, WikiNote
from src.wiki.store import KnowledgeWiki, slug


def test_ema_three_correct_anchor_0_8285():
    art = WikiArticle(subject="Математика", topic="Дроби")
    assert art.mastery == 0.5
    art.apply_result(1.0, True, feedback="")
    assert art.mastery == 0.65
    art.apply_result(1.0, True, feedback="")
    assert art.mastery == 0.755
    art.apply_result(1.0, True, feedback="")
    assert art.mastery == 0.8285
    assert art.attempts == 3
    assert art.correct == 3
    assert art.accuracy == 1.0


def test_note_dedup_by_feedback_updates_date_and_backfills():
    art = WikiArticle(subject="Математика", topic="Дроби")
    art.last_studied = "2026-09-04T10:00:00"
    art.add_note(
        feedback="Неверно. Правильный ответ: 3/4",
        question="Чему равно 1/2 + 1/4?",
        student_answer="1",
    )
    assert len(art.notes) == 1
    assert art.notes[0].date == "2026-09-04"
    art.last_studied = "2026-09-05T11:00:00"
    art.add_note(feedback="Неверно. Правильный ответ: 3/4", correct_answer="3/4")
    assert len(art.notes) == 1
    note = art.notes[0]
    assert note.date == "2026-09-05"
    assert note.student_answer == "1"
    assert note.correct_answer == "3/4"
    assert note.question == "Чему равно 1/2 + 1/4?"


def test_notes_capped_at_ten_keeps_tail():
    art = WikiArticle(subject="Математика", topic="Дроби")
    for i in range(11):
        art.add_note(feedback=f"Ошибка {i}")
    assert len(art.notes) == WikiArticle.MAX_NOTES == 10
    assert [n.feedback for n in art.notes] == [f"Ошибка {i}" for i in range(1, 11)]


def test_long_feedback_dedup_prefix_180():
    long_fb = "Ошибка." * 60
    art = WikiArticle(subject="Математика", topic="Дроби")
    art.add_note(feedback=long_fb, student_answer="1")
    art.add_note(feedback=long_fb, correct_answer="2")
    assert len(art.notes) == 1
    assert len(art.notes[0].feedback) == 180
    assert art.notes[0].correct_answer == "2"


def test_dict_round_trip_preserves_fields_and_notes():
    art = WikiArticle(
        subject="Математика",
        topic="Дроби",
        grade="5",
        curriculum="ru",
        attempts=2,
        correct=1,
        body="Сложение дробей.",
        section_number="3.1",
        weak_areas=["сложение"],
        source="https://example.com",
    )
    art.apply_result(0.0, False, feedback="Неверно", question="Вопрос", student_answer="Ответ")
    data = art.to_dict()
    assert data["accuracy"] == round(1 / 3, 4)
    restored = WikiArticle.from_dict("Математика", "Дроби", data)
    assert restored.to_dict() == data
    assert isinstance(restored.notes[0], WikiNote)


def test_to_markdown_contains_title_and_default_body():
    art = WikiArticle(subject="Математика", topic="Дроби")
    md = art.to_markdown()
    assert md.startswith("---\n")
    assert "\n# Дроби\n\n" in md
    assert "Материал по теме «Дроби» накапливается по мере прохождения квизов." in md


def test_note_normalization_from_dict_str_and_note():
    data = {
        "title": "Дроби",
        "topic": "Дроби",
        "subject": "Математика",
        "mastery": 0.75,
        "attempts": 2,
        "correct": 1,
        "last_studied": "2026-09-04T10:00:00",
        "notes": [
            {"date": "2026-09-04", "feedback": "фидбек"},
            "2026-09-03: старый формат",
        ],
    }
    art = WikiArticle.from_dict("Математика", "Дроби", data)
    assert art.mastery == 0.75
    assert len(art.notes) == 2
    assert art.notes[1].date == "2026-09-03"
    assert art.notes[1].feedback == "старый формат"


# --- KnowledgeWiki (Task 3) ---


def test_slug_unicode_and_separators():
    assert slug("Тема/Раздел") == "тема-раздел"
    assert slug("  ") == "topic"
    assert slug("") == "topic"
    assert slug("Дроби и Деление") == "дроби-и-деление"
    assert slug("Физика.Механика-1_2") == "физика.механика-1_2"
    assert slug("A\\B") == "a-b"


def test_store_round_trip_strips_heading_and_keeps_fields(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    art = WikiArticle(
        subject="Математика",
        topic="Дроби",
        grade="5",
        curriculum="ru",
        mastery=0.755,
        attempts=3,
        correct=2,
        body="Сложение дробей с разными знаменателями.",
        weak_areas=["знаменатель"],
    )
    wiki.upsert(art)
    raw = wiki.article_path("Математика", "Дроби").read_text(encoding="utf-8")
    assert "\n# Дроби\n\n" in raw
    loaded = wiki.get("Математика", "Дроби")
    assert loaded is not None
    assert loaded.body == "Сложение дробей с разными знаменателями."
    assert loaded.subject == "Математика"
    assert loaded.grade == "5"
    assert loaded.curriculum == "ru"
    assert loaded.attempts == 3
    assert loaded.correct == 2
    assert "accuracy" in loaded.to_dict()
    assert loaded.to_dict()["accuracy"] == round(2 / 3, 4)


def test_store_read_file_strips_leading_heading(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    raw = (
        "---\nokf_version: '0.2'\ntype: Topic\ntitle: Дроби\ntopic: Дроби\n"
        "subject: Математика\nmastery: 0.5\nattempts: 0\ncorrect: 0\n"
        "last_studied: '2026-09-04T10:00:00'\n---\n"
        "# Дроби\n\nРучной конспект с заголовком."
    )
    p = wiki.article_path("Математика", "Дроби")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw, encoding="utf-8")
    loaded = wiki.get("Математика", "Дроби")
    assert loaded is not None
    assert loaded.body == "Ручной конспект с заголовком."


def test_store_default_body_when_empty(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    art = WikiArticle(subject="Математика", topic="Дроби")
    md = art.to_markdown()
    assert "Материал по теме «Дроби» накапливается по мере прохождения квизов." in md
    wiki.upsert(art)
    loaded = wiki.get("Математика", "Дроби")
    assert loaded is not None
    assert "накапливается по мере прохождения квизов" in loaded.body


def test_store_broken_file_returns_none(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    cases = {
        "nofront": "просто текст без frontmatter",
        "badyaml": "---\nokf_version: ['незакрыто\n: bad: [\n---\nтело",
        "unclosed": "---\nokf_version: '0.2'\ntype: Topic\n# Тема\n\nтело",
    }
    for topic, content in cases.items():
        p = wiki.article_path("Математика", topic)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        assert wiki.get("Математика", topic) is None
    assert wiki.list_articles() == []


def test_apply_record_creates_and_is_idempotent(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    rec1 = {
        "topic": "Дроби",
        "score01": 1.0,
        "correct": True,
        "feedback": "Верно",
        "question": "1/2 + 1/4?",
        "student_answer": "3/4",
        "correct_answer": "3/4",
    }
    art = wiki.apply_record(rec1, grade="5", curriculum="ru")
    assert art is not None
    assert art.attempts == 1
    stored = wiki.get("общая тема", "Дроби")
    assert stored is not None
    assert stored.grade == "5"
    assert stored.curriculum == "ru"
    wiki.apply_record(rec1, grade="5", curriculum="ru")
    stored = wiki.get("общая тема", "Дроби")
    assert stored.attempts == 2
    assert stored.correct == 2
    assert wiki.list_articles() and len(wiki.list_articles()) == 1


def test_apply_record_guards_no_topic_or_score(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    assert wiki.apply_record({"score01": 1.0, "correct": True}) is None
    assert wiki.apply_record({"topic": "X", "correct": True}) is None
    assert wiki.apply_record({"topic": "X", "score01": 0.5}) is None
    assert wiki.apply_record(None) is None
    assert wiki.list_articles() == []


def test_index_sorted_by_mastery_and_deleted_with_last_article(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    wiki.upsert(WikiArticle(subject="Математика", topic="Дроби", mastery=0.65))
    wiki.upsert(WikiArticle(subject="Математика", topic="Уравнения", mastery=0.8))
    idx = wiki.subject_dir("Математика") / "_index.md"
    text = idx.read_text(encoding="utf-8")
    assert "[Дроби](дроби.md)" in text
    assert "[Уравнения](уравнения.md)" in text
    assert text.find("уравнения.md") < text.find("дроби.md")
    assert wiki.delete("Математика", "Дроби") is True
    text = idx.read_text(encoding="utf-8")
    assert "дроби.md" not in text
    assert wiki.delete("Математика", "Уравнения") is True
    assert not idx.exists()
    assert wiki.delete("Математика", "Нет") is False


def test_article_by_slug_matches_human_frontmatter(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    wiki.upsert(WikiArticle(subject="Математика", topic="Дроби", mastery=0.7))
    art = wiki.article_by_slug("математика", "дроби")
    assert art is not None
    assert art.subject == "Математика"
    assert art.topic == "Дроби"
    assert wiki.article_by_slug("математика", "нет") is None


def test_sync_mastery_creates_shells_without_attempts(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    arts = wiki.sync_mastery("Математика", {"Дроби": 0.9, "Уравнения": 0.6})
    assert len(arts) == 2
    stored = wiki.get("Математика", "Дроби")
    assert stored is not None
    assert stored.mastery == 0.9
    assert stored.attempts == 0
    assert stored.correct == 0
    wiki.sync_mastery("Математика", {"Дроби": 0.95})
    stored = wiki.get("Математика", "Дроби")
    assert stored.mastery == 0.95
    assert stored.attempts == 0


def test_to_summary_dict_groups_by_human_subject(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    wiki.upsert(WikiArticle(subject="Математика", topic="Дроби", mastery=0.7))
    wiki.upsert(WikiArticle(subject="Физика", topic="Силы", mastery=0.5))
    summary = wiki.to_summary_dict()
    assert [g["subject"] for g in summary] == ["Математика", "Физика"]
    assert summary[0]["articles"][0]["topic"] == "Дроби"


def test_list_subjects_excludes_empty_and_index_only(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    assert wiki.list_subjects() == []
    wiki.upsert(WikiArticle(subject="Математика", topic="Дроби"))
    assert wiki.list_subjects() == ["математика"]
    wiki.subject_dir("Биология")
    assert wiki.list_subjects() == ["математика"]
    assert wiki.list_articles("Биология") == []


# --- enrich_body (Task 4) ---


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    def __init__(self, text="", error=None) -> None:
        self.text = text
        self.error = error
        self.calls = 0

    async def chat(self, messages, model="", temperature=0.7, max_tokens=1024, **kw):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return _Resp(self.text)


def _write_shell(tmp_path, topic: str, body: str = "") -> KnowledgeWiki:
    """Статья-оболочка на диске с пустым/коротким телом (файл без дефолтной заглушки)."""
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    p = wiki.article_path("Математика", topic)
    p.parent.mkdir(parents=True, exist_ok=True)
    front = (
        f"---\nokf_version: '0.2'\ntype: Topic\ntitle: {topic}\ntopic: {topic}\n"
        f"subject: Математика\nmastery: 0.5\nattempts: 0\ncorrect: 0\n"
        f"last_studied: '2026-09-04T10:00:00'\n---\n"
    )
    p.write_text(front + f"# {topic}\n\n{body}".strip() + "\n", encoding="utf-8")
    return wiki


async def test_enrich_body_empty_body_writes_summary(tmp_path):
    wiki = _write_shell(tmp_path, "Дроби")
    llm = _FakeLLM(text="Конспект: дроби, числитель и знаменатель. Сложение дробей.")
    result = await enrich_body(
        wiki, "Математика", "Дроби", ["Сниппет о дробях"], llm, model=""
    )
    assert llm.calls == 1
    assert result is not None
    assert "числитель" in result["body"]
    stored = wiki.get("Математика", "Дроби")
    assert stored is not None
    assert "числитель" in stored.body


async def test_enrich_body_no_context_or_llm_keeps_file(tmp_path):
    wiki = _write_shell(tmp_path, "Дроби")
    llm = _FakeLLM(text="Конспект какой-то достаточно длинный")
    before = wiki.article_path("Математика", "Дроби").read_text(encoding="utf-8")
    assert await enrich_body(wiki, "Математика", "Дроби", [], llm, model="") is None
    assert await enrich_body(wiki, "Математика", "Дроби", ["контекст"], None, model="") is None
    assert llm.calls == 0
    after = wiki.article_path("Математика", "Дроби").read_text(encoding="utf-8")
    assert after == before


async def test_enrich_body_skips_existing_long_body(tmp_path):
    wiki = KnowledgeWiki(tmp_path, student_id="s1")
    wiki.upsert(
        WikiArticle(
            subject="Математика",
            topic="Дроби",
            body="Уже есть длинный конспект статьи длиной заметно больше двадцати символов.",
        )
    )
    llm = _FakeLLM(text="Другой конспект по дробям")
    assert await enrich_body(wiki, "Математика", "Дроби", ["сниппет"], llm, model="") is None
    assert llm.calls == 0


async def test_enrich_body_short_answer_keeps_shell(tmp_path):
    wiki = _write_shell(tmp_path, "Дроби")
    llm = _FakeLLM(text="коротко")
    assert await enrich_body(wiki, "Математика", "Дроби", ["сниппет"], llm, model="") is None
    assert llm.calls == 1
    stored = wiki.get("Математика", "Дроби")
    assert stored is not None
    assert (stored.body or "").strip() == ""


async def test_enrich_body_llm_error_returns_none(tmp_path):
    wiki = _write_shell(tmp_path, "Дроби")
    llm = _FakeLLM(error=RuntimeError("LLM недоступен"))
    assert await enrich_body(wiki, "Математика", "Дроби", ["сниппет"], llm, model="") is None
    assert llm.calls == 1
    stored = wiki.get("Математика", "Дроби")
    assert stored is not None
    assert (stored.body or "").strip() == ""


def test_enrich_build_messages_truncates_context():
    chunks = [f"Сниппет {i}. " + "длинный контекст " * 80 for i in range(10)]
    messages = build_messages("Дроби", chunks)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    user = messages[1]["content"]
    assert user.startswith("Тема: Дроби\nФрагменты материалов:\n")
    prefix = "Тема: Дроби\nФрагменты материалов:\n"
    assert len(user) <= len(prefix) + 4000
    assert "\n---\n" in user


# --- Knowledge Wiki (E4 Task 7): API-эндпоинты и хук в _run_chat ---


class _TutorLLM(LLMClient):
    """Планировщик: сразу отвечает текстом, без вызова инструментов."""

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        return LLMResponse(
            content="Привет! Разберём тему шаг за шагом.",
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


class _JudgeLLM(LLMClient):
    """Критик: всегда одобряет."""

    def __init__(self):
        self.calls = 0

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        return LLMResponse(
            content='{"passed": true, "issues": []}',
            model=model,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


def _fake_runtime_factory():
    """AgentRuntime на фейковых LLM (без сети)."""
    return AgentRuntime(
        llm=_TutorLLM(),
        models={"planner": "test", "fast": "test", "judge": "test"},
        tool_context=ToolContext(region="GLOBAL"),
        critic=Critic(llm=_JudgeLLM(), model="judge"),
    )


class _EnvelopePlanner(LLMClient):
    """Планировщик, возвращающий заранее заданный JSON-конверт."""

    def __init__(self, payload: str):
        self._payload = payload

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        return LLMResponse(
            content=self._payload,
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


def _eval_runtime_factory(payload: str):
    """Фабрика рантайма, чей planner сразу возвращает evaluation-конверт."""

    def factory():
        return AgentRuntime(
            llm=_EnvelopePlanner(payload),
            models={"planner": "p", "fast": "f", "judge": "j"},
            tool_context=ToolContext(region="GLOBAL"),
            critic=Critic(llm=_JudgeLLM(), model="judge"),
        )

    return factory


def _fill(wiki_dir, student_id: str, rows: list[dict]) -> None:
    """Прямое наполнение статей через apply_record (спека: статья на первом ответе)."""
    wiki = KnowledgeWiki(wiki_dir, student_id=student_id)
    for row in rows:
        wiki.apply_record(
            {
                "topic": row["topic"],
                "score01": 1.0,
                "correct": True,
                "feedback": "Верно",
            },
            subject=row["subject"],
            grade=row.get("grade", "5"),
            curriculum="",
        )


@pytest.fixture
def wiki_client(tmp_path, monkeypatch):
    """HTTP-клиент с wiki-каталогом во временной папке и без RAG/провижининга."""
    monkeypatch.setattr(settings, "knowledge_wiki_dir", str(tmp_path / "wiki"))
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(runtime_factory=_fake_runtime_factory, student_store=store)
    app.state.rag_engine = None
    app.state.provisioner = None
    with TestClient(app) as c:
        yield c
    store.close()


def test_wiki_list_empty_is_subjects_and_prefilled_has_article_dict(wiki_client, tmp_path):
    assert wiki_client.get("/student/empty_stu/wiki").json() == {"subjects": []}
    _fill(
        tmp_path / "wiki", "stu_1",
        [{"subject": "Math", "topic": "Drob"}, {"subject": "Physics", "topic": "Force"}],
    )
    body = wiki_client.get("/student/stu_1/wiki").json()
    assert [g["subject"] for g in body["subjects"]] == ["Math", "Physics"]
    art = body["subjects"][0]["articles"][0]
    assert art["topic"] == "Drob"
    assert art["mastery"] == 0.65
    assert art["accuracy"] == 1.0
    assert "body" in art


def test_wiki_list_filter_by_subject_and_get_article(wiki_client, tmp_path):
    _fill(
        tmp_path / "wiki", "stu_2",
        [
            {"subject": "Math", "topic": "Drob"},
            {"subject": "Math", "topic": "Equations"},
            {"subject": "Physics", "topic": "Force"},
        ],
    )
    body = wiki_client.get("/student/stu_2/wiki", params={"subject": "math"}).json()
    assert body["subject"] == "Math"
    assert [a["topic"] for a in body["articles"]] == ["Drob", "Equations"]
    art = wiki_client.get("/student/stu_2/wiki/Math/Drob").json()
    assert art["topic"] == "Drob"
    assert art["mastery"] == 0.65
    missing = wiki_client.get("/student/stu_2/wiki/Math/Nope")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Тема не найдена в базе знаний"


def test_wiki_delete_removes_article_and_404_on_repeat(wiki_client, tmp_path):
    _fill(tmp_path / "wiki", "stu_3", [{"subject": "Math", "topic": "Drob"}])
    resp = wiki_client.delete("/student/stu_3/wiki/Math/Drob")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "subject": "Math", "topic": "Drob"}
    assert wiki_client.get("/student/stu_3/wiki/Math/Drob").status_code == 404
    assert KnowledgeWiki(tmp_path / "wiki", student_id="stu_3").get("Math", "Drob") is None
    repeat = wiki_client.delete("/student/stu_3/wiki/Math/Drob")
    assert repeat.status_code == 404


def test_wiki_enrich_without_rag_returns_helping_note(wiki_client, tmp_path):
    _fill(tmp_path / "wiki", "stu_4", [{"subject": "Math", "topic": "Drob"}])
    resp = wiki_client.post(
        "/student/stu_4/wiki/enrich", json={"subject": "Math", "topic": "Drob"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["article"] is None
    assert body["note"].startswith("Нет материалов по теме в базе знаний")


def test_wiki_hook_on_evaluation_creates_article_and_record(tmp_path, monkeypatch):
    """Evaluation-ход: wiki.apply_record(record) + запись в session_records."""
    monkeypatch.setattr(settings, "knowledge_wiki_dir", str(tmp_path / "wiki"))
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(
        runtime_factory=_eval_runtime_factory(
            '{"type": "evaluation", "text": "Верно!",'
            '"payload": {"correct": true, "feedback": "ок", "knowledge_delta": 0.2}}'
        ),
        student_store=store,
    )
    app.state.rag_engine = None
    app.state.provisioner = None
    with TestClient(app) as c:
        resp = c.post(
            "/chat",
            json={
                "message": "Ответ: 3/4",
                "session_id": "ses_w",
                "student_id": "stu_w",
                "topic": "Drob",
                "subject": "Math",
            },
        )
        assert resp.status_code == 200
        wiki_body = c.get("/student/stu_w/wiki").json()
    art = wiki_body["subjects"][0]["articles"][0]
    assert art["topic"] == "Drob"
    assert art["mastery"] == 0.65
    assert art["attempts"] == 1
    rows = store.list_records("stu_w")
    assert len(rows) == 1
    assert rows[0]["topic"] == "Drob"
    assert rows[0]["correct"] == 1
    store.close()
