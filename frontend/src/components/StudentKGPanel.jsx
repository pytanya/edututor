// StudentKGPanel — «Мои знания»: статистика и список тем ученика из /knowledge-graph.
import { useEffect, useState } from 'react'
import api from '../api'

export const statusMeta = {
  in_progress: { order: 0, label: 'В процессе', color: '#fbbf24' },
  not_studied: { order: 1, label: 'Не изучалось', color: '#9ca3af' },
  mastered: { order: 2, label: 'Освоено', color: '#4ade80' },
}

export const masteryColor = (m) => {
  if (m >= 0.75) return 'high'
  if (m >= 0.45) return 'mid'
  return 'low'
}

export function sortedByStatus(topics) {
  const rank = (s) => (statusMeta[s] ? statusMeta[s].order : 1)
  const lastSeen = (t) =>
    typeof t.last_seen === 'number' ? t.last_seen : Date.parse(t.last_seen || '') || 0
  return [...topics].sort((a, b) => {
    const d = rank(a.status) - rank(b.status)
    if (d !== 0) return d
    return lastSeen(b) - lastSeen(a)
  })
}

export default function StudentKGPanel({
  studentId = '',
  subject = '',
  onStartReview = null,
  busy = false,
  reloadKey = 0,
  onStudy = null,
}) {
  const [kg, setKg] = useState(null)
  const [error, setError] = useState(null)
  const [dueCount, setDueCount] = useState(0)

  useEffect(() => {
    if (!studentId) return undefined
    let alive = true
    setKg(null)
    setError(null)
    api
      .getKnowledgeGraph(studentId, subject)
      .then((d) => {
        if (alive) setKg(d)
      })
      .catch((e) => {
        if (alive) setError(e?.message || String(e))
      })
    return () => {
      alive = false
    }
  }, [studentId, subject, reloadKey])

  useEffect(() => {
    if (!studentId) return undefined
    let alive = true
    api
      .getReview(studentId)
      .then((d) => {
        if (alive) setDueCount(d?.stats?.due || 0)
      })
      .catch(() => {
        if (alive) setDueCount(0)
      })
    return () => {
      alive = false
    }
  }, [studentId, reloadKey])

  if (!studentId) return null

  const topics = sortedByStatus(
    Object.entries((kg && kg.topics) || {}).map(([key, t]) => ({ ...t, topic: t?.topic || key })),
  )
  const stats = (kg && kg.stats) || {}

  const statTiles = [
    ['всего', stats.total || 0, null],
    ['освоено', stats.mastered || 0, statusMeta.mastered.color],
    ['в процессе', stats.in_progress || 0, statusMeta.in_progress.color],
    ['не изучено', stats.not_studied || 0, statusMeta.not_studied.color],
  ]

  return (
    <section className="panel kg-panel">
      <div className="kg-head">
        <h3>Мои знания</h3>
        {subject && <span className="kg-subject">· {subject}</span>}
        {onStartReview && dueCount > 0 && (
          <button type="button" className="btn review kg-review" disabled={busy} onClick={onStartReview}>
            Повторить ({dueCount})
          </button>
        )}
      </div>

      {error ? (
        <div className="kg-error">{error}</div>
      ) : !kg ? (
        <div className="kg-loading">Загрузка…</div>
      ) : (
        <>
          <div className="kg-stats">
            {statTiles.map(([label, value, color]) => (
              <div key={label} className="kg-stat">
                <span className="kg-stat-value" style={color ? { color } : undefined}>{value}</span>
                <span className="kg-stat-label">{label}</span>
              </div>
            ))}
          </div>

          {topics.length === 0 ? (
            <div className="kg-empty">Тем пока нет. Пройдите квиз по теме — знания накопятся.</div>
          ) : (
            <ul className="kg-list">
              {topics.map((t) => {
                const meta = statusMeta[t.status] || {
                  order: 1,
                  label: t.status || '—',
                  color: statusMeta.not_studied.color,
                }
                const attempts = t.attempts || 0
                const correct = t.correct || 0
                const accuracy =
                  typeof t.accuracy === 'number' ? t.accuracy : attempts > 0 ? correct / attempts : 0
                const weak = Array.isArray(t.weak_areas) ? t.weak_areas : []
                return (
                  <li key={t.topic} className={`kg-topic kg-status-${t.status || 'not_studied'}`}>
                    <span className="kg-status-dot" style={{ background: meta.color }} />
                    <div className="kg-topic-main">
                      <div className="kg-topic-title-row">
                        {onStudy ? (
                          <button
                            type="button"
                            className="kg-topic-name kg-study"
                            onClick={() => onStudy(t.topic)}
                          >
                            {t.topic}
                          </button>
                        ) : (
                          <span className="kg-topic-name">{t.topic}</span>
                        )}
                        <span className="kg-topic-status">{meta.label}</span>
                      </div>
                      {(attempts > 0 || weak.length > 0) && (
                        <div className="kg-topic-meta">
                          {attempts > 0 && (
                            <span>
                              {correct}/{attempts} · {Math.round(accuracy * 100)}%
                            </span>
                          )}
                          {weak.length > 0 && (
                            <span className="kg-weak">
                              слабые: {weak.slice(0, 2).join(', ')}
                              {weak.length > 2 ? '…' : ''}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </>
      )}
    </section>
  )
}
