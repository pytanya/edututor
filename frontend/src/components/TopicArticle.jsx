// TopicArticle — ридер конспекта (OKF-статья): мета, статистика, markdown+LaTeX, заметки, концепции.
import Latex from './Latex'
import { masteryClass } from './MasteryWall'

export default function TopicArticle({ article, onClose = null, onEnrich = null, enriching = false }) {
  if (!article) return null
  const mastery = typeof article.mastery === 'number' ? article.mastery : 0
  const accuracy = typeof article.accuracy === 'number' ? article.accuracy : 0
  const attempts = article.attempts || 0
  const body = article.body || ''
  const notes = Array.isArray(article.notes) ? article.notes : []
  const concepts = Array.isArray(article.concepts) ? article.concepts : []
  const weakAreas = Array.isArray(article.weak_areas) ? article.weak_areas : []
  const shortBody = body.trim().length <= 20
  const pct = Math.round(mastery * 100)
  const cls = masteryClass(mastery)

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
            </div>
          </div>
          <div className="topic-actions">
            {shortBody && onEnrich ? (
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

        <Latex text={body} />

        {weakAreas.length > 0 ? (
          <div className="topic-weak">Слабые места: {weakAreas.join(', ')}</div>
        ) : null}

        {notes.length > 0 ? (
          <div className="topic-block">
            <h3 className="topic-block-title">Заметки об ошибках</h3>
            <ul className="topic-notes">
              {notes.map((n, i) => (
                <li className="note" key={`${n.date || ''}-${i}`}>
                  {n.date ? <span className="note-date">{n.date}</span> : null}
                  {n.feedback ? <span className="note-feedback">{n.feedback}</span> : null}
                  {n.student_answer ? <span className="note-answer">Ваш ответ: {n.student_answer}</span> : null}
                </li>
              ))}
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
      </div>
    </div>
  )
}
