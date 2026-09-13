import { useState } from 'react'
import Latex, { InlineText } from './Latex'

const LETTERS = ['А', 'Б', 'В', 'Г', 'Д', 'Е', 'Ж', 'З', 'И', 'К']
const DIFF_LABEL = { easy: 'лёгкий', medium: 'средний', hard: 'сложный' }

export default function QuizBlock({ envelope, onSend }) {
  const [open, setOpen] = useState('')
  // Индекс выбранного варианта (single) или true (open) — после отправки кнопки блокируются.
  const [sent, setSent] = useState(null)
  const payload = envelope?.payload || {}
  const options = payload.answer_type === 'single' ? payload.options || [] : []
  const difficulty = envelope?.difficulty
  const review = payload.review === true
  const progress = payload.num_questions
    ? ` · ${payload.question_num}/${payload.num_questions}`
    : ''
  const header = (
    <div className="block-head">
      <div className="block-label">Вопрос</div>
      {review && <span className="badge review">повторение</span>}
      {difficulty && (
        <span className={`badge difficulty ${difficulty}`}>
          {DIFF_LABEL[difficulty] || difficulty}
          {progress}
        </span>
      )}
    </div>
  )
  if (payload.answer_type === 'open' || options.length === 0) {
    const handleOpen = () => {
      if (sent) return
      setSent(true)
      onSend(`Ответ: ${open.trim()}`)
    }
    return (
      <div className="block quiz">
        {header}
        <div className="quiz-question">
          <Latex text={envelope?.text || ''} />
        </div>
        <div className="quiz-open">
          <input value={open} onChange={(e) => setOpen(e.target.value)} placeholder="Ваш ответ…" disabled={!!sent} />
          <button className="btn" disabled={!open.trim() || !!sent} onClick={handleOpen}>Ответить</button>
        </div>
      </div>
    )
  }
  return (
    <div className="block quiz">
      {header}
      <div className="quiz-question">
        <Latex text={envelope?.text || ''} />
      </div>
      <div className="quiz-options">
        {options.map((opt, i) => {
          const letter = LETTERS[i] || String(i + 1)
          const chosen = sent === i
          return (
            <button
              key={`${i}-${opt}`}
              type="button"
              className={`quiz-option${chosen ? ' chosen' : ''}`}
              aria-label={`${letter}. ${opt}`}
              disabled={sent != null}
              onClick={() => { setSent(i); onSend(`Ответ: ${opt}`) }}
            >
              <span className="quiz-option-letter" aria-hidden="true">{letter}</span>
              <span className="quiz-option-text">
                <InlineText text={opt} />
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
