# E4 — Knowledge Wiki и экспорт (учителю): дизайн

> Статус: утверждено к планированию. Реализация не выполнялась.
> Этап 4 gap-аудита `docs/superpowers/2026-09-04-gap-audit-edututor-vs-reference.md`.
> Референс: `C:\otus\project_work` (wiki.py, okf.py, export.py, api/routes/wiki.py,
> api/routes/documents.py, api/routes/graph.py, frontend KnowledgeWikiPanel.jsx/MasteryWall.jsx).
> Зависимости: answer-record (E1, `src/student/answer_record.py`), mastery-EMA/статусы
> (E2, `src/student/mastery.py`), source graph + mastery-оверлей (E2, `src/kg/*`).

## 1. Цель и границы

- **Knowledge Wiki**: персональные статьи по темам с мастерством (markdown-файлы с YAML-frontmatter
  OKF v0.2), синхронизация на каждый ответ, заметки об ошибках с дедупом, тело-«конспект»
  (ленивое обогащение LLM), индекс по предмету; UI-панель статей/тепловая карта мастерства.
- **Экспорт для учителя**: CSV по сессиям (вопросы + сводка) и OKF-бандл графа источника с
  мастерством — персистентные файлы + download-эндпоинты.
- **Хранилище журнала ответов** `session_records` — источник для CSV и истории (вводится здесь).

**Вне границ:** review-карточки (E1), построение графа/мастерства (E2), онбординг (E3).
Wiki-статьи в E4 читают мастерство из Слоя 2 и answer record; источник графа для OKF — из E2.

## 2. Модель Wiki (файлы на диске)

Ценность Wiki — читаемые/экспортируемые markdown-файлы OKF, поэтому храним **файлами**, не в SQLite.

### 2.1 Пути и slug

- Корень: `settings.knowledge_wiki_dir` = `data/knowledge_wiki` (резолвится от `adaptive_tutor/`,
  аналогично `student_db_path`).
- Статья: `<root>/<student_id>/<slug(subject)>/<slug(topic)>.md`.
- Индекс предмета: `<root>/<student_id>/<slug(subject)>/_index.md`.

`_slug(text)` (1:1 с референсом): посимвольно: `isalnum() or "-_."` → как есть; пробелы и `/`,`\`
→ `-`; lowercase; trim `-`; пустой результат → `"topic"`. Кириллица сохраняется.

### 2.2 Формат статьи (OKF v0.2)

Frontmatter (YAML, `allow_unicode`, `sort_keys=False`) ключи в порядке:

```yaml
---
okf_version: '0.2'
type: Topic
title: <title>
topic: <topic>
subject: <subject (человеческое имя)>
grade: <grade или ''>
curriculum: <'' или код>
mastery: 0.75
accuracy: 0.5
attempts: 2
correct: 1
last_studied: '2026-09-04T10:00:00'
section_number: ...          # только если есть
weak_areas: [...]            # только если есть (в E2 не наполняется — зарезервировано)
relations: [{target, relation}]  # только если есть (из графа)
notes:
  - date: '2026-09-04'            # только если есть
    feedback: 'Неверно. Правильный ответ: …'
    question: '…'                 # вопрос, если есть
    student_answer: '…'           # ответ ученика, если есть
    correct_answer: '…'           # правильный ответ, если есть
concepts: [...]                   # только если есть
source: 'https://…'               # только если есть
---
# <title>

<body>
```

(Многоточия в примере — значения, а не плейсхолдеры реализации.)

Тело по умолчанию (пока не обогащено): «Материал по теме «{title}» накапливается по мере
прохождения квизов.» Заголовок `# title` всегда перегенерируется; при чтении первая строка
`# ` из body срезается. `accuracy = correct/attempts` (round 4; 0.0 при attempts=0) —
производное, не хранится в yaml как вход (но присутствует в to_dict/API).

### 2.3 Классы (адаптация wiki.py)

- `WikiNote`: `date, question?, student_answer?, feedback, correct_answer?`; дедуп по
  `feedback[:180]` (точное сравнение): при дубле обновить дату, добить отсутствующие
  `student_answer/correct_answer`; максимум 10 заметок (хвост).
- `WikiArticle`: поля из frontmatter + `body`; `MAX_NOTES=10`; `apply_result(score01, correct,
  feedback, ...)`: EMA `mastery = round(0.7*mastery + 0.3*score01, 4)`, `attempts += 1`,
  `correct += int(correct)`, `last_studied = now`, при неверном — `add_note`.
- `KnowledgeWiki(root_dir, student_id)`: `get(subject, topic)`, `apply_record(record)`,
  `sync_mastery(topic_updates)`, `enrich_body(...)`, `upsert(article)` (пишет файл + обновляет
  `_index.md`), `delete(subject, topic)`, `list_subjects()`, `list_articles(subject=None)`,
  `to_summary_dict()`.
- `apply_record(record)` → если `topic/score01/correct` отсутствуют → None; иначе создать
  статью (grade/curriculum из контекста) и `apply_result`.

### 2.4 Синхронизация (когда)

В `_run_chat` рядом с E2-хуками:
- **На каждый evaluation** (обычный и review-ответ): `wiki.apply_record(answer_record)`;
- **Обогащение тела** (лениво): если статья существует и body пуст/≤20 символов — запустить
  `enrich_body` (LLM, роль `fast`, temp 0.3, max_tokens 400; фрагменты = RAG-сниппеты темы,
  лимит 4000 символов; вернуть тело 3-6 предложений; при ошибке/пустом результате — оставить
  заглушку). Роль быстрая, ошибки глотаются.
- **Мастерство на конец занятия** не требуется (нет summary-события в edututor) — при
  необходимости синхронизация идемпотентна на каждый ответ (EMA сходится к тому же значению).

### 2.5 API Wiki

Под `/student`:
- `GET /student/{student_id}/wiki?subject=` →
  `{"subjects": [{subject, articles: [article_dict]}]}` или (с subject) `{"subject", "articles": [...]}`;
- `GET /student/{student_id}/wiki/{subject}/{topic}` → article_dict; 404 «Тема не найдена в базе знаний»;
- `POST /student/{student_id}/wiki/enrich` body `{subject, topic}` → `{"article": dict|null,
  "note": "..."}` (при отсутствии RAG-материалов — сообщение-подсказка, не 404);
- `DELETE /student/{student_id}/wiki/{subject}/{topic}` → `{"deleted": true, ...}`.

`article_dict` (to_dict) = frontmatter + `accuracy` + `body`.

## 3. Журнал ответов `session_records` (источник CSV)

SQLite-таблица в том же `students.db`:

```sql
CREATE TABLE IF NOT EXISTS session_records (
  student_id TEXT NOT NULL,
  record_id  TEXT NOT NULL,           -- rec_<uuid12>, генерится на каждый evaluation
  session_id TEXT NOT NULL,
  ts         REAL NOT NULL,           -- time.time() (unix), для порядка/сортировки
  subject    TEXT DEFAULT '',
  topic      TEXT DEFAULT '',
  question_id TEXT DEFAULT '',        -- оригинальный id квиза или 'review:<card_id>'
  question   TEXT DEFAULT '',
  options    TEXT DEFAULT NULL,       -- JSON-массив
  answer_type TEXT DEFAULT 'open',
  difficulty TEXT DEFAULT 'medium',
  student_answer TEXT DEFAULT '',
  correct    INTEGER DEFAULT 0,
  feedback   TEXT DEFAULT '',
  score01    REAL DEFAULT 0.0,
  PRIMARY KEY (student_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_records_student ON session_records (student_id, ts);
```

- Запись пишется при каждом evaluation (из answer record; `record_id = rec_<uuid12>`,
  `ts = time.time()`; вопрос/варианты — из склейки quiz-конверта, при их отсутствии
  `question=""`). Review-ответы тоже логируются (question_id `review:<card_id>`) — учителю видно
  повторение. ISO-метки для CSV вычисляются на лету из `ts`.
- Метод: `store.append_record(student_id, session_id, record) -> None` и
  `store.list_records(student_id, subject=None, session_id=None, limit=500)`.

## 4. Экспорт для учителя

### 4.1 CSV (download)

`GET /student/{student_id}/export/csv?subject=` → `text/csv; charset=utf-8` (utf-8-sig — Excel),
`Content-Disposition: attachment; filename="<student_id>_session_log.csv"`.

Колонки (вопросы): `timestamp, session_id, subject, topic, question_id, question, options,
answer_type, difficulty, student_answer, score01, correct, feedback` (`options` — через `" | "`).
Плюс сводный блок строкой-заголовком не смешиваем: отдельный CSV
`GET /student/{student_id}/export/summary.csv` — по одной строке на сессию:
`session_id, subject, topic, started_at, ended_at, questions, correct, accuracy, mastered_topics`.

Сборка — чистые функции `src/export/csv_exporter.py`: `questions_csv(rows)`, `summary_csv(rows)`.

### 4.2 OKF-бандл

Адаптация `okf.emit_okf_bundle`: экспортирует **граф источника subject|grade** (E2) как
каталог markdown:
- `data/okf/<student_id>/<subject>/index.md` — frontmatter `{okf_version: '0.2', type: Index,
  title: Учебник «{subject}», subject, grade, curriculum}` + ссылки на `topics/<slug>.md` для
  каждого узла, кроме `book`;
- `topics/<slug(title)>.md` — на узел (не book): frontmatter `{type: Topic|Section, title,
  subject, grade, mastery (если есть), relations: [..]}` + короткое тело;
- `log.md` — история.
`GET /student/{student_id}/export/okf?subject=&grade=` генерирует бандл, возвращает
`{"dir", "conformant": true|false, "errors": [], "files": [...]}`; `validate_bundle` — каждый
`.md` начинается с `---`, валидный YAML, непустой `type`.

### 4.3 Роли

Экспортные эндпоинты требуют `student_id` из запроса (без аккаунтов — тот же режим доверия,
что у `/student/{id}/sessions`). CSV/OKF строятся на лету из SQLite + файлов wiki/графа.

## 5. UI

### 5.1 KnowledgeWikiPanel («Конспекты») + MasteryWall

- Левая/правая панель: список предметов и статей (заголовок, mastery %, попытки), открытие
  статьи в модальном/встроенном ридере (рендер markdown+LaTeX существующими `markdown.js`/`Latex`).
- Тепловая карта мастерства (`MasteryWall`) по `/student/{id}/wiki` — как в E2, но источник —
  wiki (квадратики по темам, `>=0.75 high / >=0.45 mid / low`, клик → открыть статью).
- Обновление после каждого `done`-события (статьи/мастерство меняются после ответа) и после
  `enrich`.
- Панель скрыта/пустая, если у ученика нет статей («Пройдите квиз по теме — конспекты появятся»).

### 5.2 Экспорт в UI

Кнопка «⬇ Экспорт» у панели/профиля: CSV по ученику (download) и OKF (манифест). Точное место —
App (заголовок профиля/адаптивной панели). Ранняя реализация — простая ссылка download.

## 6. Зависимости/конфиг

`src/config.py`/`.env.example`: `knowledge_wiki_dir` (default `data/knowledge_wiki`),
`okf_dir` (default `data/okf`), `wiki_enabled: bool = True`, `wiki_enrich_enabled: bool = True`.
Зависимость `PyYAML>=6.0` уже есть в окружении (6.0.3, транзитивно) — зафиксировать явно в
`pyproject.toml`/`requirements.txt`, т.к. wiki/OKF читают и пишут YAML-frontmatter напрямую.
Прочих новых Python-зависимостей нет.

## 7. Крайние случаи

- Битый файл статьи → `get` возвращает None (молча), файл не удаляем; `upsert` перезапишет.
- Collision slug (`Тема/Раздел` vs `Тема-Раздел`) → известное ограничение (путь = slug);
  человеческое имя берётся из frontmatter.
- Статья без ответов → не создаётся (статья создаётся на первом оценённом ответе).
- `weak_areas` — зарезервированное поле OKF; в этом порте не наполняется (в референсе тоже
  никогда не писалось; источник слабых мест — Слой 2 в SQLite, см. E2).
- Экспорт с пустыми записями → пустые CSV (заголовки есть).
- Слишком много записей → лимит 500 последних по умолчанию (query-параметр).
- Ошибки enrich/БД/файлов — try/except, чат не падает.
- Путь subject в URL: slug-имя; в ответе возвращаем человеческое имя.
- Экспорт CSV/OKF требует указания student_id (как и существующие `/student/{id}/…`); авторизации
  нет — тот же режим доверия, что и у остальных эндпоинтов профиля.

## 8. Наблюдаемость

JSONL: `wiki.updated` (subject, topic, mastery), `wiki.note` (при добавлении), `export.csv`
(count, bytes), `export.okf` (files, conformant).

## 9. Тесты

Backend:
- slug (`Тема/Раздел`, юникод, пустой → topic); статья round-trip (frontmatter↔dict); body
  default; чтение битого файла → None.
- EMA/notes: мастерство 3 верных = 0.8285 с якорем; дедуп заметок по `feedback[:180]`; кап 10.
- `apply_record` idempotent (по answer record); enrich (пустое тело → запрос, ошибка → заглушка).
- `session_records`: append/лимит; CSV-сборка (utf-8-sig, колонки, options join).
- OKF: валидный бандл (validate: frontmatter/type); не-book узлы только; mastery в frontmatter.
- API: `/wiki`, `/wiki/enrich`, DELETE, `/export/csv`, `/export/okf`.

Frontend (vitest): MasteryWall цветовые классы; панель статей рендер пустого состояния.

## 10. Файлы

Создать: `src/wiki/__init__.py`, `src/wiki/models.py` (WikiNote/WikiArticle), `src/wiki/store.py`
(KnowledgeWiki + slug/индекс), `src/wiki/enrich.py`, `src/export/csv_exporter.py`,
`src/export/okf.py`, тесты `tests/test_wiki.py`, `tests/test_export.py`; фронтенд
`components/KnowledgeWikiPanel.jsx`, `components/MasteryWall.jsx`, `components/TopicArticle.jsx`
(ридер статьи) + стили.

Изменить: `src/student/store.py` (`session_records` + методы), `src/config.py`, `.env.example`,
`pyproject.toml`/`requirements.txt` (PyYAML), `src/api/server.py` (wiki/export-эндпоинты +
хук wiki/records в `_run_chat`), `frontend/src/api.js`, `frontend/src/App.jsx`,
`frontend/src/index.css`, README/docs.
