# E1 — Карточки для повторений (spaced repetition, SM-2): дизайн

> Статус: утверждено к планированию. Реализация не выполнялась.
> Этап 1 из gap-аудита `docs/superpowers/2026-09-04-gap-audit-edututor-vs-reference.md`.
> Референс: `C:\otus\project_work` (src/review.py, evaluation.py, api/routes/*, frontend).

## 1. Цель и границы

Добавить в edututor автономную подсистему интервальных повторений:

- **Модель карточки** (вопрос, варианты, правильный ответ, тип ответа, сложность, SM-2-параметры).
- **Хранилище** — таблица `review_cards` в SQLite (`data/students.db`, тот же файл, что профили).
- **SM-2-алгоритм** (тот же, что в референсе) с учётом верного/неверного ответа.
- **Авто-добавление**: ошибочный ответ на квиз → карточка (дедуп по вопросу).
- **Блиц «Повторить (N)»**: кнопка в UI, сервер раздаёт due-карточки по одной как quiz-конверты,
  ответы грейдятся без LLM-агента (закрытые — сравнением, открытые — LLM), применяется SM-2,
  новая карточка из ответа на review-карточку НЕ создаётся.
- **API**: `GET /student/{id}/review` (статистика + due), запуск блица через существующий
  `POST /chat/stream` c `kind="review_request"`.
- **UI**: бейдж «повторение» на quiz-блоке, кнопка «Повторить (N)» с живым счётчиком due.

**Вне границ (другие этапы):** Student Knowledge Graph/mastery (E2), Knowledge Wiki (E4),
экспорт (E4), онбординг (E3). EMA/mastery-логика E2 не проектируется здесь; в E1 вводится
только общий источник данных — «answer record» (раздел 3), который в E2/E4 будут потреблять.

## 2. Существующее состояние (что переиспользуем)

- Конверты агента: `ContentEnvelope(type=quiz|evaluation|…)`, `payload` — `src/models/schemas.py:62`.
- История сессии хранит `meta.kind` + полный `meta.envelope` у assistant-сообщений
  (`session_store.append_message`, `src/api/server.py:344-350`), т.е. сервер уже видит
  «последний quiz-конверт».
- Сервер обновляет профиль после evaluation в `_run_chat` (`src/api/server.py:354-366`) —
  единственная точка интеграции.
- SSE `POST /chat/stream` возвращает события `agent.step/agent.tool/message/done`
  (`src/api/server.py:448-512`), фронтенд-редьюсер `feedReducer` умеет накапливать
  **несколько** `message`-событий в одном стриме (`frontend/src/components/Chat.jsx:11`).
- QuizBlock рендерит `answer_type == "single"` как кнопки (шлёт `Ответ: <opt>`), иначе —
  инпут (`frontend/src/components/QuizBlock.jsx:4`); EvaluationBlock — ✅/❌ по `payload.correct`.
- `ChatRequest.kind: Literal["message","hint_request"]` (`server.py:74`).

## 3. Общий источник данных: «answer record» (серверная склейка)

**Проблема:** правильный ответ квиза неизвестен серверу (LLM его не должен показывать ученику),
а без него нельзя ни создать карточку, ни корректно грейдить повторение. Доверять LLM в том,
что он повторит вопрос в evaluation, нельзя.

**Решение:**

1. Промпт конверта `quiz` расширяется скрытым полем:
   - `payload["_correct_answer"]` (str) — эталонный ответ. Для `answer_type == "single"` это
     текст правильного варианта (обязательно совпадает с одной из `options`); для `open` —
     эталонная формулировка.
   - Промпт требует: никогда не помещать правильный ответ в видимый `text`/`options`.
2. **Санитайзер** `sanitize_envelope(env: ContentEnvelope) -> ContentEnvelope`: возвращает копию
   без ключей payload, начинающихся с `_`, и без ключа `"correct_answer"`. Применяется к любому
   конверту, который покидает сервер (SSE `message`, ответ `POST /chat`) И к копии, сохраняемой
   в `meta.envelope` истории сессии — так `_correct_answer` никогда не попадает в
   `GET /chat/history/{session_id}` (resync).
3. **Секрет квиза живёт на сессии, не в истории.** При сохранении assistant-сообщения вида
   `quiz` сервер (до санитизации meta) делает снимок
   `session.last_quiz = {question, options, answer_type, difficulty, correct_answer, topic?,
   subject?}` — полноценный quiz-вопрос с эталоном — и затем кладёт в историю санитизированный
   конверт. `last_quiz` затирается при следующем quiz-сообщении и после обработки evaluation.
4. При приходе evaluation-конверта сервер строит **answer record**:
   - `student_answer` = текст последнего user-сообщения в сессии (то, что уже добавлено в
     историю до ответа агента);
   - `topic` = `body.topic` (ключ темы), `subject` = `body.subject`, `correct`/`feedback` — из
     evaluation;
   - поля квиза — из `session.last_quiz` (если не None): `question`, `options`, `answer_type`,
     `difficulty`, `correct_answer`.
   Если `last_quiz` пуст (evaluation без предшествующего квиза) — record строится без полей
   квиза (`question=""`): **мастерство/статистика обновляются всегда** (correct/topic известны),
   а карточка/Wiki/CSV-строка с полным вопросом — только при наличии квиза.
5. Answer record — **чистая функция-модель** в новом модуле без БД:
   `src/student/answer_record.py`: `build_answer_record(..., last_quiz, student_answer, topic,
   subject, correct, feedback, session_id) -> AnswerRecord`.
   E2 (mastery/статусы) и E4 (wiki/CSV) читают ответ из той же функции — единый источник.

**Расширение `ChatSession`** (`session_store.py`): поля `subject: str = ""`,
`last_quiz: dict | None = None` + review-поля (раздел 5.2). `subject` проставляется из
`ChatRequest.subject` при `get_or_create` (в `_run_chat`), `grade` — аналогично если потребуется
(поле `grade` тоже добавить для E2/E4). История сессии содержит только санитизированные
конверты — утечки эталона нет.

## 4. Хранилище: таблица `review_cards`

Схема (новый `CREATE TABLE IF NOT EXISTS` в `_SCHEMA` модуля `src/student/store.py`):

```sql
CREATE TABLE IF NOT EXISTS review_cards (
  student_id TEXT NOT NULL,
  card_id    TEXT NOT NULL,          -- sha256(question)[:16]
  subject    TEXT DEFAULT '',
  topic      TEXT DEFAULT '',
  question   TEXT NOT NULL,
  options    TEXT DEFAULT NULL,      -- JSON-массив или NULL
  answer_type TEXT DEFAULT 'open',
  correct_answer TEXT DEFAULT '',
  difficulty TEXT DEFAULT 'medium',
  added_at   TEXT DEFAULT '',        -- ISO naive, seconds
  last_reviewed TEXT DEFAULT '',
  due_at     TEXT DEFAULT '',        -- ISO naive; '' => due сразу
  interval_days REAL DEFAULT 1.0,
  ease       REAL DEFAULT 2.5,
  reps       INTEGER DEFAULT 0,
  lapses     INTEGER DEFAULT 0,
  PRIMARY KEY (student_id, card_id)
);
CREATE INDEX IF NOT EXISTS idx_review_due
  ON review_cards (student_id, subject, due_at);
```

- Один SQLite-connection + `threading.Lock` (тот же паттерн `StudentStore`), все мутации —
  в транзакции. Файл БД общий: `settings.resolved_student_db_path`.
- `card_id = sha256(question.strip().encode("utf-8")).hexdigest()[:16]`.
- Лимит банка `max_cards = 200` на ученика: при добавлении НОВОЙ карточки удаляются самые
  старые по `added_at` сверх лимита (за один UPDATE/DELETE; порядок по дате добавления).
- Конфиг в `src/config.py`: `review_enabled: bool = True`, `review_quiz_size: int = 5`,
  `review_bank_max_cards: int = 200`.

### SM-2-алгоритм (точные формулы, 1:1 с референсом)

`review_card(student_id, card_id, correct)`:

- Верный ответ: `reps += 1`; если `reps == 1` → `interval_days = 1.0`, иначе
  `interval_days = round(interval_days * ease, 1)`;
  `ease = max(1.3, round(ease + (0.1 - max(0, 3 - reps) * 0.05), 2))`.
- Неверный ответ: `reps = 0`, `interval_days = 1.0`, `lapses += 1`,
  `ease = max(1.3, round(ease - 0.2, 2))`.
- Затем в обеих ветках: `last_reviewed = now_iso()`; `due_at = iso(now + timedelta(days=interval_days))`.
- Стартовые значения карточки: `interval_days=1.0, ease=2.5, reps=0, lapses=0,
  last_reviewed=""`, `due_at = added_at = now` (карточка due сразу).
- `is_due`: пустой/непарсящийся `due_at` → True; иначе `due_at <= now`.

`add_from_record(record)`:
- Пустой `question` → False (нет записи).
- `cid = card_id_for(question)`; карточки нет → создать (поля выше) и добавить; есть → обновить
  только `topic/subject/correct_answer` непустыми значениями и сбросить `last_reviewed=""`
  (SM-2-состояние существующей карточки НЕ трогать). Возвращает True только для новой.
- Лишние карточки сверх `max_cards` — отсечь старые по `added_at`.

### Методы хранилища (интерфейс на `StudentStore`)

```python
add_review_card(student_id, record: dict) -> bool          # False если существовала
get_due_review(student_id, subject: str = "", limit: int = 5) -> list[dict]
review_card(student_id, card_id: str, correct: bool) -> dict | None
review_stats(student_id) -> dict  # {total, due, lapses, by_topic}
list_review_cards(student_id) -> list[dict]  # полные карточки (для GET /review, лимит 50 due)
```

## 5. Потоки

### 5.1 Авто-добавление при ошибке квиза

В `_run_chat` (в блоке обработки evaluation) при `envelope.type == "evaluation"`:
- построить answer record (раздел 3) из `session.last_quiz` и последнего user-сообщения;
- `session.last_quiz = None` (квиз «закрыт» — повторного record не будет);
- если `record.correct == False` и quiz найден (есть question/correct_answer) →
  `store.add_review_card(student_id, record_fields)`;
- если quiz не найден или правильный ответ отсутствует → карточку не создавать (fail-soft),
  записать в observability-лог `review.skip` с причиной.

### 5.2 Блиц (review mode)

**Режим сессии** — поля на `ChatSession` (`session_store.py`):
`review_requested: bool = False`, `review_active: bool = False`,
`review_cards: list = []`, `review_index: int = 0`, `review_correct: int = 0`, `review_reviewed: int = 0`.

**Запуск** — кнопка «Повторить (N)» во фронтенде шлёт обычный `POST /chat/stream` c
`kind="review_request"` и пустым `message`. Сервер (`_run_chat`) при `kind == "review_request"`:
- ставит `review_requested=True`; НЕ добавляет user-сообщение в историю; НЕ зовёт агента;
- вызывает `_review_turn(...)` и возвращает результат как в обычном ходе.

**Инвариант очереди:** карточка считается «отвечаемой» (last served), когда она уже отдана
ученику. Сервер держит только «следующий неотданный индекс»: после отдачи карточки N значение
`review_index` равно N+1, и на следующем ходе грейдится карточка `review_cards[review_index - 1]`.

**Обслуживание** — функция `_review_turn(app, session, store, student_id, incoming: str | None)`
(вызывается из `_run_chat`, события шлются в тот же SSE-стрим):
1. Если `review_requested and not review_active`:
   - `due = store.get_due_review(student_id, subject=session.subject,
     limit=settings.review_quiz_size)`;
   - `due` пусто → событие `message` (type `theory`): «Карточек на повторение нет.»;
     `review_requested=False`; возврат;
   - иначе `review_active=True`, `review_cards=due`, `review_index=0`, `review_correct=0`,
     `review_reviewed=0`, `review_requested=False`; перейти к шагу 3 (отдать первую карточку).
2. Иначе если `review_active and review_index > 0` (есть отвечаемая карточка): `incoming`
   обязан быть ответом ученика на карточку `review_cards[review_index - 1]`:
   - грейд (раздел 5.3) → `store.review_card(student_id, card_id, correct)`;
   - событие `message` (type `evaluation`, payload `{correct, feedback}`), `review_reviewed += 1`,
     при верном `review_correct += 1`;
   - перейти к шагу 3.
3. Если `review_index < len(review_cards)`: карточка `c = review_cards[review_index]`; событие
   `message` с конвертом `type="quiz"`, `text=c.question`, санитизированный payload
   `{answer_type, options, review: True, card_id: f"review:{c.card_id}", num_questions,
   question_num: review_index + 1}`; `review_index += 1`.
4. Иначе (все карточки отвечены): событие `message` (type `theory`):
   «Повторение завершено: верно {review_correct} из {review_reviewed}.»; сброс review-полей.

Агент в review-режиме не вызывается (для закрытых вопросов LLM не нужен; для открытых —
короткий вызов грейдера 5.3). В `_run_chat` review-ветка стоит ДО ветки агента: если
`review_requested or review_active` — обслуживаем очередь, добавляем нужные записи в историю
(user-ответ при шаге 2 — как обычное сообщение; assistant-ответы — с meta) и завершаем ход.

**Механика SSE (контракт для плана).** Внутренний `_run_chat` возвращает список
«сообщений для фида» (`messages: list[dict]`, каждый `{content, envelope}`):
- обычный ход → 1 элемент (финальный ответ агента);
- review-ход → 1..2 элемента (фидбек предыдущей карточки + следующая карточка, либо фидбек +
  финал, либо одна карточка на старте).
`chat_stream.run()` эмитит каждый элемент как SSE `message` (с `adaptive`), затем `done`;
`POST /chat` (fallback) использует последний элемент. Таким образом несколько `message`-событий
в одном стриме приходят без дублей и без правок фильтра `on_event`.

**Внешний вид для фронтенда** не меняется: quiz-карточка повторения рендерится обычным
QuizBlock (+ бейдж «повторение»), фидбек — EvaluationBlock, финал — обычным сообщением.

**Примечание:** review-состояние сессии живёт в памяти (TTL ~1 ч) — прерванный блиц не
восстанавливается после истечения TTL (известное ограничение, фиксируем в коде комментарием).

### 5.3 Грейдинг ответа на review-карточку (без создания карточки)

- `answer_type == "single"` и есть `options` → детерминированно: из `incoming` извлечь выбор
  (нормализация: срезать префикс `Ответ: `, сравнить по нормализованной строке c
  `card.correct_answer`). `correct = (выбор == card.correct_answer)`.
- `answer_type == "open"` → LLM-грейдер (роль `fast`, температура 0): промпт сравнивает
  ответ ученика с `card.correct_answer` по смыслу, возвращает JSON `{"correct": bool,
  "feedback": str}`; при сбое — `correct=False`, feedback «Не удалось проверить ответ».
- Результат → `store.review_card(student_id, card_id, correct)`. Карточка НЕ создаётся и НЕ
  обновляется как новая (только SM-2). `touch_topic`/mastery для review-ответов в E1 НЕ
  вызываются (статус тем — E2); примечание оставить для E2/E4, где review-ответ тоже
  обновляет mastery/wiki (как в референсе).

## 6. API

`GET /student/{student_id}/review` (в `server.py`, рядом с `/student/{id}/sessions`):
- 200: `{"stats": {total,due,lapses,by_topic}, "due": [card_dict,…]}` (due ≤ 50, без subject-фильтра).
- Исключения БД → пустой fallback `{"stats": {"total":0,"due":0,"lapses":0,"by_topic":{}}, "due": []}`.

Запуск блица — **без отдельного REST-эндпоинта**: `kind="review_request"` на `/chat/stream`.

Новый литерал: `ChatRequest.kind: Literal["message","hint_request","review_request"]`.

## 7. Конфигурация

Добавить в `src/config.py` и `.env.example`:

```python
review_enabled: bool = Field(default=True)
review_quiz_size: int = Field(default=5)
review_bank_max_cards: int = Field(default=200)
```

`review_enabled=False` отключает и авто-добавление, и блиц (fail-soft).

## 8. Фронтенд

- `api.js`: `getReview(studentId)` → `GET /student/{id}/review`.
- QuizBlock: если `payload.review` → бейдж `повторение` у «Вопрос».
- Правая колонка: блок «Повторение» в `AdaptivePanel.jsx` (или рядом): при
  `reviewStats.due > 0` — кнопка «Повторить (N)», `disabled={busy}`; клик →
  `runTurn('', 'review_request', {session_id})`. `reviewStats` грузится после каждого
  `done`-события и после каждого evaluation-хода.
- App: в `feedReducer`/обработчике `message` конверт уже попадает как элемент фида — новых
  типов элементов не требуется (quiz/evaluation блоки переиспользуются). После `done`
  перечитать due-счётчик.
- Примечание: в E2 кнопка переедет в панель «Мои знания» (StudentKG), как в референсе;
  E1 не проектирует эту миграцию.

## 9. Обработка ошибок и крайние случаи

- Пустой банк / нет due: блиц выдаёт системное сообщение, ничего не ломает.
- Битый/чужой `card_id` у review-ответа: `review_card` возвращает None → пропуск (no crash).
- Ошибка БД в любом хуке: try/except + лог, чат не падает (паттерн существующего кода).
- Дубликаты вопросов: дедуп `sha256`; повторная ошибка обновляет карточку (без сброса SM-2).
- Кап 200: отсечение по `added_at` при добавлении новой.
- Гонки: один `threading.Lock` хранилища; в review-режиме сессия блокирует повторный запуск
  блица (флаг `review_active`); параллельные сессии одного ученика допускаются, но мутации —
  атомарные транзакции SQLite.
- Санитайзер: `_correct_answer` не должен просочиться ни в один ответ фронтенду (тест).
- Не-наивные таймзоны: храним только naive ISO от `datetime.now()`.

## 10. Наблюдаемость

События агент-лупа не затрагиваются. В JSONL-лог добавляются записи:
`review.add` (auto-add: new|refreshed|skipped+причина), `review.turn` (blitz: due, отданные
карточки), `review.grade` (card_id, correct). Используется существующий `JsonlLogger`.

## 11. Тесты

Бэкенд (pytest, по образцу существующих `tests/`):
- SM-2: 1-й верный → интервал 1.0; 2-й верный → `interval*ease`; неверный → сброс + lapse + ease−0.2;
  кламп ease ≥1.3; `due_at` в будущем после верного ответа; `is_due` для пустого due_at.
- Хранилище: add/дедуп/обновление существующей без сброса SM-2/кап 200/статистика.
- Санитайзер: `_correct_answer` вырезается, остальной payload не меняется.
- `build_answer_record`: склейка последнего quiz + user-ответа; отсутствие quiz → None.
- Блиц через TestClient `/chat/stream` с `kind=review_request`: закрытая карточка грейдится
  детерминированно; агент не вызывается (заглушка runtime_factory, которая падает при вызове);
  после последней карточки — финальное сообщение и сброс режима.
- API `GET /student/{id}/review` на пустом банке → пустой fallback.

Фронтенд (vitest + существующий `blocks.test.jsx`):
- QuizBlock с `payload.review` показывает бейдж.
- `api.getReview` (мок fetch) разбирает stats.

## 12. Ключевые файлы, которые появятся/изменятся

Создать: `src/student/answer_record.py`; `src/review/sm2.py` (если нужно выделить чистую
SM-2-логику) — допустимо разместить в `src/student/store.py`, но чистая функция
`apply_sm2(card: dict, correct: bool) -> dict` обязана быть в отдельном модуле без sqlite;
`tests/test_review_sm2.py`, `tests/test_answer_record.py`, `tests/test_review_api.py`.

Изменить: `src/models/schemas.py` (`ChatRequest.kind`), `src/agent/prompts.py` (`quiz.payload._correct_answer`),
`src/student/store.py` (схема + методы), `src/config.py`, `.env.example`,
`src/api/server.py` (`sanitize_envelope`, answer-record хук, review-ветка в `_run_chat`,
`GET /student/{id}/review`), `src/api/session_store.py` (review-поля на `ChatSession`),
`frontend/src/api.js`, `frontend/src/components/QuizBlock.jsx`, `frontend/src/components/AdaptivePanel.jsx`
(или новый `ReviewBlock`), `frontend/src/App.jsx`, документация `README.md`/`docs/api.md`.
