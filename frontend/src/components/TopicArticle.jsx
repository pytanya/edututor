// TopicArticle — ридер конспекта: мета, статистика, изложение, заметки-карточки
// с цветом по типу, концепции, слабые места, навигация по темам предмета.
import { useState } from 'react'
import Latex from './Latex'
import { masteryClass } from './MasteryWall'

export function isStubBody(body) {
  const b = (body || '').trim()
  return !b || b.includes('накапливается по мере прохождения квизов')
}

export function noteType(n) {
  const fb = String(n?.feedback || '').toLowerCase()
  if (/ошибк|неверн|неправильн/.test(fb)) return 'error'
  if (n?.question || n?.student_answer || n?.correct_answer) return 'clarification'
  return 'info'
}

const ICONS = { error: '🔴', clarification: '🟡', info: '🟢' }

export default function TopicArticle({ article, onClose = null, onEnrich = null, enriching = false, siblings = [], topicIndex = -1, onNavigate = null }) {
  const [openNotes, setOpenNotes] = useState(() => new Set([0]))
  if (!article) return null
  const mastery = typeof article.mastery === 'number' ? article.mastery : 0
  const accuracy = typeof article.accuracy === 'number' ? article.accuracy : 0
  const attempts = article.attempts || 0
  const body = article.body || ''
  const notes = Array.isArray(article.notes) ? article.notes : []
  const concepts = Array.isArray(article.concepts) ? article.concepts : []
  const weakAreas = Array.isArray(article.weak_areas) ? article.weak_areas : []
  const stub = isStubBody(body)
  const pct = Math.round(mastery * 100)
  const cls = masteryClass(mastery)

  const toggleNote = (i) => {
    setOpenNotes((prev) => {
      const next = new Set(prev)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })
  }

  const prevArt = topicIndex > 0 ? siblings[topicIndex - 1] : null
  const nextArt = topicIndex >= 0 && topicIndex < siblings.length - 1 ? siblings[topicIndex + 1] : null

  return (
    <div className="topic-overlay" role="dialog" aria-modal="true" aria-label={article.title}>
      <div className="topic-article panel">
        <div className="topic-head">
          <div className="topic-heading">
            <h2 className="topic-title">{article.title}</h2>
            <div className="topic-meta">
              {article.subject ? <span>{article.subject}</span> : null}
              {article.grade ? <span>· {article.grade}</span> : null}
              {article.curriculum ? <span>· {article.curriculum}</span> : null}
              {article.source ? <span>· 📎 {article.source}</span> : null}
            </div>
          </div>
          <div className="topic-actions">
            {onEnrich ? (
              <button type="button" className="btn small" disabled={enriching} onClick={onEnrich}>
                {enriching ? 'Обогащаем…' : 'Обогатить конспект'}
              </button>
            ) : null}
            {onClose ? (
              <button type="button" className="topic-close" aria-label="Закрыть" onClick={onClose}>✕</button>
            ) : null}
          </div>
        </div>

        <div className="topic-stats">
          <div className="topic-stat">
            <span className="topic-stat-label">Освоенность</span>
            <div className="topic-mastery-track"><div className={`topic-mastery-fill ${cls}`} style={{ width: `${pct}%` }} /></div>
            <span className={`topic-stat-value ${cls}`}>{pct}%</span>
          </div>
          <div className="topic-stat">
            <span className="topic-stat-label">Точность</span>
            <span className="topic-stat-value">{Math.round(accuracy * 100)}%</span>
          </div>
          <div className="topic-stat">
            <span className="topic-stat-label">Попытки</span>
            <span className="topic-stat-value">{attempts}</span>
          </div>
        </div>

        {stub ? (
          <div className="topic-placeholder">
            ИИ ещё не написал конспект по этой теме{onEnrich ? ' — нажмите «Обогатить конспект»' : ''}.
          </div>
        ) : (
          <Latex text={body} />
        )}

        {weakAreas.length > 0 ? (
          <div className="topic-weak">Слабые места: {weakAreas.join(', ')}</div>
        ) : null}

        {notes.length > 0 ? (
          <div className="topic-block">
            <h3 className="topic-block-title">Заметки ({notes.length})</h3>
            <ul className="topic-notes">
              {notes.map((n, i) => {
                const t = noteType(n)
                return (
                  <li key={`${n.date || ''}-${i}`} className={`note-card ${t}`}>
                    <button type="button" className="note-card-head" aria-expanded={openNotes.has(i)} onClick={() => toggleNote(i)}>
                      <span className="note-card-icon">{ICONS[t]}</span>
                      <span className="note-card-title">{n.feedback}</span>
                      {n.date ? <span className="note-date">{n.date}</span> : null}
                    </button>
                    {openNotes.has(i) ? (
                      <div className="note-card-body">
                        {n.question ? <div className="note-row"><span className="note-label">Вопрос:</span> {n.question}</div> : null}
                        {n.student_answer ? <div className="note-row"><span className="note-label">Ваш ответ:</span> <em>{n.student_answer}</em></div> : null}
                        {n.correct_answer ? <div className="note-row correct"><span className="note-label">Правильный ответ:</span> <strong>{n.correct_answer}</strong></div> : null}
                      </div>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          </div>
        ) : null}

        {concepts.length > 0 ? (
          <div className="topic-block">
            <h3 className="topic-block-title">Концепции</h3>
            <div className="topic-concepts">
              {concepts.map((c) => <span className="concept-chip" key={c}>{c}</span>)}
            </div>
          </div>
        ) : null}

        {prevArt || nextArt ? (
          <div className="topic-nav">
            {prevArt ? (
              <button type="button" className="topic-nav-prev" onClick={() => onNavigate && onNavigate(prevArt)}>← {prevArt.title || prevArt.topic}</button>
            ) : <span />}
            {nextArt ? (
              <button type="button" className="topic-nav-next" onClick={() => onNavigate && onNavigate(nextArt)}>{nextArt.title || nextArt.topic} →</button>
            ) : <span />}
          </div>
        ) : null}
      </div>
    </div>
  )
}