# ФГОС-сверка: соответствие тем учебной программе по предмету и классу — дизайн

Дата: 2026-09-06. Воркспейс: `C:\otus\edututor`. Тип: новое под-системное
изменение бэкенда + минимальный UI. Коммит не выполняется без явной просьбы.

## 1. Цель и место в продукте

Ученик занимается по **свободным темам** (`ChatSession.topic/subject/grade`,
`src/api/session_store.py:24-48`; тело запроса
`ChatRequest`, `src/api/server.py:118-146`, поля `topic/subject/grade`
`:127-129`), а у продукта есть граф знаний ученика по темам (`student_kg`:
`topics` с `subject/mastery/status/accuracy`, SQLite `src/student/store.py:27-40`,
payload-хелперы `src/student/student_kg.py:24-60,84-98`) и профиль с классом
школьника (`students.learner_type/grade`, миграция `store.py:150-158`,
`set_profile` `store.py:737-764`). При этом **продукт ничего не знает об
учебной программе (ФГОС)**: темы не сверяются с программой предмета/класса,
ученику не видно, какие разделы программы ещё не пройдены и в каком порядке их
изучать.

Заявка в аудите — «Сверка с ФГОС (offline-база): `src/curriculum.py`»
(`docs/superpowers/2026-09-04-gap-audit-edututor-vs-reference.md:97`,
Таблица C `:191`), помечена как опциональная («Этап 5», `:211`). В коде
edututor ФГОС-сверки нет.

Этап внедряет **ФГОС-сверку** в минимальном объёме, вписывающемся в
архитектуру MVP:

1. статический каталог «учебной программы» (bundled seed) по ключу
   `(subject, grade)` с разделами и (опционально) кодами разделов;
2. серверные API: список программы, сверка отдельной темы, покрытие программы
   темами из `student_kg` данного ученика;
3. минимальный UI: системная заметка в чате при теме вне программы + блок
   «Программа (ФГОС)» в панели «Мои знания» (`StudentKGPanel`) со списком
   непройденных разделов.

Каталог — **стартовый набор данных, а не полный ФГОС** (см. §4 и §12).

## 2. Решения (согласовано)

1. **Каталог — bundled Python-модуль**, а не JSON-ресурс/файлы на диске.
   В референсе ФГОС-база лежит в `data/fgos_reference/*.json`
   (`config.py:117` `FGOS_REFERENCE_DIR`), но в edututor данные-константы
   хранятся в коде (константы LinUCB `src/student/linucb.py`, пороги
   `src/student/mastery.py:8-10`). Python-модуль: нет путей/кодировок/IO,
   проще юнит-тесты и extend. Альтернатива (JSON в `adaptive_tutor/data/`)
   зафиксирована в §13 как возможное развитие.
2. **Сверка детерминированная, по нормализованному точному равенству**
   заголовка темы и алиаса раздела каталога (как матчинг title↔topic в
   `src/api/server.py:707-710` / `src/kg/build.py:43-49`). LLM-маппинг — только
   как опциональное будущее расширение (fail-soft), в MVP **не** вызывается:
   тесты детерминированы, нет скрытой стоимости на каждую тему, честный
   результат «не входит в программу» полезен и без LLM (см. §13).
3. **Без изменений схемы БД**: каталог статичен; «покрытие» вычисляется
   на лету из `store.list_topics(student_id)` (строки тем с `subject/mastery/
   status`, `store.py:182-189`). Ничего не персистится, кроме памяти сессии
   для подавления дублей заметки (поле `ChatSession.last_fgos_topic`).
4. **Surfacing минимальный**:
   - в чате — системная SSE-заметка `kind="fgos.note"` при старте темы, если
     для предмета+класса программа известна, а тема в неё **не** входит
     (паттерн `mastery.gate`, сервер `src/api/server.py:1410-1433`, фронт
     `frontend/src/components/Chat.jsx:34-44`);
   - панель «Мои знания» (`StudentKGPanel`) получает блок «Программа (ФГОС)»:
     счётчик пройденных разделов и список непройденных с кнопкой «Изучить».
   Уведомление показывается только при известном классе (число) и только в
   SSE-потоке (`POST /chat/stream`), как и существующий `mastery.gate`
   (`server.py:1414-1415` — условие `on_event is not None`).

## 3. Исследование (источники и как сверяться)

### 3.1. Референс (`C:\otus\project_work`)

- `src/curriculum.py:2-8` — модуль «сверка с ФГОС»: `load_fgos_reference`
  (`:56-72`), `lookup_fgos` (`:100-118`), `grade_curriculum` (`:121-168`),
  сбор базы crawl4ai (`:174-226`).
- Синонимы предметов `SUBJECT_SYNONYMS` `:19-28` (`geography→география…`,
  `math→математика/алгебра/геометрия/мат` — матчинг подстрокой `syn in s`,
  `:90-97`); ключ предмета — латинский canonical.
- Grade: ключи-диапазоны «5-6», одиночные «6» (`:75-87` `_grade_matches`).
- Данные: `data/fgos_reference/geography_5-6.json`,
  `data/fgos_reference/history_6.json`. Форма:
  `{subject: {gradeKey: [{"topics": [алиасы…], "code": "ГЕОГ.5-6.2"}…]}}`
  (пример `geography_5-6.json:1-10`).
- Вызов: `src/graph.py:814-823` и `:852-861` — в intake-нодах
  `grade_curriculum(...)` заполняет `st.curriculum = fgos_code`, иначе
  `"unverified"`, а warning уходит в `agent_message`. Код потом попадает в
  промпты урока (`tutor.py:292-306,469-507`).
- Замечание: референс сверяет ОДНУ текущую тему с кодом ФГОС и кладёт код в
  состояние; он не считает «покрытие/пробелы» по графу тем ученика и хранит
  базу в JSON. Мы не копируем 1:1: каталог в коде, сверка и покрытие строятся
  поверх уже существующего `student_kg` (SQLite), без состояния `curriculum`
  на сессии.

### 3.2. Наш домен (edututor)

- Сессия: `ChatSession` `src/api/session_store.py:24-48` — поля
  `topic/subject/grade` `:29-31`, флаг-подавитель `last_gate_topic` `:33`.
- `ChatRequest` `src/api/server.py:118-146` (`topic/subject/grade` `:127-129`);
  профиль-карточка `IntakeProfile` (`learner_type: student|schoolchild`, `grade`)
  `:92-115`.
- Профиль ученика: `GET /student/{id}` `:2030-2045` отдаёт
  `learner_type/grade`; `students.learner_type/grade` мигрируются в
  `src/student/store.py:150-158`, пишутся `set_profile` `:737-764`.
- Темы: `topics`-схема `store.py:27-40` (subject/mastery/status/attempts/
  correct/weak_areas/relations); `list_topics` `:182-189`; `get_topic` `:588`;
  `apply_result` (EMA+status) `:442-495`; mastery-семантика
  `src/student/mastery.py:20-45` (`derive_status`, `is_mastered`).
- Сессии-таблица пишут subject/grade колонки, но чат регистрирует только topic
  (`register_session(student_id, session_id, body.topic or "")` `server.py:1370`,
  сигнатура `store.py:225-247`), поэтому **не полагаемся** на fallback
  предмета/класса из последней сессии, кроме уже существующего хелпера
  `_resolve_subject_grade` `server.py:718-734` (используется только графом).
  Для ФГОС-контекста берём предмет/класс из запроса, при пустом классе — из
  профиля ученика (`store.get_student(student_id).grade`).
- Знания ученика: `GET /student/{id}/knowledge-graph` `server.py:2087-2100`
  (фильтр по `subject`, статусы/мастерство), `GET /student/{id}/recommendations`
  `:2102-2127`; payload-хелперы `src/student/student_kg.py:24-60,84-98`.
- Адаптивный блок `_build_adaptive` `server.py:354-411` (для панели; сюда
  блок программы НЕ добавляем — см. §9).
- Chat/SSE: `POST /chat` `server.py:1892-1926`, `POST /chat/stream` `:1928-…`
  (обратный вызов `on_event` `:1933-1936`, события «system»/«message»/«done»);
  мастерство-гейт эмитится в `_run_chat` `:1410-1433`.
- Фронт: `api.js` (`getKnowledgeGraph` `:94-95`, `getGraph` `:98-99`);
  `Chat.jsx` `feedReducer` `:11-54` (системные события `:34-45`), рендер
  заметок `:120-141`; `StudentKGPanel.jsx` загрузка KG `:43-75`, рендер
  `:113-237`; `App.jsx` использует панель `:286-293`, передаёт
  `current.subject/grade`; `TopicForm.jsx:4,20` (subject по умолчанию
  «математика», placeholder класса «8 класс», темы — «квадратные уравнения»).
- Формат класса в UI — свободный текст с числом: «8 класс»/«8»
  (`TopicForm.jsx:20`, `IntakeCard.jsx:63-65`, тесты `App.test.jsx:32-33`).

### 3.3. «Тема / раздел / класс» в референсе и у нас

- Референс: раздел = запись `{topics: [ключевые слова-алиасы], code}` внутри
  grade-блока; тема занятия = свободный текст ученика; сверка — пересечение
  подстрок/равенство алиаса и темы.
- У нас: «раздел программы» вводится явно (`section`) с набором алиасов
  (`topics`), «тема занятия» — заголовок темы в `topics`-таблице и
  `ChatRequest.topic`; предмет — свободная русская строка (в т.ч.
  «алгебра»/«геометрия» рядом с «математика», см. папки
  `data/knowledge_wiki/…/алгебра|математика|физика|русский-язык`); класс —
  «8 класс»/«8». Приведение к canonical и числу — в каталоге (§4).

## 4. Каталог `src/curriculum/catalog.py` (данные и форма)

Новый пакет `src/curriculum/` (пути — относительно `adaptive_tutor/src/`).

`catalog.py` содержит:

- `@dataclass(frozen=True) CurriculumSection`:
  `code: str`, `section: str`, `topics: tuple[str, ...]`;
- `SUBJECT_SYNONYMS: dict[str, tuple[str, ...]]` — синонимы для приведения
  предмета к canonical-ключу (подстрока в обе стороны, как референс `:90-97`);
- `CURRICULUM: dict[str, dict[str, tuple[CurriculumSection, ...]]]` — каталог:
  `canonical_subject → grade_key → упорядоченные разделы`;
- чистые функции (без I/O):
  - `normalize_title(value: str) -> str` — lowercase, сжатие пробелов, срез
    префикса «Урок/Параграф/Тема/Раздел N…» (по образцу
    `src/kg/build.py:43-49`);
  - `resolve_subject(subject: str) -> str` — canonical по `SUBJECT_SYNONYMS`,
    иначе нормализованная входная строка;
  - `grade_number(grade: str | None) -> int | None` — первое целое в строке
    («8 класс»→8); нет числа → `None`;
  - `grade_matches(key: str, g: int | None) -> bool` — равенство либо диапазон
    «5-6» (как референс `:75-87`);
  - `@dataclass(frozen=True) CurriculumLookup`: `subject`, `grade: int | None`,
    `sections: tuple[CurriculumSection, ...]`, `found: bool`;
  - `curriculum_for(subject: str, grade: str | None) -> CurriculumLookup`.

### 4.1. Seed (стартовый набор данных, НЕ исчерпывающий ФГОС)

Три предмета × один класс-год, коды — правдоподобные «примеры», темы-алиасы —
реальные формулировки. Точный контент (детерминирует тесты §11):

```python
SUBJECT_SYNONYMS = {
    "математика": ("математика", "математике", "алгебра", "алгебре",
                   "геометрия", "геометрии", "мат", "алг"),
    "физика": ("физика", "физике", "физ"),
    "история": ("история", "истории", "истор", "всеобщая история"),
    "русский язык": ("русский", "русскому", "русский язык", "русский-язык"),
    "биология": ("биология", "биологии", "биол"),
    "химия": ("химия", "химии", "хим"),
    "география": ("география", "географии", "геогр"),
    "литература": ("литература", "литературе", "лит"),
}

CURRICULUM = {
    "математика": {
        "8": (
            CurriculumSection("МАТ.8.1", "Квадратные уравнения", (
                "квадратные уравнения", "неполные квадратные уравнения",
                "формула корней квадратного уравнения", "теорема виета",
                "квадратный трёхчлен")),
            CurriculumSection("МАТ.8.2", "Квадратичная функция", (
                "квадратичная функция", "график квадратичной функции",
                "парабола", "свойства квадратичной функции")),
            CurriculumSection("МАТ.8.3", "Четырёхугольники", (
                "четырёхугольники", "параллелограмм", "прямоугольник",
                "ромб", "квадрат", "трапеция")),
        ),
    },
    "физика": {
        "7": (
            CurriculumSection("ФИЗ.7.1", "Механические явления", (
                "механические явления", "механическое движение", "скорость",
                "инерция", "взаимодействие тел", "масса",
                "плотность вещества")),
            CurriculumSection("ФИЗ.7.2", "Давление", (
                "давление", "давление твёрдых тел",
                "давление жидкостей и газов", "закон паскаля",
                "атмосферное давление")),
            CurriculumSection("ФИЗ.7.3", "Строение вещества", (
                "строение вещества", "молекулы", "диффузия",
                "агрегатные состояния вещества")),
        ),
    },
    "история": {
        "6": (
            CurriculumSection("ИСТ.6.2", "Средневековый мир", (
                "средние века", "средневековье", "крестовые походы",
                "византия", "феодализм")),
            CurriculumSection("ИСТ.6.3", "Древняя Русь", (
                "древняя русь", "киевская русь", "древнерусское государство")),
        ),
    },
}
```

Состав и порядок seed фиксированы и проверяются тестом (см. §11, catalog).
Демо-поток по умолчанию формы (`TopicForm.jsx:4,20,23`: «математика» / «8
класс» / «квадратные уравнения») совпадает с `МАТ.8.1`.

## 5. Сверка и покрытие `src/curriculum/service.py`

Чистые функции (без sqlite/LLM), входные строки тем — payload Слоя 2
(форма `row_payload`, `src/student/student_kg.py:24-60`).

- `section_match(lookup, topic) -> CurriculumSection | None` — первый раздел,
  у которого есть алиас, равный `normalize_title(topic)`; при
  `lookup.found=False` → `None`.
- `align_topic(lookup, topic) -> dict` — ответ сверки одной темы (§7.2):
  `status ∈ {"matched", "not_found", "catalog_unavailable"}`.
- `build_coverage(lookup, rows) -> dict` — покрытие программы строками тем
  ученика. Строка учитывается, только если
  `resolve_subject(row["subject"]) == lookup.subject`. Раздел
  `covered`, если хотя бы один алиас раздела нормализованно равен теме какой-то
  строки; `student_topics` — имена этих строк; `mastery` — максимум
  `mastery` по ним. `not_covered` — иначе. Раздел «учтён» один раз
  (первое совпадение; разделы не пересекаются по алиасам).

## 6. Конфигурация

`src/config.py` (Settings, блок после «Граф знаний (E2)…», `:134-158`):

```python
    # ФГОС-сверка (2026-09-06)
    curriculum_enabled: bool = Field(default=True, description="Включить ФГОС-сверку")
```

В `.env.example` (в конец, после LinUCB):

```
# ФГОС-сверка тем с учебной программой
TUTOR_CURRICULUM_ENABLED=true
```

`curriculum_enabled` — kill-switch: выключает и SSE-заметку, и (серверную часть)
покрытия (эндпоинты остаются, но возвращают «выключено», см. §7.1-§7.3). Чистые
функции §4-§5 от флага не зависят.

## 7. Server API

Регистрация — внутри `create_app` (`src/api/server.py:1833`), рядом с
`/knowledge` и `/student/…`: вставляем блок после обработчика
`/student/{student_id}/recommendations` (закрывается `:2127`) и до
`@app.get("/student/{student_id}/graph")` (`:2129`). Ответы всегда JSON `200`
(никаких новых 4xx, стиль fail-soft `/recommendations` `:2102-2127` и
`/knowledge-graph` `:2087-2100`).

Общие принципы:
- предмет/класс из query; класс может быть «8 класс»/«8»; canonical/число —
  внутри каталога;
- при пустом классе в запросе coverage пытается взять `grade` из профиля
  (`store.get_student(student_id).get("grade")`), как описано ниже;
- сбой БД/импорта — `try/except`, ответ не падает (принцип fail-soft).

### 7.1. `GET /curriculum` — список программы

Query: `subject: str = ""`, `grade: str = ""`.

```json
{
  "subject": "математика",
  "grade": 8,
  "found": true,
  "sections": [
    {"code": "МАТ.8.1", "section": "Квадратные уравнения",
     "topics": ["квадратные уравнения", "неполные квадратные уравнения",
                "формула корней квадратного уравнения", "теорема виета",
                "квадратный трёхчлен"], "order": 0},
    {"code": "МАТ.8.2", "section": "Квадратичная функция", "topics": ["…"], "order": 1},
    {"code": "МАТ.8.3", "section": "Четырёхугольники", "topics": ["…"], "order": 2}
  ]
}
```

Нет совпадения предмета/класса (или `curriculum_enabled=False`):
`{"subject": "<canonical или входная>", "grade": null, "found": false, "sections": []}`.

### 7.2. `GET /curriculum/match` — сверка одной темы

Query: `subject: str = ""`, `grade: str = ""`, `topic: str` (обязательный,
непустой — иначе стандартный 422 FastAPI).

```json
{
  "subject": "математика",
  "grade": 8,
  "found": true,
  "topic": "теорема виета",
  "status": "matched",
  "code": "МАТ.8.1",
  "section": "Квадратные уравнения",
  "matched_topic": "теорема виета"
}
```

- совпадения нет → `{"status": "not_found", "code": null, "section": null, …}`;
- каталога нет (`found=false`) → `{"status": "catalog_unavailable", …}`.

### 7.3. `GET /student/{student_id}/curriculum` — покрытие программы

Query: `subject: str = ""`, `grade: str = ""`.

Резолв контекста (helper `_curriculum_ctx(store, student_id, subject, grade)`):
`subject` из query, при пустом — из последней сессии
(`store.get_last_session(student_id)`); `grade` из query, при пустом — из
профиля (`store.get_student(student_id).get("grade")`), затем из последней
сессии. Затем `curriculum_for(subject, grade)`.

```json
{
  "student_id": "stu_x",
  "subject": "математика",
  "grade": 8,
  "found": true,
  "sections": [
    {"code": "МАТ.8.1", "section": "Квадратные уравнения",
     "topics": ["квадратные уравнения", "…"], "status": "covered",
     "student_topics": ["квадратные уравнения"], "mastery": 0.9},
    {"code": "МАТ.8.2", "section": "Квадратичная функция",
     "topics": ["…"], "status": "not_covered", "student_topics": [], "mastery": null}
  ],
  "stats": {"total": 3, "covered": 1, "remaining": 2},
  "remaining_sections": ["Квадратичная функция", "Четырёхугольники"]
}
```

`store is None` / ученик неизвестен → строки пусты (sections все
`not_covered`, stats из нулей). Каталог неизвестен или `curriculum_enabled=False`
→ `found=false`, `sections: []`, `stats` нули, `remaining_sections: []`.

### 7.4. Вспомогательный резолв контекста

Повторно используемый модуль-хелпер в `src/api/server.py`
(рядом с `_resolve_subject_grade`, `:718-734`):

```python
def _curriculum_ctx(app, student_id, subject, grade):
    """(canonical_subject, grade_int|None, store|None) для ФГОС-эндпоинтов.

    subject из запроса (иначе последняя сессия), grade из запроса
    (иначе профиль ученика, затем последняя сессия).
    """
    from src.curriculum.catalog import curriculum_for, grade_number, resolve_subject

    store = app.state.student_store
    subj = (subject or "").strip()
    gr = (grade or "").strip()
    if store is not None:
        if not subj:
            subj = str((store.get_last_session(student_id) or {}).get("subject") or "")
        if not gr:
            st_row = store.get_student(student_id) or {}
            gr = str(st_row.get("grade") or "")
            if not gr:
                gr = str((store.get_last_session(student_id) or {}).get("grade") or "")
    lookup = curriculum_for(subj, gr)
    return lookup, store
```

## 8. Интеграция с чатом (системная заметка `fgos.note`)

### 8.1. `ChatSession`

Добавить поле `last_fgos_topic: str = ""` в `src/api/session_store.py` сразу
после `last_gate_topic` (`:33`). Назначение — подавить повтор заметки на одной
теме в рамках одной сессии (аналог `last_gate_topic` в mastery-гейте).

### 8.2. Эмиссия в `_run_chat`

Блок вставляется в `src/api/server.py` **после** mastery-гейта (`:1433`) и
**до** `if body.topic:` провижининга (`:1435`):

```python
    # ФГОС-сверка (2026-09-06): системная заметка, только когда программа
    # для предмета/класса известна, а тема в неё НЕ входит (обучение свободной
    # темой не блокируется). Один раз на тему в сессии (last_fgos_topic).
    if (
        settings.curriculum_enabled
        and on_event is not None
        and store is not None
        and body.topic.strip()
        and session.last_fgos_topic != body.topic
    ):
        session.last_fgos_topic = body.topic
        try:
            from src.curriculum.catalog import curriculum_for
            from src.curriculum.service import section_match

            grade_raw = (body.grade or "").strip()
            if not grade_raw:
                grade_raw = str((store.get_student(student_id) or {}).get("grade") or "")
            lookup = curriculum_for(body.subject, grade_raw)
            if lookup.found and section_match(lookup, body.topic) is None:
                message = (
                    "Заметка: тема «{topic}» не входит в учебную программу (ФГОС) "
                    "по предмету «{subject}» за {grade}. Это свободная тема — "
                    "заниматься можно, но к программе класса она не относится."
                ).format(topic=body.topic, subject=lookup.subject, grade=grade_raw)
                on_event("system", {
                    "kind": "fgos.note", "message": message,
                    "topic": body.topic, "subject": lookup.subject,
                    "grade": grade_raw,
                })
        except Exception as exc:  # noqa: BLE001 — сбой сверки не роняет чат
            print(f"[curriculum] сверка не выполнена: {exc}")
```

Условия и ограничения:
- только `on_event is not None` (т.е. `POST /chat/stream` `server.py:1933-1936`),
  как у `mastery.gate` (`:1414-1415`);
- заметка только при `lookup.found` (есть каталог) и `section_match is None`;
  совпавшие темы и темы без известного класса не шумят;
- в контекст модели заметка НЕ добавляется (это информация для ученика, а не
  директива репетитору; модель не ограничивается программой);
- `store is None` → пропуск (нет профиля для grade-fallback).

### 8.3. Фронт

`frontend/src/components/Chat.jsx`, `feedReducer` (`:11-54`): в ветке
`name === 'system'` (`:34-45`) перед `return feed` добавить:

```js
  if (name === 'system') {
    if (data.kind === 'mastery.gate') {
      return { ...feed, items: [/* существующее */] }
    }
    if (data.kind === 'fgos.note') {
      return {
        ...feed,
        items: [
          ...feed.items,
          { id: `c${feed.items.length}`, kind: 'system-note', content: data.message || '' },
        ],
      }
    }
    return feed
  }
```

Повторный рендер не требуется: тип `system-note` уже рендерится
(`Chat.jsx:140-142`, класс `.system-note` в `frontend/src/index.css:1497`).

## 9. UI: блок «Программа (ФГОС)» в `StudentKGPanel`

Выбор поверхности: панель «Мои знания» (`StudentKGPanel`) уже живёт в правой
колонке под выбранным предметом и перезагружается по `reloadKey` после каждого
хода (`App.jsx:286-293`, эффект `StudentKGPanel.jsx:43-75`), поэтому блок
программы обновляется там же, где и темы. В `AdaptivePanel` (уровень темы/ход)
программа не помещается — там нет предметно-классового контекста колонки.

Изменения фронтенда (точный список, ниже — тесты §11.3):

1. `frontend/src/api.js` — три метода:
   - `getCurriculum(subject, grade)` → `GET /curriculum?subject=…&grade=…`;
   - `matchCurriculum(subject, grade, topic)` →
     `GET /curriculum/match?subject=…&grade=…&topic=…`;
   - `getCurriculumCoverage(studentId, subject, grade)` →
     `GET /student/{id}/curriculum?subject=…&grade=…`.
   (экранирование `encodeURIComponent`, стиль `api.js:94-99`).
2. `frontend/src/components/StudentKGPanel.jsx`:
   - новый проп `grade = ''`;
   - эффект: при `studentId && subject && grade` — `api.getCurriculumCoverage
     (studentId, subject, grade)` в состояние `fgos` (fail-soft: catch → `null`);
     деп `[studentId, subject, grade, reloadKey]`;
   - рендер между `.kg-stats` и `.kg-filters`:
     - `fgos === null` или нет `subject/grade` → ничего;
     - `fgos.found === false` → строка «Программа (ФГОС) для предмета и класса
       пока не добавлена.»;
     - иначе — заголовок «Программа (ФГОС)» и счётчик
       «пройдено {stats.covered} из {stats.total} разделов»; далее список
       `remaining_sections` (непройденные разделы) кнопками «Изучить» →
       `onStudy(section)`. Покрытые разделы отдельным списком не дублируются
       (видны в счётчике и в списке тем панели).
   - новые классы `.fgos-*` (см. п.4); не конфликтуют с `.kg-*`.
3. `frontend/src/App.jsx` — передать `grade={current?.grade || ''}` в
   `StudentKGPanel` (`:286-293`).
4. `frontend/src/index.css` — минимальные стили (после `.kg-*` блока,
   `:896-965`), на CSS-переменных палитры:
   `.fgos`, `.fgos-title`, `.fgos-count`, `.fgos-list`, `.fgos-item`,
   `.fgos-item-covered`, `.fgos-btn` (тонкая монопространственная типографика,
   как `.kg-head h3`).

Кнопка «Изучить» запускает `studyNext(section)` — обычное занятие по названию
раздела («Квадратные уравнения» — валидная тема для RAG/поиска). Для
«Средневековый мир»/«Древняя Русь» это тоже допустимые формулировки.

## 10. Персистентность и интеграция

- Схема БД НЕ меняется. Каталог — статический код; «покрытие» пересчитывается
  на лету из `store.list_topics(student_id)` (`store.py:182-189`) в момент
  запроса. Новые колонки/таблицы не нужны.
- Единственная память состояния — `ChatSession.last_fgos_topic` (in-memory,
  TTL сессии, `src/api/session_store.py`), как у `last_gate_topic`.
- Никаких фоновых задач/кэшей/графов: сверка и покрытие — чистые функции
  (§4-§5), вызываются синхронно.
- JSONL-логирование не добавляем (заметка дублируется в истории чата через
  `system-note`); при желании добавить `curriculum.note` — развитие (§13).

## 11. Тесты

### 11.1. Юнит (новый `adaptive_tutor/tests/test_curriculum.py`)

Каталог: точные составы seed (3 предмета; математика-8 — 3 раздела и порядок
кодов `МАТ.8.1…8.3`, физика-7 — 3, история-6 — 2); `normalize_title`
(регистр, сжатие, «Параграф 12: Теорема Виета» → «теорема виета»);
`resolve_subject` («алгебра»→«математика», регистр, неизвестный → как есть,
пусто → «»); `grade_number` («8 класс»→8, «8»→8, «»→None, «abc»→None);
`grade_matches` (равенство и диапазон «5-6»); `curriculum_for`
(известное/неизвестное сочетание → `found` и число разделов).

Сервис: `section_match` (точное нормализованное совпадение, в т.ч. через
префикс; None для «интегралы» в математика-8); `align_topic`
(три статуса); `build_coverage` (строка чужого предмета игнорируется,
субъект-синоним «алгебра» учитывается, mastery — максимум по matched,
`stats.covered/remaining`, `remaining_sections`).

### 11.2. Интеграция API (новый `adaptive_tutor/tests/test_curriculum_api.py`)

Стиль `tests/test_kg_server_hooks.py:67-93` (`create_app(runtime_factory=…,
student_store=…)` + `TestClient`, фиктивный рантайм). Проверяются:
`GET /curriculum` (найдено/не найдено), `GET /curriculum/match` (matched,
not_found, catalog_unavailable), `GET /student/{id}/curriculum` (ученик с темой
«Квадратные уравнения» subject «математика» → `covered=1`, `remaining=2`;
без ученика → нули; неизвестная программа → `found=false`).

### 11.3. SSE-заметка (дополнение `tests/test_kg_server_hooks.py`)

Рядом с `test_mastery_gate_emitted_once` (`:108-127`):
- тема вне программы (subject «математика», grade «8 класс», topic
  «интегралы») → в `POST /chat/stream` текст содержит `fgos.note` и «не входит
  в учебную программу»; повторный запрос той же сессии → заметки нет
  (однократность);
- тема из программы («квадратные уравнения», тот же контекст) → `fgos.note`
  в ответе нет.

### 11.4. Фронтенд (vitest)

- `frontend/src/api.test.js`: URL трёх новых методов (стиль `:95-102`).
- `frontend/src/components/Chat.test.jsx`: `feedReducer` для `system` c
  `kind='fgos.note'` добавляет элемент `kind: 'system-note'` с текстом
  сообщения; рендер строки через существующий `.system-note`.
- `frontend/src/components/StudentKGPanel.test.jsx`: в `vi.mock('../api', …)`
  добавить `getCurriculumCoverage`; тесты: блок рендерится при `subject/grade`
  и показывает непройденные разделы кнопками (клик → `onStudy(section)`);
  `found=false` — строка «пока не добавлена»; без `grade` вызов
  `getCurriculumCoverage` не происходит.
- `frontend/src/App.test.jsx`: в `vi.mock('./api', …)` добавить
  `getCurriculumCoverage: vi.fn(() => Promise.resolve({ found: false, sections: [],
  stats: { total: 0, covered: 0, remaining: 0 }, remaining_sections: [] }))`
  (иначе после старта занятия панель вызовет несуществующий метод).

## 12. Известные ограничения

- Каталог — стартовый seed (3 сочетания «предмет×класс», см. §4.1): для
  остальных предметов/классов `found=false`, функции работают честно
  («программа не добавлена», не «тема не входит»).
- Детерминированный матчер — точное равенство нормализованных заголовков:
  перефразировки («решение квадратных уравнений» vs «квадратные уравнения»)
  дают `not_found` даже при попадании в программу. LLM-маппинг — вне MVP (§13).
- Коды разделов seed — правдоподобные примеры, НЕ официальная нумерация ФГОС;
  каталог не претендует на полноту/актуальность.
- Синонимия предметов неполная; неизвестный предмет не сопоставляется ни с
  одним canonical; темы с пустым `subject` (legacy-строки, созданные до
  `apply_result`) не учитываются в покрытии.
- Класс берётся из запроса/профиля одним числом: ученик 8 класса может изучать
  тему 9 класса — сверка покажет «не входит в программу 8 класса» (это
  ожидаемо для свободных тем).
- Заметка `fgos.note` видна только в SSE-потоке (`POST /chat/stream`), как и
  `mastery.gate`; синхронный `POST /chat` заметки не несёт (фронтенд использует
  stream).
- Покрытие в панели пересчитывается при смене subject/grade/reloadKey;
  мгновенной реактивности на середину хода нет (обновление — по `done`, см.
  `App.jsx:133-139`).

## 13. MVP-объём и вне изменений

MVP включает: каталог-модуль с seed (§4), сервис сверки/покрытия (§5),
конфиг-флаг (§6), три эндпоинта (§7), SSE-заметку `fgos.note` (§8), блок
программы в `StudentKGPanel` (§9), тесты (§11). Схема БД не меняется.

Вне объёма (возможные развития, по возрастанию стоимости):

- LLM-сверка перефразировок (`lookup` + необязательный `llm_match` как в
  референсе `src/curriculum.py:153-163`) — за флагом, fail-soft;
- расширение каталога до полных предметов/классов, перенос seed в
  JSON-ресурсы `adaptive_tutor/data/fgos_reference/…` с загрузчиком
  (`importlib.resources`/путь из настроек), поддержка диапазонов класса «5-6»;
- коды ФГОС в промпт репетитора/заметку при совпадении (в референсе код
  попадает в `st.curriculum` и текст урока: `src/graph.py:814-823`,
  `tutor.py:292-306`);
- JSONL-событие `curriculum.note`;
- показ «рекомендуемого порядка» разделов и отметок mastered в блоке;
- встраивание программы в граф источника (`/graph`) и/или `AdaptivePanel`.

---

## Что осталось (вне объёма данного этапа, следующий шаг)

- Live-smoke с реальным LLM и реальным `data/students.db`: тема «квадратные
  уравнения» (математика, 8 класс) — совпадение `МАТ.8.1` без заметки; тема
  «интегралы» — заметка `fgos.note`; блок «Программа (ФГОС)» в панели.
- После имплементации по плану — полный прогон:
  `.venv/Scripts/python.exe -m pytest tests -q`, `.venv/Scripts/python.exe -m
  ruff check src tests -q` из `adaptive_tutor/`, `npm test` и `npm run lint` из
  `frontend/`.
