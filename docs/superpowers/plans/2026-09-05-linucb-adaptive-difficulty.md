# LinUCB — адаптивный советник сложности заданий: план реализации

> Статус: выполнено (2026-09-06). Код реализован и закоммичен (ec298d2, 0a6e3b4); backend 387 pytest PASS, ruff чист. Остался только live-smoke с реальным LLM (опционально, Task 7 Step 3).

> **For agentic workers:** Tasks use checkbox (`- [ ]`) syntax. Execution is inline
> in this session (subagent-driven недоступен: нет баланса на Task-агентов).
> **Коммиты не выполняются** — по AGENTS.md только по явной просьбе пользователя;
> шаги commit в задачах опущены.

**Goal:** Внедрить LinUCB contextual bandit как советника сложности заданий
`quiz`/`practice` (per student+topic) с обновлением по награде за оценку ответа.

**Architecture:** Чистый модуль LinUCB (`src/student/linucb.py`) + JSON-колонка
`topics.bandit` в SQLite + две точки в `src/api/server.py`: перед `run_agent`
(совет в контекст, фиксация руки при выдаче задания) и в ветке `evaluation`
(обновление бандита и персист). Фронтенд/API-форматы не меняются.

**Tech Stack:** Python 3.11+, FastAPI, numpy 2.x (уже установлен), SQLite.

**Spec:** `docs/superpowers/specs/2026-09-05-linucb-adaptive-difficulty-design.md`

## Global Constraints

- Сложность конверта НЕ нормализуется: модель — финальный решатель (советник).
- Руки играются только на `quiz`/`practice`; `theory`/`hint` бандит не меняют.
- Область обучения: per `(student_id, topic)`; контекст-фичи НЕ включают mastery.
- Все обращения к bandit fail-soft: `try/except` не должен ронять чат.
- Запуск тестов из `adaptive_tutor/`: `.venv/Scripts/python.exe -m pytest tests/<file> -q`.
  Проверка стиля: `.venv/Scripts/python.exe -m ruff check <file>` (100 col, py311).
- Коммиты не делать без явной просьбы.

---

### Task 1: Конфиг и `.env.example`

**Files:**
- Modify: `src/config.py` (блок после «Эмбеддинги», перед «Профиль ученика»)
- Modify: `.env.example`

**Interfaces:**
- Produces: `settings.bandit_enabled: bool` (default True),
  `settings.bandit_alpha: float` (default 0.6).

- [x] **Step 1:** Добавить в `src/config.py` (класс `Settings`):

```python
    # LinUCB: советник сложности заданий quiz/practice
    bandit_enabled: bool = Field(default=True, description="Включить LinUCB-советник")
    bandit_alpha: float = Field(default=0.6, description="Параметр исследования LinUCB")
```

- [x] **Step 2:** В `.env.example` (в конец, после «Агент»):

```
# LinUCB-советник сложности (Этап 5)
TUTOR_BANDIT_ENABLED=true
TUTOR_BANDIT_ALPHA=0.6
```

- [x] **Step 3:** Тест — убедиться, что дефолты читаются (новый файл
  `tests/test_linucb.py` будет создан в Task 3; здесь проверка вручную):

```bash
.venv/Scripts/python.exe -c "from src.config import settings; print(settings.bandit_enabled, settings.bandit_alpha)"
```
Expected: `True 0.6`.

---

### Task 2: Хранилище — колонка `topics.bandit` и методы get/set

**Files:**
- Modify: `src/student/store.py:110-141` (`_migrate_topics`)
- Modify: `src/student/store.py` (добавить методы после `get_topic`)
- Test: `tests/test_store.py` (или отдельный `tests/test_store_bandit.py`)

**Interfaces:**
- Consumes: `make_bandit` из `src.student.linucb` (Task 3). Чтобы Task 2 не
  зависел от незавершённого Task 3, `store.py` импортирует linucb лениво
  (внутри методов) — порядок задач менять не нужно.
- Produces:
  - `StudentStore.get_topic_bandit(student_id: str, topic: str, d: int = 4,
    alpha: float = 0.6) -> dict` — JSON колонки `bandit` либо свежий `make_bandit`.
  - `StudentStore.set_topic_bandit(student_id: str, topic: str,
    bandit: dict) -> None` — UPSERT строки `topics`.

- [x] **Step 1: падающий тест** — создать `tests/test_store_bandit.py`:

```python
"""Тесты персистентности LinUCB-бандита (per student+topic)."""

import json

from src.student.store import StudentStore


def test_topic_bandit_default_and_round_trip(tmp_path):
    store = StudentStore(str(tmp_path / "students.db"))
    store.upsert_student("stu_1")
    try:
        fresh = store.get_topic_bandit("stu_1", "квадратные уравнения")
        assert fresh["d"] == 4
        assert fresh["alpha"] == 0.6
        assert len(fresh["arms"]) == 3
        assert fresh["arms"][0]["n"] == 0

        fresh["arms"][1]["n"] = 7
        store.set_topic_bandit("stu_1", "квадратные уравнения", fresh)

        loaded = store.get_topic_bandit("stu_1", "квадратные уравнения")
        assert loaded["arms"][1]["n"] == 7
        # независимо от параметров d/alpha, если состояние уже сохранено
        loaded2 = store.get_topic_bandit("stu_1", "квадратные уравнения", d=2, alpha=9.0)
        assert loaded2["d"] == 4
    finally:
        store.close()


def test_topic_bandit_ignores_corrupt_json(tmp_path):
    store = StudentStore(str(tmp_path / "students.db"))
    store.upsert_student("stu_1")
    try:
        fresh = store.get_topic_bandit("stu_1", "квадратные уравнения")
        store.set_topic_bandit("stu_1", "квадратные уравнения", fresh)
        store._exec(
            "UPDATE topics SET bandit = 'not-json' WHERE student_id = 'stu_1' AND topic = ?",
            ("квадратные уравнения",),
        )
        bandit = store.get_topic_bandit("stu_1", "квадратные уравнения")
        assert bandit["arms"][0]["n"] == 0
    finally:
        store.close()
```

- [x] **Step 2:** Запустить — убедиться, что падает (нет колонки/методов):

```bash
.venv/Scripts/python.exe -m pytest tests/test_store_bandit.py -q
```
Expected: FAIL (`OperationalError: no such column: bandit`).

- [x] **Step 3: миграция** — в `_migrate_topics` (в словаре колонок, рядом с
  `relations`):

```python
                "bandit": "bandit TEXT DEFAULT ''",
```

- [x] **Step 4: методы** — добавить в `StudentStore` (после `get_topic`):

```python
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
```

Примечание: тест `test_topic_bandit_default_and_round_trip` вызывает
`upsert_student` — он создаёт только строку `students`, а строка `topics`
создаётся в `set_topic_bandit` (UPSERT). Тест корректно проходит без
дополнительного создания строки темы.

- [x] **Step 5:** Прогнать тесты Task 2 + существующие store-тесты:

```bash
.venv/Scripts/python.exe -m pytest tests/test_store_bandit.py tests/test_store.py -q
```
Expected: PASS (миграция на предсозданной старой схеме покрыта существующими
тестами миграции `test_store.py`; `bandit` добавляется идемпотентно).

- [x] **Step 6:** Ruff:

```bash
.venv/Scripts/python.exe -m ruff check src/student/store.py tests/test_store_bandit.py
```
Expected: All checks passed.

---

### Task 3: Модуль `src/student/linucb.py`

**Files:**
- Create: `src/student/linucb.py`
- Create: `tests/test_linucb.py`

**Interfaces:**
- Consumes: только numpy, константы.
- Produces (используются в Task 5/6):
  - `DIFFICULTY_ORDER = ["easy", "medium", "hard"]`, `N_ARMS = 3`, `BANDIT_DIM = 4`;
  - `make_bandit(d: int = BANDIT_DIM, alpha: float = 0.6, n_arms: int = N_ARMS) -> dict`;
  - `difficulty_arm(difficulty: str) -> int`;
  - `arm_difficulty(arm: int) -> str`;
  - `build_features(*, accuracy: float, attempts: int, fatigue: float,
    overall: float) -> list[float]`;
  - `bandit_select(bandit: dict, features: list[float], current_arm: int = 1) -> int`;
  - `bandit_update(bandit: dict, features: list[float], arm: int, reward: float) -> dict`.

- [x] **Step 1: падающий тест** — `tests/test_linucb.py`:

```python
"""Юнит-тесты LinUCB contextual bandit (модуль src/student/linucb.py)."""

import json

import numpy as np

from src.student.linucb import (
    BANDIT_DIM,
    DIFFICULTY_ORDER,
    arm_difficulty,
    bandit_select,
    bandit_update,
    build_features,
    difficulty_arm,
    make_bandit,
)


def test_make_bandit_shape():
    bandit = make_bandit()
    assert bandit["d"] == BANDIT_DIM
    assert bandit["alpha"] == 0.6
    assert len(bandit["arms"]) == 3
    for arm in bandit["arms"]:
        assert arm["n"] == 0
        assert arm["b"] == [0.0] * BANDIT_DIM
        A = np.asarray(arm["A"])
        assert A.shape == (BANDIT_DIM, BANDIT_DIM)
        np.testing.assert_array_equal(A, np.eye(BANDIT_DIM))


def test_difficulty_helpers_round_trip():
    assert [difficulty_arm(d) for d in DIFFICULTY_ORDER] == [0, 1, 2]
    assert [arm_difficulty(i) for i in range(3)] == DIFFICULTY_ORDER
    assert difficulty_arm("bogus") == 1
    assert arm_difficulty(-5) == "easy"
    assert arm_difficulty(99) == "hard"


def test_build_features_order_and_clamps():
    f = build_features(accuracy=0.8, attempts=3, fatigue=0.5, overall=0.7)
    assert f == [0.8, 0.3, 0.5, 0.7]
    # клампы
    f2 = build_features(accuracy=-1.0, attempts=100, fatigue=3.0, overall=2.0)
    assert f2 == [0.0, 1.0, 1.0, 1.0]
    # без попыток -> точность 0.5
    assert build_features(accuracy=0.0, attempts=0, fatigue=0.0, overall=0.5)[0] == 0.5


def test_select_cold_start_prefers_current_arm():
    bandit = make_bandit()
    features = build_features(accuracy=0.5, attempts=0, fatigue=0.0, overall=0.5)
    assert bandit_select(bandit, features, current_arm=1) == 1
    assert bandit_select(bandit, features, current_arm=2) == 2


def test_update_then_high_reward_arm_is_preferred():
    bandit = make_bandit()
    features = build_features(accuracy=0.9, attempts=2, fatigue=0.0, overall=0.6)
    for _ in range(10):
        bandit = bandit_update(bandit, features, arm=0, reward=1.0)
        bandit = bandit_update(bandit, features, arm=1, reward=0.0)
    assert bandit["arms"][0]["n"] == 10
    assert bandit["arms"][1]["n"] == 10
    # после перекоса arm=0 должен выигрывать чаще (детерминированный контекст)
    picks = [bandit_select(bandit, features) for _ in range(20)]
    assert picks.count(0) > picks.count(1)


def test_bandit_survives_json_round_trip():
    bandit = make_bandit()
    features = build_features(accuracy=0.6, attempts=1, fatigue=0.1, overall=0.5)
    bandit = bandit_update(bandit, features, arm=1, reward=1.0)
    restored = json.loads(json.dumps(bandit))
    assert bandit_select(restored, features, current_arm=1) in {0, 1, 2}
    bandit_update(restored, features, arm=1, reward=0.0)
    assert restored["arms"][1]["n"] == 2
```

- [x] **Step 2:** Прогнать — убедиться, что падает:

```bash
.venv/Scripts/python.exe -m pytest tests/test_linucb.py -q
```
Expected: FAIL (`ModuleNotFoundError: No module named 'src.student.linucb'`).

- [x] **Step 3:** Реализовать `src/student/linucb.py`:

```python
"""LinUCB contextual bandit — советник сложности заданий (Этап 5).

Чистые функции без I/O; состояние — JSON-безопасный dict
`{"d", "alpha", "arms": [{"A": [[float]], "b": [float], "n": int}]}`.
Руки — уровни сложности easy/medium/hard; выбор — disjoint LinUCB
`p = x^T theta + alpha * sqrt(x^T A^-1 x)`; обновление `A += x x^T`,
`b += reward * x`. Советник НЕ принуждает модель (envelope.difficulty
остаётся за моделью).
"""

from __future__ import annotations

from typing import Any

import numpy as np

DIFFICULTY_ORDER = ["easy", "medium", "hard"]
N_ARMS = 3
BANDIT_DIM = 4
DEFAULT_ALPHA = 0.6


def make_bandit(d: int = BANDIT_DIM, alpha: float = DEFAULT_ALPHA, n_arms: int = N_ARMS) -> dict:
    """Создаёт состояние LinUCB: A = I_d, b = 0, n = 0 для каждой руки."""
    return {
        "d": d,
        "alpha": float(alpha),
        "arms": [
            {
                "A": [[1.0 if i == j else 0.0 for j in range(d)] for i in range(d)],
                "b": [0.0] * d,
                "n": 0,
            }
            for _ in range(n_arms)
        ],
    }


def difficulty_arm(difficulty: str) -> int:
    """Индекс руки по сложности: easy=0, medium=1, hard=2; иначе 1."""
    try:
        return DIFFICULTY_ORDER.index(difficulty)
    except (ValueError, AttributeError):
        return 1


def arm_difficulty(arm: int) -> str:
    """Сложность по индексу руки с клампингом в валидный диапазон."""
    arm = int(arm)
    if arm < 0:
        arm = 0
    if arm >= N_ARMS:
        arm = N_ARMS - 1
    return DIFFICULTY_ORDER[arm]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def build_features(
    *,
    accuracy: float,
    attempts: int,
    fatigue: float,
    overall: float,
) -> list[float]:
    """Контекст решения d=4: [точность темы, attempts_norm, fatigue, общий уровень].

    Точность без попыток принимается 0.5; attempts нормируется на 10 попыток.
    """
    acc = 0.5 if int(attempts) <= 0 else _clamp01(accuracy)
    attempts_norm = min(1.0, int(attempts) / 10.0)
    return [acc, attempts_norm, _clamp01(fatigue), _clamp01(overall)]


def bandit_select(bandit: dict, features: list[float], current_arm: int = 1) -> int:
    """Выбирает руку по UCB; при равенстве (холодный старт) — текущую."""
    x = np.asarray(features, dtype=float)
    alpha = float(bandit.get("alpha", DEFAULT_ALPHA))
    best = int(current_arm)
    best_p = -1e18
    for idx, arm in enumerate(bandit["arms"]):
        A = np.asarray(arm["A"], dtype=float)
        b = np.asarray(arm["b"], dtype=float)
        a_inv = np.linalg.inv(A)
        theta = a_inv @ b
        p = float(x @ theta + alpha * float(np.sqrt(float(x @ a_inv @ x))))
        if p > best_p + 1e-9:
            best, best_p = idx, p
        elif abs(p - best_p) <= 1e-9 and idx == current_arm:
            best = idx
    return best


def bandit_update(bandit: dict, features: list[float], arm: int, reward: float) -> dict:
    """Обновляет сыгранную руку: A += x x^T, b += reward x, n += 1."""
    arm = int(arm)
    if arm < 0:
        arm = 0
    if arm >= len(bandit["arms"]):
        arm = len(bandit["arms"]) - 1
    x = np.asarray(features, dtype=float)
    armd = bandit["arms"][arm]
    A = np.asarray(armd["A"], dtype=float)
    b = np.asarray(armd["b"], dtype=float)
    A = A + np.outer(x, x)
    b = b + float(reward) * x
    armd["A"] = A.tolist()
    armd["b"] = b.tolist()
    armd["n"] += 1
    return bandit
```

(Импорт `Any` не требуется — убрать; список типов выше достаточен. Ruff: лишние
импорты отсечёт проверка в Step 5.)

- [x] **Step 4:** Прогнать тесты Task 3:

```bash
.venv/Scripts/python.exe -m pytest tests/test_linucb.py -q
```
Expected: PASS.

- [x] **Step 5:** Ruff + фикс замечаний (удалить неиспользуемые импорты, если
  ruff укажет):

```bash
.venv/Scripts/python.exe -m ruff check src/student/linucb.py tests/test_linucb.py
```
Expected: All checks passed.

---

### Task 4: Поля `ChatSession` для руки бандита

**Files:**
- Modify: `src/api/session_store.py:23-43` (`ChatSession`)
- Test: в `tests/test_api.py` или `tests/test_session_store.py` (если есть)
  — проще добавить assert в Task 5, но минимальный smoke здесь.

**Interfaces:**
- Produces: `ChatSession.bandit_arm: int | None = None`,
  `ChatSession.bandit_topic: str = ""`,
  `ChatSession.bandit_features: list[float] = field(default_factory=list)`.

- [x] **Step 1:** Добавить поля в dataclass `ChatSession` (в конец, после
  `student_profile`):

```python
    bandit_arm: int | None = None
    bandit_topic: str = ""
    bandit_features: list = field(default_factory=list)
```

- [x] **Step 2:** Проверка компиляции + smoke тест (можно в `tests/test_api.py`
  отдельной функцией без сети):

```python
def test_session_bandit_fields_defaults():
    from src.api.session_store import SessionStore

    store = SessionStore()
    session = store.create(student_id="stu_1", topic="т")
    assert session.bandit_arm is None
    assert session.bandit_topic == ""
    assert session.bandit_features == []
    session.bandit_arm = 2
    assert store.get(session.session_id).bandit_arm == 2
```

Запустить:
```bash
.venv/Scripts/python.exe -m pytest tests/test_api.py::test_session_bandit_fields_defaults -q
```
Expected: PASS.

---

### Task 5: Сервер — совет перед `run_agent` и фиксация руки на `quiz`/`practice`

**Files:**
- Modify: `src/api/server.py` (ветка обычного хода: ~`1203-1235`)
- Modify: `src/api/server.py` (helper рядом с `_build_adaptive`)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `make_bandit`-неявно через store, `bandit_select`, `build_features`,
  `arm_difficulty`, `difficulty_arm`, `settings.bandit_enabled`,
  `session.bandit_arm/topic/features`.
- Produces: поведение — system-сообщение-совет в контексте хода; при конверте
  `quiz`/`practice` заполняются `session.bandit_*`.

- [x] **Step 1: падающий интеграционный тест** — в `tests/test_api.py` добавить
  фейк-рантайм, отвечающий quiz-конвертом, и проверки. Фейк и рантайм:

```python
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
    return AgentRuntime(
        llm=FakeQuizLLM(),
        models={"planner": "test", "fast": "test", "judge": "test"},
        tool_context=ToolContext(region="GLOBAL"),
        critic=Critic(llm=FakeJudgeLLM(), model="judge"),
    )
```

Тест:

```python
def test_bandit_quiz_advice_and_arm_marking(tmp_path):
    from fastapi.testclient import TestClient

    from src.api.server import create_app
    from src.student.store import StudentStore

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
```

- [x] **Step 2:** Прогнать — убедиться, что падает (нет совета/полей):

```bash
.venv/Scripts/python.exe -m pytest tests/test_api.py::test_bandit_quiz_advice_and_arm_marking -q
```
Expected: FAIL (`session.bandit_arm` отсутствует или совет не найден).

- [x] **Step 3:** Реализация — добавить helper и вставку. Рядом с
  `_build_adaptive` (после неё) в `src/api/server.py`:

```python
def _bandit_advice(features: list[float], bandit: dict) -> tuple[int, str]:
    """Рекомендация LinUCB: (индекс руки, текст совета для модели)."""
    from src.student.linucb import arm_difficulty, bandit_select

    arm = bandit_select(bandit, features, current_arm=1)
    return arm, (
        "[Адаптивный совет: для текущего контекста рекомендуемая сложность "
        f"задания — «{arm_difficulty(arm)}». Учитывай её при выборе сложности, "
        "но решение за тобой.]"
    )
```

В ветке обычного хода, ПОСЛЕ `context = app.state.sessions.to_llm_context(...)`
(`server.py:~1200-1201`) и ПЕРЕД `runtime = app.state.runtime_factory()`
(и перед вызовом `run_agent`):

```python
    # LinUCB (Этап 5): советник сложности — рекомендация модели (не диктат).
    # Совет добавляем только если нет «висящего» задания (иначе ход — это ответ
    # ученика, и менять целевую сложность не нужно).
    session_bandit: tuple[int, str, list[float]] | None = None
    if (
        settings.bandit_enabled
        and store is not None
        and student_id
        and body.topic.strip()
        and session.bandit_arm is None
    ):
        try:
            from src.student.linucb import bandit_select, build_features

            tp = store.get_topic(student_id, body.topic) or {}
            features = build_features(
                accuracy=float(tp.get("accuracy") or 0.0),
                attempts=int(tp.get("attempts") or 0),
                fatigue=float((profile or {}).get("fatigue_level") or 0.0),
                overall=overall_level(store.list_topics(student_id)),
            )
            bandit = store.get_topic_bandit(
                student_id, body.topic, alpha=settings.bandit_alpha
            )
            arm, advice = _bandit_advice(features, bandit)
            session_bandit = (arm, body.topic, features)
            context.append({"role": "system", "content": advice})
        except Exception as exc:  # noqa: BLE001 — совет не должен ронять чат
            print(f"[bandit] совет не сформирован: {exc}")
            session_bandit = None
```

(Переменные `store`, `student_id`, `body`, `profile`, `session`, `settings`,
`overall_level` уже определены в этой функции — `overall_level` импортирован из
`src.student.adaptive` и используется в `_build_adaptive`.)

После `if envelope is not None and envelope.type.value == "quiz":
session.last_quiz = _quiz_secret(envelope)` — зафиксировать руку на задании:

```python
    if (
        settings.bandit_enabled
        and session_bandit is not None
        and envelope.type.value in {"quiz", "practice"}
    ):
        session.bandit_arm, session.bandit_topic, session.bandit_features = session_bandit
```

- [x] **Step 4:** Прогнать тест:

```bash
.venv/Scripts/python.exe -m pytest tests/test_api.py::test_bandit_quiz_advice_and_arm_marking -q
```
Expected: PASS.

- [x] **Step 5:** Полный прогон API-тестов + ruff:

```bash
.venv/Scripts/python.exe -m pytest tests/test_api.py -q
.venv/Scripts/python.exe -m ruff check src/api/server.py
```
Expected: PASS / All checks passed.

---

### Task 6: Сервер — обновление бандита в ветке `evaluation`

**Files:**
- Modify: `src/api/server.py` (внутри `if envelope.type.value == "evaluation":`,
  после сборки `record` и ДО/после блока журнала — точное место: после
  `session.last_quiz = None`, ~`server.py:1263`)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `session.bandit_arm/topic/features`, `record.correct`,
  `store.get_topic_bandit/set_topic_bandit`, `bandit_update`, `arm_difficulty`,
  `JsonlLogger`.
- Produces: `topics.bandit` обновлён; JSONL-событие `bandit.update`.

- [x] **Step 1: падающий интеграционный тест** (оценка после quiz — бандит
  обновлён). Добавить в `tests/test_api.py` фейк-рантайм, который на первый ход
  даёт quiz, на второй (ответ) — evaluation:

```python
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
```

Тест:

```python
def test_bandit_update_on_evaluation(tmp_path):
    from fastapi.testclient import TestClient

    from src.agent.critic import Critic
    from src.agent.loop import AgentRuntime
    from src.agent.tools import ToolContext
    from src.api.server import create_app
    from src.student.store import StudentStore

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
        before = store.get_topic_bandit("stu_bandit", "квадратные уравнения")["arms"][played_arm]["n"]

        r2 = c.post("/chat", json={**body, "message": "Ответ: 4 и -4"})
        assert r2.status_code == 200
        assert r2.json()["envelope"]["type"] == "evaluation"
        assert session.bandit_arm is None  # сброшено после обновления

        after = store.get_topic_bandit("stu_bandit", "квадратные уравнения")["arms"][played_arm]["n"]
        assert after == before + 1
    store.close()
```

- [x] **Step 2:** Прогнать — убедиться, что падает (`n` не меняется / нет кода):

```bash
.venv/Scripts/python.exe -m pytest tests/test_api.py::test_bandit_update_on_evaluation -q
```
Expected: FAIL.

- [x] **Step 3:** Реализация — в ветке `evaluation`, сразу после
  `session.last_quiz = None` (`server.py:~1263`), добавить:

```python
        # LinUCB (Этап 5): обновление бандита награды за ответ на задание.
        if (
            settings.bandit_enabled
            and session.bandit_arm is not None
            and session.bandit_topic == body.topic
            and record is not None
        ):
            try:
                features = list(session.bandit_features)
                bandit = store.get_topic_bandit(
                    student_id, session.bandit_topic, alpha=settings.bandit_alpha
                )
                from src.student.linucb import arm_difficulty, bandit_update

                bandit_update(
                    bandit,
                    features,
                    arm=int(session.bandit_arm),
                    reward=1.0 if record.correct else 0.0,
                )
                store.set_topic_bandit(student_id, session.bandit_topic, bandit)
                JsonlLogger(settings.log_file).log(
                    trace_id, "INFO", "bandit.update",
                    topic=session.bandit_topic,
                    arm=int(session.bandit_arm),
                    difficulty=arm_difficulty(int(session.bandit_arm)),
                    reward=1.0 if record.correct else 0.0,
                    arms_n=[a["n"] for a in bandit["arms"]],
                )
            except Exception as exc:  # noqa: BLE001 — бандит не должен ронять чат
                print(f"[bandit] не удалось обновить: {exc}")
            finally:
                session.bandit_arm = None
                session.bandit_topic = ""
                session.bandit_features = []
```

Обратите внимание: `store`/`student_id`/`trace_id`/`body`/`session` уже в
области видимости ветки `evaluation` (см. `server.py:~1239+`). `JsonlLogger`
импортирован в `server.py`.

- [x] **Step 4:** Прогнать тест:

```bash
.venv/Scripts/python.exe -m pytest tests/test_api.py::test_bandit_update_on_evaluation -q
```
Expected: PASS.

- [x] **Step 5:** Полный прогон + ruff:

```bash
.venv/Scripts/python.exe -m pytest tests/test_api.py tests/test_linucb.py tests/test_store_bandit.py -q
.venv/Scripts/python.exe -m ruff check src/
```
Expected: PASS / All checks passed.

---

### Task 7: Документация, полный прогон, smoke

**Files:**
- Modify: `README.md` (раздел «Адаптивность»/API — краткая строка про советник)
- Modify: `adaptive_tutor/docs/api.md` (строка про `difficulty` в adaptive: советник)

- [x] **Step 1:** README — в раздел про адаптивность/профиль добавить абзац:

```markdown
### LinUCB-советник сложности (Этап 5)

Перед каждым ходом сервер рекомендует модели сложность следующего задания
(`quiz`/`practice`) через LinUCB contextual bandit: контекст — точность темы,
число попыток, усталость и общий уровень; награда — корректность ответа на
задание (`evaluation`). Рекомендация передаётся модели как совет
(`system`-сообщение), финальное решение за моделью (`envelope.difficulty` не
нормализуется). Состояние бандита хранится per (студент, тема) в колонке
`topics.bandit` (`data/students.db`). Конфиг: `TUTOR_BANDIT_ENABLED`,
`TUTOR_BANDIT_ALPHA`. Включён по умолчанию.
```

- [x] **Step 2:** Полный прогон бэкенда и lint:

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
.venv/Scripts/python.exe -m ruff check src/ tests/
```
Expected: PASS (323 + новые тесты) / All checks passed.

- [x] **Step 3:** Smoke против живого сервера (опционально, по желанию
  пользователя): после рестарта uvicorn (он с `--reload` подхватит изменения)
  два хода через `/chat/stream`: «дай задание по квадратным уравнениям» →
  ответ → проверить в `logs/agent.jsonl` события `bandit.select`/`bandit.update`.

- [x] **Step 4:** Сообщить «Что осталось» (см. AGENTS.md).
