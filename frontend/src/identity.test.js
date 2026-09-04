import { describe, it, expect, beforeEach } from 'vitest'
import {
  getStudentId,
  getSessionId,
  setSessionId,
  clearSession,
  saveSession,
  deriveIdentityKey,
  deriveStudentId,
  resolveStudentId,
  loadStudentRecord,
  saveStudentRecord,
} from './identity'

beforeEach(() => localStorage.clear())

describe('identity', () => {
  it('generates and persists student id', () => {
    const id = getStudentId()
    expect(id).toMatch(/^stu_[0-9a-f]{12}$/)
    expect(JSON.parse(localStorage.getItem('edututor_student')).student_id).toBe(id)
    expect(getStudentId()).toBe(id)
  })
  it('roundtrips session id', () => {
    expect(getSessionId()).toBe('')
    setSessionId('ses_1')
    expect(getSessionId()).toBe('ses_1')
    clearSession()
    expect(getSessionId()).toBe('')
  })
  it('saveSession persists both', () => {
    saveSession('ses_2', 'stu_x')
    expect(getSessionId()).toBe('ses_2')
    expect(JSON.parse(localStorage.getItem('edututor_student')).student_id).toBe('stu_x')
  })
})

describe('deterministic student id', () => {
  it('fnv-1a is stable, 8 hex, format stu_', () => {
    const a = deriveStudentId('Иван Иванов', 'schoolchild', '8 класс')
    const b = deriveStudentId('Иван Иванов', 'schoolchild', '8 класс')
    expect(a).toBe(b)
    expect(a).toBe('stu_8debd364') // золотое значение FNV-1a (см. spec §3.1)
    expect(a).toMatch(/^stu_[0-9a-f]{8}$/)
  })

  it('different identity -> different id (кириллица хэшируется как есть)', () => {
    expect(deriveStudentId('Иван Иванов', 'schoolchild', '8 класс')).not.toBe(
      deriveStudentId('Мария Сидорова', 'student', '')
    )
  })

  it('normalizes case and whitespace, grade participates', () => {
    expect(deriveStudentId('  ИВАН  ИВАНОВ ', 'SCHOOLCHILD', '8 КЛАСС')).toBe('stu_8debd364')
    // grade входит в ключ даже пустой — «студент» и «студент 8 класс» разные люди
    expect(deriveStudentId('Мария Сидорова', 'student', '')).not.toBe(
      deriveStudentId('Мария Сидорова', 'student', '8 класс')
    )
  })

  it('canonicalName/deriveIdentityKey collapse spaces', () => {
    expect(deriveIdentityKey(' Иван   Петров ', 'student', '1 курс')).toBe(
      'иван петров|student|1 курс'
    )
  })
})

describe('resolveStudentId — правила spec §3.2', () => {
  const key = (name, type, grade) => deriveIdentityKey(name, type, grade)

  it('правило 1: та же личность + legacy id -> сохраняет id', () => {
    const stored = { student_id: 'stu_hist', identity: key('Иван Иванов', 'schoolchild', '8 класс'), legacy: true }
    const out = resolveStudentId('Иван Иванов', 'schoolchild', '8 класс', stored, { fields: [] })
    expect(out).toEqual({
      studentId: 'stu_hist',
      identity: key('Иван Иванов', 'schoolchild', '8 класс'),
      legacy: true,
    })
  })

  it('правило 1b: та же личность + канонический детерминированный id -> сохраняет', () => {
    const stored = { student_id: 'stu_8debd364', identity: key('Иван Иванов', 'schoolchild', '8 класс') }
    const out = resolveStudentId('Иван Иванов', 'schoolchild', '8 класс', stored, { fields: [] })
    expect(out.studentId).toBe('stu_8debd364')
    expect(out.legacy).toBe(false)
  })

  it('правило 2: другая личность -> новый детерминированный id (изоляция)', () => {
    const stored = { student_id: 'stu_8debd364', identity: key('Иван Иванов', 'schoolchild', '8 класс') }
    const out = resolveStudentId('Мария Сидорова', 'student', '', stored, { fields: [] })
    expect(out.studentId).toBe(deriveStudentId('Мария Сидорова', 'student', ''))
    expect(out.legacy).toBe(false)
  })

  it('правило 3: первый филл после апгрейда совпадает с префиллом -> legacy id', () => {
    const stored = { student_id: 'stu_anon_old', identity: '' }
    const card = {
      fields: [
        { key: 'name', value: 'Иван Иванов' },
        { key: 'learner_type', value: 'schoolchild' },
        { key: 'grade', value: '8 класс' },
      ],
    }
    const out = resolveStudentId('Иван Иванов', 'schoolchild', '8 класс', stored, card)
    expect(out.studentId).toBe('stu_anon_old')
    expect(out.legacy).toBe(true)
  })

  it('правило 4: иначе (новый браузер/расхождение) -> детерминированный id', () => {
    const out = resolveStudentId('Иван Иванов', 'schoolchild', '8 класс', {}, { fields: [] })
    expect(out.studentId).toBe('stu_8debd364')
    expect(out.identity).toBe(key('Иван Иванов', 'schoolchild', '8 класс'))
    expect(out.legacy).toBe(false)
  })
})

describe('student record в localStorage', () => {
  it('сохраняет и читает JSON-запись', () => {
    saveStudentRecord({ student_id: 'stu_x', student_name: 'Иван Иванов', learner_type: 'schoolchild', grade: '8 класс', identity: 'иван иванов|schoolchild|8 класс', legacy: true })
    expect(loadStudentRecord().student_name).toBe('Иван Иванов')
    expect(JSON.parse(localStorage.getItem('edututor_student')).legacy).toBe(true)
  })

  it('читает старый «сырой» анонимный id как legacy-запись', () => {
    localStorage.setItem('edututor_student', 'stu_old123456')
    expect(loadStudentRecord()).toEqual({ student_id: 'stu_old123456' })
    expect(getStudentId()).toBe('stu_old123456') // id не перегенерируется
  })

  it('не создаёт профиль без student_name: getStudentId пишет только id', () => {
    const id = getStudentId()
    expect(loadStudentRecord().student_name).toBeUndefined()
    expect(loadStudentRecord().student_id).toBe(id)
  })
})
