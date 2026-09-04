"""Потокобезопасное in-memory хранилище многоходовых чат-сессий.

Каждая сессия держит историю сообщений, профиль и владельца (student_id) —
HTTP-слой может передавать контекст разговора агенту между запросами по
session_id. Сессии живут в памяти и вытесняются по TTL бездействия (лениво при
доступе и массово в prune_expired). Ничего не пишем на диск: чистая in-memory
структура.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

# Максимум сообщений, хранимых в истории сессии (append_message обрезает хвост).
MAX_MESSAGES = 100

_VALID_ROLES = frozenset({"user", "assistant"})


@dataclass
class ChatSession:
    """Одна многоходовая чат-сессия."""

    session_id: str
    student_id: str = ""
    topic: str = ""
    subject: str = ""
    grade: str = ""
    last_quiz: dict | None = None
    last_gate_topic: str = ""
    review_requested: bool = False
    review_active: bool = False
    review_cards: list = field(default_factory=list)
    review_index: int = 0
    review_correct: int = 0
    review_reviewed: int = 0
    messages: list[dict] = field(default_factory=list)
    student_profile: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


def _is_identical_user_message(message: dict | None, content: str) -> bool:
    """True, если message — user-сообщение с ровно этим содержимым."""
    return bool(message) and message.get("role") == "user" and message.get("content") == content


def _content_len(message: dict) -> int:
    """Длина текста сообщения (0, если content отсутствует/не строка)."""
    content = message.get("content")
    return len(content) if isinstance(content, str) else 0


def _newest_user_index(messages: list[dict]) -> int | None:
    """Индекс последнего user-сообщения (None, если их нет)."""
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            return i
    return None


class SessionStore:
    """Хранилище чат-сессий с TTL бездействия.

    Все публичные методы защищены одним threading.Lock — асинхронный сервер
    вызывает их из разных потоков безопасно. Сессия считается истёкшей, если
    ``time.time() - updated_at > ttl_sec``; доступ (get/list/to_llm_context)
    обновляет updated_at, продлевая жизнь сессии.
    """

    def __init__(self, ttl_sec: float = 3600.0) -> None:
        self._ttl_sec = float(ttl_sec)
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()

    # --- внутренние помощники (вызываются только под self._lock) -----------

    def _new_session(
        self,
        session_id: str,
        student_profile: dict | None,
        now: float,
        student_id: str = "",
        topic: str = "",
        subject: str = "",
        grade: str = "",
    ) -> ChatSession:
        """Создаёт ChatSession с копией профиля (внешние мутации не влияют)."""
        return ChatSession(
            session_id=session_id,
            student_id=student_id,
            topic=topic,
            subject=subject,
            grade=grade,
            created_at=now,
            updated_at=now,
            student_profile=dict(student_profile) if student_profile else {},
        )

    def _generate_sid_locked(self) -> str:
        """Генерирует уникальный session_id вида ``ses_<12 hex>``."""
        while True:
            sid = f"ses_{uuid.uuid4().hex[:12]}"
            if sid not in self._sessions:
                return sid

    def _live_session_locked(self, session_id: str, now: float) -> ChatSession | None:
        """Возвращает живую сессию либо None; истёкшую удаляет лениво."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if now - session.updated_at > self._ttl_sec:
            del self._sessions[session_id]
            return None
        return session

    # --- публичный API ------------------------------------------------------

    def create(
        self,
        student_profile: dict | None = None,
        student_id: str = "",
        topic: str = "",
        subject: str = "",
        grade: str = "",
    ) -> ChatSession:
        """Создаёт новую сессию со сгенерированным id и возвращает её.

        student_id/topic фиксируются на сессии (владелец и тема обсуждения).
        """
        now = time.time()
        with self._lock:
            session = self._new_session(
                self._generate_sid_locked(),
                student_profile,
                now,
                student_id=student_id.strip() if student_id else "",
                topic=topic.strip() if topic else "",
                subject=subject.strip() if subject else "",
                grade=grade.strip() if grade else "",
            )
            self._sessions[session.session_id] = session
            return session

    def get(self, session_id: str) -> ChatSession | None:
        """Возвращает живую сессию или None (неизвестна/истекла). Трогает updated_at."""
        now = time.time()
        with self._lock:
            session = self._live_session_locked(session_id, now)
            if session is not None:
                session.updated_at = now
            return session

    def get_or_create(
        self,
        session_id: str | None = None,
        student_profile: dict | None = None,
        student_id: str = "",
        topic: str = "",
        subject: str = "",
        grade: str = "",
    ) -> ChatSession:
        """Возвращает живую сессию или создаёт новую.

        Если session_id задан и существует — возвращает её (touch), при этом
        «ничейная» сессия (student_id == "") привязывается к первому запросившему
        её непустому student_id, а topic/subject/grade обновляются на свежие, если
        они заданы.

        Правило владения: если существующая сессия принадлежит другому ученику
        (её student_id непустой и отличается от запрошенного непустого), история
        ему не отдаётся — создаётся НОВАЯ сессия со свежим сгенерированным id.
        Пустой запрошенный student_id сохраняет старое поведение (возврат
        существующей). Если id задан, но неизвестен/истёк — создаёт сессию с этим
        id; пустой/whitespace id или None — генерирует новый id.
        """
        now = time.time()
        owner = student_id.strip() if student_id else ""
        topic_s = topic.strip() if topic else ""
        subject_s = subject.strip() if subject else ""
        grade_s = grade.strip() if grade else ""
        sid = session_id.strip() if session_id and session_id.strip() else None
        with self._lock:
            if sid is not None:
                existing = self._sessions.get(sid)
                if existing is not None:
                    expired = now - existing.updated_at > self._ttl_sec
                    foreign = (
                        not expired
                        and existing.student_id
                        and owner
                        and existing.student_id != owner
                    )
                    if foreign:
                        # Чужая живая сессия: не отдаём историю, заводим новую.
                        sid = None
                    elif expired:
                        del self._sessions[sid]
                    else:
                        existing.updated_at = now
                        if owner and not existing.student_id:
                            existing.student_id = owner
                        if topic_s:
                            existing.topic = topic_s
                        if subject_s:
                            existing.subject = subject_s
                        if grade_s:
                            existing.grade = grade_s
                        return existing
            if sid is None:
                sid = self._generate_sid_locked()
            session = self._new_session(sid, student_profile, now, owner, topic_s,
                                        subject_s, grade_s)
            self._sessions[sid] = session
            return session

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        meta: dict | None = None,
    ) -> None:
        """Добавляет сообщение в историю; meta (kind/envelope/adaptive) кладётся рядом."""
        if role not in _VALID_ROLES:
            raise ValueError(f"Недопустимая роль: {role!r}; ожидается user или assistant")
        now = time.time()
        with self._lock:
            session = self._live_session_locked(session_id, now)
            if session is None:
                raise ValueError(f"Сессия {session_id!r} не найдена или истекла")
            record: dict = {"role": role, "content": content}
            if meta:
                record.update(meta)
            session.messages.append(record)
            if len(session.messages) > MAX_MESSAGES:
                del session.messages[:-MAX_MESSAGES]
            session.updated_at = now

    def consecutive_assistant_kind(self, session_id: str, kind: str) -> int:
        """Число подряд идущих assistant-сообщений с meta.kind == kind (с конца)."""
        now = time.time()
        with self._lock:
            session = self._live_session_locked(session_id, now)
            if session is None:
                return 0
            session.updated_at = now
            messages = list(session.messages)
        count = 0
        for message in reversed(messages):
            if message.get("role") != "assistant":
                break
            if message.get("kind") == kind:
                count += 1
            else:
                break
        return count

    def list_messages(self, session_id: str) -> list[dict]:
        """Возвращает копию истории сообщений ([] для неизвестной/истекшей)."""
        now = time.time()
        with self._lock:
            session = self._live_session_locked(session_id, now)
            if session is None:
                return []
            session.updated_at = now
            return [dict(message) for message in session.messages]

    def delete(self, session_id: str) -> bool:
        """Удаляет сессию; True, если она существовала."""
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def prune_expired(self, now: float | None = None) -> int:
        """Удаляет истёкшие сессии и возвращает число удалённых."""
        now = time.time() if now is None else now
        with self._lock:
            expired = [
                sid for sid, session in self._sessions.items()
                if now - session.updated_at > self._ttl_sec
            ]
            for sid in expired:
                del self._sessions[sid]
            return len(expired)

    def active_count(self) -> int:
        """Число живых (не истёкших) сессий в хранилище."""
        now = time.time()
        with self._lock:
            return sum(
                1 for session in self._sessions.values()
                if now - session.updated_at <= self._ttl_sec
            )

    def to_llm_context(
        self,
        session_id: str,
        incoming_user_message: str | None = None,
        max_messages: int = 20,
        max_chars: int = 16000,
    ) -> list[dict]:
        """Собирает список сообщений для LLM из истории сессии.

        Правила:
        - берём последние max_messages записей истории;
        - отбрасываем старые, пока суммарный объём текста > max_chars
          (минимум — последнее user-сообщение сохраняется всегда);
        - если задан incoming_user_message и последняя запись истории — не
          идентичное user-сообщение, добавляем его в конец;
        - в результат попадают только поля role/content (meta обрезается); это
          копии, история хранилища не меняется.
        """
        now = time.time()
        with self._lock:
            session = self._live_session_locked(session_id, now)
            if session is None:
                history: list[dict] = []
            else:
                session.updated_at = now
                history = [dict(message) for message in session.messages]

        window = history[-max_messages:] if max_messages > 0 else []
        context = [{"role": m["role"], "content": m["content"]} for m in window]

        if incoming_user_message is not None:
            last_entry = history[-1] if history else None
            if not _is_identical_user_message(last_entry, incoming_user_message):
                context.append({"role": "user", "content": incoming_user_message})

        total = sum(_content_len(message) for message in context)
        while total > max_chars and len(context) > 1:
            newest_user = _newest_user_index(context)
            if newest_user == 0:
                break
            dropped = context.pop(0)
            total -= _content_len(dropped)
        return context
