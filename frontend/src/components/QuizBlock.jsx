import { useState } from 'react'
import Latex from './Latex'

export default function QuizBlock({ envelope, onSend }) {
  const [open, setOpen] = useState('')
  const payload = envelope?.payload || {}
  const options = payload.answer_type === 'single' ? payload.options || [] : []
  const header = (
    <div className="block-head">
      {payload.review === true && <span className="badge review">повторение</span>}
      <div className="block-label">Вопрос</div>
    </div>
  )
  if (payload.answer_type === 'open' || options.length === 0) {
    return (
      <div className="block quiz">
        {header}
        <Latex text={envelope?.text || ''} />
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
      <Latex text={envelope?.text || ''} />
      <div className="quiz-options">
        {options.map((opt) => (
          <button key={opt} className="btn option" onClick={() => onSend(`Ответ: ${opt}`)}>{opt}</button>
        ))}
      </div>
    </div>
  )
}
