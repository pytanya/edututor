// StudentKGPanel — «Мои знания»: статусы тем, прогресс-бары мастерства,
// вопросы и ответы, конспекты в модале, экспорт CSV/OKF.
// Объединяет бывшие панели «Мои знания» и «Конспекты».
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import api from '../api'
import { masteryClass } from './MasteryWall'
import TopicArticle from './TopicArticle'

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

function toPct(m) {
  return Math.round((Number(m) || 0) * 100)
}

export default function StudentKGPanel({
  studentId = '',
  subject = '',
  grade = '',
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
  const [article, setArticle] = useState(null)
  const [enriching, setEnriching] = useState(false)
  const [enrichNote, setEnrichNote] = useState('')
  const [wikiGroups, setWikiGroups] = useState([])
  const [exportNote, setExportNote] = useState('')
  const chipTouched = useRef(false)

  // ─── Загрузка knowledge-graph ───
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

  // ─── Загрузка review stats + records ───
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

  // ─── Загрузка wiki (для мастерства и конспектов) ───
  const loadWiki = useCallback(async () => {
    if (!studentId) return
    try {
      const data = await api.wiki(studentId)
      setWikiGroups(data?.subjects || [])
    } catch {
      setWikiGroups([])
    }
  }, [studentId])

  useEffect(() => {
    loadWiki()
  }, [loadWiki, reloadKey])

  if (!studentId) return null

  const rawTopics = useMemo(
    () =>
      Object.entries((kg && kg.topics) || {}).map(([key, t]) => ({ ...t, topic: t?.topic || key })),
    [kg],
  )

  // ─── Объединяем данные KG и Wiki для каждой темы ───
  const wikiByKey = useMemo(() => {
    const m = {}
    for (const g of wikiGroups || []) {
      for (const a of g.articles || []) {
        const key = `${(a.subject || g.subject || '').trim().toLowerCase()}|${(a.topic || '').trim()}`
        m[key] = a
      }
    }
    return m
  }, [wikiGroups])

  const subjectMap = useMemo(() => {
    const m = {}
    for (const t of rawTopics) {
      const s = (t.subject || '').trim() || 'общая тема'
      if (!m[s]) m[s] = []
      // Мержим данные wiki
      const wKey = `${s.toLowerCase()}|${(t.topic || '').trim()}`
      const wiki = wikiByKey[wKey]
      m[s].push({
        ...t,
        mastery: wiki?.mastery ?? t.mastery ?? 0,
        attempts: wiki?.attempts ?? t.attempts ?? 0,
        correct: wiki?.correct ?? t.correct ?? 0,
        accuracy: wiki?.accuracy ?? t.accuracy,
        last_studied: wiki?.last_studied,
        hasArticle: !!wiki,
      })
    }
    for (const k of Object.keys(m)) m[k] = sortedByStatus(m[k])
    return m
  }, [rawTopics, wikiByKey])

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

  // ─── Открытие конспекта ───
  const openArticle = async (t, subjectName) => {
    const s = t.subject || subjectName || ''
    setEnrichNote('')
    try {
      const full = await api.wikiArticle(studentId, s, t.topic)
      setArticle(full)
    } catch {
      // Если wiki-статья не найдена — показываем заглушку
      setArticle({ title: t.topic, topic: t.topic, subject: s, body: '', mastery: t.mastery || 0, accuracy: t.accuracy || 0, attempts: t.attempts || 0 })
    }
  }

  const doEnrich = async () => {
    if (!studentId || !article || enriching) return
    setEnriching(true)
    setEnrichNote('')
    try {
      const res = await api.enrichWiki(studentId, article.subject, article.topic)
      if (res?.article) setArticle(res.article)
      setEnrichNote(res?.note || (res?.article ? 'Конспект обогащён.' : 'Не удалось обогатить конспект.'))
      await loadWiki()
    } catch (e) {
      setEnrichNote(e?.message || String(e))
    } finally {
      setEnriching(false)
    }
  }

  // ─── Клик по теме ───
  const handleTopicClick = (t, subjectName) => {
    if (t.status === 'not_studied') {
      // Неизученная — запускаем новый урок
      if (onStudy) onStudy(t.topic)
    } else {
      // Пройденная/в_процессе — открываем конспект
      openArticle(t, subjectName)
    }
  }

  // ─── Учить заново (из модала конспекта) ───
  const handleRestudy = (topic) => {
    setArticle(null)
    if (onStudy) onStudy(topic)
  }

  // ─── Экспорт ───
  const exportCsv = async () => {
    try {
      await api.exportCsv(studentId)
    } catch (e) {
      setExportNote(e?.message || String(e))
    }
  }

  const exportOkf = async () => {
    try {
      const res = await api.exportOkf(studentId, subject, grade)
      const files = Array.isArray(res?.files) ? res.files.length : 0
      const status = res?.conformant ? 'соответствует OKF' : 'есть ошибки валидации'
      setExportNote(`OKF: ${files} файлов · ${status}${res?.dir ? ` · ${res.dir}` : ''}`)
    } catch (e) {
      setExportNote(`OKF: ${e?.message || String(e)}`)
    }
  }

  // ─── Siblings для навигации в модале ───
  const siblings = article
    ? Object.values(subjectMap)
        .flat()
        .filter((t) => (t.subject || '') === (article.subject || ''))
        .map((t) => ({ subject: t.subject || article.subject, topic: t.topic }))
    : []
  const curIndex = article ? siblings.findIndex((s) => s.topic === article.topic) : -1

  return (
    <section className="panel kg-panel">
      <div className="kg-head">
        {subject && <span className="kg-subject">· {subject}</span>}
        <div className="kg-head-actions">
          {onStartReview && dueCount > 0 && (
            <button type="button" className="btn review kg-review" disabled={busy} onClick={onStartReview}>
              Повторить ({dueCount})
            </button>
          )}
        </div>
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
                        const mastery = Number(t.mastery) || 0
                        const pct = toPct(mastery)
                        const cls = masteryClass(mastery)
                        const weak = Array.isArray(t.weak_areas) ? t.weak_areas : []
                        const key = `${s}|${t.topic}`
                        const topicRecords = recordsByKey[recordKey(s, t.topic)] || []
                        const open = !!openTopic[key]
                        return (
                          <li key={t.topic} className={`kg-topic kg-status-${t.status || 'not_studied'}`}>
                            <span className="kg-status-dot" style={{ background: meta.color }} />
                            <div className="kg-topic-main">
                              <div className="kg-topic-title-row">
                                <button
                                  type="button"
                                  className="kg-topic-name kg-study"
                                  onClick={() => handleTopicClick(t, s)}
                                  title={t.status === 'not_studied' ? 'Начать урок' : 'Открыть конспект'}
                                >
                                  {t.topic}
                                </button>
                                <span className="kg-topic-status">{meta.label}</span>
                              </div>

                              {/* Мини-прогресс мастерства */}
                              {attempts > 0 && (
                                <div className="kg-mastery-row">
                                  <div className="kg-mastery-mini-track">
                                    <div className={`kg-mastery-mini-fill ${cls}`} style={{ width: `${pct}%` }} />
                                  </div>
                                  <span className="kg-mastery-pct">{pct}%</span>
                                  <span className="kg-topic-attempts">{correct}/{attempts} · {Math.round(accuracy * 100)}%</span>
                                </div>
                              )}

                              {weak.length > 0 && (
                                <div className="kg-topic-meta">
                                  <span className="kg-weak">
                                    слабые: {weak.slice(0, 2).join(', ')}
                                    {weak.length > 2 ? '…' : ''}
                                  </span>
                                </div>
                              )}

                              {topicRecords.length > 0 && (
                                <button
                                  type="button"
                                  className="kg-records-toggle"
                                  aria-expanded={open}
                                  onClick={() => toggleTopic(s, t.topic)}
                                >
                                  {open ? '▾' : '▸'} Вопросы ({topicRecords.length})
                                </button>
                              )}
                              {open && (
                                <div className="kg-records">
                                  {topicRecords.slice(0, MAX_RECORDS).map((r) => (
                                    <div className="kg-record" key={r.record_id || r.question_id || `${r.ts}-${r.question}`}>
                                      <div className="kg-record-q">{r.question || '—'}</div>
                                      <div className={`kg-record-a ${r.correct ? 'correct' : 'wrong'}`}>
                                        Ваш ответ: {r.student_answer || '—'} {r.correct ? '· верно' : '· неверно'}
                                      </div>
                                      {!r.correct && r.feedback ? (
                                        <div className="kg-record-fb">{r.feedback}</div>
                                      ) : null}
                                    </div>
                                  ))}
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

          {/* Экспорт (перенесено из КонспектWikiPanel) */}
          <div className="kg-export">
            <button type="button" className="btn small" disabled={!studentId} onClick={exportCsv}>⬇ Журнал (CSV)</button>
            <button type="button" className="btn small" disabled={!studentId} onClick={exportOkf}>OKF</button>
          </div>
          {exportNote ? <div className="kg-export-note">{exportNote}</div> : null}
        </>
      )}

      {/* Модал конспекта */}
      {article ? (
        <TopicArticle
          article={article}
          onClose={() => setArticle(null)}
          onEnrich={doEnrich}
          enriching={enriching}
          enrichNote={enrichNote}
          siblings={siblings}
          topicIndex={curIndex}
          onNavigate={(a) => openArticle(a, a.subject)}
          onRestudy={handleRestudy}
        />
      ) : null}
    </section>
  )
}