"""Тесты HTTP-слоя API: без реальных ключей и сети (фейковый рантайм)."""

import json

import pytest
from fastapi.testclient import TestClient

from src.agent.critic import Critic
from src.agent.loop import AgentRuntime
from src.agent.quiz_guard import QUIZ_BLOCKED_TEXT, QUIZ_REJECT_RETRY_TEXT
from src.agent.tools import ToolContext
from src.api.server import create_app
from src.config import settings
from src.llm.base import LLMClient, LLMResponse, TokenUsage
from src.student.store import StudentStore


class FakeTutorLLM(LLMClient):
    """Планировщик: сразу отвечает финальным ответом, без вызова инструментов."""

    def __init__(self):
        self.calls = 0

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        self.calls += 1
        return LLMResponse(
            content="Привет! Разберём тему шаг за шагом.",
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


class FakeJudgeLLM(LLMClient):
    """Критик: всегда одобряет ответ."""

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


class FakeHintLLM(LLMClient):
    """Планировщик: на hint-ходе схемы инструментов не получает и отвечает hint.

    Если схемы всё же переданы — воспроизводит наблюдаемый баг: «вызывает»
    generate_quiz (новый вопрос) вместо подсказки к текущей задаче.
    """

    def __init__(self):
        self.saw_tools: list[bool] = []

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        self.saw_tools.append(tools is not None)
        if tools:
            return LLMResponse(
                content="",
                model=model,
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
                finish_reason="tool_calls",
                tool_calls=[
                    {
                        "function": {
                            "name": "generate_quiz",
                            "arguments": json.dumps({"topic": "сила тяжести"}),
                        }
                    }
                ],
            )
        return LLMResponse(
            content=(
                '{"type": "hint", "text": "Намек: смотри на правую часть уравнения.", '
                '"payload": {"task_ref": "..."}, "difficulty": "medium"}'
            ),
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


def _fake_runtime_factory():
    """Собирает AgentRuntime на фейковых LLM (без сети и API-ключей)."""
    return AgentRuntime(
        llm=FakeTutorLLM(),
        models={"planner": "test", "fast": "test", "judge": "test"},
        tool_context=ToolContext(region="GLOBAL"),
        critic=Critic(llm=FakeJudgeLLM(), model="judge"),
    )


@pytest.fixture
def client(tmp_path):
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(runtime_factory=_fake_runtime_factory, student_store=store)
    with TestClient(app) as c:
        yield c
    store.close()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["region"] == settings.region.value


def test_chat_valid_message(client):
    resp = client.post(
        "/chat",
        json={
            "message": "Объясни квадратное уравнение",
            "session_id": "sess-1",
            "student_profile": {
                "current_knowledge_level": 0.7,
                "learning_style": "visual",
                "fatigue_level": 0.2,
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"]
    assert body["error"] is None
    assert body["trace_id"]
    assert body["session_id"] == "sess-1"
    assert body["difficulty"] == "medium"
    assert isinstance(body["steps"], int)
    assert body["terminated"] is True
    assert "messages" not in body
    assert "tools_result" not in body
    assert body["envelope"] is not None
    assert body["envelope"]["type"] in {"theory", "practice", "hint", "quiz", "evaluation"}
    assert "student_id" in body["adaptive"]


@pytest.mark.parametrize("message", ["", "   ", "\t\n"])
def test_chat_blank_message_rejected(client, message):
    resp = client.post("/chat", json={"message": message})
    assert resp.status_code == 422


# --- История сессий ---

def test_chat_persists_history_per_session(client):
    first = client.post("/chat", json={"message": "Вопрос 1", "session_id": "sess-h"})
    assert first.status_code == 200
    second = client.post("/chat", json={"message": "Вопрос 2", "session_id": "sess-h"})
    assert second.status_code == 200

    hist = client.get("/chat/history/sess-h")
    assert hist.status_code == 200
    messages = hist.json()["messages"]
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert messages[0]["content"] == "Вопрос 1"
    assert messages[2]["content"] == "Вопрос 2"


def test_chat_history_not_found(client):
    assert client.get("/chat/history/nope").status_code == 404
    assert client.delete("/chat/history/nope").status_code == 404


def test_chat_history_delete(client):
    client.post("/chat", json={"message": "hi", "session_id": "sess-del"})
    assert client.get("/chat/history/sess-del").status_code == 200
    assert client.delete("/chat/history/sess-del").status_code == 204
    assert client.get("/chat/history/sess-del").status_code == 404


# --- SSE /chat/stream ---

def test_chat_stream_returns_sse_events(client):
    with client.stream(
        "POST",
        "/chat/stream",
        json={"message": "Объясни теорему", "session_id": "sess-sse"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = "".join(resp.iter_text())

    assert "event: message" in text
    assert "event: done" in text
    assert "data: {" in text


def test_chat_stream_message_has_envelope_and_adaptive(client):
    with client.stream(
        "POST",
        "/chat/stream",
        json={"message": "Привет", "session_id": "sse-env"},
    ) as resp:
        text = "".join(resp.iter_text())
    assert '"envelope"' in text
    assert '"adaptive"' in text


def test_student_endpoint(client):
    # после чата студент создаётся
    client.post("/chat", json={"message": "hello", "student_id": "stu_e2e"})
    resp = client.get("/student/stu_e2e")
    assert resp.status_code == 200
    assert resp.json()["student_id"] == "stu_e2e"
    assert resp.json()["topics"] == []
    assert client.get("/student/unknown").status_code == 404


def test_student_endpoint_includes_profile_fields(client):
    client.post("/chat", json={"message": "hello", "student_id": "stu_prof1"})
    resp = client.get("/student/stu_prof1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["student_id"] == "stu_prof1"
    assert body["name"] == ""
    assert body["learner_type"] == ""
    assert body["grade"] == ""
    assert body["topics"] == []


def test_post_student_profile_upserts_and_is_returned_by_get(client):
    resp = client.post(
        "/student/stu_prof2/profile",
        json={"name": "Иван Иванов", "learner_type": "schoolchild", "grade": "8 класс"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "student_id": "stu_prof2",
        "name": "Иван Иванов",
        "learner_type": "schoolchild",
        "grade": "8 класс",
    }
    body = client.get("/student/stu_prof2").json()
    assert body["name"] == "Иван Иванов"
    assert body["learner_type"] == "schoolchild"
    assert body["grade"] == "8 класс"


def test_post_student_profile_overwrites_previous(client):
    client.post(
        "/student/stu_prof3/profile",
        json={"name": "Петя Петров", "learner_type": "student"},
    )
    resp = client.post(
        "/student/stu_prof3/profile",
        json={"name": "Пётр Петров", "learner_type": "student", "grade": "1 курс"},
    )
    body = resp.json()
    assert body["name"] == "Пётр Петров"
    assert body["grade"] == "1 курс"


def test_post_student_profile_empty_name_is_allowed(client):
    # Пустое имя валидно (422 только для непустого имени из одного слова).
    resp = client.post("/student/stu_prof4/profile", json={"name": ""})
    assert resp.status_code == 200
    assert resp.json()["name"] == ""


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Иван"},                      # одно слово
        {"name": "  Иван  "},                  # одно слово после strip
        {"learner_type": "teacher"},           # вне списка
    ],
)
def test_post_student_profile_validation_422(client, payload):
    resp = client.post("/student/stu_prof5/profile", json=payload)
    assert resp.status_code == 422


def test_hint_request_empty_message_allowed(client):
    """Кнопка «Подсказка» шлёт пустой message + kind=hint_request (не 422)."""
    resp = client.post(
        "/chat",
        json={
            "message": "",
            "kind": "hint_request",
            "session_id": "ses_148b93f46170",
            "student_id": "stu_8b16adb3",
            "topic": "сила тяжести",
            "subject": "физика",
            "grade": "7",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["reply"]


def test_hint_request_does_not_append_user_message(client):
    client.post("/chat", json={"message": "как решить?", "session_id": "sess-h1"})
    client.post(
        "/chat",
        json={"message": "подсказка", "kind": "hint_request", "session_id": "sess-h1"},
    )
    hist = client.get("/chat/history/sess-h1").json()["messages"]
    roles = [m["role"] for m in hist]
    assert roles == ["user", "assistant", "assistant"]
    user_contents = [m["content"] for m in hist if m["role"] == "user"]
    assert "подсказка" not in user_contents


def test_hint_request_does_not_invoke_generate_quiz(tmp_path):
    """На hint-ходе модели не даются схемы инструментов — generate_quiz не вызывается.

    Регрессия: планировщик ошибочно вызывал generate_quiz на просьбу о подсказке
    и выдавал новый вопрос вместо hint-конверта (allow_tools=False, server.py).
    """
    store = StudentStore(str(tmp_path / "students.db"))
    llm = FakeHintLLM()

    def factory():
        return AgentRuntime(
            llm=llm,
            models={"planner": "test", "fast": "test", "judge": "test"},
            tool_context=ToolContext(region="GLOBAL"),
            critic=Critic(llm=FakeJudgeLLM(), model="judge"),
        )

    app = create_app(runtime_factory=factory, student_store=store)
    with TestClient(app) as c:
        resp = c.post(
            "/chat",
            json={
                "message": "",
                "kind": "hint_request",
                "session_id": "ses_hint_tools",
                "student_id": "stu_hint_tools",
                "topic": "уравнения",
                "subject": "математика",
                "grade": "7",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["reply"]
    store.close()
    # Планировщик ни разу не получал tool-схемы => не мог вызвать generate_quiz.
    assert llm.saw_tools and not any(llm.saw_tools)


# --- Knowledge provisioning ---

async def _stub_provisioner(topic: str, subject: str = "", grade: str = ""):
    return 3


@pytest.fixture
def client_with_provisioner(tmp_path):
    store = StudentStore(str(tmp_path / "students.db"))
    app = create_app(
        runtime_factory=_fake_runtime_factory,
        provisioner=_stub_provisioner,
        student_store=store,
    )
    with TestClient(app) as c:
        yield c
    store.close()


def test_knowledge_provision_endpoint(client_with_provisioner):
    resp = client_with_provisioner.post(
        "/knowledge/provision",
        json={"topic": "квадратные уравнения", "subject": "алгебра", "grade": "8 класс"},
    )
    assert resp.status_code == 200
    assert resp.json()["indexed"] == 3


def test_knowledge_provision_dedupes(client_with_provisioner):
    payload = {"topic": "теорема пифагора", "subject": "геометрия"}
    assert client_with_provisioner.post("/knowledge/provision", json=payload).json()["indexed"] == 3
    # Повторный запрос той же темы не должен индексировать заново.
    assert client_with_provisioner.post("/knowledge/provision", json=payload).json()["indexed"] == 0
    body = client_with_provisioner.get("/knowledge").json()
    assert body["rag_enabled"] is True
    assert "|".join(("геометрия", "", "теорема пифагора")) in body["provisioned_topics"]


def test_chat_with_topic_triggers_provisioning(client_with_provisioner):
    resp = client_with_provisioner.post(
        "/chat",
        json={
            "message": "Расскажи про интегралы",
            "session_id": "sess-k",
            "topic": "интегралы",
            "subject": "математика",
        },
    )
    assert resp.status_code == 200
    assert client_with_provisioner.get("/knowledge").json()["ingested_chunks"] == 3


# --- Сессии ученика: привязка session <-> student ---

def test_student_sessions_lists_session_after_chat(client):
    """После чата с student_id/topic эндпоинт /student/{id}/sessions показывает сессию."""
    resp = client.post(
        "/chat",
        json={"message": "hi", "session_id": "ses_qa", "student_id": "stu_A", "topic": "интегралы"},
    )
    assert resp.status_code == 200
    body = client.get("/student/stu_A/sessions").json()
    assert body["student_id"] == "stu_A"
    sessions = [s for s in body["sessions"] if s["session_id"] == "ses_qa"]
    assert len(sessions) == 1
    assert set(sessions[0]) == {"session_id", "topic", "started_at", "ended_at"}
    assert sessions[0]["topic"] == "интегралы"


def test_student_sessions_does_not_404_for_unknown(client):
    """Неизвестный ученик отдаёт пустой список (200), а не 404."""
    resp = client.get("/student/who-is-this/sessions")
    assert resp.status_code == 200
    assert resp.json() == {"student_id": "who-is-this", "sessions": []}


def test_chat_with_foreign_session_creates_new_and_no_leak(client):
    """Ученик A с session_id ученика B получает новую сессию и не видит историю B."""
    first = client.post(
        "/chat", json={"message": "секрет B", "session_id": "ses_shared", "student_id": "stu_B"}
    )
    assert first.status_code == 200
    shared_history = client.get("/chat/history/ses_shared").json()["messages"]
    assert any(
        m["role"] == "user" and m["content"] == "секрет B" for m in shared_history
    )

    second = client.post(
        "/chat", json={"message": "привет A", "session_id": "ses_shared", "student_id": "stu_A"}
    )
    assert second.status_code == 200
    body = second.json()
    assert body["session_id"] != "ses_shared"
    new_sid = body["session_id"]

    leaked = client.get(f"/chat/history/{new_sid}").json()["messages"]
    new_contents = [m["content"] for m in leaked if m["role"] == "user"]
    assert "секрет B" not in new_contents
    assert "привет A" in new_contents

    # Чужая история не тронута, а новая сессия привязана к A.
    still_shared = client.get("/chat/history/ses_shared").json()["messages"]
    assert any(m["content"] == "секрет B" for m in still_shared if m["role"] == "user")
    assert not any(m["content"] == "привет A" for m in still_shared if m["role"] == "user")
    a_sessions = client.get("/student/stu_A/sessions").json()["sessions"]
    assert new_sid in {s["session_id"] for s in a_sessions}
    b_sessions = client.get("/student/stu_B/sessions").json()["sessions"]
    assert "ses_shared" in {s["session_id"] for s in b_sessions}


def test_anonymous_turns_share_stable_fallback_student(client):
    """Ходы без student_id привязываются к одному стабильному fallback-ученику.

    Fallback (stu_*) фиксируется на in-memory сессии, поэтому прогресс
    не теряется между анонимными ходами (регрессия бага 2).
    """
    first = client.post("/chat", json={"message": "вопрос 1", "session_id": "ses_anon"})
    assert first.status_code == 200
    sid1 = first.json()["adaptive"]["student_id"]
    assert sid1.startswith("stu_")

    second = client.post("/chat", json={"message": "вопрос 2", "session_id": "ses_anon"})
    assert second.status_code == 200
    assert second.json()["adaptive"]["student_id"] == sid1

    sessions = client.get(f"/student/{sid1}/sessions").json()["sessions"]
    assert any(s["session_id"] == "ses_anon" for s in sessions)


def test_claimed_session_keeps_owner_on_anonymous_followup(client):
    """Анонимный ход по сессии не отбирает владельца и не плодит нового ученика."""
    first = client.post(
        "/chat", json={"message": "hi", "session_id": "ses_K", "student_id": "stu_K"}
    )
    assert first.status_code == 200
    assert first.json()["adaptive"]["student_id"] == "stu_K"

    second = client.post("/chat", json={"message": "ещё вопрос", "session_id": "ses_K"})
    assert second.status_code == 200
    assert second.json()["adaptive"]["student_id"] == "stu_K"
    owner_sessions = client.get("/student/stu_K/sessions").json()["sessions"]
    assert "ses_K" in {s["session_id"] for s in owner_sessions}


# --- Журнал ответов (E4, session_records) ---

class _EnvelopePlanner(LLMClient):
    """Возвращает заранее заданный JSON-конверт в каждом вызове planner."""

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


def _evaluation_runtime_factory(payload: str):
    """Фабрика рантайма, чей planner сразу возвращает evaluation-конверт."""

    def factory():
        return AgentRuntime(
            llm=_EnvelopePlanner(payload),
            models={"planner": "p", "fast": "f", "judge": "j"},
            tool_context=ToolContext(region="GLOBAL"),
            critic=Critic(llm=FakeJudgeLLM(), model="judge"),
        )

    return factory


def test_evaluation_writes_session_record(tmp_path):
    """Evaluation-ход пишет 1 строку в session_records (record_id ~ rec_)."""
    store = StudentStore(str(tmp_path / "s.db"))
    app = create_app(
        runtime_factory=_evaluation_runtime_factory(
            '{"type": "evaluation", "text": "Верно!",'
            '"payload": {"correct": true, "feedback": "ок", "knowledge_delta": 0.2}}'
        ),
        student_store=store,
    )
    with TestClient(app) as c:
        resp = c.post(
            "/chat",
            json={
                "message": "Ответ: 4",
                "session_id": "ses_j",
                "student_id": "stu_J",
                "topic": "интегралы",
                "subject": "математика",
            },
        )
    assert resp.status_code == 200
    rows = store.list_records("stu_J")
    assert len(rows) == 1
    row = rows[0]
    assert row["record_id"].startswith("rec_")
    assert row["session_id"] == "ses_j"
    assert row["topic"] == "интегралы"
    assert row["subject"] == "математика"
    assert row["correct"] == 1
    assert row["score01"] == 1.0
    assert row["student_answer"] == "Ответ: 4"
    store.close()


def test_session_bandit_fields_defaults():
    from src.api.session_store import SessionStore

    store = SessionStore()
    session = store.create(student_id="stu_1", topic="т")
    assert session.bandit_arm is None
    assert session.bandit_topic == ""
    assert session.bandit_features == []
    session.bandit_arm = 2
    assert store.get(session.session_id).bandit_arm == 2


# --- LinUCB (Этап 5): совет перед run_agent и фиксация руки на quiz/practice ---


class FakeQuizLLM(LLMClient):
    """Планировщик: возвращает готовый quiz-конверт (без инструментов)."""

    def __init__(self):
        self.last_messages = []

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        self.last_messages = list(messages)
        return LLMResponse(
            content=(
                '{"type": "quiz", "text": "Чему равен x в x^2=16?", '
                '"payload": {"answer_type": "single", "options": ["4", "-4", "4 и -4"], '
                '"_correct_answer": "4 и -4"}, "difficulty": "medium"}'
            ),
            model=model,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


def _quiz_runtime_factory():
    """Собирает AgentRuntime с FakeQuizLLM (для тестов без сети)."""
    return AgentRuntime(
        llm=FakeQuizLLM(),
        models={"planner": "test", "fast": "test", "judge": "test"},
        tool_context=ToolContext(region="GLOBAL"),
        critic=Critic(llm=FakeJudgeLLM(), model="judge"),
    )


def test_bandit_quiz_advice_and_arm_marking(tmp_path):
    fake = FakeQuizLLM()
    app = create_app(
        runtime_factory=lambda: AgentRuntime(
            llm=fake,
            models={"planner": "test", "fast": "test", "judge": "test"},
            tool_context=ToolContext(region="GLOBAL"),
            critic=Critic(llm=FakeJudgeLLM(), model="judge"),
        ),
        student_store=StudentStore(str(tmp_path / "students.db")),
    )
    with TestClient(app) as c:
        resp = c.post(
            "/chat",
            json={
                "message": "Дай задание",
                "session_id": "bandit-s1",
                "student_id": "stu_bandit",
                "topic": "квадратные уравнения",
                "student_profile": {
                    "current_knowledge_level": 0.6,
                    "learning_style": "visual",
                    "fatigue_level": 0.1,
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["envelope"]["type"] == "quiz"
        assert body["envelope"]["difficulty"] == "medium"
        # совет ушёл модели в контексте
        joined = "\n".join(
            m.get("content", "") for m in fake.last_messages if m.get("role") == "system"
        )
        assert "Адаптивный совет" in joined
        # сессия запомнила сыгранную руку
        session = c.app.state.sessions.get("bandit-s1")
        assert session.bandit_arm in {0, 1, 2}
        assert session.bandit_topic == "квадратные уравнения"
        assert len(session.bandit_features) == 4


# --- LinUCB (Этап 5): обновление бандита наградой в ветке evaluation ---


class FakeQuizThenEvalLLM(FakeQuizLLM):
    """Ход 1: quiz. Ход 2+: evaluation correct=true."""

    def __init__(self):
        super().__init__()
        self.turns = 0

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        self.last_messages = list(messages)
        self.turns += 1
        if self.turns == 1:
            return LLMResponse(
                content=(
                    '{"type": "quiz", "text": "Чему равен x в x^2=16?", '
                    '"payload": {"answer_type": "single", "options": ["4", "-4", "4 и -4"], '
                    '"_correct_answer": "4 и -4"}, "difficulty": "medium"}'
                ),
                model=model,
                usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
                finish_reason="stop",
            )
        return LLMResponse(
            content=(
                '{"type": "evaluation", "text": "Верно!", '
                '"payload": {"correct": true, "feedback": "ok", "knowledge_delta": 0.2}, '
                '"difficulty": "medium"}'
            ),
            model=model,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
            finish_reason="stop",
        )


def test_bandit_update_on_evaluation(tmp_path):
    store = StudentStore(str(tmp_path / "students.db"))
    fake = FakeQuizThenEvalLLM()
    app = create_app(
        runtime_factory=lambda: AgentRuntime(
            llm=fake,
            models={"planner": "test", "fast": "test", "judge": "test"},
            tool_context=ToolContext(region="GLOBAL"),
            critic=Critic(llm=FakeJudgeLLM(), model="judge"),
        ),
        student_store=store,
    )
    with TestClient(app) as c:
        body = {
            "session_id": "bandit-s2",
            "student_id": "stu_bandit",
            "topic": "квадратные уравнения",
            "student_profile": {
                "current_knowledge_level": 0.6,
                "learning_style": "visual",
                "fatigue_level": 0.1,
            },
        }
        r1 = c.post("/chat", json={**body, "message": "Дай задание"})
        assert r1.json()["envelope"]["type"] == "quiz"
        session = c.app.state.sessions.get("bandit-s2")
        played_arm = session.bandit_arm
        assert played_arm is not None
        before = store.get_topic_bandit("stu_bandit", "квадратные уравнения")["arms"][played_arm][
            "n"
        ]

        r2 = c.post("/chat", json={**body, "message": "Ответ: 4 и -4"})
        assert r2.status_code == 200
        assert r2.json()["envelope"]["type"] == "evaluation"
        assert session.bandit_arm is None  # сброшено после обновления

        after = store.get_topic_bandit("stu_bandit", "квадратные уравнения")["arms"][played_arm][
            "n"
        ]
        assert after == before + 1
    store.close()


# --- Несколько JSON-конвертов в одном ответе модели (theory + practice) ---


class FakeMultiEnvelopeLLM(FakeTutorLLM):
    """Планировщик: возвращает ДВА конверта подряд (theory + practice)."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024, tools=None, tool_choice=None
    ):
        self.calls += 1
        return LLMResponse(
            content=(
                '{"type": "theory", "text": "Теория физических явлений.", '
                '"payload": {"topic": "Физические явления"}, "difficulty": "easy"}\n\n'
                '{"type": "practice", "text": "Приведите пример явления.", '
                '"payload": {"task_ref": "daily"}, "difficulty": "medium"}'
            ),
            model=model,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


def test_chat_multi_envelope_becomes_separate_blocks(tmp_path):
    """Сырой JSON-«словарь» не должен попадать в чат: каждый конверт — блок."""
    app = create_app(
        runtime_factory=lambda: AgentRuntime(
            llm=FakeMultiEnvelopeLLM(),
            models={"planner": "test", "fast": "test", "judge": "test"},
            tool_context=ToolContext(region="GLOBAL"),
            critic=Critic(llm=FakeJudgeLLM(), model="judge"),
        ),
        student_store=StudentStore(str(tmp_path / "students.db")),
    )
    with TestClient(app) as c:
        resp = c.post(
            "/chat",
            json={
                "message": "Объясни и дай задание",
                "session_id": "multi-1",
                "student_id": "stu_multi",
                "topic": "физические явления",
                "subject": "физика",
                "grade": "7 класс",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "Приведите пример явления."
        assert body["envelope"]["type"] == "practice"
        # в истории оба блока отдельными сообщениями (не сырой JSON)
        hist = c.get("/chat/history/multi-1").json()["messages"]
        assistant = [m for m in hist if m.get("role") == "assistant"]
        assert [m.get("kind") for m in assistant] == ["theory", "practice"]
        assert assistant[0]["content"] == "Теория физических явлений."
        assert assistant[1]["content"] == "Приведите пример явления."
        assert "{\"type\"" not in assistant[0]["content"]
        assert "{\"type\"" not in assistant[1]["content"]


# --- Quiz.reject: восстановление вместо тупика (2026-09-06) -----------------

_BAD_QUIZ = (
    '{"type": "quiz", "text": "Мерой инертности тела является его масса.", '
    '"payload": {"answer_type": "single", "options": ["Сила", "Масса", "Энергия", '
    '"Импульс"], "_correct_answer": "Масса"}, "difficulty": "medium"}'
)
_GOOD_QUIZ = (
    '{"type": "quiz", "text": "Что является мерой инертности тела?", '
    '"payload": {"answer_type": "single", "options": ["Сила", "Масса", "Энергия", '
    '"Импульс"], "_correct_answer": "Масса"}, "difficulty": "medium"}'
)
_PRACTICE = (
    '{"type": "practice", "text": "Приведите пример тела, сохраняющего скорость.", '
    '"payload": {"task_ref": "daily"}, "difficulty": "medium"}'
)


class QueueQuizLLM(LLMClient):
    """Планировщик с очередью ответов; при переполнении повторяет последний."""

    def __init__(self, *contents: str):
        self.queue = list(contents)
        self.calls = 0
        self.last_messages: list[dict] = []

    async def chat(
        self, messages, model, temperature=0.7, max_tokens=1024,
        tools=None, tool_choice=None
    ):
        self.calls += 1
        self.last_messages = list(messages)
        content = self.queue[min(self.calls - 1, len(self.queue) - 1)]
        return LLMResponse(
            content=content,
            model=model,
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3),
            finish_reason="stop",
        )

    async def chat_stream(self, *args, **kwargs):
        yield ""


def _recovery_app(tmp_path, fake, monkeypatch):
    """FastAPI app на QueueQuizLLM (без критика) с JSONL во временный файл."""
    monkeypatch.setattr(settings, "log_file", str(tmp_path / "tutor.jsonl"))
    app = create_app(
        runtime_factory=lambda: AgentRuntime(
            llm=fake,
            models={"planner": "test", "fast": "test", "judge": "test"},
            tool_context=ToolContext(region="GLOBAL"),
            critic=None,
        ),
        student_store=StudentStore(str(tmp_path / "students.db")),
    )
    app.state.rag_engine = None
    app.state.provisioner = None
    return app


def _recover_post(client, message, session="recover-s1", student="stu_rec",
                  topic="инерция"):
    resp = client.post("/chat", json={
        "message": message, "session_id": session, "student_id": student,
        "topic": topic, "subject": "физика",
    })
    assert resp.status_code == 200
    return resp.json()


def test_quiz_rejected_then_regenerated_once(tmp_path, monkeypatch):
    """Невалидный quiz -> ровно одна регенерация; ученик получает валидный quiz."""
    fake = QueueQuizLLM(_BAD_QUIZ, _GOOD_QUIZ)
    app = _recovery_app(tmp_path, fake, monkeypatch)
    with TestClient(app) as c:
        body = _recover_post(c, "давай квиз")
        session = c.app.state.sessions.get("recover-s1")
        log = (tmp_path / "tutor.jsonl").read_text(encoding="utf-8")
    assert body["envelope"]["type"] == "quiz"
    assert "Что является мерой инертности тела?" in body["reply"]
    assert "контроль качества" not in body["reply"]
    assert fake.calls == 2  # 1 planner + ровно 1 регенерация
    joined = "\n".join(m.get("content", "") for m in fake.last_messages)
    assert "Твой предыдущий quiz-конверт отклонён" in joined
    assert session.last_quiz is not None
    assert session.quiz_reject_streak == 0
    assert session.quiz_blocked is False
    assert log.count('"event": "quiz.reject"') == 1
    regen_lines = [ln for ln in log.splitlines() if '"quiz.regen"' in ln]
    assert len(regen_lines) == 1 and '"status": "ok"' in regen_lines[0]


def test_quiz_regen_failure_shows_neutral_fallback_and_streak(tmp_path, monkeypatch):
    """Регенерация тоже невалидна -> нейтральный retry-текст, streak=1."""
    fake = QueueQuizLLM(_BAD_QUIZ, _BAD_QUIZ)
    app = _recovery_app(tmp_path, fake, monkeypatch)
    with TestClient(app) as c:
        body = _recover_post(c, "давай квиз")
        session = c.app.state.sessions.get("recover-s1")
        log = (tmp_path / "tutor.jsonl").read_text(encoding="utf-8")
    assert body["envelope"]["type"] == "theory"
    assert body["reply"] == QUIZ_REJECT_RETRY_TEXT
    assert "другой вопрос" not in body["reply"]
    assert "сформулирую его заново" not in body["reply"]
    assert fake.calls == 2
    assert session.quiz_reject_streak == 1
    assert session.quiz_blocked is False
    assert log.count('"event": "quiz.reject"') == 2  # оригинал + регенерация
    regen_lines = [ln for ln in log.splitlines() if '"quiz.regen"' in ln]
    assert len(regen_lines) == 1 and '"status": "failed"' in regen_lines[0]


def test_quiz_blocked_after_two_failures_and_practice_unblocks(
    tmp_path, monkeypatch
):
    """Две неудачи -> блок; 3-й quiz не выдаётся; корректная practice снимает блок."""
    fake = QueueQuizLLM(
        _BAD_QUIZ, _BAD_QUIZ,  # ход 1: planner + регенерация
        _BAD_QUIZ, _BAD_QUIZ,  # ход 2: planner + регенерация
        _BAD_QUIZ,             # ход 3: только planner (блок, без регенерации)
        _PRACTICE,             # ход 4: practice снимает блок
    )
    app = _recovery_app(tmp_path, fake, monkeypatch)
    with TestClient(app) as c:
        body1 = _recover_post(c, "давай квиз")
        session = c.app.state.sessions.get("recover-s1")
        assert session.quiz_reject_streak == 1
        assert session.quiz_blocked is False
        assert body1["reply"] == QUIZ_REJECT_RETRY_TEXT

        body2 = _recover_post(c, "другой вопрос")
        session = c.app.state.sessions.get("recover-s1")
        assert session.quiz_reject_streak == 2
        assert session.quiz_blocked is True
        assert body2["reply"] == QUIZ_REJECT_RETRY_TEXT

        body3 = _recover_post(c, "другой вопрос")
        session = c.app.state.sessions.get("recover-s1")
        assert session.quiz_reject_streak == 3
        assert session.quiz_blocked is True
        assert body3["envelope"]["type"] == "theory"
        assert body3["reply"] == QUIZ_BLOCKED_TEXT
        assert "другой вопрос" not in body3["reply"]
        joined = "\n".join(m.get("content", "") for m in fake.last_messages)
        assert "НЕ выдавай quiz" in joined

        body4 = _recover_post(c, "дай задание")
        session = c.app.state.sessions.get("recover-s1")
        assert body4["envelope"]["type"] == "practice"
        assert session.quiz_reject_streak == 0
        assert session.quiz_blocked is False
        assert fake.calls == 6
    log = (tmp_path / "tutor.jsonl").read_text(encoding="utf-8")
    assert log.count('"event": "quiz.regen"') == 2  # только ходы 1 и 2


def test_valid_quiz_happy_path_unchanged(tmp_path, monkeypatch):
    """Валидный quiz: без регенерации, last_quiz выставлен, streak не растёт."""
    fake = QueueQuizLLM(_GOOD_QUIZ)
    app = _recovery_app(tmp_path, fake, monkeypatch)
    with TestClient(app) as c:
        body = _recover_post(c, "дай задание", session="recover-d1",
                             student="stu_rec", topic="инерция")
        session = c.app.state.sessions.get("recover-d1")
        log = (tmp_path / "tutor.jsonl").read_text(encoding="utf-8")
    assert body["envelope"]["type"] == "quiz"
    assert fake.calls == 1
    assert session.last_quiz is not None
    assert session.quiz_reject_streak == 0
    assert session.quiz_blocked is False
    assert '"event": "quiz.regen"' not in log


async def test_tool_quiz_card_overrides_distorted_model_envelope():
    """Авторитетная карточка generate_quiz заменяет искажённый quiz-конверт модели."""
    from src.api.server import _resolve_tool_quiz
    from src.models.schemas import AgentGraphState, ContentEnvelope

    card = {
        "question": "Что является мерой инертности тела?",
        "answer_type": "single",
        "options": ["Масса", "Скорость", "Сила", "Ускорение"],
        "_correct_answer": "Масса",
        "difficulty": "medium",
    }
    state = AgentGraphState(
        messages=[],
        tools_result={
            "generate_quiz": json.dumps({"status": "ok", "data": card}, ensure_ascii=False)
        },
    )
    # Модель в финальном конверте превратила вопрос в утверждение с ответом.
    distorted = ContentEnvelope(
        type="quiz",
        text="Мерой инертности тела является его масса.",
        payload={
            "answer_type": "single",
            "options": ["Масса", "Скорость", "Сила", "Ускорение"],
            "_correct_answer": "Масса",
        },
    )

    class _Dummy:
        tool_context = None

    out = await _resolve_tool_quiz(state, _Dummy(), "trc_x", "инерция", [distorted])
    assert len(out) == 1
    assert out[0].type.value == "quiz"
    assert out[0].text == "Что является мерой инертности тела?"
    assert out[0].payload["_correct_answer"] == "Масса"


async def test_tool_quiz_card_appended_when_model_missed_quiz():
    """Модель вызвала generate_quiz, но не оформила quiz-конверт — карточка добавляется."""
    from src.api.server import _resolve_tool_quiz
    from src.models.schemas import AgentGraphState, ContentEnvelope

    card = {
        "question": "Какое слово является сказуемым в предложении «Солнце светит»?",
        "answer_type": "single",
        "options": ["светит", "Солнце", "ярко", "в предложении"],
        "_correct_answer": "светит",
        "difficulty": "easy",
    }
    state = AgentGraphState(
        messages=[],
        tools_result={
            "generate_quiz": json.dumps({"status": "ok", "data": card}, ensure_ascii=False)
        },
    )

    class _Dummy:
        tool_context = None

    out = await _resolve_tool_quiz(
        state, _Dummy(), "trc_y", "тема",
        [ContentEnvelope(type="theory", text="Теория готова.")],
    )
    assert [e.type.value for e in out] == ["theory", "quiz"]
    assert out[1].payload["_correct_answer"] == "светит"


