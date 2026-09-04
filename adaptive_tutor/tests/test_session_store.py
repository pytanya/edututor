"""Тесты SessionStore: создание, TTL-вытеснение, история, контекст для LLM."""

import time

import pytest

from src.api import session_store
from src.api.session_store import SessionStore


def test_create_returns_unique_ids() -> None:
    """create генерирует уникальные id вида ses_<12 hex>."""
    store = SessionStore()
    first = store.create()
    second = store.create()
    assert first.session_id != second.session_id
    assert first.session_id.startswith("ses_")
    assert len(first.session_id) == 16
    assert store.active_count() == 2


def test_get_or_create_reuses_existing() -> None:
    """get_or_create по известному id возвращает ту же сессию."""
    store = SessionStore()
    created = store.create(student_profile={"level": 0.5})
    reused = store.get_or_create(created.session_id)
    assert reused is created
    assert store.active_count() == 1


def test_get_or_create_blank_creates_new() -> None:
    """Пустой/None id ведёт к созданию новой сессии со сгенерированным id."""
    store = SessionStore()
    for session_id in (None, "", "   "):
        session = store.get_or_create(session_id)
        assert session.session_id.startswith("ses_")
    assert store.active_count() == 3


def test_get_on_unknown_returns_none() -> None:
    """get по несуществующему id возвращает None."""
    assert SessionStore().get("ses_unknown") is None


def test_get_or_create_unknown_id_uses_that_id() -> None:
    """get_or_create по несуществующему непустому id создаёт сессию с ним."""
    store = SessionStore()
    session = store.get_or_create("client-42", student_profile={"style": "visual"})
    assert session.session_id == "client-42"
    assert session.student_profile == {"style": "visual"}


def test_append_and_list_messages_roundtrip() -> None:
    """append_message + list_messages сохраняют пары user/assistant."""
    store = SessionStore()
    session = store.create()
    store.append_message(session.session_id, "user", "Привет")
    store.append_message(session.session_id, "assistant", "Здравствуй!")
    assert store.list_messages(session.session_id) == [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуй!"},
    ]


def test_list_messages_returns_copy() -> None:
    """Мутация возвращённого списка не затрагивает хранилище."""
    store = SessionStore()
    session = store.create()
    store.append_message(session.session_id, "user", "вопрос")
    returned = store.list_messages(session.session_id)
    returned[0]["content"] = "изменено"
    returned.append({"role": "assistant", "content": "фейк"})
    assert store.list_messages(session.session_id) == [
        {"role": "user", "content": "вопрос"}
    ]


def test_append_rejects_bad_role() -> None:
    """Недопустимая роль в append_message вызывает ValueError."""
    store = SessionStore()
    session = store.create()
    with pytest.raises(ValueError):
        store.append_message(session.session_id, "system", "блок")
    with pytest.raises(ValueError):
        store.append_message(session.session_id, "user ", "пробел")


def test_max_messages_trim_keeps_most_recent(monkeypatch) -> None:
    """append_message обрезает историю до MAX_MESSAGES, сохраняя хвост."""
    monkeypatch.setattr(session_store, "MAX_MESSAGES", 5)
    store = SessionStore()
    session = store.create()
    for i in range(9):
        role = "user" if i % 2 == 0 else "assistant"
        store.append_message(session.session_id, role, f"msg-{i}")
    messages = store.list_messages(session.session_id)
    assert len(messages) == 5
    assert [message["content"] for message in messages] == [
        f"msg-{i}" for i in range(4, 9)
    ]


def test_to_llm_context_messages_cap() -> None:
    """to_llm_context берёт только последние max_messages записей."""
    store = SessionStore()
    session = store.create()
    for i in range(30):
        role = "user" if i % 2 == 0 else "assistant"
        store.append_message(session.session_id, role, f"m{i}")
    context = store.to_llm_context(session.session_id, max_messages=10, max_chars=10**6)
    assert len(context) == 10
    assert context[0]["content"] == "m20"
    assert context[-1]["content"] == "m29"


def test_to_llm_context_chars_cap_keeps_newest_user() -> None:
    """to_llm_context обрезает старые сообщения, сохраняя последнее user."""
    store = SessionStore()
    session = store.create()
    for content in ("A" * 200, "B" * 200, "C" * 200):
        store.append_message(session.session_id, "assistant", content)
        store.append_message(session.session_id, "user", "e")
    store.append_message(session.session_id, "user", "hi")
    context = store.to_llm_context(session.session_id, max_messages=100, max_chars=1)
    assert context == [{"role": "user", "content": "hi"}]


def test_to_llm_context_appends_incoming_user_message() -> None:
    """incoming_user_message добавляется в конец, история не меняется."""
    store = SessionStore()
    session = store.create()
    store.append_message(session.session_id, "user", "Привет")
    store.append_message(session.session_id, "assistant", "Здравствуй")
    context = store.to_llm_context(session.session_id, incoming_user_message="Как дела?")
    assert len(context) == 3
    assert context[-1] == {"role": "user", "content": "Как дела?"}
    assert store.list_messages(session.session_id) == [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Здравствуй"},
    ]


def test_to_llm_context_does_not_duplicate_identical_last_user() -> None:
    """Если последнее user-сообщение истории == incoming — дубль не добавляем."""
    store = SessionStore()
    session = store.create()
    store.append_message(session.session_id, "assistant", "Продолжим")
    store.append_message(session.session_id, "user", "Давай")
    context = store.to_llm_context(session.session_id, incoming_user_message="Давай")
    assert [message for message in context if message["content"] == "Давай"] == [
        {"role": "user", "content": "Давай"}
    ]


def test_delete_returns_true_then_false() -> None:
    """delete возвращает True для существующей сессии и False иначе."""
    store = SessionStore()
    session = store.create()
    assert store.delete(session.session_id) is True
    assert store.get(session.session_id) is None
    assert store.delete(session.session_id) is False
    assert store.delete("ses_missing") is False


def test_prune_expired_removes_only_expired() -> None:
    """prune_expired удаляет только истёкшие сессии и возвращает их число."""
    store = SessionStore(ttl_sec=100.0)
    now = time.time()
    fresh = store.create()
    expired_a = store.create()
    expired_b = store.create()
    expired_a.updated_at = now - 101.0
    expired_b.updated_at = now - 500.0
    assert store.prune_expired() == 2
    assert store.get(fresh.session_id) is not None
    assert store.get(expired_a.session_id) is None
    assert store.get(expired_b.session_id) is None
    assert store.active_count() == 1


def test_prune_expired_respects_explicit_now() -> None:
    """prune_expired(now) работает с переданным моментом времени."""
    store = SessionStore(ttl_sec=100.0)
    base = time.time()
    first = store.create()
    first.updated_at = base
    assert store.prune_expired(now=base + 50.0) == 0
    assert store.prune_expired(now=base + 200.0) == 1


def test_get_expired_returns_none_and_pops() -> None:
    """get на истёкшей сессии возвращает None и удаляет её лениво."""
    store = SessionStore(ttl_sec=100.0)
    session = store.create()
    session.updated_at = time.time() - 200.0
    assert store.get(session.session_id) is None
    assert store.active_count() == 0


def test_list_messages_expired_returns_empty() -> None:
    """list_messages на истёкшей сессии возвращает []."""
    store = SessionStore(ttl_sec=100.0)
    session = store.create()
    store.append_message(session.session_id, "user", "старый вопрос")
    session.updated_at = time.time() - 200.0
    assert store.list_messages(session.session_id) == []
    assert store.active_count() == 0


def test_get_or_create_expired_creates_fresh_with_same_id() -> None:
    """get_or_create на истёкшей сессии создаёт новую с тем же id."""
    store = SessionStore(ttl_sec=100.0)
    session = store.create()
    old_id = session.session_id
    session.updated_at = time.time() - 200.0
    fresh = store.get_or_create(old_id, student_profile={"level": 0.9})
    assert fresh.session_id == old_id
    assert fresh is not session
    assert store.list_messages(old_id) == []
    assert store.active_count() == 1


def test_active_count_excludes_expired() -> None:
    """active_count учитывает только живые сессии."""
    store = SessionStore(ttl_sec=100.0)
    fresh = store.create()
    expired = store.create()
    expired.updated_at = time.time() - 200.0
    assert store.active_count() == 1
    assert store.get(fresh.session_id) is not None


def test_get_touches_updated_at() -> None:
    """Успешный get продлевает жизнь сессии (обновляет updated_at)."""
    store = SessionStore(ttl_sec=100.0)
    session = store.create()
    session.updated_at = time.time() - 90.0
    assert store.get(session.session_id) is not None
    assert session.updated_at >= time.time() - 1.0


def test_append_message_with_meta_roundtrip() -> None:
    """meta (kind/envelope) сохраняется рядом с role/content в записи."""
    store = SessionStore()
    session = store.create()
    store.append_message(session.session_id, "user", "hi")
    store.append_message(
        session.session_id,
        "assistant",
        "Привет",
        meta={"kind": "theory", "envelope": {"type": "theory"}},
    )
    messages = store.list_messages(session.session_id)
    assert messages[1]["kind"] == "theory"
    assert messages[1]["envelope"]["type"] == "theory"


def test_to_llm_context_strips_meta() -> None:
    """to_llm_context возвращает только role/content, обрезая meta."""
    store = SessionStore()
    session = store.create()
    store.append_message(session.session_id, "user", "hi")
    store.append_message(
        session.session_id, "assistant", "Привет", meta={"kind": "theory", "envelope": {}}
    )
    ctx = store.to_llm_context(session.session_id)
    assert ctx == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Привет"}]


def test_consecutive_assistant_kind() -> None:
    """Счётчик подряд идущих assistant-сообщений заданного kind с конца."""
    store = SessionStore()
    session = store.create()
    store.append_message(session.session_id, "user", "hi")
    store.append_message(session.session_id, "assistant", "h1", meta={"kind": "hint"})
    store.append_message(session.session_id, "assistant", "h2", meta={"kind": "hint"})
    assert store.consecutive_assistant_kind(session.session_id, "hint") == 2
    store.append_message(session.session_id, "assistant", "т", meta={"kind": "theory"})
    assert store.consecutive_assistant_kind(session.session_id, "hint") == 0


# --- Привязка сессия <-> ученик ---

def test_create_stores_student_id_and_topic() -> None:
    """create хранит student_id и topic на сессии."""
    store = SessionStore()
    session = store.create(student_id="stu_1", topic="интегралы")
    assert session.student_id == "stu_1"
    assert session.topic == "интегралы"


def test_get_or_create_returns_existing_for_same_student() -> None:
    """Повторный заход того же ученика возвращает ту же сессию и свежую тему."""
    store = SessionStore()
    created = store.get_or_create("ses_own", student_id="stu_1", topic="тема 1")
    reused = store.get_or_create("ses_own", student_id="stu_1", topic="тема 2")
    assert reused is created
    assert reused.student_id == "stu_1"
    assert reused.topic == "тема 2"
    assert store.active_count() == 1


def test_get_or_create_different_student_does_not_expose_history() -> None:
    """Чужой непустой student_id на занятой сессии даёт НОВУЮ сессию без истории."""
    store = SessionStore()
    first = store.get_or_create("ses_shared", student_id="stu_A", topic="секрет A")
    store.append_message(first.session_id, "user", "секрет A")
    store.append_message(first.session_id, "assistant", "ответ A")
    second = store.get_or_create("ses_shared", student_id="stu_B", topic="тема B")
    assert second is not first
    assert second.session_id != "ses_shared"
    assert second.student_id == "stu_B"
    assert second.topic == "тема B"
    assert store.list_messages(second.session_id) == []
    assert store.list_messages("ses_shared")[0]["content"] == "секрет A"
    assert store.active_count() == 2


def test_get_or_create_empty_student_keeps_existing() -> None:
    """Пустой запрошенный student_id возвращает существующую сессию как раньше."""
    store = SessionStore()
    created = store.get_or_create("ses_anon", student_id="stu_A")
    reused = store.get_or_create("ses_anon", student_id="")
    assert reused is created
    assert reused.student_id == "stu_A"


def test_get_or_create_claims_unowned_session_for_first_student() -> None:
    """«Ничейная» сессия привязывается к первому непустому student_id."""
    store = SessionStore()
    anon = store.get_or_create("ses_open", student_profile={"level": 0.5})
    assert anon.student_id == ""
    claimed = store.get_or_create("ses_open", student_id="stu_1")
    assert claimed is anon
    assert claimed.student_id == "stu_1"
    different = store.get_or_create("ses_open", student_id="stu_2")
    assert different is not claimed
    assert different.session_id != "ses_open"


def test_session_subject_grade_roundtrip() -> None:
    """create хранит subject/grade на сессии."""
    store = SessionStore()
    s = store.create(student_profile={}, student_id="stu_1", topic="t",
                     subject="физика", grade="7")
    assert s.subject == "физика"
    assert s.grade == "7"


def test_get_or_create_updates_subject() -> None:
    """Повторный заход с новым subject обновляет его на существующей сессии."""
    store = SessionStore()
    s = store.get_or_create(student_id="stu_1", topic="t1", subject="физика")
    s2 = store.get_or_create(session_id=s.session_id, student_id="stu_1",
                             topic="t2", subject="математика")
    assert s2 is s
    assert s2.subject == "математика"
    assert s.last_quiz is None
    assert s.review_active is False
