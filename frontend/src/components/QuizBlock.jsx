import { useState } from 'react'
import Latex, { InlineText } from './Latex'

const LETTERS = ['А', 'Б', 'В', 'Г', 'Д', 'Е', 'Ж', 'З', 'И', 'К']
const DIFF_LABEL = { easy: 'лёгкий', medium: 'средний', hard: 'сложный' }

export default function QuizBlock({ envelope, onSend }) {
  const [open, setOpen] = useState('')
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
    return (
      <div className="block quiz">
        {header}
        <div className="quiz-question">
          <Latex text={envelope?.text || ''} />
        </div>
        <div className="quiz-open">
          <input value={open} onChange={(e) => setOpen(e.target.value)} placeholder="Ваш ответ…" />
          <button className="btn" disabled={!open.trim()} onClick={() => onSend(`Ответ: ${open.trim()}`)}>Ответить</button>
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
          return (
            <button
              key={`${i}-${opt}`}
              type="button"
              className="quiz-option"
              aria-label={`${letter}. ${opt}`}
              onClick={() => onSend(`Ответ: ${opt}`)}
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
