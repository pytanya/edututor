"""SQLite-хранилище профилей учеников: студенты, темы, сессии.

Использует один connection + threading.Lock. Ничего не пишем в логи —
store не должен ронять чат: вызывающие оборачивают в try/except.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any

from ..review.sm2 import apply_sm2, card_id_for, is_due, now_iso
from .answer_record import AnswerRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
  student_id TEXT PRIMARY KEY,
  name TEXT DEFAULT '',
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS topics (
  student_id TEXT,
  topic TEXT,
  subject TEXT DEFAULT '',
  level REAL DEFAULT 0.5,
  mastery REAL DEFAULT 0.0,
  status TEXT DEFAULT 'not_studied',
  attempts INT DEFAULT 0,
  correct INT DEFAULT 0,
  weak_areas TEXT DEFAULT '[]',
  relations TEXT DEFAULT '{"prerequisite":[],"related":[]}',
  last_seen REAL,
  PRIMARY KEY (student_id, topic)
);
CREATE TABLE IF NOT EXISTS sessions (
  student_id TEXT,
  session_id TEXT,
  topic TEXT,
  subject TEXT DEFAULT '',
  grade TEXT DEFAULT '',
  started_at REAL,
  ended_at REAL,
  UNIQUE (student_id, session_id)
);
CREATE TABLE IF NOT EXISTS review_cards (
  student_id TEXT NOT NULL,
  card_id    TEXT NOT NULL,
  subject    TEXT DEFAULT '',
  topic      TEXT DEFAULT '',
  question   TEXT NOT NULL,
  options    TEXT DEFAULT NULL,
  answer_type TEXT DEFAULT 'open',
  correct_answer TEXT DEFAULT '',
  difficulty TEXT DEFAULT 'medium',
  added_at   TEXT DEFAULT '',
  last_reviewed TEXT DEFAULT '',
  due_at     TEXT DEFAULT '',
  interval_days REAL DEFAULT 1.0,
  ease       REAL DEFAULT 2.5,
  reps       INTEGER DEFAULT 0,
  lapses     INTEGER DEFAULT 0,
  PRIMARY KEY (student_id, card_id)
);
CREATE INDEX IF NOT EXISTS idx_review_due
  ON review_cards (student_id, subject, due_at);
CREATE TABLE IF NOT EXISTS session_records (
  student_id TEXT NOT NULL,
  record_id  TEXT NOT NULL,
  session_id TEXT NOT NULL,
  ts         REAL NOT NULL,
  subject    TEXT DEFAULT '',
  topic      TEXT DEFAULT '',
  question_id TEXT DEFAULT '',
  question   TEXT DEFAULT '',
  options    TEXT DEFAULT NULL,
  answer_type TEXT DEFAULT 'open',
  difficulty TEXT DEFAULT 'medium',
  student_answer TEXT DEFAULT '',
  correct    INTEGER DEFAULT 0,
  feedback   TEXT DEFAULT '',
  score01    REAL DEFAULT 0.0,
  PRIMARY KEY (student_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_records_student ON session_records (student_id, ts);
"""


class StudentStore:
    """Потокобезопасное SQLite-хранилище профилей учеников."""

    def __init__(self, db_path: str):
        if db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate_students()
            self._conn.commit()
        self._migrate_topics()

    def _migrate_topics(self) -> None:
        """Идемпотентная миграция: новые колонки topics/sessions (если отсутствуют).

        Запускается на уже созданной схеме; для старых БД добавляет колонки
        subject/mastery/status/weak_areas/relations (topics) и subject/grade
        (sessions). DDL выполняется голым self._conn под единственным взятием
        блокировки (threading.Lock не реентерабелен).
        """

        def _existing(table: str) -> set[str]:
            cur = self._conn.execute(f"PRAGMA table_info({table})")
            return {row[1] for row in cur.fetchall()}

        with self._lock:
            topics = _existing("topics")
            for col, ddl in {
                "subject": "subject TEXT DEFAULT ''",
                "mastery": "mastery REAL DEFAULT 0.0",
                "status": "status TEXT DEFAULT 'not_studied'",
                "weak_areas": "weak_areas TEXT DEFAULT '[]'",
                "relations": "relations TEXT DEFAULT '{\"prerequisite\":[],\"related\":[]}'",
                "bandit": "bandit TEXT DEFAULT ''",
            }.items():
                if col not in topics:
                    self._conn.execute(f"ALTER TABLE topics ADD COLUMN {ddl}")
            sessions = _existing("sessions")
            for col, ddl in {
                "subject": "subject TEXT DEFAULT ''",
                "grade": "grade TEXT DEFAULT ''",
            }.items():
                if col not in sessions:
                    self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {ddl}")
            self._conn.commit()

    @staticmethod
    def _existing_student_columns(conn: sqlite3.Connection) -> set[str]:
        """Имена колонок таблицы students (для идемпотентной миграции)."""
        rows = conn.execute("PRAGMA table_info(students)").fetchall()
        return {row[1] for row in rows}

    def _migrate_students(self) -> None:
        """Идемпотентно добавляет learner_type/grade (spec §4.1)."""
        columns = self._existing_student_columns(self._conn)
        if "learner_type" not in columns:
            self._conn.execute(
                "ALTER TABLE students ADD COLUMN learner_type TEXT DEFAULT ''"
            )
        if "grade" not in columns:
            self._conn.execute("ALTER TABLE students ADD COLUMN grade TEXT DEFAULT ''")

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def _exec(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def upsert_student(self, student_id: str) -> None:
        now = time.time()
        self._exec(
            "INSERT INTO students (student_id, created_at, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(student_id) DO UPDATE SET updated_at = excluded.updated_at",
            (student_id, now, now),
        )

    def get_student(self, student_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM students WHERE student_id = ?", (student_id,))
        return rows[0] if rows else None

    def list_topics(self, student_id: str) -> list[dict]:
        rows = self._rows(
            "SELECT topic, subject, level, mastery, status, attempts, correct, "
            "weak_areas, relations, last_seen FROM topics WHERE student_id = ? "
            "ORDER BY last_seen DESC",
            (student_id,),
        )
        return [self._topic_payload(r) for r in rows]

    def touch_topic(
        self,
        student_id: str,
        topic: str,
        level_delta: float = 0.0,
        correct: bool | None = None,
    ) -> float:
        from .adaptive import apply_delta

        rows = self._rows(
            "SELECT level, attempts, correct FROM topics WHERE student_id = ? AND topic = ?",
            (student_id, topic),
        )
        if rows:
            level = rows[0]["level"]
            attempts = rows[0]["attempts"] + 1
            correct_count = rows[0]["correct"] + (1 if correct else 0)
        else:
            level = 0.5
            attempts = 1
            correct_count = 1 if correct else 0
        new_level = apply_delta(level, level_delta)
        now = time.time()
        self._exec(
            "INSERT INTO topics (student_id, topic, level, attempts, correct, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(student_id, topic) DO UPDATE SET "
            "level = excluded.level, attempts = excluded.attempts, "
            "correct = excluded.correct, last_seen = excluded.last_seen",
            (student_id, topic, new_level, attempts, correct_count, now),
        )
        self.upsert_student(student_id)
        return new_level

    def register_session(
        self,
        student_id: str,
        session_id: str,
        topic: str,
        subject: str = "",
        grade: str = "",
    ) -> None:
        """Регистрирует ход сессии ученика (upsert).

        Первый вызов фиксирует started_at; повторные — обновляют topic/ended_at
        (последняя активность), сохраняя исходный started_at нетронутым.
        subject/grade пишутся для резолва предмета/класса (слой 2).
        """
        now = time.time()
        self._exec(
            "INSERT INTO sessions (student_id, session_id, topic, subject, grade, "
            "started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(student_id, session_id) DO UPDATE SET "
            "topic = excluded.topic, subject = excluded.subject, grade = excluded.grade, "
            "ended_at = excluded.ended_at",
            (student_id, session_id, topic, subject or "", grade or "", now, now),
        )

    def get_last_session(self, student_id: str) -> dict | None:
        """Последняя сессия ученика (по started_at) или None."""
        rows = self._rows(
            "SELECT * FROM sessions WHERE student_id = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (student_id,),
        )
        return rows[0] if rows else None

    def list_sessions(self, student_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Последние сессии ученика: [{session_id, topic, started_at, ended_at}]."""
        if not student_id or limit <= 0:
            return []
        return self._rows(
            "SELECT session_id, topic, started_at, ended_at FROM sessions "
            "WHERE student_id = ? ORDER BY started_at DESC LIMIT ?",
            (student_id, limit),
        )

    def get_topic_level(self, student_id: str, topic: str) -> float:
        rows = self._rows(
            "SELECT level FROM topics WHERE student_id = ? AND topic = ?",
            (student_id, topic),
        )
        return rows[0]["level"] if rows else 0.5

    @staticmethod
    def _card_from_row(row: dict) -> dict:
        row = dict(row)
        row["options"] = json.loads(row["options"]) if row.get("options") else None
        return row

    def add_review_card(
        self, student_id: str, record: dict, max_cards: int | None = None
    ) -> bool:
        """Добавить/освежить карточку по записи. True — добавлена новая."""
        question = str(record.get("question") or "").strip()
        if not question:
            return False
        cid = card_id_for(question)
        now = now_iso()
        existing = self._rows(
            "SELECT * FROM review_cards WHERE student_id = ? AND card_id = ?",
            (student_id, cid),
        )
        if not existing:
            if max_cards is None:
                from ..config import settings

                max_cards = int(settings.review_bank_max_cards)
            options = record.get("options")
            with self._lock:
                self._conn.execute(
                    "INSERT INTO review_cards (student_id, card_id, subject, topic, "
                    "question, options, answer_type, correct_answer, difficulty, "
                    "added_at, due_at, interval_days, ease) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        student_id, cid,
                        str(record.get("subject") or ""), str(record.get("topic") or ""),
                        question,
                        json.dumps(options, ensure_ascii=False) if options else None,
                        str(record.get("answer_type") or "open"),
                        str(record.get("correct_answer") or ""),
                        str(record.get("difficulty") or "medium"),
                        now, now, 1.0, 2.5,
                    ),
                )
                # Кап банка: удалить самые старые добавленные сверх лимита.
                self._conn.execute(
                    "DELETE FROM review_cards WHERE student_id = ? AND card_id IN ("
                    "  SELECT card_id FROM review_cards WHERE student_id = ? "
                    "  ORDER BY added_at DESC LIMIT -1 OFFSET ?)",
                    (student_id, student_id, max_cards),
                )
                self._conn.commit()
            return True
        # Refresh существующей (SM-2-состояние не трогаем).
        self._exec(
            "UPDATE review_cards SET topic = CASE WHEN ? <> '' THEN ? ELSE topic END, "
            "subject = CASE WHEN ? <> '' THEN ? ELSE subject END, "
            "correct_answer = CASE WHEN ? <> '' THEN ? ELSE correct_answer END, "
            "last_reviewed = '' "
            "WHERE student_id = ? AND card_id = ?",
            (
                str(record.get("topic") or ""), str(record.get("topic") or ""),
                str(record.get("subject") or ""), str(record.get("subject") or ""),
                str(record.get("correct_answer") or ""), str(record.get("correct_answer") or ""),
                student_id, cid,
            ),
        )
        return False

    def get_due_review(
        self, student_id: str, subject: str = "", limit: int = 5
    ) -> list[dict]:
        rows = self._rows(
            "SELECT * FROM review_cards WHERE student_id = ? "
            "AND (subject = ? OR ? = '') AND due_at <> '' "
            "ORDER BY due_at ASC LIMIT ?",
            (student_id, subject, subject, int(limit)),
        )
        return [self._card_from_row(r) for r in rows]

    def review_card(self, student_id: str, card_id: str, correct: bool) -> dict | None:
        rows = self._rows(
            "SELECT * FROM review_cards WHERE student_id = ? AND card_id = ?",
            (student_id, card_id),
        )
        if not rows:
            return None
        card = self._card_from_row(rows[0])
        updated = apply_sm2(card, correct)
        self._exec(
            "UPDATE review_cards SET reps = ?, interval_days = ?, ease = ?, "
            "lapses = ?, last_reviewed = ?, due_at = ? "
            "WHERE student_id = ? AND card_id = ?",
            (
                updated["reps"], updated["interval_days"], updated["ease"],
                updated["lapses"], updated["last_reviewed"], updated["due_at"],
                student_id, card_id,
            ),
        )
        card.update(updated)
        return card

    def review_stats(self, student_id: str) -> dict:
        rows = self._rows(
            "SELECT card_id, due_at, lapses, topic FROM review_cards WHERE student_id = ?",
            (student_id,),
        )
        total = len(rows)
        due = sum(1 for r in rows if is_due(r))
        by_topic: dict[str, int] = {}
        for r in rows:
            by_topic[r["topic"]] = by_topic.get(r["topic"], 0) + 1
        return {
            "total": total,
            "due": due,
            "lapses": sum(r["lapses"] for r in rows),
            "by_topic": by_topic,
        }

    def list_review_cards(
        self, student_id: str, limit: int = 50, only_due: bool = False
    ) -> list[dict]:
        sql = "SELECT * FROM review_cards WHERE student_id = ?"
        if only_due:
            sql += " AND due_at <> ''"
        sql += " ORDER BY due_at ASC LIMIT ?"
        rows = self._rows(sql, (student_id, int(limit)))
        return [self._card_from_row(r) for r in rows]

    def _topic_payload(self, row: dict) -> dict:
        """Нормализует строку topics в словарь Слоя 2.

        weak_areas/relations принимаются как JSON-строки (из БД) или уже
        декодированные объекты; accuracy = round(correct/attempts, 4) (0.0 при
        0 попытках). Старый ключ level сохраняется (level = mastery при отсутствии).
        """

        def _decode(value: Any, default: Any) -> Any:
            if isinstance(value, (list, dict)):
                return value
            if not value:
                return default
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return default

        out = dict(row)
        out["weak_areas"] = _decode(out.get("weak_areas"), [])
        rel = _decode(out.get("relations"), {"prerequisite": [], "related": []})
        if not isinstance(rel, dict):
            rel = {}
        rel.setdefault("prerequisite", [])
        rel.setdefault("related", [])
        out["relations"] = rel
        attempts = int(out.get("attempts") or 0)
        correct = int(out.get("correct") or 0)
        out["accuracy"] = round(correct / attempts, 4) if attempts else 0.0
        out.setdefault("level", out.get("mastery", 0.0))
        return out

    def apply_result(
        self, student_id: str, topic: str, subject: str, record: AnswerRecord
    ) -> dict:
        """EMA-мастерство по answer record; level поддерживается = mastery.

        Увеличивает attempts, при неверном ответе добавляет feedback в weak_areas
        (без дублей, не более 3 — вытесняются старые), relations не трогает.
        """
        from .mastery import apply_mastery, derive_status

        rows = self._rows(
            "SELECT mastery, attempts, correct, weak_areas FROM topics "
            "WHERE student_id = ? AND topic = ?",
            (student_id, topic),
        )
        if rows:
            prev = rows[0]
            attempts = int(prev["attempts"]) + 1
            correct_count = int(prev["correct"]) + (1 if record.correct else 0)
            mastery = apply_mastery(
                float(prev["mastery"] or 0.0), bool(record.correct), int(prev["attempts"])
            )
            weak = json.loads(prev["weak_areas"] or "[]")
        else:
            attempts = 1
            correct_count = 1 if record.correct else 0
            mastery = apply_mastery(0.0, bool(record.correct), 0)
            weak = []
        if not record.correct and (record.feedback or "").strip():
            fb = record.feedback.strip()
            if fb not in weak:
                weak.append(fb)
                weak = weak[-3:]
        status = derive_status(attempts, mastery)
        now = time.time()
        self._exec(
            "INSERT INTO topics (student_id, topic, subject, level, mastery, status, "
            "attempts, correct, weak_areas, relations, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(student_id, topic) DO UPDATE SET "
            "subject = excluded.subject, level = excluded.level, mastery = excluded.mastery, "
            "status = excluded.status, attempts = excluded.attempts, correct = excluded.correct, "
            "weak_areas = excluded.weak_areas, last_seen = excluded.last_seen",
            (
                student_id, topic, subject or "", mastery, mastery, status,
                attempts, correct_count, json.dumps(weak, ensure_ascii=False), "{}", now,
            ),
        )
        self.upsert_student(student_id)
        return self._topic_payload({
            "topic": topic, "subject": subject or "", "mastery": mastery,
            "status": status, "attempts": attempts, "correct": correct_count,
            "weak_areas": weak, "relations": {}, "last_seen": now,
        })

    def append_record(self, student_id: str, session_id: str, record: dict) -> None:
        """Пишет строку журнала ответов (из answer record). record_id = rec_<uuid12>."""
        options = record.get("options")
        options_json = None
        if isinstance(options, list):
            options_json = json.dumps(options, ensure_ascii=False)
        self._exec(
            "INSERT OR REPLACE INTO session_records "
            "(student_id, record_id, session_id, ts, subject, topic, question_id, "
            " question, options, answer_type, difficulty, student_answer, correct, "
            " feedback, score01) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                student_id,
                record.get("record_id") or f"rec_{uuid.uuid4().hex[:12]}",
                session_id,
                float(record.get("ts") or time.time()),
                str(record.get("subject") or ""),
                str(record.get("topic") or ""),
                str(record.get("question_id") or ""),
                str(record.get("question") or ""),
                options_json,
                str(record.get("answer_type") or "open"),
                str(record.get("difficulty") or "medium"),
                str(record.get("student_answer") or ""),
                int(1 if record.get("correct") else 0),
                str(record.get("feedback") or ""),
                float(record.get("score01", 1.0 if record.get("correct") else 0.0)),
            ),
        )

    def list_records(
        self,
        student_id: str,
        subject: str | None = None,
        session_id: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Записи журнала: окно из limit ПОСЛЕДНИХ (по ts), отдано ASC.

        options возвращается списком (JSON-колонка декодируется).
        """
        if not student_id or limit <= 0:
            return []
        sql = "SELECT * FROM session_records WHERE student_id = ?"
        params: list[Any] = [student_id]
        if subject:
            sql += " AND subject = ?"
            params.append(subject)
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        sql += " ORDER BY ts DESC LIMIT ?"
        rows = self._rows(sql, (*params, int(limit)))
        decoded = []
        for row in reversed(rows):
            out = dict(row)
            if out.get("options"):
                try:
                    out["options"] = json.loads(out["options"])
                except (TypeError, ValueError):
                    out["options"] = None
            decoded.append(out)
        return decoded

    def set_relations(self, student_id: str, topic: str, relations: dict) -> None:
        """Объединяет relations темы по union (prerequisite/related); нет темы — no-op."""
        rows = self._rows(
            "SELECT relations FROM topics WHERE student_id = ? AND topic = ?",
            (student_id, topic),
        )
        if not rows:
            return
        try:
            existing = json.loads(rows[0]["relations"] or "{}")
        except (TypeError, ValueError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        merged = {
            "prerequisite": list(existing.get("prerequisite") or []),
            "related": list(existing.get("related") or []),
        }
        for key in ("prerequisite", "related"):
            for item in relations.get(key) or []:
                if item not in merged[key]:
                    merged[key].append(item)
        self._exec(
            "UPDATE topics SET relations = ? WHERE student_id = ? AND topic = ?",
            (json.dumps(merged, ensure_ascii=False), student_id, topic),
        )

    def get_topic(self, student_id: str, topic: str) -> dict | None:
        """Состояние темы (payload Слоя 2) или None."""
        rows = self._rows(
            "SELECT topic, subject, level, mastery, status, attempts, correct, "
            "weak_areas, relations, last_seen FROM topics WHERE student_id = ? AND topic = ?",
            (student_id, topic),
        )
        return self._topic_payload(rows[0]) if rows else None

    def get_topic_bandit(
        self,
        student_id: str,
        topic: str,
        d: int = 4,
        alpha: float = 0.6,
    ) -> dict:
        """LinUCB-состояние темы (JSON `topics.bandit`) или свежий бандит.

        Отсутствие строки/пустое/битое значение даёт свежий бандит
        (`make_bandit`); параметры d/alpha применяются только при создании.
        """
        from .linucb import make_bandit

        rows = self._rows(
            "SELECT bandit FROM topics WHERE student_id = ? AND topic = ?",
            (student_id, topic),
        )
        raw = rows[0]["bandit"] if rows else ""
        if raw:
            try:
                state = json.loads(raw)
                if isinstance(state, dict) and state.get("arms"):
                    return state
            except (TypeError, ValueError):
                pass
        return make_bandit(d=d, alpha=alpha)

    def set_topic_bandit(self, student_id: str, topic: str, bandit: dict) -> None:
        """Сохраняет состояние бандита темы (UPSERT строки topics)."""
        self._exec(
            "INSERT INTO topics (student_id, topic, bandit, last_seen) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(student_id, topic) DO UPDATE SET bandit = excluded.bandit",
            (student_id, topic, json.dumps(bandit, ensure_ascii=False), time.time()),
        )

    def get_weak_topics(
        self,
        student_id: str,
        subject: str = "",
        threshold: float = 0.5,
        min_attempts: int = 2,
    ) -> list[dict]:
        """Слабые темы: attempts >= min_attempts и accuracy < threshold (accuracy asc)."""
        out = [
            p for p in self.list_topics(student_id)
            if (not subject or p.get("subject") == subject)
            and p["attempts"] >= min_attempts
            and p["accuracy"] < threshold
        ]
        out.sort(key=lambda p: p["accuracy"])
        return out

    def get_in_progress_topics(self, student_id: str, subject: str = "") -> list[dict]:
        """Темы в процессе изучения (status == in_progress), last_seen desc."""
        out = [
            p for p in self.list_topics(student_id)
            if p.get("status") == "in_progress"
            and (not subject or p.get("subject") == subject)
        ]
        out.sort(key=lambda p: p.get("last_seen") or 0.0, reverse=True)
        return out

    def get_mastered_topics(self, student_id: str, subject: str = "") -> list[dict]:
        """Освоенные темы по is_mastered (attempts/mastery/status), mastery desc."""
        from .mastery import is_mastered

        out = [
            p for p in self.list_topics(student_id)
            if is_mastered(p["attempts"], p["mastery"], p.get("status") or "")
            and (not subject or p.get("subject") == subject)
        ]
        out.sort(key=lambda p: p["mastery"], reverse=True)
        return out

    def get_prerequisite_gaps(self, student_id: str, topic: str) -> list[str]:
        """Пререквизиты темы, которых нет или которые не освоены."""
        from .mastery import is_mastered

        ts = self.get_topic(student_id, topic)
        if not ts:
            return []
        prereqs = (ts.get("relations") or {}).get("prerequisite") or []
        gaps = []
        for pid in prereqs:
            p = self.get_topic(student_id, pid)
            if p is None or not is_mastered(
                p["attempts"], p["mastery"], p.get("status") or ""
            ):
                gaps.append(pid)
        return gaps

    def recommend_topics(
        self,
        student_id: str,
        subject: str = "",
        current_topic: str = "",
        limit: int = 5,
    ) -> list[dict]:
        """Рекомендация тем без дублей (спека §3.3).

        Порядок: (1) слабые accuracy asc; (2) пробелы пререквизитов current_topic;
        (3) in_progress по last_seen desc; (4) not_studied без пробелов пререквизитов.
        Обрезается до limit.
        """
        rows = [
            p for p in self.list_topics(student_id)
            if not subject or p.get("subject") == subject
        ]
        result: list[dict] = []
        seen: set[str] = set()

        def _add(payload: dict) -> None:
            if payload["topic"] not in seen:
                seen.add(payload["topic"])
                result.append(payload)

        weak = [p for p in rows if p["attempts"] >= 2 and p["accuracy"] < 0.5]
        weak.sort(key=lambda p: p["accuracy"])
        for p in weak:
            _add(p)
        if current_topic:
            for gid in self.get_prerequisite_gaps(student_id, current_topic):
                if gid not in seen:
                    _add(self._topic_payload({
                        "topic": gid, "subject": subject, "mastery": 0.0,
                        "status": "not_studied", "attempts": 0, "correct": 0,
                    }))
        in_progress = [p for p in rows if p.get("status") == "in_progress"]
        in_progress.sort(key=lambda p: p.get("last_seen") or 0.0, reverse=True)
        for p in in_progress:
            _add(p)
        not_studied = [
            p for p in rows if p.get("status") == "not_studied"
            and not self.get_prerequisite_gaps(student_id, p["topic"])
        ]
        for p in not_studied:
            _add(p)
        return result[:limit]

    def set_profile(
        self,
        student_id: str,
        *,
        name: str | None = None,
        learner_type: str | None = None,
        grade: str | None = None,
    ) -> dict:
        """Upsert полей профиля ученика; None-аргумент не меняет текущее значение."""
        row = self.get_student(student_id)
        current = (row or {}).get
        merged = {
            "name": str(name) if name is not None else current("name", ""),
            "learner_type": (
                str(learner_type) if learner_type is not None else current("learner_type", "")
            ),
            "grade": str(grade) if grade is not None else current("grade", ""),
        }
        now = time.time()
        self._exec(
            "INSERT INTO students (student_id, name, learner_type, grade, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(student_id) DO UPDATE SET "
            "name = excluded.name, learner_type = excluded.learner_type, "
            "grade = excluded.grade, updated_at = excluded.updated_at",
            (student_id, merged["name"], merged["learner_type"], merged["grade"], now, now),
        )
        return {"student_id": student_id, **merged}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
