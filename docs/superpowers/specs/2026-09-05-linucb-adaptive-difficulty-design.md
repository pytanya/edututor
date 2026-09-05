# LinUCB: адаптивный советник сложности заданий — дизайн (Этап 5)

Дата: 2026-09-05. Воркспейс: `C:\otus\edututor`. Тип: новое под-системное изменение
бэкенда (советник поверх существующего потока сложности). Коммит не выполняется
без явной просьбы.

## 1. Цель и место в продукте

Сейчас сложность каждого хода выбирает **сама LLM** (поле `difficulty` в
JSON-конверте, `src/agent/prompts.py:27`); системный промпт лишь намекает на
уровень знаний/усталость (`prompts.py:_build_adaptation_block`). Эвристики
«адаптации» нет.

Этап внедряет **LinUCB contextual bandit** как «советника»: для каждого хода
сервер вычисляет рекомендуемую сложность следующего **задания**
(`quiz`/`practice`) и подсказывает её модели. Модель остаётся финальным
решателем (режим «бандит-советник», согласовано). Бандит обучается по ответам
ученика: награда приходит из оценки ответа на задание.

Заявка в README/архитектуре («адаптивная сложность easy/medium/hard»), в коде
отсутствовала (gap-аудит, таблица B: адаптивность «базовая»).

## 2. Решения (согласовано)

1. **Советник, не диктатор**: рекомендованная сложность добавляется в контекст
   хода; `envelope.difficulty` модель выбирает сама и не нормализуется.
2. **Руки играются только на заданиях** `quiz`/`practice`: перед выдачей задания
   фиксируется сыгранная рука (рекомендация бандита), на `evaluation` за ответ
   на это задание — обновление. `theory`/`hint` бандит не меняют.
3. **Состояние — per (student, topic)**: колонка `topics.bandit` (JSON).
   Контекст-фичи не включают mastery темы (она и есть объект обучения) и берутся
   из доступных сигналов профиля/темы.
4. **Без изменений фронтенда и форматов API**: `adaptive.difficulty` уже
   отображается (`frontend/src/components/AdaptivePanel.jsx:25`).

## 3. Модуль `src/student/linucb.py`

Чистые функции (без I/O), numpy-математика, JSON-безопасное состояние.

Константы:
- `DIFFICULTY_ORDER = ["easy", "medium", "hard"]`, `N_ARMS = 3`;
- размерность фич `BANDIT_DIM = 4`, дефолт `alpha` из настроек.

Функции:

- `make_bandit(d: int = 4, alpha: float, n_arms: int = 3) -> dict`
  → `{"d", "alpha", "arms": [{"A": I_d, "b": [0]*d, "n": 0} × n_arms]}`.
- `difficulty_arm(difficulty: str) -> int` — `easy=0, medium=1, hard=2`
  (невалидное/пустое → `1`).
- `arm_difficulty(arm: int) -> str` — клампинг в `[0, n_arms-1]`.
- `build_features(*, accuracy: float, attempts: int, fatigue: float,
  overall: float) -> list[float]` — d=4, порядок:
  1. `accuracy` темы (0..1; без попыток → 0.5);
  2. `attempts_norm = min(1.0, attempts / 10.0)`;
  3. `fatigue = clamp01(profile.fatigue_level)` (default 0);
  4. `overall = общий уровень` (0..1, default 0.5).
- `bandit_select(bandit, features, current_arm: int = 1) -> int` —
  LinUCB (disjoint): `A_inv = inv(A_a)`, `theta = A_inv·b_a`,
  `p_a = xᵀθ + alpha·sqrt(xᵀA_inv x)`; при равенстве/холодном старте
  предпочитается `current_arm`.
- `bandit_update(bandit, features, arm: int, reward: float) -> dict` —
  `A_a += x·xᵀ`, `b_a += reward·x`, `n += 1`; мутирует и возвращает dict.

Инварианты: состояние всегда проходится `json.loads(json.dumps(...))`;
выбор/обновление устойчивы к этому (только списки/float/int).

## 4. Конфигурация

`src/config.py` (Settings), `.env.example`, `.env`:
- `TUTOR_BANDIT_ENABLED: bool = True` — kill-switch фичи;
- `TUTOR_BANDIT_ALPHA: float = 0.6` — параметр исследования/эксплуатации.

## 5. Персистентность (`src/student/store.py`)

- Идемпотентная миграция `_migrate_topics`: добавить колонку
  `bandit TEXT DEFAULT ''` в `topics` (паттерн уже есть для
  `weak_areas`/`relations`, `store.py:110–141`).
- `get_topic_bandit(student_id, topic) -> dict` — декодирует JSON колонки
  `topics.bandit`; при отсутствии строки/пустом поле → свежий `make_bandit`.
- `set_topic_bandit(student_id, topic, bandit: dict) -> None` — UPSERT строки
  `topics` минимальным набором полей (`INSERT … ON CONFLICT(student_id, topic)
  DO UPDATE SET bandit=excluded.bandit`), чтобы не зависеть от наличия строки.
- Все операции под существующим `threading.Lock`; исключения не пробрасываются
  в чат (вызывающий уже оборачивает try/except).

## 6. Поток в `src/api/server.py`

Секция обычного (не review/hint) хода, ветка «до `run_agent`» и «после»:

1. **Перед `run_agent`** (~`server.py:1203–1217`), если
   `settings.bandit_enabled` и есть `store`, непустые `student_id` и `topic`,
   **и у сессии нет «висящего» задания** (`session.bandit_arm is None` — иначе
   текущий ход это ответ ученика, и менять целевую сложность не нужно):
   - фичи = `build_features(...)` из `store.get_topic(...)`
     (`accuracy`/`attempts`) + `student_profile.fatigue_level` + общий уровень;
   - `bandit = store.get_topic_bandit(student_id, topic)`;
   - `rec_arm = bandit_select(bandit, features, current_arm=1)`;
   - в конец `context` (перед передачей в `run_agent`) добавить
     `{"role": "system", "content": "[Адаптивный совет: рекомендуемая сложность
     задания — «<arm_difficulty>». Учитывай её при выборе сложности, но решение
     за тобой.]"}`;
   - JSONL `INFO bandit.select` (`trace_id`, `topic`, `features`, `arm`,
     `difficulty`).
2. **После `run_agent`** (после разбора `envelope`): если
   `settings.bandit_enabled` и `envelope.type` в `{quiz, practice}` —
   `session.bandit_arm = rec_arm`; `session.bandit_topic = topic`;
   `session.bandit_features = features` (тот же контекст, что при выборе).
3. **Ветка `evaluation`** (внутри `if envelope.type.value == "evaluation"`,
   ~`server.py:1239–1337`), после сборки `record`:
   если `session.bandit_arm is not None` и `session.bandit_topic == body.topic`:
   - `reward = 1.0 if record.correct else 0.0`;
   - обновление бандита **тем же контекстом** `session.bandit_features`, что был
     при выборе руки (канонический LinUCB), `bandit_update(...)` и
     `store.set_topic_bandit(...)`;
   - сброс `session.bandit_arm = None` (и `bandit_features`); JSONL
     `INFO bandit.update`.

Сбои bandit (БД, математика) оборачиваются `try/except` — чат не падает
(тот же принцип fail-soft, что у mastery/wiki).

## 7. `ChatSession` (`src/api/session_store.py`)

Добавить поля dataclass: `bandit_arm: int | None = None`,
`bandit_topic: str = ""`, `bandit_features: list[float] = field(...)`.
Значения живут только в памяти (время жизни сессии), не персистятся отдельно;
персистится только сам бандит.

## 8. Наблюдаемость

`JsonlLogger` (тот же файл `logs/agent.jsonl`):
- `bandit.select` — {trace_id, topic, student_id, features, arm, difficulty};
- `bandit.update` — {trace_id, topic, student_id, arm, reward, arms_n}.

## 9. Тесты

- `tests/test_linucb.py` (юнит):
  - `make_bandit` — A=I_d, b=0, n=0, d/alpha/arms из параметров;
  - `difficulty_arm`/`arm_difficulty` — round-trip и клампинг;
  - `build_features` — порядок, клампы (attempts>10, fatigue>1, без попыток → 0.5);
  - холодный старт: `bandit_select` возвращает `current_arm` при нулевом опыте;
  - после обновлений высоконаграждаемая рука выбирается стабильнее;
  - JSON-сериализация: select/update корректны после
    `json.loads(json.dumps(bandit))`;
  - `bandit_update` увеличивает `n`, `A` остаётся симметричной PD.
- `tests/test_store.py` (или существующий файл store-тестов):
  - миграция добавляет `bandit` на предсозданной старой схеме;
  - `get_topic_bandit` — свежий дефолт без строки/значения; round-trip
    `set`/`get`.
- `tests/test_api.py` (интеграция): фейковый runtime (стиль существующих тестов)
  — первый ход возвращает `quiz`, в контекст добавлен совет; второй ход
  `evaluation` вызывает update (проверка через вызов-заглушку store либо
  чтение `topics.bandit`).

## 10. Объём вне изменений

- Фронтенд и SSE/JSON-форматы не меняются.
- `src/student/adaptive.py` (legacy level/рекомендации) остаётся: LinUCB —
  отдельный советник, не заменяет его.
- Эвристика сложности в SM-2/quiz-карточках (`difficulty` карточки) не
  переписывается.

## 11. Что осталось вне данного этапа

- Блиц повторений (`review_request`) не играет рук бандита (карточки имеют свою
  difficulty) — возможное развитие.
- Показ «рекомендованной сложности»/истории бандита в UI — отдельно.
- Наблюдение за «долей следования совету» модели — метрика для будущего
  включения жёсткого режима.
