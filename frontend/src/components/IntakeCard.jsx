import { useState } from 'react'

const LEARNER_TYPES = [
  { value: 'schoolchild', label: 'Школьник' },
  { value: 'student', label: 'Студент' },
]

export default function IntakeCard({ prefill = {}, onSubmit, onSkip }) {
  const [values, setValues] = useState({
    name: prefill.name || '',
    learner_type: prefill.learner_type || '',
    grade: prefill.grade || '',
    subject: prefill.subject || '',
    topic: prefill.topic || '',
    mode: 'lesson',
  })

  const setField = (key, val) => setValues((v) => ({ ...v, [key]: val }))

  const text = (key) => String(values[key] ?? '').trim()
  const nameWords = text('name').split(/\s+/).filter(Boolean).length
  const badName = text('name') && nameWords < 2 ? 'Укажи имя и фамилию (минимум два слова).' : ''
  const gradeRequired = values.learner_type === 'schoolchild'
  const missing = [
    !text('name') && 'имя',
    !text('learner_type') && 'тип',
    gradeRequired && !text('grade') && 'класс',
    !text('subject') && 'предмет',
    !text('topic') && 'тему',
  ].filter(Boolean)
  const invalid = missing.length > 0 || !!badName

  const submit = (e) => {
    e.preventDefault()
    if (invalid) return
    onSubmit({
      name: text('name'),
      learner_type: values.learner_type,
      grade: text('grade'),
      subject: text('subject'),
      topic: text('topic'),
      mode: values.mode || 'lesson',
    })
  }

  return (
    <form className="panel topic-form intake-card" onSubmit={submit}>
      <h3>Знакомство и план занятия</h3>

      <label>Как тебя зовут (имя и фамилия)?
        <input value={values.name} onChange={(e) => setField('name', e.target.value)} placeholder="Иван Иванов" />
      </label>

      <label>Ты школьник или студент?
        <select value={values.learner_type} onChange={(e) => setField('learner_type', e.target.value)}>
          <option value="">— выбери —</option>
          {LEARNER_TYPES.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>

      {gradeRequired && (
        <label>Класс
          <input value={values.grade} onChange={(e) => setField('grade', e.target.value)} placeholder="8 класс" />
        </label>
      )}

      <label>Предмет
        <input value={values.subject} onChange={(e) => setField('subject', e.target.value)} placeholder="математика" />
      </label>

      <label>Тема
        <input value={values.topic} onChange={(e) => setField('topic', e.target.value)} placeholder="квадратные уравнения" />
      </label>

      <label>Что делаем?
        <select value={values.mode} onChange={(e) => setField('mode', e.target.value)}>
          <option value="lesson">Урок (изучим тему)</option>
        </select>
      </label>

      {missing.length > 0 && <p className="intake-card__hint muted">Заполни: {missing.join(', ')}</p>}
      {badName && <p className="intake-card__hint muted">{badName}</p>}

      <button className="btn" type="submit" disabled={invalid}>Начать занятие</button>
      <button className="btn intake-skip" type="button" onClick={onSkip}>Начать без карточки</button>
    </form>
  )
}
