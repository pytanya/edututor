"""Журнал ответов session_records (store) и экспорт для учителя (CSV + OKF)."""

import csv
import io
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.config import settings
from src.export.csv_exporter import (
    QUESTION_COLUMNS,
    SUMMARY_COLUMNS,
    iso_ts,
    questions_csv,
    summary_csv,
)
from src.export.okf import _node_relations, emit_okf_bundle, validate_bundle
from src.student.answer_record import AnswerRecord
from src.student.store import StudentStore


@pytest.fixture
def store(tmp_path):
    s = StudentStore(str(tmp_path / "students.db"))
    yield s
    s.close()


def _rec(record_id: str, **kw) -> dict:
    base = {"session_id": "ses_1", "topic": "интегралы", "subject": "математика"}
    base.update(kw)
    base["record_id"] = record_id
    return base


def test_append_record_creates_row_and_upserts_same_record_id(store):
    store.append_record("stu_1", "ses_1", _rec("rec_111111111111", correct=True, feedback="ок"))
    store.append_record("stu_1", "ses_1", _rec("rec_111111111111", correct=False, feedback="нет"))
    rows = store.list_records("stu_1")
    assert len(rows) == 1
    assert rows[0]["record_id"] == "rec_111111111111"
    assert rows[0]["correct"] == 0
    assert rows[0]["feedback"] == "нет"


def test_list_records_last_n_asc_and_filters(store):
    for i in range(1, 6):
        store.append_record(
            "stu_1", f"ses_{i}", _rec(f"rec_{i:012d}", ts=float(i), subject="математика")
        )
    rows = store.list_records("stu_1", limit=2)
    assert [r["record_id"] for r in rows] == ["rec_000000000004", "rec_000000000005"]
    assert [r["session_id"] for r in rows] == ["ses_4", "ses_5"]
    assert store.list_records("stu_1", subject="физика") == []
    subject = store.list_records("stu_1", subject="математика", session_id="ses_3")
    assert len(subject) == 1
    assert subject[0]["session_id"] == "ses_3"


def test_options_roundtrip_and_score01_derived(store):
    store.append_record(
        "stu_1", "ses_1",
        _rec("rec_222222222222", options=["3", "4", "5"], answer_type="single", correct=True),
    )
    store.append_record(
        "stu_1", "ses_1", _rec("rec_333333333333", correct=False, score01=0.3)
    )
    by_id = {r["record_id"]: r for r in store.list_records("stu_1")}
    assert by_id["rec_222222222222"]["options"] == ["3", "4", "5"]
    assert by_id["rec_222222222222"]["score01"] == pytest.approx(1.0)
    assert by_id["rec_333333333333"]["options"] is None
    assert by_id["rec_333333333333"]["score01"] == pytest.approx(0.3)


def test_list_records_empty_and_limit_guard(store):
    store.append_record("stu_1", "ses_1", _rec("rec_444444444444", correct=True))
    assert store.list_records("") == []
    assert store.list_records("stu_1", limit=0) == []
    assert store.list_records("stu_1", limit=-5) == []
    assert store.list_records("stu_2") == []
    assert store.list_records("stu_1")[0]["ts"] > 0


def _question_row(**kw: object) -> dict:
    base = {
        "timestamp": 1_700_000_000,
        "session_id": "ses_1",
        "subject": "математика",
        "topic": "интегралы",
        "question_id": "q_1",
        "question": "Чему равен интеграл?",
        "options": ["x^2/2", "x"],
        "answer_type": "single",
        "difficulty": "medium",
        "student_answer": "x^2/2",
        "score01": 1.0,
        "correct": True,
        "feedback": "верно",
    }
    base.update(kw)
    return base


def _parse(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_questions_csv_header_options_iso_and_bool():
    rows = [
        _question_row(),
        _question_row(
            question_id="q_2", timestamp=1_700_000_100, options=["1", "2", "3"],
            student_answer="2", correct=False, score01=0.0, feedback="нет",
        ),
    ]
    parsed = _parse(questions_csv(rows))
    assert parsed[0] == QUESTION_COLUMNS
    first, second = parsed[1], parsed[2]
    assert first[0] == iso_ts(1_700_000_000)
    assert second[0] == iso_ts(1_700_000_100)
    assert first[6] == "x^2/2 | x"
    assert second[6] == "1 | 2 | 3"
    assert first[11] == "1"
    assert second[11] == "0"
    assert first[4] == "q_1" and second[4] == "q_2"


def test_questions_csv_empty_only_headers():
    parsed = _parse(questions_csv([]))
    assert parsed == [QUESTION_COLUMNS]


def test_summary_csv_columns_and_list_join():
    rows = [{
        "session_id": "ses_1",
        "subject": "математика",
        "topic": "интегралы",
        "started_at": 1_700_000_000,
        "ended_at": 1_700_000_060,
        "questions": 3,
        "correct": 2,
        "accuracy": 0.6667,
        "mastered_topics": ["интегралы", "производные"],
    }]
    parsed = _parse(summary_csv(rows))
    assert parsed[0] == SUMMARY_COLUMNS
    row = parsed[1]
    assert row[3] == iso_ts(1_700_000_000)
    assert row[4] == iso_ts(1_700_000_060)
    assert row[5] == "3"
    assert row[8] == "интегралы | производные"


def test_csv_text_has_no_bom():
    text = questions_csv([_question_row()])
    assert not text.startswith("\ufeff")


def _frontmatter_data(path) -> dict:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    return yaml.safe_load(parts[1])


def test_emit_okf_bundle_skips_book_adds_mastery_and_relations(tmp_path):
    graph = {
        "nodes": [
            {"id": "book:math", "title": "Математика", "type": "book"},
            {"id": "topic:integrals", "title": "Интегралы", "type": "topic"},
            {"id": "sec:definite", "title": "Определённый интеграл", "type": "section",
             "section_number": "2.1", "parent_id": "topic:integrals"},
            {"id": "concept:antider", "title": "Первообразная", "type": "concept",
             "parent_id": "topic:integrals"},
        ],
        "edges": [
            {"source": "book:math", "target": "topic:integrals", "relation": "part_of"},
            {"source": "topic:integrals", "target": "sec:definite", "relation": "part_of"},
            {"source": "topic:integrals", "target": "concept:antider", "relation": "related"},
            {"source": "sec:definite", "target": "concept:antider", "relation": "requires"},
        ],
    }
    mastery = {"Интегралы": 0.75}
    out = tmp_path / "okf"
    emit_okf_bundle(out, "математика", grade="11", curriculum="профиль",
                    graph=graph, mastery=mastery)

    index = out / "index.md"
    assert index.exists() and (out / "log.md").exists()
    index_text = index.read_text(encoding="utf-8")
    assert "Интегралы" in index_text
    assert "topics/интегралы.md" in index_text
    assert "topics/математика.md" not in index_text

    topic_md = out / "topics/интегралы.md"
    section_md = out / "topics/определённый-интеграл.md"
    concept_md = out / "topics/первообразная.md"
    assert topic_md.exists() and section_md.exists() and concept_md.exists()

    topic_fm = _frontmatter_data(topic_md)
    assert topic_fm["type"] == "Topic"
    assert topic_fm["mastery"] == pytest.approx(0.75)
    assert topic_fm["curriculum"] == "профиль"
    assert topic_fm["relations"] == [
        {"target": "sec:definite", "relation": "part_of"},
        {"target": "concept:antider", "relation": "related"},
    ]
    section_fm = _frontmatter_data(section_md)
    assert section_fm["type"] == "Section"
    assert section_fm["section_number"] == "2.1"
    assert "mastery" not in section_fm
    concept_fm = _frontmatter_data(concept_md)
    assert concept_fm["type"] == "Section"

    report = validate_bundle(out)
    assert report["conformant"] is True
    assert report["errors"] == []
    assert "index.md" in report["files"]
    assert "topics/интегралы.md" in report["files"]


def test_validate_bundle_rejects_broken_files(tmp_path):
    out = tmp_path / "broken"
    topics = out / "topics"
    topics.mkdir(parents=True)
    (out / "index.md").write_text("нет frontmatter", encoding="utf-8")
    (topics / "empty-type.md").write_text("---\ntitle: X\n---\nтело", encoding="utf-8")

    report = validate_bundle(out)
    assert report["conformant"] is False
    assert report["errors"] == [
        "index.md: нет YAML-frontmatter",
        "topics/empty-type.md: поле type пустое",
    ]
    assert len(report["files"]) == 2


def test_emit_okf_bundle_empty_graph(tmp_path):
    out = tmp_path / "empty"
    emit_okf_bundle(out, "физика", grade="9")
    assert (out / "index.md").exists()
    assert (out / "log.md").exists()
    report = validate_bundle(out)
    assert report["conformant"] is True
    assert len(report["files"]) >= 2
    assert report["files"] == sorted(report["files"])


def test_node_relations_only_outgoing():
    edges = [
        {"source": "a", "target": "b", "relation": "part_of"},
        {"source": "c", "target": "a", "relation": "related"},
        {"source": "a", "target": "d", "relation": "requires"},
    ]
    assert _node_relations("a", edges) == [
        {"target": "b", "relation": "part_of"},
        {"target": "d", "relation": "requires"},
    ]
    assert _node_relations("b", edges) == []


# --- API-блок (Task 8): /export/csv, /export/summary.csv, /export/okf ---


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """HTTP-клиент экспорт-эндпоинтов: OKF-каталог и БД — во временной папке."""
    monkeypatch.setattr(settings, "okf_dir", str(tmp_path / "okf"))
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(student_store=store)
    app.state.rag_engine = None
    app.state.provisioner = None
    with TestClient(app) as c:
        yield c, store
    store.close()


def _answer(student_id, session_id, topic, subject, correct, ts):
    return AnswerRecord(
        record_id=f"rec_{int(ts * 1000)}",
        session_id=session_id,
        student_id=student_id,
        topic=topic,
        subject=subject,
        correct=bool(correct),
        feedback="верно" if correct else "нет",
        ts=ts,
    )


def _fill_student(store: StudentStore, student_id: str = "stu_1") -> None:
    """Журнал: две сессии, тема «интегралы» освоена (3 верных EMA-ответа)."""
    for i in range(3):
        store.apply_result(
            student_id, "интегралы", "Математика",
            _answer(student_id, f"ses_{i + 1}", "интегралы", "Математика", True, 1000 + i),
        )
    rows = [
        {"record_id": "rec_aaa", "ts": 1000.0, "question_id": "q_1",
         "question": "Чему равен интеграл?", "options": ["x^2/2", "x"],
         "answer_type": "single", "difficulty": "medium",
         "student_answer": "x^2/2", "correct": True, "score01": 1.0, "feedback": "верно"},
        {"record_id": "rec_bbb", "ts": 1001.0, "question_id": "q_2",
         "question": "Интеграл от 2x?", "options": ["x^2", "2x"],
         "answer_type": "single", "difficulty": "medium",
         "student_answer": "2x", "correct": False, "score01": 0.0, "feedback": "нет"},
        {"record_id": "rec_ccc", "ts": 1002.0, "question_id": "q_3",
         "question": "Интеграл от x?", "options": None, "answer_type": "open",
         "difficulty": "easy", "student_answer": "x^2/2", "correct": True,
         "score01": 1.0, "feedback": "верно"},
    ]
    for i, row in enumerate(rows):
        session_id = "ses_1" if i < 2 else "ses_2"
        store.append_record(
            student_id, session_id,
            {"session_id": session_id, "subject": "Математика", "topic": "интегралы", **row},
        )


def test_export_csv_download(api_client):
    client, store = api_client
    _fill_student(store)
    resp = client.get("/student/stu_1/export/csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "stu_1_session_log.csv" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"\xef\xbb\xbf")
    parsed = _parse(resp.content.decode("utf-8-sig"))
    assert parsed[0] == QUESTION_COLUMNS
    assert len(parsed) == 4
    sessions = {row[1] for row in parsed[1:]}
    assert sessions == {"ses_1", "ses_2"}
    assert {row[6] for row in parsed[1:]} == {"x^2/2 | x", "x^2 | 2x", ""}


def test_export_summary_csv(api_client):
    client, store = api_client
    _fill_student(store)
    resp = client.get("/student/stu_1/export/summary.csv")
    assert resp.status_code == 200
    assert "stu_1_summary.csv" in resp.headers["content-disposition"]
    parsed = _parse(resp.content.decode("utf-8-sig"))
    assert parsed[0] == SUMMARY_COLUMNS
    assert len(parsed) == 3
    by_session = {row[0]: row for row in parsed[1:]}
    ses_1, ses_2 = by_session["ses_1"], by_session["ses_2"]
    assert ses_1[1] == "Математика" and ses_1[2] == "интегралы"
    assert ses_1[5] == "2" and ses_1[6] == "1" and ses_1[7] == "0.5"
    assert ses_2[5] == "1" and ses_2[6] == "1" and ses_2[7] == "1.0"
    assert ses_1[8] == "интегралы" and ses_2[8] == "интегралы"


def test_export_csv_empty_student_only_headers(api_client):
    client, _store = api_client
    resp = client.get("/student/stu_empty/export/csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.content.startswith(b"\xef\xbb\xbf")
    assert _parse(resp.content.decode("utf-8-sig")) == [QUESTION_COLUMNS]


def test_export_okf_bundle_with_mastery(api_client):
    client, store = api_client
    _fill_student(store)
    resp = client.get(
        "/student/stu_1/export/okf", params={"subject": "Математика", "grade": "7"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["conformant"] is True
    assert body["errors"] == []
    out = Path(body["dir"])
    assert (out / "index.md").exists()
    assert (out / "log.md").exists()
    assert (out / "topics").is_dir()
    assert "index.md" in body["files"]
    topic_file = out / "topics" / "интегралы.md"
    assert topic_file.exists()
    assert _frontmatter_data(topic_file)["mastery"] == pytest.approx(0.8285)


def test_export_okf_empty_history_builds_scaffold(api_client):
    client, _store = api_client
    resp = client.get(
        "/student/stu_empty/export/okf", params={"subject": "Математика", "grade": "7"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["conformant"] is True
    assert body["errors"] == []
    index_text = (Path(body["dir"]) / "index.md").read_text(encoding="utf-8")
    assert "Учебник «Математика»" in index_text
