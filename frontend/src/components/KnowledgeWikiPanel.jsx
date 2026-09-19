// KnowledgeWikiPanel — «Конспекты»: stats-бар, чипы-фильтры по предметам,
// карточки статей с прогресс-баром, ридер TopicArticle, экспорт CSV/OKF.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import api from '../api'
import { masteryClass } from './MasteryWall'
import TopicArticle from './TopicArticle'

const EMPTY_TEXT = 'Пройдите квиз по теме — конспекты появятся.'

function toPct(m) {
  return Math.round((Number(m) || 0) * 100)
}

function shortDate(iso) {
  return iso ? String(iso).slice(0, 10) : ''
}

export default function KnowledgeWikiPanel({ studentId, refreshKey = 0, subject = '', grade = '', onError = null }) {
  const [groups, setGroups] = useState([])
  const [article, setArticle] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [query, setQuery] = useState('')
  const [chip, setChip] = useState('')
  const [collapsedSubjects, setCollapsedSubjects] = useState({})
  const [enrichNote, setEnrichNote] = useState('')
  const chipTouched = useRef(false)

  const load = useCallback(async () => {
    if (!studentId) return
    try {
      const data = await api.wiki(studentId)
      setGroups(data?.subjects || [])
    } catch {
      setGroups([])
    }
  }, [studentId])

  useEffect(() => {
    load()
  }, [load, refreshKey])

  const fail = (e) => onError?.(e?.message || String(e))

  const subjects = useMemo(() => (groups || []).map((g) => g.subject || '').filter(Boolean), [groups])

  const subjectCount = (s) =>
    (groups.find((g) => (g.subject || '') === s)?.articles || []).length

  // Активный предмет по умолчанию — предмет текущей сессии (если он есть в конспектах).
  useEffect(() => {
    if (!chipTouched.current && subject && subjects.includes(subject)) {
      setChip(subject)
    }
  }, [subject, subjects])

  const active = chip || (subject && subjects.includes(subject) ? subject : '')

  const toggleSubject = (subj) => {
    setCollapsedSubjects((prev) => ({ ...prev, [subj]: !prev[subj] }))
  }

  // По умолчанию раскрыт только активный предмет; «Все» — все раскрыты.
  const isCollapsed = (subj) =>
    collapsedSubjects[subj] !== undefined ? collapsedSubjects[subj] : active !== '' && subj !== active

  const openArticle = async (a) => {
    if (!studentId) return
    setNote('')
    setEnrichNote('')
    try {
      const full = await api.wikiArticle(studentId, a.subject, a.topic)
      setArticle(full)
    } catch (e) {
      fail(e)
    }
  }

  const doEnrich = async () => {
    if (!studentId || !article || busy) return
    setBusy(true)
    setEnrichNote('')
    try {
      const res = await api.enrichWiki(studentId, article.subject, article.topic)
      if (res?.article) setArticle(res.article)
      setEnrichNote(res?.note || (res?.article ? 'Конспект обогащён.' : 'Не удалось обогатить конспект.'))
      await load()
    } catch (e) {
      setEnrichNote(e?.message || String(e))
    } finally {
      setBusy(false)
    }
  }

  const removeArticle = async (a) => {
    if (!studentId) return
    try {
      await api.deleteWiki(studentId, a.subject, a.topic)
      setArticle((cur) =>
        cur && cur.subject === a.subject && cur.topic === a.topic ? null : cur,
      )
      setNote('')
      await load()
    } catch (e) {
      fail(e)
    }
  }

  const exportCsv = async () => {
    try {
      await api.exportCsv(studentId)
    } catch (e) {
      fail(e)
    }
  }

  const exportOkf = async () => {
    try {
      const res = await api.exportOkf(studentId, subject, grade)
      const files = Array.isArray(res?.files) ? res.files.length : 0
      const status = res?.conformant ? 'соответствует OKF' : 'есть ошибки валидации'
      setNote(`OKF: ${files} файлов · ${status}${res?.dir ? ` · ${res.dir}` : ''}`)
    } catch (e) {
      setNote(`OKF: ${e?.message || String(e)}`)
    }
  }

  if (!studentId) return null

  const totalArticles = (groups || []).reduce((n, g) => n + (g.articles || []).length, 0)

  const filteredGroups = useMemo(() => {
    let list = (groups || []).map((g) => ({
      ...g,
      articles: [...(g.articles || [])].sort((a, b) =>
        String(b.last_studied || '').localeCompare(String(a.last_studied || '')),
      ),
    }))
    const q = query.trim().toLowerCase()
    if (q) {
      list = list
        .map((g) => ({
          ...g,
          articles: (g.articles || []).filter(
            (a) =>
              (a.topic || a.title || '').toLowerCase().includes(q) ||
              (a.subject || g.subject || '').toLowerCase().includes(q) ||
              (a.concepts || []).some((c) => String(c).toLowerCase().includes(q)),
          ),
        }))
        .filter((g) => g.articles.length > 0)
    }
    if (chipTouched.current && chip) {
      list = list.filter((g) => (g.subject || '') === chip)
    }
    return [...list].sort((a, b) => {
      const aActive = (a.subject || '') === active ? 0 : 1
      const bActive = (b.subject || '') === active ? 0 : 1
      if (aActive !== bActive) return aActive - bActive
      return (a.subject || '').localeCompare(b.subject || '')
    })
  }, [groups, query, active])

  const stats = useMemo(() => {
    let attempts = 0
    let correct = 0
    let topics = 0
    for (const g of filteredGroups) {
      for (const a of g.articles) {
        topics += 1
        attempts += a.attempts || 0
        correct += a.correct || 0
      }
    }
    const avgMastery = topics
      ? filteredGroups.reduce(
          (s, g) => s + g.articles.reduce((ss, a) => ss + (a.mastery || 0), 0),
          0,
        ) / topics
      : 0
    return { topics, attempts, avgMastery, accuracy: attempts ? correct / attempts : 0 }
  }, [filteredGroups])

  const siblings = article
    ? ((groups || []).find((g) => (g.subject || '') === (article.subject || ''))?.articles || [])
        .map((a) => ({ subject: a.subject || article.subject, topic: a.topic }))
    : []
  const curIndex = article ? siblings.findIndex((s) => s.topic === article.topic) : -1

  return (
    <section className="panel wiki-panel">
      <div className="wiki-head">
        <h3>Конспекты</h3>
        <div className="export-row">
          <button type="button" className="btn small" disabled={!studentId} onClick={exportCsv}>⬇ Журнал (CSV)</button>
          <button type="button" className="btn small" disabled={!studentId} onClick={exportOkf}>OKF</button>
        </div>
      </div>

      {note ? <div className="wiki-note">{note}</div> : null}

      {totalArticles === 0 ? (
        <p className="muted">{EMPTY_TEXT}</p>
      ) : (
        <>
          <div className="wiki-stats">
            <div className="wiki-stat"><b>{stats.topics}</b><span>тем</span></div>
            <div className="wiki-stat"><b>{stats.attempts}</b><span>попыток</span></div>
            <div className="wiki-stat"><b>{Math.round(stats.avgMastery * 100)}%</b><span>ср.мастерство</span></div>
            <div className="wiki-stat"><b>{Math.round(stats.accuracy * 100)}%</b><span>точность</span></div>
          </div>

          <div className="wiki-chips">
            <button type="button" className={`wiki-chip${active === '' ? ' active' : ''}`} onClick={() => setChip('')}>Все · {totalArticles}</button>
            {subjects.map((s) => (
              <button type="button" key={s} className={`wiki-chip${active === s ? ' active' : ''}`} onClick={() => { chipTouched.current = true; setChip(s) }}>{s} · {subjectCount(s)}</button>
            ))}
          </div>

          <div className="wiki-search">
            <input
              type="text"
              placeholder="Поиск по конспектам…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          <div className="wiki-groups">
            {(filteredGroups || []).map((g) => (
              <div className="wiki-group" key={g.subject || 'subject'}>
                <div className={`wiki-group-header${g.subject === active ? ' current' : ''}`} onClick={() => toggleSubject(g.subject || '')}>
                  <h4 className="wiki-subject">{g.subject}</h4>
                  <span className="wiki-count">{(g.articles || []).length}</span>
                  <span className={`collapsible-arrow ${isCollapsed(g.subject || '') ? '' : 'open'}`}>▾</span>
                </div>
                {!isCollapsed(g.subject || '') && (
                  <ul className="wiki-articles">
                    {(g.articles || []).map((a) => {
                      const pct = toPct(a.mastery)
                      const cls = masteryClass(Number(a.mastery) || 0)
                      return (
                        <li className="wiki-card" key={a.topic || a.title}>
                          <div className="wiki-card-row">
                            <span className={`wiki-dot ${cls}`} />
                            <button type="button" className="wiki-article-title" onClick={() => openArticle({ ...a, subject: a.subject || g.subject })}>
                              {a.title || a.topic}
                            </button>
                            <span className="wiki-pct">{pct}%</span>
                          </div>
                          <div className="wiki-bar"><div className={`wiki-bar-fill ${cls}`} style={{ width: `${pct}%` }} /></div>
                          <div className="wiki-card-foot">
                            <span className="wiki-attempts">
                              попыток: {a.attempts || 0}{a.last_studied ? ` · ${shortDate(a.last_studied)}` : ''}
                            </span>
                            <button type="button" className="wiki-delete" aria-label={`Удалить ${a.title || a.topic}`} title="Удалить конспект" onClick={() => removeArticle({ ...a, subject: a.subject || g.subject })}>✕</button>
                          </div>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {article ? (
        <TopicArticle
          article={article}
          onClose={() => setArticle(null)}
          onEnrich={doEnrich}
          enriching={busy}
          enrichNote={enrichNote}
          siblings={siblings}
          topicIndex={curIndex}
          onNavigate={openArticle}
        />
      ) : null}
    </section>
  )
}

export { EMPTY_TEXT }