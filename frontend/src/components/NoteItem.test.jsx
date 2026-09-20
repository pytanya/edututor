import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import NoteItem, { noteType, normalizeNote } from './NoteItem'

describe('noteType', () => {
  it('returns error for ошибка feedback', () => {
    expect(noteType({ feedback: 'Ошибка в расчёте' })).toBe('error')
  })
  it('returns error for неверн feedback', () => {
    expect(noteType({ feedback: 'Неверный ответ' })).toBe('error')
  })
  it('returns clarification for question', () => {
    expect(noteType({ feedback: 'ok', question: 'Что такое X?' })).toBe('clarification')
  })
  it('returns clarification for student_answer', () => {
    expect(noteType({ feedback: 'ok', student_answer: '42' })).toBe('clarification')
  })
  it('returns info by default', () => {
    expect(noteType({ feedback: 'Хороший ответ' })).toBe('info')
  })
})

describe('normalizeNote', () => {
  it('parses legacy string', () => {
    const n = normalizeNote('2026-01-01: Ответ неверный')
    expect(n.date).toBe('2026-01-01')
    expect(n.feedback).toBe('Ответ неверный')
  })
  it('parses string without date', () => {
    const n = normalizeNote('Просто заметка')
    expect(n).toBeTruthy()
    expect(n.feedback).toBe('Просто заметка')
  })
  it('passes object through', () => {
    const obj = { feedback: 'test', date: '2026-01-01' }
    expect(normalizeNote(obj)).toBe(obj)
  })
  it('returns null for falsy', () => {
    expect(normalizeNote(null)).toBeNull()
    expect(normalizeNote(undefined)).toBeNull()
  })
})

describe('NoteItem', () => {
  it('renders feedback text collapsed', () => {
    render(<NoteItem note={{ feedback: 'Неверный ответ', date: '2026-01-15' }} />)
    expect(screen.getByText('Неверный ответ')).toBeTruthy()
    expect(screen.getByText('2026-01-15')).toBeTruthy()
  })

  it('expands on click to show details', async () => {
    const user = userEvent.setup()
    render(<NoteItem note={{
      feedback: 'Ошибка в вычислениях',
      question: 'Сколько 2+2?',
      student_answer: '5',
      correct_answer: '4',
      date: '2026-01-15',
    }} />)
    const header = screen.getByRole('button')
    await user.click(header)
    expect(screen.getByText('Вопрос:')).toBeTruthy()
    expect(screen.getByText('Ваш ответ:')).toBeTruthy()
    expect(screen.getByText('Правильный ответ:')).toBeTruthy()
  })

  it('renders nothing for empty note', () => {
    const { container } = render(<NoteItem note={{}} />)
    expect(container.innerHTML).toBe('')
  })

  it('renders nothing for null', () => {
    const { container } = render(<NoteItem note={null} />)
    expect(container.innerHTML).toBe('')
  })

  it('has correct CSS class for error type', () => {
    const { container } = render(<NoteItem note={{ feedback: 'Ошибка!' }} />)
    expect(container.querySelector('.note-item--error')).toBeTruthy()
  })

  it('has correct CSS class for info type', () => {
    const { container } = render(<NoteItem note={{ feedback: 'Хорошо' }} />)
    expect(container.querySelector('.note-item--info')).toBeTruthy()
  })
})
