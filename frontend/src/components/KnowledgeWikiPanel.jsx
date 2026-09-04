// KnowledgeWikiPanel — «Конспекты»: предметы/статьи из /student/{id}/wiki,
// тепловая карта мастерства (MasteryWall), ридер TopicArticle, экспорт CSV/OKF.
import { useCallback, useEffect, useState } from 'react'
import api from '../api'
import MasteryWall, { masteryClass } from './MasteryWall'
import TopicArticle from './TopicArticle'

const EMPTY_TEXT = 'Пройдите квиз по теме — конспекты появятся.'

function toPct(m) {
  return Math.round((Number(m) || 0) * 100)
}

export default function KnowledgeWikiPanel({ studentId, refreshKey = 0, subject = '', grade = '', onError = null }) {
  const [groups, setGroups] = useState([])
  const [article, setArticle] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

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

  const openArticle = async (a) => {
    if (!studentId) return
    setNote('')
    try {
      const full = await api.wikiArticle(studentId, a.subject, a.topic)
      setArticle(full)
    } catch (e) {
      fail(e)
    }
  }

  const openByTopic = (topic) => {
    for (const g of groups) {
      const a = (g.articles || []).find((x) => x.topic === topic)
      if (a) {
        openArticle({ ...a, subject: a.subject || g.subject })
        return
      }
    }
  }

  const doEnrich = async () => {
    if (!studentId || !article || busy) return
    setBusy(true)
    setNote('')
    try {
      const res = await api.enrichWiki(studentId, article.subject, article.topic)
      if (res?.note) setNote(res.note)
      if (res?.article) setArticle(res.article)
      await load()
    } catch (e) {
      fail(e)
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

  const exportSummary = async () => {
    try {
      await api.exportSummary(studentId)
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

  const cells = []
  for (const g of groups || []) {
    for (const a of g.articles || []) {
      cells.push({ ...a, subject: a.subject || g.subject })
    }
  }
  const wallTopics = cells.map((a) => ({ topic: a.topic, subject: a.subject, mastery: Number(a.mastery) || 0 }))

  return (
    <section className="panel wiki-panel">
      <div className="wiki-head">
        <h3>Конспекты</h3>
        <div className="export-row">
          <button type="button" className="btn small" disabled={!studentId} onClick={exportCsv}>⬇ CSV</button>
          <button type="button" className="btn small" disabled={!studentId} onClick={exportSummary}>⬇ Сводка</button>
          <button type="button" className="btn small" disabled={!studentId} onClick={exportOkf}>OKF</button>
        </div>
      </div>

      {note ? <div className="wiki-note">{note}</div> : null}

      {cells.length === 0 ? (
        <p className="muted">{EMPTY_TEXT}</p>
      ) : (
        <>
          <MasteryWall topics={wallTopics} onSelect={openByTopic} />
          <div className="wiki-groups">
            {(groups || []).map((g) => (
              <div className="wiki-group" key={g.subject || 'subject'}>
                <h4 className="wiki-subject">{g.subject}</h4>
                <ul className="wiki-articles">
                  {(g.articles || []).map((a) => {
                    const pct = toPct(a.mastery)
                    return (
                      <li className="wiki-article-row" key={a.topic || a.title}>
                        <button type="button" className="wiki-article-title" onClick={() => openArticle({ ...a, subject: a.subject || g.subject })}>
                          {a.title || a.topic}
                        </button>
                        <span className={`wiki-badge ${masteryClass(Number(a.mastery) || 0)}`}>{pct}%</span>
                        <span className="wiki-attempts">попыток: {a.attempts || 0}</span>
                        <button
                          type="button"
                          className="wiki-delete"
                          aria-label={`Удалить ${a.title || a.topic}`}
                          title="Удалить конспект"
                          onClick={() => removeArticle({ ...a, subject: a.subject || g.subject })}
                        >✕</button>
                      </li>
                    )
                  })}
                </ul>
              </div>
            ))}
          </div>
        </>
      )}

      {article ? (
        <TopicArticle article={article} onClose={() => setArticle(null)} onEnrich={doEnrich} enriching={busy} />
      ) : null}
    </section>
  )
}

export { EMPTY_TEXT }
