// TopicArticle — ридер конспекта: текст урока, статистика, слабые места,
// концепции, навигация по темам предмета. Рендерится через Portal в body
// для корректного отображения поверх всего UI.
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import Latex from './Latex'
import NoteItem from './NoteItem'
import { masteryClass } from './MasteryWall'

export function isStubBody(body) {
  const b = (body || '').trim()
  return !b || b.includes('накапливается по мере прохождения квизов')
}

export default function TopicArticle({ article, onClose = null, onEnrich = null, enriching = false, enrichNote = '', siblings = [], topicIndex = -1, onNavigate = null, onRestudy = null }) {
  if (!article) return null
  const mastery = typeof article.mastery === 'number' ? article.mastery : 0
  const accuracy = typeof article.accuracy === 'number' ? article.accuracy : 0
  const attempts = article.attempts || 0
  const body = article.body || ''
  const concepts = Array.isArray(article.concepts) ? article.concepts : []
  const weakAreas = Array.isArray(article.weak_areas) ? article.weak_areas : []
  const notes = Array.isArray(article.notes) ? article.notes : []
  const stub = isStubBody(body)
  const pct = Math.round(mastery * 100)
  const cls = masteryClass(mastery)

  const prevArt = topicIndex > 0 ? siblings[topicIndex - 1] : null
  const nextArt = topicIndex >= 0 && topicIndex < siblings.length - 1 ? siblings[topicIndex + 1] : null

  // Закрытие по Escape
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape' && onClose) onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  // Блокировка прокрутки body при открытом модале
  useEffect(() => {
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = '' }
  }, [])

  const handleBackdropClick = (e) => {
    if (e.target === e.currentTarget && onClose) onClose()
  }

  const modal = (
    <div className="topic-overlay" role="dialog" aria-modal="true" aria-label={article.title} onClick={handleBackdropClick}>
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
            {onRestudy ? (
              <button type="button" className="btn small" onClick={() => onRestudy(article.topic)}>
                Учить заново →
              </button>
            ) : null}
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

        {enrichNote ? <div className="topic-enrich-note">{enrichNote}</div> : null}

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
            <div className="topic-notes">
              {notes.map((n, i) => <NoteItem key={`note-${i}`} note={n} />)}
            </div>
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

  return createPortal(modal, document.body)
}