// identity — student_id/session_id в localStorage (переживают перезагрузку).
const STUDENT_KEY = 'edututor_student'
const SESSION_KEY = 'edututor_session'

function hex(n) {
  const bytes = new Uint8Array(n)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

// ---- Детерминированная личность (spec §3.1; 1:1 с референсом) ----

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

// Идентичность, которая уже живёт в профиле (префилл карточки знакомства).
// null — если профиль пуст (новый браузер, ещё никто не заполнял карточку).
export function prefilledIdentity(card) {
  if (!card?.fields) return null
  const get = (key) => {
    const f = card.fields.find((x) => x.key === key)
    return f ? String(f.value ?? '') : ''
  }
  const name = get('name')
  const type = get('learner_type')
  const grade = get('grade')
  if (!name && !type && !grade) return null
  return deriveIdentityKey(name, type, grade)
}

// Единственная точка принятия решения «какой student_id использовать» (spec §3.2).
//
// Правила:
// 1. Та же личность (identity совпадает) и сохранённый id канонический (детерминированный
//    для этой identity) или честно закреплён при апгрейде (legacy) — оставляем сохранённый id.
// 2. Известна прежняя личность, но карточка заполнена ДРУГАЯ → новый детерминированный id
//    (изолированная ветка данных).
// 3. Первое заполнение после «апгрейда» (в localStorage анонимный id без identity,
//    префилл профиля совпадает с identity карточки) → исторический id, помечаем legacy: true.
// 4. Иначе — детерминированный id из заполненной карточки.
export function resolveStudentId(name, type, grade, stored, card) {
  const identity = deriveIdentityKey(name, type, grade)
  const deterministic = deriveStudentId(name, type, grade)
  if (stored?.identity) {
    if (stored.identity === identity) {
      if (stored.student_id === deterministic || stored.legacy === true) {
        return { studentId: stored.student_id, identity, legacy: stored.legacy === true }
      }
    }
    return { studentId: deterministic, identity, legacy: false }
  }
  const legacy = prefilledIdentity(card)
  if (stored?.student_id && legacy === identity) {
    return { studentId: stored.student_id, identity, legacy: true }
  }
  return { studentId: deterministic, identity, legacy: false }
}

// ---- Локальная запись ученика (JSON под edututor_student) ----
export function loadStudentRecord() {
  try {
    const raw = localStorage.getItem(STUDENT_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && typeof parsed.student_id === 'string') {
      return parsed
    }
    if (typeof parsed === 'string' && parsed) return { student_id: parsed }
    return {}
  } catch {
    // Старый формат: под ключом лежала «голая» строка анонимного id.
    const raw = localStorage.getItem(STUDENT_KEY)
    return raw ? { student_id: String(raw) } : {}
  }
}

export function saveStudentRecord(record) {
  try {
    localStorage.setItem(STUDENT_KEY, JSON.stringify(record || {}))
  } catch {
    /* ignore */
  }
}

export function getStudentId() {
  const record = loadStudentRecord()
  if (record.student_id) return record.student_id
  const id = `stu_${hex(6)}`
  saveStudentRecord({ student_id: id })
  return id
}

export function getSessionId() {
  try {
    return localStorage.getItem(SESSION_KEY) || ''
  } catch {
    return ''
  }
}

export function setSessionId(id) {
  try {
    localStorage.setItem(SESSION_KEY, String(id || ''))
  } catch {
    /* ignore */
  }
}

export function clearSession() {
  try {
    localStorage.removeItem(SESSION_KEY)
  } catch {
    /* ignore */
  }
}

export function saveSession(sessionId, studentId) {
  try {
    if (sessionId) localStorage.setItem(SESSION_KEY, sessionId)
    if (studentId) saveStudentRecord({ ...loadStudentRecord(), student_id: studentId })
  } catch {
    /* ignore */
  }
}
