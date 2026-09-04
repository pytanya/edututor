# E3 — Онбординг-интервью и детерминированный student_id: дизайн

> Статус: утверждено к планированию. Реализация не выполнялась.
> Этап 3 gap-аудита `docs/superpowers/2026-09-04-gap-audit-edututor-vs-reference.md`.
> Референс: `C:\otus\project_work` (intake.py, api/routes/intake.py, frontend IntakeCard.jsx/
> IntakeWizard.jsx/identity.js).

## 1. Цель и границы

- **Карточка знакомства** перед началом занятия: ФИО (≥2 слов), тип (школьник/студент),
  класс (если школьник), предмет, тема, режим («урок»). Prefill из сохранённого профиля.
- **Детерминированный `student_id`** из `ФИО|тип|класс` (FNV-1a 32-bit, формат `stu_`+8 hex),
  стабильный между устройствами/браузерами, с правилами миграции анонимного id из localStorage.
- **Backend-профиль**: персистентные поля `name`, `learner_type`, `grade` в SQLite; эндпоинты
  чтения/обновления; prefill TopicForm из профиля.
- Emergency-старт (начало занятия без профиля) сохраняется.

**Вне границ:** SM-2 (E1), граф знаний (E2), Wiki/экспорт (E4). Онбординг не вводит новый
LLM-режим общения: это карточка на клиенте + лёгкие серверные поля профиля.

## 2. Существующее состояние

- `frontend/src/identity.js`: `getStudentId()` создаёт случайный `stu_<12 hex>` в localStorage
  (привязка к браузеру), `getSessionId/setSessionId/clearSession/saveSession`.
- Сервер: `students(student_id, name, created_at, updated_at)`; `GET /student/{id}` возвращает
  только темы/рекомендацию; `name` не пишется нигде (`store.upsert_student` не сохраняет имя).
- Вход в занятие — только `TopicForm` (предмет/класс/тема) → `startTopic` → первый ход агента
  (`App.jsx:90-96`).
- `student_profile` (learning_style/fatigue) в `ChatRequest` — дефолты, не персистятся.

## 3. Детерминированный student_id

### 3.1 Алгоритм (1:1 с референсом, `frontend/src/identity.js`)

```javascript
export function canonicalName(name) {
  return String(name || '').trim().replace(/\s+/g, ' ').toLowerCase()
}

export function deriveIdentityKey(name, type, grade) {
  return `${canonicalName(name)}|${String(type || '').trim().toLowerCase()}|${String(grade || '').trim().toLowerCase()}`
}

export function hashStr(s) {
  // FNV-1a 32-bit — стабильный детерминированный хеш (без обращения к crypto)
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(16).padStart(8, '0')
}

export function deriveStudentId(name, type, grade) {
  return `stu_${hashStr(deriveIdentityKey(name, type, grade))}`
}
```

- Кириллица хэшируется как есть (UTF-16 code units), транслитерации нет.
- Пробелы в имени схлопываются, регистр нижний; `type` и `grade` — trim+lowercase.
- Идентичность — строка `name|type|grade` (grade участвует даже пустой).
- Формат `stu_` + ровно 8 hex (не крипто; изоляционный ключ, не секрет).

### 3.2 Reconciliation с анонимным id (правила референса)

`resolveStudentId(name, type, grade, stored, card)` возвращает
`{studentId, identity, legacy}`:

1. `stored.identity === deriveIdentityKey(...)` и `stored.student_id === deriveStudentId(...)`
   или `stored.legacy === true` → оставить сохранённый id.
2. Известна прежняя идентичность, но карточка заполнена другой → новый детерминированный id
   (изолированная ветка данных).
3. Первое заполнение после «апгрейда» (в localStorage анонимный id, prefill-профиль совпадает с
   идентичностью из карточки) → оставить исторический id, пометить `legacy: true`.
4. Иначе → детерминированный id из заполненной карточки.

Хранимая запись в localStorage (`edututor_student`): `{student_id, student_name, learner_type,
grade, identity, legacy}`. Старые данные под анонимным id остаются «осиротевшими» (merge не
делаем — продукты на ранней стадии; фиксируем как известное ограничение).

## 4. Backend: профиль

### 4.1 Миграция

```sql
ALTER TABLE students ADD COLUMN learner_type TEXT DEFAULT '';  -- student|schoolchild
ALTER TABLE students ADD COLUMN grade TEXT DEFAULT '';
```

Идемпотентно при старте `StudentStore` (PRAGMA table_info). `name` уже есть.

### 4.2 Методы StudentStore

```python
set_profile(student_id, *, name=None, learner_type=None, grade=None) -> dict  # upsert; возвращает профиль
get_student(student_id) -> dict | None  # теперь включает name/learner_type/grade
```

### 4.3 API

- `GET /student/{student_id}` — дополнить ответ полями `name, learner_type, grade` (для prefill
  при повторном заходе). 404 сохраняется для неизвестного ученика.
- `POST /student/{student_id}/profile` — body
  `{"name": str="", "learner_type": Literal["student","schoolchild"]|"", "grade": str=""}`:
  валидация: `name` при непустом — ≥2 слов (иначе 422); `learner_type` — из списка;
  сохраняет и возвращает профиль. Не 404 для нового id (upsert).

## 5. Frontend: поток онбординга

### 5.1 Состояние и запуск

В `App.jsx` при старте и после `loadSessions`:
- читаем `localStorage[STUDENT_KEY]`; если записи нет или `!student_name` → `intakeRequired = true`;
- вместо (или поверх) `TopicForm` показываем **`IntakeCard`** — карточку знакомства.

### 5.2 IntakeCard (компонент)

Поля (порядок как в референсе; prefill из профиля, если есть):

| key | label | type | required | options |
|---|---|---|---|---|
| `name` | Как тебя зовут (имя и фамилия)? | text | да | — |
| `learner_type` | Ты школьник или студент? | choice | да | schoolchild «Школьник» / student «Студент» |
| `grade` | Класс (если школьник) | text | если школьник | — |
| `subject` | Предмет | text | да | — |
| `topic` | Тема | text | да | — |
| `mode` | Что делаем? | choice | да | только `lesson` «Урок (изучим тему)» (референс: расширенные режимы — после, через чат) |

Валидация: кнопка «Начать занятие» неактивна, пока не заполнены обязательные; `name` должен
содержать ≥2 слова (подсказка «Укажи имя и фамилию (минимум два слова).»); `grade` требуется
только для `schoolchild`.

Кнопка **«Начать без карточки»** (emergency/anonymous): продолжает работать с анонимным id
(текущее поведение), профиль не создаётся; карточка снова появится при следующем запуске
до тех пор, пока профиль не заполнен.

### 5.3 Сабмит карточки

1. `resolveStudentId(...)` по правилам 3.2 → `{studentId, identity, legacy}`;
2. сохранить в localStorage запись `edututor_student` (`student_id, student_name, learner_type,
   grade, identity, legacy`);
3. `studentId.current` в App заменяется на useState (тек. useRef фиксирует id один раз при
   инициализации — нужен переключаемый стейт); `sessionId` сбрасывается;
4. `api.profile(studentId, {name, learner_type, grade})` — персист на сервер (fail-soft);
5. запустить занятие: `startTopic({subject, grade, topic})` (режим всегда lesson) — переиспользуя
   текущий `runTurn`/`_provision_for`; первые ходы агента идут под детерминированным id.

### 5.4 Повторный заход

- `GET /student/{id}` + localStorage → prefill полей карточки (при возвращающемся ученике
  заполнены name/тип/класс; предмет/тема — новые). Если профиль полон — карточка не
  показывается, сразу `TopicForm` (класс и тип prefill'ятся из профиля).
- `TopicForm` дополняется: `grade` prefill из профиля (для `schoolchild`), плейсхолдер темы.

### 5.5 api.js

```javascript
profile: (studentId, body) => request(`/student/${id}/profile`, { method: 'POST', body: JSON.stringify(body) })
```
и расширенный `student(studentId)` (поля профиля уже в ответе).

## 6. Крайние случаи

- Открыто на двух устройствах с одинаковыми ФИО/классом → один и тот же детерминированный id
  (желаемое поведение); разные ФИО/класс → разные ветки.
- Смена ФИО/типа/класса → новый id и «пустая» ветка (осознанная изоляция, как в референсе).
- Класс «5Б»/«5 б»/«7-А»: grade хранится как строка; классный фильтр провижининга использует
  строку как раньше (совместимость не ломается).
- Сервер недоступен при сабмите → локально сохраняем профиль, серверную запись повторим при
  следующем ходе (upsert на каждом `_run_chat` уже есть — добавить set_profile при наличии
  полей в localStorage? Нет: пишем профиль только по явному POST; допускается повторный POST
  при следующем запуске).
- Уже существующие анонимные сессии/темы в БД под старым id остаются доступны через
  SessionList только в том же браузере (id в localStorage меняется после карточки — это
  известное ограничение, фиксируем; списки сессий грузятся по текущему id).

## 7. Тесты

Frontend (vitest, `identity.test.js` расширить):
- FNV-1a: стабильность, формат `/^stu_[0-9a-f]{8}$/`, разные ФИО → разные id, регистр/пробелы
  нормализуются;
- `resolveStudentId`: 4 правила (сохранение, новая ветка, legacy-первое заполнение, обычный).
- IntakeCard: рендер полей, disabled-кнопка, подсказка о 2 словах, «Начать без карточки».

Backend (pytest):
- миграция колонок; `set_profile` upsert; `GET /student/{id}` отдаёт профиль;
- `POST /student/{id}/profile` валидация (пустое/одно слово имя → 422, тип вне списка → 422).

## 8. Файлы

Создать: `frontend/src/components/IntakeCard.jsx`; тесты `identity.test.js`/`App`-расширения.
Изменить: `frontend/src/identity.js` (derive/hash/resolve + локальная запись студента),
`frontend/src/api.js`, `frontend/src/App.jsx`, `frontend/src/components/TopicForm.jsx`,
`frontend/src/index.css`, `adaptive_tutor/src/student/store.py` (миграция + set_profile),
`src/api/server.py` (GET /student профиль + POST /student/{id}/profile), `tests/test_api.py`,
`tests/test_student_store.py`, README/docs.
