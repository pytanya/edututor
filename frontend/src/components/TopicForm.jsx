import { useState } from 'react'

export default function TopicForm({ onStart, prefill = {} }) {
  const [subject, setSubject] = useState('математика')
  const [grade, setGrade] = useState(prefill.grade || '')
  const [topic, setTopic] = useState('')
  return (
    <form
      className="panel topic-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (topic.trim()) onStart({ subject: subject.trim(), grade: grade.trim(), topic: topic.trim() })
      }}
    >
      <h3>Новое занятие</h3>
      <label>Предмет
        <input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="математика" />
      </label>
      <label>Класс
        <input value={grade} onChange={(e) => setGrade(e.target.value)} placeholder="8 класс" />
      </label>
      <label>Тема
        <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="квадратные уравнения" />
      </label>
      <button className="btn" disabled={!topic.trim()} type="submit">Начать</button>
    </form>
  )
}
