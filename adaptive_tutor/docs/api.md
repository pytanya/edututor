# Adaptive Tutor — HTTP API (для фронтенда)

Базовый URL: `http://127.0.0.1:8000`. Запуск: `.venv/Scripts/python.exe -m src.api.server`.

Все запросы/ответы — JSON. CORS разрешён для `localhost`/`127.0.0.1` на любом порту.

---

## Health

`GET /health`

```json
{
  "status": "ok",
  "region": "RU",
  "sessions": 3,
  "knowledge_chunks": 0,
  "rag_enabled": true
}
```

`region` берётся из `TUTOR_REGION` (`RU` → RouterAI + Yandex→DuckDuckGo; `GLOBAL` → OpenRouter + Tavily→DuckDuckGo).

---

## Чат (синхронный)

`POST /chat`

Body:

```json
{
  "message": "Объясни, что такое квадратное уравнение",
  "session_id": "ses_abc",          // опционально; пустое/отсутствующее -> сервер создаст и вернёт
  "student_id": "stu_abc",          // опционально; пустое -> сервер генерирует стабильный fallback (см. ниже)
  "student_profile": {              // опционально, адаптация (DeepTutor)
    "current_knowledge_level": 0.7, // 0..1
    "learning_style": "visual",     // visual|auditory|kinesthetic|reading
    "fatigue_level": 0.2            // 0..1
  },
  "topic": "квадратные уравнения",  // опционально: тема для авто-провижининга RAG
  "subject": "алгебра",             // опционально
  "grade": "8 класс"                // опционально
}
```

Ответ `200`:

```json
{
  "reply": "Ответ репетитора (markdown, формулы в $...$ / $$...$$ для MathJax/KaTeX)",
  "error": null,
  "trace_id": "…",
  "session_id": "ses_abc",
  "difficulty": "medium",
  "steps": 3,
  "terminated": true
}
```

Ошибки: `422` — пустое/whitespace `message`; `500` — внутренняя ошибка.

---

## Чат (SSE-стрим)

`POST /chat/stream` — то же тело, что у `/chat`. Ответ — `text/event-stream`.
Формат кадра:

```
event: <тип>
data: {json}
```

События:

| event          | data (JSON)                                              |
|----------------|----------------------------------------------------------|
| `agent.step`   | `{action, model, tool, status, reason?, step}`           |
| `agent.tool`   | `{name, status}`                                         |
| `agent.finalize` | `{status}`                                             |
| `message`      | `{content, envelope, adaptive, error, session_id}` — финальный ответ хода. За один запрос может прийти **несколько** `message`-событий (см. блиц повторений) |
| `done`         | `{session_id, trace_id, steps}`                          |
| `error`        | `{message}`                                              |
| `heartbeat`    | `{ts}` (каждые 15 с простоя)                             |
| `system`       | `{kind: "mastery.gate", message, gaps, topic}` — advisory-совет повторить незакрытые пререквизиты темы (обучение не блокируется) |
| `graph.ready`  | `{root, stats: {nodes, edges}}` — фоновая LLM-сборка графа источника завершена (первый фрейм следующего хода) |

`system` (`mastery.gate`) и `graph.ready` эмитятся только на SSE-канале `/chat/stream`
и **не** приходят в `POST /chat`:

Фронтенд ждёт `message` (ответ целиком, рендерить markdown+LaTeX), завершение — по `done`.
События не содержат chain-of-thought, секретов или полных промптов. Поле `envelope` —
санитизированный конверт контента (без скрытого `_correct_answer`/`correct_answer`).

Тело запроса `/chat` и `/chat/stream` дополнительно принимает поле `kind`:
`"message"` (по умолчанию), `"hint_request"` (кнопка «Подсказка»),
`"review_request"` (старт блица повторений, `message` может быть пустым).

---

## История диалога

- `GET /chat/history/{session_id}` → `{"session_id": …, "messages": [{"role":"user"|"assistant","content":…}, …]}`; `404`, если сессии нет.
- `DELETE /chat/history/{session_id}` → `204` (удаляет сессию и историю); `404`, если нет.

Сессии живут в памяти и автоматически удаляются после ~1 часа бездействия.

### Привязка сессии <-> ученик

- Сессия привязана к `student_id`: первый непустой `student_id` закрепляется за
  сессией (`session.student_id`) на весь её жизненный цикл.
- Если `student_id` в запросе пустой — сервер генерирует fallback
  (`stu_<hex>`), фиксирует его на сессии и возвращает в блоке `adaptive`.
  Повторные ходы без `student_id` продолжают того же fallback-ученика —
  прогресс между ходами не теряется.
- Владение: запрос с непустым чужим `student_id` по занятой `session_id`
  НЕ получает историю — сервер создаёт новую сессию (без утечки чужого
  контекста).
- Фронтенд стартует новую тему (TopicForm / «Изучить →») с пустым
  `session_id`, чтобы получить свежую сессию, а «мёртвый» `session_id` после
  `404` на `/chat/history` не сохраняет.

---

## Профиль ученика (карточка знакомства)

- `GET /student/{student_id}` → 200: `{student_id, name, learner_type, grade, topics, recommended_next}`;
  404, если ученик неизвестен.
- `POST /student/{student_id}/profile` body:
  `{"name": "Иван Иванов", "learner_type": "schoolchild", "grade": "8 класс"}` →
  сохраняет профиль (upsert, для нового id не 404) и возвращает
  `{student_id, name, learner_type, grade}`.
  `learner_type`: `"" | "student" | "schoolchild"`; `name` при непустом — минимум два слова,
  иначе `422`. Фронтенд шлёт этот POST из «карточки знакомства» (fail-soft: при недоступном
  сервере профиль хранится в localStorage и POST повторяется при следующем запуске).

---

## Knowledge (RAG-провижининг)

Векторная база наполняется сниппетами веб-поиска по теме (как source-finder в референсе):
ищутся материалы по `subject/grade/topic`, индексируются с метаданными
`subject/grade/topic/source`, и агентский инструмент `rag_search` возвращает их с источниками.

- `POST /knowledge/provision` body `{topic, subject?, grade?}` → `{"indexed": N, "topic": …}`.
  Повторный вызов той же темы не индексирует заново (`indexed: 0`).
- `GET /knowledge` → `{"rag_enabled", "ingested_chunks", "provisioned_topics"}`.

Авто-провижининг: если в `POST /chat`/`/chat/stream` передан `topic`, материалы
ищутся автоматически перед запуском агента (best-effort, ошибки не роняют чат).
RAG отключён, если `TUTOR_EMBEDDING_PROVIDER=api` (пока не реализован API-эмбеддер) —
в этом случае `rag_enabled: false` и `rag_search` вернёт ошибку инструмента.

---

## Карточки повторений: статистика (SM-2)

`GET /student/{student_id}/review`

Ответ `200` (для неизвестного ученика и при сбое БД — пустые статистика/`due`, не `404`):

```json
{
  "stats": {
    "total": 3,
    "due": 1,
    "lapses": 1,
    "by_topic": { "арифметика": 3 }
  },
  "due": [
    {
      "card_id": "a1b2c3d4e5f60718",
      "subject": "математика",
      "topic": "арифметика",
      "question": "Сколько будет 2+2?",
      "options": ["3", "4", "5"],
      "answer_type": "single",
      "difficulty": "medium",
      "added_at": "2026-09-04T10:00:00",
      "last_reviewed": "",
      "due_at": "2026-09-04T10:00:00",
      "interval_days": 1.0,
      "ease": 2.5,
      "reps": 0,
      "lapses": 0
    }
  ]
}
```

- `stats.total` — всего карточек в банке; `stats.due` — «созревших» к повторению;
  `stats.lapses` — суммарное число сбросов (неверных ответов); `stats.by_topic` —
  число карточек по темам.
- `due` — карточки, готовые к повторению (сортировка по `due_at`, максимум 50).
  `options` — `null` или список вариантов для закрытого вопроса; для открытого
  вопроса `options` отсутствует/`null`, а `correct_answer` хранит эталон
  (в самом ответе пользователю он не показывается).

---

## Блиц повторений в чате (`kind="review_request"`)

Запускается через существующий чат: `POST /chat/stream` (или `POST /chat`) с
полем `kind: "review_request"` и пустым `message` — карточки шлёт сервер, агент
в этом режиме **не вызывается**. Остальные поля тела — как у обычного чата
(`session_id`, `student_id`, `topic`, `subject` …). Дальнейшие ответы на карточки
идут обычными ходами с `kind: "message"` (например, `message: "Ответ: 4"`).

Порядок ходов («очередь блица»): карточка N отдана → следующий ход грейдит ответ
на неё и (если остались) выдаёт карточку N+1. За ход приходит **1–2**
`message`-события, затем одно `done`. Внутри `message` тип блока виден по
`envelope.type`:

| Ход | `message`-события |
|-----|-------------------|
| Старт блица (`kind="review_request"`) | первая due-карточка — `quiz` |
| Ответ на карточку | `evaluation` (оценка предыдущего ответа) → следующая `quiz` (если осталась) |
| Ответ на последнюю карточку | `evaluation` → `theory`-итог «Повторение завершено: верно X из Y.» |
| Due-карточек нет | одно `theory` «Карточек на повторение нет.» |

Особенности:

- Каждое `message`-событие несёт `{content, envelope, adaptive, error, session_id}`;
  конверты санитизированы (без `correct_answer`).
- Review-`quiz`-конверт помечен `payload.review = true`, содержит `card_id`
  вида `review:<id>`, `question_num` / `num_questions`, `answer_type`, `options` —
  фронтенд показывает бейдж «повторение» и не ждёт ответа на кнопку «Подсказка».
- Закрытые карточки (есть `options`) грейдятся детерминированно сравнением
  выбора с эталоном; открытые — коротким LLM-грейдером (роль `fast`).
- Повторный `kind="review_request"` во время активного блица игнорируется
  (0 `message`-событий, только `done`).
- SM-2-состояние карточки обновляется при каждом ответе; ответы сбрасывают
  «зрелость» повторно ошибочных карточек (`due_at` в ближайшее время).

---

## Граф знаний (E2): мастерство ученика и граф источника

Трёхслойная модель:

- **Слой 2 — мастерство ученика**: живёт в SQLite (`topics` профиля) и отвечает
  на вопросы «что ученик знает/повторил» (статусы, EMA-мастерство, weak_areas,
  relations, рекомендации). Эндпоинты `/knowledge-graph` и `/recommendations`.
- **Слой 1 — граф источника**: сеть узлов/рёбер предмета `subject|grade`
  (`book`/`lesson`/`section`/`topic`/`concept`), собираемая LLM-онтологом из
  RAG-сниппетов с эвристическим фолбэком и JSON-кэшем. Эндпоинты `/graph`,
  `/graph/{node}/related`.
- **Слой 3 — Knowledge Wiki**: конспекты ученика с мастерством и заметками об
  ошибках. E2-эндпоинт `/graph/{node}/wiki` остаётся заглушкой `wiki: null`;
  реальные статьи в E4 отдаются отдельными эндпоинтами
  `/student/{student_id}/wiki/...` (см. раздел «Knowledge Wiki (E4)» ниже).

### Модель мастерства (Слой 2)

- **EMA-мастерство** (`mastery`, 0..1): после каждого ответа
  `mastery = 0.7 · mastery_old + 0.3 · score01`; на первом ответе вместо
  `mastery_old` берётся якорь `0.5`.
- **Статус темы** (автоматический, по `attempts` и `mastery`):
  `not_studied` (0 попыток) → `in_progress` (>0 попыток) → `mastered`
  (`attempts >= 3` и `mastery >= 0.8`). Пороги настраиваются конфигом.
- Поля темы в ответах (`row_payload`): `topic`, `subject`, `status`, `mastery`,
  `attempts`, `correct`, `accuracy` (верные/попытки, 0.0 без попыток),
  `weak_areas` (фидбек неверных ответов, ≤3), `last_seen`, `relations`
  (`{prerequisite: [...], related: [...]}`).
- `accuracy` также считается от верных ответов, когда поле не хранится.

### `GET /student/{student_id}/knowledge-graph`

Слой 2: статусы и статистика тем ученика. `?subject=` фильтрует по предмету
(пусто — все предметы). Неизвестный ученик — не `404`, а пустые `topics`/`stats`.

```json
{
  "student_id": "stu_abc",
  "subject": "алгебра",
  "topics": {
    "квадратные уравнения": {
      "topic": "квадратные уравнения",
      "subject": "алгебра",
      "status": "in_progress",
      "mastery": 0.5,
      "attempts": 1,
      "correct": 1,
      "accuracy": 1.0,
      "weak_areas": [],
      "last_seen": 1756980000.123,
      "relations": { "prerequisite": [], "related": [] }
    }
  },
  "stats": { "mastered": 0, "in_progress": 1, "not_studied": 0, "total": 1 }
}
```

`topics` — словарь `{topic: payload}`; `stats.mastered` считается по `is_mastered`
(статус `mastered` ИЛИ порог попыток/мастерства).

### `GET /student/{student_id}/recommendations`

Рекомендации тем. Query: `current_topic`, `subject`, `limit` (1..20, по умолчанию 5).
Порядок: слабые темы (`attempts >= 2`, `accuracy < 0.5`) → пробелы пререквизитов
`current_topic` → темы `in_progress` → `not_studied` без открытых пререквизитов.
Ответ — нормализованные `row_payload`; сбой БД даёт пустые списки:

```json
{
  "recommendations": [ { "topic": "…", "status": "…", "mastery": 0.0, "attempts": 0, "correct": 0, "accuracy": 0.0, "weak_areas": [], "relations": { "prerequisite": [], "related": [] } } ],
  "weak_topics": [],
  "prerequisite_gaps": []
}
```

### `GET /student/{student_id}/graph`

Слой 1: граф источника `subject|grade` с оверлеем мастерства ученика. Query:
`subject`, `grade`. Недостающие `subject`/`grade` берутся из последней сессии
ученика (если их нет вовсе — пустой/деградировавший граф).

Запрос **не блокируется на LLM**: если готового графа нет ни в JSON-кэше, ни в
in-memory реестре, возвращается мгновенный **каркас** — корень
`book:{subject|grade}` + узлы известных тем с рёбрами `part_of`, а полная
LLM-сборка запускается **fire-and-forget** (`_ensure_graph_build`; повторный
запрос того же `subject|grade` не дублирует задачу). О готовности сообщает
SSE-событие `graph.ready` первым фреймом следующего хода `/chat/stream`.

Узлы: базовые поля `{id, title, type, color}` (+ опционально `section_number`,
`parent_id`). Для тем, которые ученик уже изучал, добавляется оверлей мастерства
`mastery` / `attempts` / `correct` / `accuracy` / `status`. Сопоставление
«тема ученика ↔ узел» идёт по **нормализованному заголовку** `_norm_topic`:
срезается префикс-номер («Урок 12: …», «Параграф 5 …») с регистронезависимостью,
пробелы сжимаются, регистр приводится к нижнему.

Рёбра графа (`prerequisite`/`related`/`part_of`) при чтении **разово
синхронизируются** в `relations` тем ученика (для тем без отношений) — так гейт
пререквизитов (см. SSE `mastery.gate`) знает зависимости.

```json
{
  "root": "book:алгебра|8_класс",
  "nodes": [
    { "id": "book:алгебра|8_класс", "title": "Учебник «алгебра|8_класс»", "type": "book", "color": "#F4A261" },
    { "id": "topic:квадратные_уравнения", "title": "Квадратные уравнения", "type": "topic", "color": "#69F0AE", "parent_id": "book:алгебра|8_класс", "mastery": 0.5, "attempts": 1, "correct": 1, "accuracy": 1.0, "status": "in_progress" }
  ],
  "edges": [ { "source": "book:алгебра|8_класс", "target": "topic:квадратные_уравнения", "relation": "part_of" } ],
  "active_topic": null,
  "stats": { "nodes": 2, "edges": 1 }
}
```

### `GET /student/{student_id}/graph/{node_id}/related`

Соседи узла по исходящим рёбрам до глубины 2 (для «клик по узлу → детали»).
Неизвестный `node_id` — `{"node": null, "related": []}`.

```json
{
  "node": { "id": "topic:…", "title": "…", "type": "topic", "color": "#69F0AE" },
  "related": [
    { "source": "topic:…", "source_title": "…", "relation": "prerequisite", "target": "topic:…", "target_title": "…", "target_type": "topic", "depth": 1 }
  ]
}
```

### `GET /student/{student_id}/graph/{node_id}/wiki`

Заглушка Слоя 3: возвращает узел и `wiki: null` — статья графа отдельно не
собирается. Реальные статьи ученика (конспекты E4) живут в Knowledge Wiki и
читаются через `GET /student/{student_id}/wiki/{subject}/{topic}`.
Неизвестный `node_id` → `404`.

```json
{ "node": { "id": "topic:…", "title": "…" }, "wiki": null }
```

### SSE-события графа на `/chat/stream`

- `system` `{kind: "mastery.gate", message, gaps, topic}` — advisory-событие
  при выборе темы с незакрытыми пререквизитами (по `relations` темы, которых нет
  или которые не освоены). Уходит system-фреймом ДО основного `message`; обучение
  **не блокируется** — фронтенд показывает баннер с кнопками «Всё равно
  продолжить» / «Перейти к „gap“». Эмитится один раз на смену темы сессии.
- `graph.ready` `{root, stats: {nodes, edges}}` — фоновая сборка графа
  `subject|grade` завершена и сохранена; накопленное событие выплёвывается
  первым фреймом следующего SSE-хода этой сессии (в `POST /chat` не приходит),
  после чего фронтенд перезапрашивает `/graph`.

---

## Knowledge Wiki (E4): конспекты ученика

Персональные статьи-«конспекты» (Слой 3) хранятся на диске как markdown
OKF v0.2: `data/knowledge_wiki/<student_id>/<slug(subject)>/<slug(topic)>.md`
(плюс `_index.md` предмета). Статья создаётся на первый оценённый ответ
(`evaluation`/повторение) и далее обновляется EMA-мастерством и заметками об
ошибках; пустое тело лениво обогащается LLM-конспектом. `student_id` берётся из
URL, авторизации нет — тот же режим доверия, что и у `/student/{id}/sessions`.
Slug по теме (unicode-safe): `Дроби и Деление` → `дроби-и-деление`.

Формат `article_dict` (поля статей в ответах): frontmatter-ключи
`okf_version: "0.2"`, `type: "Topic"`, `title`, `topic`, `subject`, `grade`,
`curriculum`, `mastery` (0..1, EMA), `accuracy` (производная
`correct/attempts`), `attempts`, `correct`, `last_studied`, опционально
`section_number`, `weak_areas`, `relations`, `notes` (`[{date, feedback,
question?, student_answer?, correct_answer?}]`), `concepts`, `source` — плюс
ключ `body` с текстом конспекта.

### `GET /student/{student_id}/wiki`

Список статей. Без параметров — все предметы:

```json
{
  "subjects": [
    { "subject": "математика", "articles": [ { "topic": "…", "mastery": 0.65, "attempts": 1, "accuracy": 1.0, "body": "…", "…": "…" } ] }
  ]
}
```

С `?subject=<имя>` — только статьи предмета (имя сопоставляется по slug;
`subject` в ответе — человеческое имя из frontmatter):

```json
{ "subject": "математика", "articles": [ { "…": "…" } ] }
```

### `GET /student/{student_id}/wiki/{subject}/{topic}`

Одна статья. URL-сегменты — человеческие имена (поиск по slug), возвращается
полный `article_dict`. Неизвестная тема → `404`:

```json
{ "detail": "Тема не найдена в базе знаний" }
```

### `POST /student/{student_id}/wiki/enrich`

Ленивое LLM-обогащение тела статьи конспектом по RAG-материалам темы. Body:

```json
{ "subject": "математика", "topic": "квадратные уравнения" }
```

Ответ `200` — `{"article": <article_dict|null>, "note": ""}`. `note` несёт
подсказку, если обогащение невозможно: «Wiki выключено.», «Нет материалов по
теме в базе знаний — пройдите квиз или добавьте источник, затем повторите.»
или «Не удалось сформировать конспект.» (статья остаётся прежней, ошибки не
роняют запрос). Требуется существующая статья (после хотя бы одного ответа) и
RAG-материалы темы.

### `DELETE /student/{student_id}/wiki/{subject}/{topic}`

Удаляет статью ученика (пересоздаёт `_index.md` предмета). Успех — `200`:

```json
{ "deleted": true, "subject": "математика", "topic": "квадратные уравнения" }
```

Повторное удаление / отсутствующая тема — `404` (`detail`: «Тема не найдена в
базе знаний»).

---

## Экспорт для учителя (E4): CSV и OKF

Журнал ответов хранится в SQLite-таблице `session_records`
(`data/students.db`): `record_id` вида `rec_<uuid12>`, `ts` (unix), `subject`,
`topic`, `question_id` (ответы блица — `review:<card_id>`), `question`,
`options` (JSON-список или `null`), `answer_type`, `difficulty`,
`student_answer`, `correct`, `feedback`, `score01`. Окно выдачи — последние
`limit` записей (по умолчанию 500).

CSV-ответы — скачивание: `utf-8-sig` (BOM для Excel), `Content-Disposition:
attachment`. Общие query-параметры: `subject` (фильтр предмета), `limit`
(1..N, по умолчанию 500). Неизвестный ученик/пустая история не дают `404` —
CSV только с заголовком.

### `GET /student/{student_id}/export/csv`

Подробный журнал вопросов. Колонки: `timestamp` (ISO из unix-ts), `session_id`,
`subject`, `topic`, `question_id`, `question`, `options` (элементы списка через
`" | "`), `answer_type`, `difficulty`, `student_answer`, `score01`, `correct`
(0/1), `feedback`.

```
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="stu_1_session_log.csv"
```

### `GET /student/{student_id}/export/summary.csv`

Сводка по сессиям (одна строка на сессию). Колонки: `session_id`, `subject`,
`topic` (список тем через `" | "`), `started_at`, `ended_at`, `questions`,
`correct`, `accuracy`, `mastered_topics` (освоенные темы сессии).

```
Content-Disposition: attachment; filename="stu_1_summary.csv"
```

### `GET /student/{student_id}/export/okf?subject=&grade=`

Эмитит OKF-бандл графа источника `subject|grade` и возвращает манифест. Если
`subject` пуст — берётся предмет последней записи ученика (иначе
`общая тема`). Бандл пишется в `data/okf/<student_id>/<slug(subject)>/`:
`index.md`, `log.md`, `topics/<slug(title)>.md` для не-`book` узлов (с
`relations`/`section_number` и `mastery` темы в frontmatter при наличии).
Мастерство узлов берётся из тем ученика (попытки > 0).

```json
{
  "dir": "C:/…/adaptive_tutor/data/okf/stu_1/алгебра",
  "conformant": true,
  "errors": [],
  "files": [ "index.md", "log.md", "topics/квадратные-уравнения.md" ]
}
```

`conformant` — прошли ли все `*.md` валидацию (frontmatter `---`, валидный YAML,
непустой `type`); `errors` — список замечаний; `files` — относительные пути всех
markdown-файлов бандла.
