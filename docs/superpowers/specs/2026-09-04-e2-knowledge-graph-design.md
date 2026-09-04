# E2 — Граф знаний (3 слоя): дизайн

> Статус: утверждено к планированию. Реализация не выполнялась.
> Этап 2 gap-аудита `docs/superpowers/2026-09-04-gap-audit-edututor-vs-reference.md`.
> Референс: `C:\otus\project_work` (knowledge_graph.py, student_kg.py, wiki.py, api/routes/graph.py,
> students.py, frontend KnowledgeGraphPanel/StudentKGPanel/MasteryWall).
> Зависит от answer-record (модуль из E1, `src/student/answer_record.py`).

## 1. Цель, слои и порядок

Цель — перенести граф знаний учебника/источника, Student Knowledge Graph и их визуализацию.
Три слоя, реализуются в этом порядке внутри этапа:

- **Слой 2 — Student Knowledge Graph (критично, делается первым).** Поверх SQLite: статусы
  тем (`not_studied/in_progress/mastered`), `mastery` (EMA), `weak_areas`, отношения
  `{prerequisite, related}`, авто-`mastered`, рекомендации по референс-правилам, мастерство в
  существующей адаптивной панели.
- **Слой 1 — граф учебника/источника.** Без загрузки учебников: LLM-онтолог строит граф из
  RAG-сниппетов провижининга по ключу `subject|grade` (аналог «web»-ветки референса) с
  эвристическим фолбэком и JSON-кэшем. Лёгкая зависимость `networkx`.
- **Слой 3 — Knowledge Wiki** — вынесен в **E4** (общая статья-источник мастерства/заметок).
  В E2 Wiki не реализуется, но API графа (`/graph/{node}/wiki`) зарезервировано и вернёт
  «статья не создана», пока E4 не подключено.

**Вне границ:** карточки повторений SM-2 — E1; онбординг — E3; Wiki/экспорт — E4.

## 2. Ключевые решения

1. **SQLite как источник истины по ученику.** Студентские темы — расширение существующей
   таблицы `topics` (столбцы добавляются через `ALTER TABLE ... ADD COLUMN` в миграции модуля
   `store.py`; таблица уже существует у пользователей). Граф источника — JSON-файлы на диске
   (кэш), т.к. это обезличенный контентный граф, общий для учеников одного предмета+класса.
2. **Мастерство** — EMA по бинарному score из answer record: `score01 = 1.0` если `correct`
   иначе `0.0`; `mastery = round(0.7 * old + 0.3 * score01, 4)`; якорь для первого ответа
   `old = 0.5` (при `attempts == 0` до обновления). Это даёт освоение ровно за 3 верных ответа
   подряд при пороге `attempts>=3 and mastery>=0.8` (как в референсе: 0.5→0.65→0.755→0.8285).
3. **Статус** выводится из попыток/мастерства: `attempts==0` → `not_studied`; `attempts>0` и
   НЕ mastered → `in_progress`; `attempts>=3 and mastery>=0.8` → `mastered`. Явный ручной
   `mastered` не предусмотрен (в E2 статусы только авто).
4. **Единый ключ темы** = строка `topic` из `ChatRequest` (как сейчас). В answer record и в
   граф источник передаётся та же тема, чтобы совпадали ключи `topics`, мастерства и узлов.
5. **Мастерство-гейт и reuse-гейт** — мягкие (advisory), как в референсе: баннер-совет перед
   первым вопросом квиза; блокировки нет.
6. **Порядок интеграции** — все хуки в `_run_chat` рядом с существующим `touch_topic`-блоком;
   агент ничего не знает о графе. Существующая `touch_topic(level_delta, correct)` сохраняется
   для обратной совместимости (тесты), но сервер переводится на `apply_result(record)`.
7. **networkx** добавляется в зависимости (чистый Python, без нативных сборок).

## 3. Данные ученика (Слой 2): модель и хранилище

### 3.1 Миграция таблицы `topics`

```sql
ALTER TABLE topics ADD COLUMN subject TEXT DEFAULT '';          -- (если отсутствует)
ALTER TABLE topics ADD COLUMN mastery REAL DEFAULT 0.0;
ALTER TABLE topics ADD COLUMN status TEXT DEFAULT 'not_studied'; -- not_studied|in_progress|mastered
ALTER TABLE topics ADD COLUMN weak_areas TEXT DEFAULT '[]';      -- JSON-массив строк
ALTER TABLE topics ADD COLUMN relations TEXT DEFAULT '{"prerequisite":[],"related":[]}';
```

Миграция выполняется идемпотентно при старте `StudentStore` (проверка `PRAGMA table_info(topics)`),
существующие строки получают `mastery=level`-инициализацию только если колонка новая и
`mastery` не заполнена — на практике данные тестовые, допустимо `mastery=0.0` + пересчёт далее.

Примечание о `level`: поле остаётся, обновляется синонимично мастерству в `apply_result`
(`level = mastery`), чтобы существующие панели/тесты не сломались; UI переводится на mastery/status.

### 3.2 Методы хранилища (StudentStore)

```python
apply_result(student_id, topic, subject, record: AnswerRecord) -> dict
    # attempts += 1; correct += int(correct); mastery = EMA; status = derive; weak_areas += [feedback] (если не correct);
    # relations не трогаем; level = mastery. Возвращает новое состояние темы {status, mastery, ...}.
set_relations(student_id, topic, relations: dict) -> None   # {prerequisite: [...], related: [...]}, merge по union
get_topic(student_id, topic) -> dict | None
get_weak_topics(student_id, subject: str = "", threshold: float = 0.5, min_attempts: int = 2) -> list[dict]
get_in_progress_topics(student_id, subject: str = "") -> list[dict]
get_mastered_topics(student_id, subject: str = "") -> list[dict]
get_prerequisite_gaps(student_id, topic) -> list[str]        # prereq не найден/не mastered
recommend_topics(student_id, subject: str = "", current_topic: str = "", limit: int = 5) -> list[dict]
```

### 3.3 Алгоритмы (1:1 с референсом, student_kg.py)

**derive_status(attempts, mastery):**
`attempts == 0 → not_studied`; `attempts >= 3 and mastery >= 0.8 → mastered`; `attempts > 0 → in_progress`.

**is_mastered(topic)**: `status == "mastered" or (attempts >= 3 and mastery >= 0.8)`.

**weak_areas**: при неверном ответе добавить `record.feedback` (обрезанный, без дублей), держать
не более 3 (удалять старые). Набранная точность = `correct/attempts`.

**get_prerequisite_gaps(topic)**: для каждого prereq в `relations.prerequisite`: если темы нет или
она не mastered → gap.

**recommend_topics**: порядок без дублей (лимит): (1) слабые темы
`attempts>=2 and accuracy<0.5`, отсортированы по точности asc; (2) пробелы пререквизитов
текущей темы (не вошедшие выше); (3) `in_progress` (по `last_seen` desc); (4) `not_studied`
без пробелов пререквизитов.

### 3.4 Синхронизация

- **На каждый evaluation** (в `_run_chat`): `store.apply_result(...)` из answer record
  (заменяет сегодняшний `touch_topic`). Также вызывается для review-ответов из E1 (в E1 было
  отложено — здесь включаем: review-ответ обновляет mastery/wiki, карточку не создаёт).
- **Relations**: заполняются из графа источника по совпадению title после построения/чтения
  графа subject|grade (см. раздел 4.4). Совпадение title — нормализованное равенство
  `title.lower()`, с обрезкой префикса `Урок/Параграф N` (правило `_norm_topic` референса:
  срезать `^(?:урок|параграф|lesson|section|module|unit|тема|раздел)\s*\d+[.:\s—–-]*`,
  lowercase, сжать пробелы).

## 4. Граф источника (Слой 1): модель и построение

### 4.1 Модель (networkx + JSON)

Обёртка `src/kg/graph.py`, поверх `nx.DiGraph`. Типы узлов: `book|section|lesson|topic|concept`.
Рёбра: `part_of|prerequisite|related`. Атрибуты узла: `id, title, type, color, section_number?,
parent_id?`. Корень: `book:{root}` с `title=f"Учебник «{root}»"`.

Сериализация (`to_dict/from_dict`): `{"nodes":[{id,title,type,color,section_number?,parent_id?}],
"edges":[{source,target,relation}]}`. Ид-конвенции: узел темы = `topic:{topic}`; секции =
`sec:{root}:{n}`; LLM-узлы = их id либо `concept:{root}:{n}`.

### 4.2 Источник входных данных

В edututor нет загрузки учебников. Граф источника строится по ключу `subject|grade` из
**RAG-сниппетов**, которые провижининг уже накопил для тем этого предмета/класса:

- сбор текстов: `RAGEngine.search` по предмету не поддерживает «все темы» — вместо этого
  используются метаданные чанков (`subject`, `grade`, `topic`) из `InMemoryVectorStore`
  (добавляется метод `list_topics(subject, grade)` / прямой доступ к чанкам с фильтром по
  метаданным); берутся до N самых свежих сниппетов по каждой провижинированной теме;
- `topics` (известные темы предмета) получаются из `app.state.provisioned`
  (`subject|grade|topic`), а при отсутствии — из сессий учеников SQLite;
- контент для LLM: `merged = "\n\n".join(snippets)`; лимит `12000` символов.

### 4.3 LLM-онтология + фолбэк

Модуль `src/kg/ontology.py`. Вызов через существующий каскад LLM, роль `fast`,
`temperature=0.0`, `max_tokens=900`, с таймаутом инструментов. Системный промпт (адаптация из
референса `knowledge_graph.py:593-612`):

```
Ты — онтолог образовательного агента EduTutor. По фрагменту учебного материала построй
граф знаний: ВЕРШИНЫ — темы и ключевые понятия из текста, РЁБРА — смысловые связи между ними.
Решения, что станет вершинами и рёбрами, принимаешь ТЫ по содержанию.
Правила:
1) Только понятия, реально присутствующие в тексте; не выдумывай.
2) Не больше 14 вершин; название 1-4 слова.
3) ПРЕДПОЧИТАЙ ПОНЯТИЙНЫЕ узлы (термины и концепции). НЕ включай структурные узлы:
   номера уроков/классов («7 класс», «Урок 5»), рубрики, оглавление.
4) relation только: part_of (входит в), prerequisite (опирается на), related (связан).
5) Для каждой вершины укажи section — §N/параграф из текста (если есть, иначе пусто).
Верни строго JSON:
{"nodes":[{"id":"c1","title":"...","type":"topic|concept","section":""}],
 "edges":[{"source":"c1","target":"c2","relation":"part_of"}]}
```

Парсинг-валидация: `type` только из `{topic,concept,section,lesson}`; relation из
`{part_of,prerequisite,related}`; неверные → `concept`/`related`; дубли (lower title), заголовки
< 2 символов и junk — отбрасывать; 0 узлов → `None` (фолбэк). Корень `book:{root}` добавляется
всегда, LLM-узлы подвешиваются ребром `part_of` к корню.

**Эвристический фолбэк** (если LLM недоступен/мусор) по структуре известных тем:
- для каждой известной темы `subject|grade` → узел `topic:{topic}` + `part_of` к корню;
  если у темы есть раздел «Урок/Параграф N. Название» в сниппетах — как `section` узлы;
- маркерные заголовки markdown `#`/`##` из сниппетов → узлы `topic`/`section` с дедупом,
  лимит 30 узлов;
- если узлов всё равно нет → один узел `topic:{root}` «Тема «{root}»».
Фильтры мусора (адаптированные из референса): CP1251-mojibake-эвристика `_try_fix_mojibake`,
`is_junk_topic` (наборы поисковых/UI-фраз), `_is_ui_element`, `_is_paragraph_number` (`^§\s*\d+$`,
`^\d+[.)]\s*$`), URL-подобные заголовки. Полные наборы списков — в референсе; в код переносятся
дословно (модуль `src/kg/junk.py`).

### 4.4 Кэш

`src/kg/cache.py`: JSON в `data/knowledge_graphs/<sha1>.json`.
`key = sha1(f"v1:{subject.lower()}:{grade}:{count}:{size}")[:16]` — счётчик тем и суммарный размер
сниппетов, чтобы граф пересобирался при росте базы; `GRAPH_SCHEMA_VERSION=1` (для этого порта).
Чтение fail-soft (битый/отсутствующий → None → пересборка). Сборка происходит лениво по запросу
`GET /student/{id}/graph` (с `subject`, `grade` из сессии ученика) и асинхронно не блокирует чат:
если графа нет — возвращаем временный «каркас из тем» сразу, а полный LLM-граф собирается
fire-and-forget с событием `graph.ready` в SSE при следующем ходе (референс: эмит после построения).

### 4.5 Мастерство-оверлей и гейты

- `GET /student/{id}/graph` возвращает узлы графа, обогащённые мастерством из Слоя 2 по
  нормализованному совпадению `title` ↔ `topic` (ключи `mastery/attempts/correct/accuracy/status`,
  только когда тема существует).
- Гейт пререквизитов: в `_run_chat` ДО `run_agent`, когда `body.topic` непустой и отличается от
  `session.last_gate_topic` (топик только что выбран/сменён; поле — на `ChatSession`),
  проверяем `get_prerequisite_gaps(topic)`; если есть пробелы — перед основным ответом в SSE
  уходит `system`-событие `kind="mastery.gate"` c `{message, gaps, topic}`:
  «Совет: прежде чем «{topic}», стоит повторить: {список}.»; `session.last_gate_topic = topic`.
  Обучение продолжается (блокировки нет). Во фронтенде событие рендерится как баннер с
  кнопками «Всё равно продолжить» (закрыть) и «Перейти к «{gap}»» (startTopic по этой теме).
- Recommendations (Слой 2, раздел 3.3) используются для кнопки «Изучить: …» в AdaptivePanel
  вместо текущей `pick_recommendation`.

## 5. API

Все под `/student` (StudentStore уже привязан к app.state).

`GET /student/{student_id}/graph?subject=&grade=` →
`{"root": <id>, "nodes": [...], "edges": [...], "active_topic": null, "stats": {nodes, edges}}`
(узлы с мастерством, см. 4.5). Если subject/grade пусты — по последней сессии ученика.

`GET /student/{student_id}/knowledge-graph?subject=` →
`{"student_id", "subject", "topics": {topic: {topic, subject, status, mastery, attempts, correct,
accuracy, weak_areas, last_seen, relations}}, "stats": {mastered, in_progress, not_studied, total}}`.

`GET /student/{student_id}/recommendations?current_topic=&subject=&limit=` →
`{"recommendations": [topic...], "weak_topics": [...], "prerequisite_gaps": [...]}`.

`GET /student/{student_id}/graph/{node_id}/related` → `{"node": {...}, "related": [...]}`
(соседи до глубины 2 с полями source/target/relation/titles).

`GET /student/{student_id}/graph/{node_id}/wiki` → `{"node": {...}, "wiki": null}` в E2
(заглушка; в E4 возвращает статью).

SSE-события: добавляются `graph.ready` (`{"root":..., "stats":...}`) и `system` c
`kind="mastery.gate"`.

## 6. UI

### 6.1 Student Knowledge Panel («Мои знания»)

Новая правая/левая панель (список статусов тем) — перенос `StudentKGPanel.jsx`:
- панель «Мои знания»: статистика `всего/освоено/в процессе/не изучено`, список тем со
  статус-цветом, точностью, слабыми местами (первые 2) и «Повторить (N)» — кнопка переезжает
  сюда из E1 (удаляется из AdaptivePanel). Данные: `GET /student/{id}/knowledge-graph`.
- тепловая карта мастерства `MasteryWall` по данным `/knowledge-graph` (не `/wiki`, пока E4
  не готово) — квадратики по темам, цвет `mastery>=0.75 high / >=0.45 mid / иначе low`.
  Взаимодействие: клик по теме → «Изучить тему» (startTopic как сегодня).

### 6.2 Граф «Созвездие» (Canvas)

Порт `KnowledgeGraphPanel.jsx` (зависимость-free canvas, ручная force-simulation):
- props `{nodes, edges, activeTopic, onSelect, sessionId}`; данные из `GET /student/{id}/graph`;
- цвета рёбер: `part_of #64DFDF / prerequisite #FFB703 / related #B388FF`; цвета узлов от
  мастерства: `>=0.61 #4ade80 / >=0.31 #fbbf24 / иначе #f87171`, иначе `node.color`;
- zoom/pan/drag, hover-тултип, поиск, кнопка «📖 Изучить тему» → `onSelect(node)`, drill-down
  (`/related`, `/wiki`), фильтр структурных узлов (`section/lesson`);
- обновление после `graph.ready` и после каждого `done` (мастерство поменялось);
- место в layout: главная область либо отдельная вкладка-панель; точное размещение — на
  усмотрение реализации с учётом текущей 3-колоночной сетки App.jsx.

### 6.3 Обработка SSE-событий в App/feedReducer

- `graph.ready` → перечитать граф (`GET /student/{id}/graph`) и (если виден) панель «Мои знания»;
- `system` c `kind == "mastery.gate"` → добавить в фид баннер (dismiss + «Перейти к «{gap}»»);
- после `done` → обновить панель «Мои знания»/мастерство (значения могли измениться от
  evaluation-конверта этого хода).

## 7. Зависимости и конфиг

- Новая зависимость: `networkx>=3.0` (добавить в `pyproject.toml` и `requirements.txt`).
- `src/config.py`/`.env.example`: `knowledge_graph_dir` (default `data/knowledge_graphs`,
  резолвится от `adaptive_tutor/` как `student_db_path`), `graph_schema_version=1`,
  `ontology_max_vertices=14`, `mastery_mastered_threshold=0.8`, `mastery_mastered_attempts=3`.

## 8. Крайние случаи

- Пустой предмет/класс/нет сниппетов → каркас из 1 узла или пустой граф; UI показывает empty-state.
- LLM вернул мусор → фолбэк; LLM недоступен → фолбэк (без падения чата).
- Пересборка кэша: ключ включает размер/количество → рост базы пересобирает; битый файл → None.
- Дубли/регистр тем («Сила тяжести» vs «сила тяжести»): нормализация при матчинге; сам ключ
  темы не трогаем.
- Совпадение topic-title неточно: гейт/мастерство просто не сработают для несопоставимых узлов.
- `level` vs `mastery`: level продолжает обновляться (=mastery) для совместимости; UI показывает mastery.
- Гонки при построении графа (fire-and-forget): ключ сборки держится в `app.state.graph_building`
  (set), повторный запрос не запускает дубль.
- Существующие тесты `test_student_store.py`, `test_adaptive.py` должны остаться зелёными
  (добавляются новые тесты; `touch_topic`/`pick_recommendation` сохраняются как legacy).

## 9. Наблюдаемость

JSONL: `kg.build` (root, nodes, edges, source: llm|heuristic), `kg.ready`, `mastery.update`
(topic, mastery, status), `mastery.gate` (topic, gaps). Без раскрытия персональных данных сверх темы.

## 10. Тесты

Бэкенд:
- EMA/статусы: 3 верных подряд → mastered; 2 верных + 1 неверный → in_progress; якорь 0.5 на
  первом ответе; слабые темы; `recommend_topics` порядок; пробелы пререквизитов.
- `apply_result` на store + миграция колонок (новая БД и «старая» без колонок).
- relations sync по нормализованному title; гейт-сообщение формируется при пробелах.
- Онтология: парсинг валидного/битого JSON, дубли/junk фильтры, фолбэк при None; кэш
  (ключ, пересборка при росте, битый файл → None).
- API: `/knowledge-graph`, `/recommendations`, `/graph` с оверлеем, `/graph/{node}/related`,
  `/graph/{node}/wiki` (null), SSE-событие `graph.ready` при построении.

Фронтенд (vitest): панель «Мои знания» рендерит статусы; `masteryColor`-логика;
Canvas-компонент — рендер без ошибок на пустом графе и на данных (без пиксель-снапшотов).

## 11. Файлы

Создать: `src/kg/__init__.py`, `src/kg/graph.py`, `src/kg/ontology.py`, `src/kg/cache.py`,
`src/kg/junk.py`, `src/student/student_kg.py`, `src/student/mastery.py` (чистые EMA/статусы без
sqlite — переиспользуется в E4), тесты `tests/test_student_kg.py`, `tests/test_mastery.py`,
`tests/test_kg_ontology.py`, `tests/test_kg_api.py`; фронтенд `components/StudentKGPanel.jsx`,
`components/MasteryWall.jsx`, `components/KnowledgeGraphPanel.jsx` + стили.

Изменить: `src/student/store.py` (миграция + методы), `src/models/schemas.py`, `src/api/server.py`
(хуки apply_result/mastery-гейт/эндпоинты/events), `src/agent/prompts.py` (не нужно, но если
гейт подсказывает — только через server), `src/student/adaptive.py` (recommend — новый вызов),
`src/config.py`, `.env.example`, `pyproject.toml`, `requirements.txt`, `frontend/src/App.jsx`,
`frontend/src/components/AdaptivePanel.jsx`, `frontend/src/api.js`, `frontend/src/index.css`,
README/API-доки.
