// NoteItem — аккордеон-карточка структурированной заметки (паттерн NoteItem из project_work)
import { useState } from 'react'
import Latex from './Latex'

/**
 * Определение типа заметки по наличию полей
 * @param {object} note
 * @returns {'error' | 'clarification' | 'info'}
 */
function noteType(note) {
  const fb = (note.feedback || '').toLowerCase()
  if (/ошибк|неверн|неправильн|wrong|incorrect/.test(fb)) return 'error'
  if (note.question || note.student_answer) return 'clarification'
  return 'info'
}

function typeIcon(type) {
  switch (type) {
    case 'error': return '🔴'
    case 'clarification': return '🟡'
    default: return '🟢'
  }
}

/**
 * Нормализация заметки: поддерживаем объекты и legacy-строки
 */
function normalizeNote(note) {
  if (!note) return null
  if (typeof note === 'string') {
    const parts = note.split(': ', 2)
    return { date: parts[0] || '', feedback: parts[1] || note }
  }
  return note
}

export { noteType, normalizeNote }

export default function NoteItem({ note }) {
  const [open, setOpen] = useState(false)
  const parsed = normalizeNote(note)
  if (!parsed) return null

  const { feedback, question, student_answer: studentAnswer, correct_answer: correctAnswer, date } = parsed
  if (!feedback && !question && !studentAnswer && !correctAnswer) return null

  const type = noteType(parsed)
  const icon = typeIcon(type)

  return (
    <div className={`note-item note-item--${type}`}>
      <button
        type="button"
        className="note-item__header"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className="note-item__icon">{icon}</span>
        <span className="note-item__feedback"><Latex text={feedback || 'Заметка'} /></span>
        {date && <span className="note-item__date">{date}</span>}
        <span className={`note-item__arrow ${open ? 'open' : ''}`}>▾</span>
      </button>

      {open && (
        <div className="note-item__body">
          {question && (
            <div className="note-item__row">
              <span className="note-item__label">Вопрос:</span>
              <span><Latex text={question} /></span>
            </div>
          )}
          {studentAnswer && (
            <div className="note-item__row">
              <span className="note-item__label">Ваш ответ:</span>
              <span>«<Latex text={studentAnswer} />»</span>
            </div>
          )}
          {correctAnswer && (
            <div className="note-item__row">
              <span className="note-item__label">Правильный ответ:</span>
              <span><Latex text={correctAnswer} /></span>
            </div>
          )}
          {feedback && type !== 'error' && (
            <div className="note-item__row">
              <span className="note-item__label">{type === 'clarification' ? 'Заметка:' : 'Комментарий:'}</span>
              <span><Latex text={feedback} /></span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
