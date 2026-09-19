// StudentKGPanel — «Мои знания»: вопросы и ответы ученика + прогресс,
// сгруппированы по предметам (источник: /knowledge-graph + /records).
import { useEffect, useMemo, useRef, useState } from 'react'
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

const MAX_RECORDS = 10

export default function StudentKGPanel({
  studentId = '',
  subject = '',
  onStartReview = null,
  busy = false,
  reloadKey = 0,
  onStudy = null,
}) {
  const [kg, setKg] = useState(null)
  const [records, setRecords] = useState([])
  const [error, setError] = useState(null)
  const [dueCount, setDueCount] = useState(0)
  const [query, setQuery] = useState('')
  const [chip, setChip] = useState('')
  const [collapsedSubjects, setCollapsedSubjects] = useState({})
  const [openTopic, setOpenTopic] = useState({})
  const chipTouched = useRef(false)

  useEffect(() => {
    if (!studentId) return undefined
    let alive = true
    setKg(null)
    setError(null)
    api
      .getKnowledgeGraph(studentId)
      .then((d) => {
        if (alive) setKg(d)
      })
      .catch((e) => {
        if (alive) setError(e?.message || String(e))
      })
    return () => {
      alive = false
    }
  }, [studentId, reloadKey])

  useEffect(() => {
    if (!studentId) return undefined
    let alive = true
    Promise.all([
      api.getReview(studentId).then((d) => d?.stats?.due || 0).catch(() => 0),
      api.records(studentId).then((d) => d?.records || []).catch(() => []),
    ]).then(([due, recs]) => {
      if (alive) {
        setDueCount(due)
        setRecords(recs)
      }
    })
    return () => {
      alive = false
    }
  }, [studentId, reloadKey])

  if (!studentId) return null

  const rawTopics = useMemo(
    () =>
      Object.entries((kg && kg.topics) || {}).map(([key, t]) => ({ ...t, topic: t?.topic || key })),
    [kg],
  )

  const subjectMap = useMemo(() => {
    const m = {}
    for (const t of rawTopics) {
      const s = (t.subject || '').trim() || 'общая тема'
      if (!m[s]) m[s] = []
      m[s].push(t)
    }
    for (const k of Object.keys(m)) m[k] = sortedByStatus(m[k])
    return m
  }, [rawTopics])

  const subjectNames = Object.keys(subjectMap).sort()

  // Активный предмет по умолчанию — предмет текущей сессии (если он в знаниях).
  useEffect(() => {
    if (!chipTouched.current && subject && subjectNames.includes(subject)) {
      setChip(subject)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subject, subjectNames])

  const active = chip || (subject && subjectNames.includes(subject) ? subject : '')

  const isCollapsed = (s) =>
    collapsedSubjects[s] !== undefined ? collapsedSubjects[s] : active !== '' && s !== active

  const toggleSubject = (s) => setCollapsedSubjects((prev) => ({ ...prev, [s]: !prev[s] }))

  const recordKey = (s, t) => `${(s || '').trim().toLowerCase()}|${(t || '').trim()}`

  const recordsByKey = useMemo(() => {
    const m = {}
    for (const r of records || []) {
      const key = recordKey(r.subject, r.topic)
      if (!m[key]) m[key] = []
      m[key].push(r)
    }
    for (const k of Object.keys(m)) {
      m[k].sort((a, b) => (Number(b.ts) || 0) - (Number(a.ts) || 0))
    }
    return m
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [records])

  const toggleTopic = (s, t) =>
    setOpenTopic((prev) => ({ ...prev, [`${s}|${t}`]: !prev[`${s}|${t}`] }))

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const only = chipTouched.current ? chip : ''
    return Object.entries(subjectMap)
      .filter(([s]) => !only || s === only)
      .map(([s, topics]) => [
        s,
        q
          ? topics.filter((t) => (t.topic || '').toLowerCase().includes(q))
          : topics,
      ])
      .filter(([, topics]) => topics.length > 0)
      .sort(([a], [b]) => {
        const aActive = a === active ? 0 : 1
        const bActive = b === active ? 0 : 1
        if (aActive !== bActive) return aActive - bActive
        return a.localeCompare(b)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjectMap, query, chip, active])

  const statTopics = active ? subjectMap[active] || [] : rawTopics
  const count = (status) => statTopics.filter((t) => t.status === status).length
  const stats = {
    total: statTopics.length,
    mastered: count('mastered'),
    in_progress: count('in_progress'),
    not_studied: count('not_studied'),
  }

  const statTiles = [
    ['всего', stats.total, null],
    ['освоено', stats.mastered, statusMeta.mastered.color],
    ['в процессе', stats.in_progress, statusMeta.in_progress.color],
    ['не изучено', stats.not_studied, statusMeta.not_studied.color],
  ]

  return (
    <section className="panel kg-panel">
      <div className="kg-head">
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

          <div className="wiki-chips kg-chips">
            <button
              type="button"
              className={`wiki-chip${active === '' ? ' active' : ''}`}
              onClick={() => setChip('')}
            >
              Все · {rawTopics.length}
            </button>
            {subjectNames.map((s) => (
              <button
                type="button"
                key={s}
                className={`wiki-chip${active === s ? ' active' : ''}`}
                onClick={() => { chipTouched.current = true; setChip(s) }}
              >
                {s} · {subjectMap[s].length}
              </button>
            ))}
          </div>

          <div className="kg-search">
            <input
              type="text"
              placeholder="Поиск тем…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          {rawTopics.length === 0 ? (
            <div className="kg-empty">Тем пока нет. Пройдите квиз по теме — знания накопятся.</div>
          ) : filtered.length === 0 ? (
            <div className="kg-empty">Ничего не найдено.</div>
          ) : (
            <div className="kg-subjects">
              {filtered.map(([s, topics]) => (
                <div className="kg-subj-group" key={s}>
                  <div className="kg-subj-header" onClick={() => toggleSubject(s)}>
                    <h4 className="kg-subj-name">{s}</h4>
                    <span className="kg-subj-count">{topics.length}</span>
                    <span className={`collapsible-arrow ${isCollapsed(s) ? '' : 'open'}`}>▾</span>
                  </div>
                  {!isCollapsed(s) && (
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
                          typeof t.accuracy === 'number'
                            ? t.accuracy
                            : attempts > 0
                              ? correct / attempts
                              : 0
                        const weak = Array.isArray(t.weak_areas) ? t.weak_areas : []
                        const key = `${s}|${t.topic}`
                        const topicRecords = recordsByKey[recordKey(s, t.topic)] || []
                        const open = !!openTopic[key]
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
                              <button
                                type="button"
                                className="kg-records-toggle"
                                aria-expanded={open}
                                onClick={() => toggleTopic(s, t.topic)}
                              >
                                {open ? '▾' : '▸'} Вопросы и ответы ({topicRecords.length})
                              </button>
                              {open && (
                                <div className="kg-records">
                                  {topicRecords.length === 0 ? (
                                    <div className="kg-record-empty">По этой теме записей пока нет.</div>
                                  ) : (
                                    topicRecords.slice(0, MAX_RECORDS).map((r) => (
                                      <div className="kg-record" key={r.record_id || r.question_id || `${r.ts}-${r.question}`}>
                                        <div className="kg-record-q">{r.question || '—'}</div>
                                        <div className={`kg-record-a ${r.correct ? 'correct' : 'wrong'}`}>
                                          Ваш ответ: {r.student_answer || '—'} {r.correct ? '· верно' : '· неверно'}
                                        </div>
                                        {!r.correct && r.feedback ? (
                                          <div className="kg-record-fb">{r.feedback}</div>
                                        ) : null}
                                      </div>
                                    ))
                                  )}
                                </div>
                              )}
                            </div>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  )
}