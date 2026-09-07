import { useCallback, useEffect, useRef, useState } from 'react'
import api from './api'
import {
  getStudentId, getSessionId, setSessionId, clearSession,
  loadStudentRecord, saveStudentRecord, resolveStudentId,
} from './identity'
import Chat, { feedReducer } from './components/Chat'
import AdaptivePanel from './components/AdaptivePanel'
import KnowledgeWikiPanel from './components/KnowledgeWikiPanel'
import StudentKGPanel from './components/StudentKGPanel'
import KnowledgeGraphPanel from './components/KnowledgeGraphPanel'
import TopicForm from './components/TopicForm'
import IntakeCard from './components/IntakeCard'
import SessionList from './components/SessionList'
import CollapsiblePanel from './components/CollapsiblePanel'

const EMPTY_GRAPH = { nodes: [], edges: [], activeTopic: null }

// Восстанавливает envelope агентского сообщения из сохранённой истории.
// Для «старых» сессий конверт может не храниться отдельно, а content содержит
// сырой JSON-конверт — распознаём и разбираем его (секретные поля payload
// (_..., correct_answer) при этом выбрасываем, чтобы не просочились в DOM).
function restoreEnvelope(m) {
  if (m.envelope && typeof m.envelope === 'object') return m.envelope
  const content = m.content
  if (!content || typeof content !== 'string') return null
  const t = content.trim()
  if (!t.startsWith('{') || !/"type"\s*:/.test(t)) return null
  try {
    const parsed = JSON.parse(t)
    if (!parsed || typeof parsed !== 'object' || !parsed.type) return null
    if (parsed.payload && typeof parsed.payload === 'object') {
      parsed.payload = Object.fromEntries(
        Object.entries(parsed.payload).filter(([k]) => !k.startsWith('_') && k !== 'correct_answer'),
      )
    }
    return parsed
  } catch {
    return null
  }
}

function profileToCard(rec) {
  return {
    fields: [
      { key: 'name', value: rec?.student_name || '' },
      { key: 'learner_type', value: rec?.learner_type || '' },
      { key: 'grade', value: rec?.grade || '' },
    ],
  }
}

export default function App() {
  const [feed, setFeed] = useState({ items: [], lastStep: null, adaptive: null, error: null })
  const [busy, setBusy] = useState(false)
  const [sessions, setSessions] = useState([])
  const [current, setCurrent] = useState(null) // {session_id, topic, subject, grade}
  const [graph, setGraph] = useState(EMPTY_GRAPH)
  const [kgReloadKey, setKgReloadKey] = useState(0)
  const [wikiVersion, setWikiVersion] = useState(0)
  const studentIdRef = useRef(getStudentId())
  const [studentId, setStudentIdState] = useState(studentIdRef.current)
  const applyStudentId = useCallback((id) => {
    studentIdRef.current = id
    setStudentIdState(id)
  }, [])
  const [profile, setProfile] = useState(() => loadStudentRecord())
  const [intakeSkipped, setIntakeSkipped] = useState(false)
  const intakeRequired = !profile?.student_name && !intakeSkipped
  const abortRef = useRef(null)
  const graphPollRef = useRef(null)
  const graphPollTriesRef = useRef(0)
  const prevBusyRef = useRef(false)

  const stopGraphPoll = useCallback(() => {
    if (graphPollRef.current) clearTimeout(graphPollRef.current)
    graphPollRef.current = null
    graphPollTriesRef.current = 0
  }, [])

  // Граф источника собирается фоном (LLM) и «доезжает» после закрытия SSE-потока
  // первого хода. Доспрашиваем /graph, пока узлы не появятся — иначе «Созвездие»
  // останется пустым до следующего сообщения ученика.
  const pollGraphUntilReady = useCallback(() => {
    const subj = current?.subject || ''
    const grd = current?.grade || ''
    if (!subj && !grd) return
    const attempt = async () => {
      try {
        const d = await api.getGraph(studentIdRef.current, subj, grd)
        if ((d?.nodes || []).length > 0) {
          setGraph({ nodes: d.nodes || [], edges: d.edges || [], activeTopic: current?.topic || null })
          stopGraphPoll()
          return
        }
      } catch {
        /* сеть/бэкенд — пробуем ещё раз ниже */
      }
      graphPollTriesRef.current += 1
      if (graphPollTriesRef.current < 12 && current?.session_id) {
        graphPollRef.current = setTimeout(attempt, 5000)
      } else {
        stopGraphPoll()
      }
    }
    stopGraphPoll()
    attempt()
  }, [current, stopGraphPoll])

  const refreshStudent = useCallback(async () => {
    try {
      const data = await api.student(studentIdRef.current)
      setFeed((f) => ({ ...f, adaptive: { ...f.adaptive, student_id: data.student_id, recommended_next: data.recommended_next } }))
    } catch {
      /* студент ещё не создан — ок */
    }
  }, [])

  const loadSessions = useCallback(async () => {
    try {
      const data = await api.studentSessions(studentIdRef.current)
      const list = (data.sessions || []).map((s) => ({
        session_id: s.session_id,
        topic: s.topic || '',
        subject: '',
        grade: '',
      }))
      setSessions(list)
      if (list.length > 0) {
        const sid = data.student_id || studentIdRef.current
        setFeed((f) => ({ ...f, adaptive: { ...f.adaptive, student_id: sid } }))
      }
    } catch {
      /* бэкенд недоступен / студент неизвестен — список остаётся пустым */
    }
  }, [])

  const loadHistory = useCallback(async (session) => {
    try {
      const data = await api.history(session.session_id)
      const items = (data.messages || []).map((m, i) => {
        if (m.role === 'user') return { id: `h${i}`, kind: 'user', content: m.content }
        const env = restoreEnvelope(m)
        return {
          id: `h${i}`,
          kind: 'agent',
          envelope: env,
          content: env && typeof env.text === 'string' ? env.text : m.content,
        }
      })
      setFeed({ items, lastStep: null, adaptive: session.adaptive || null, error: null })
      setCurrent(session)
      setSessionId(session.session_id)
    } catch {
      clearSession()
      setFeed({ items: [], lastStep: null, adaptive: null, error: null })
      setCurrent({ ...session, session_id: '', topic: session.topic })
    }
  }, [])

  const reloadKg = useCallback(() => setKgReloadKey((k) => k + 1), [])
  const loadWiki = useCallback(() => setWikiVersion((v) => v + 1), [])

  const refreshGraph = useCallback(async ({ subject, grade, topic } = {}) => {
    const subj = subject ?? current?.subject ?? ''
    const grd = grade ?? current?.grade ?? ''
    if (!subj && !grd) {
      setGraph(EMPTY_GRAPH)
      return
    }
    try {
      const d = await api.getGraph(studentIdRef.current, subj, grd)
      setGraph({
        nodes: d?.nodes || [],
        edges: d?.edges || [],
        activeTopic: topic ?? current?.topic ?? null,
      })
    } catch {
      /* граф недоступен — «Созвездие» остаётся пустой */
    }
  }, [current])

  const runTurn = useCallback(async (message, kind, meta = {}) => {
    setBusy(true)
    setFeed((f) => ({ ...f, error: null, items: message ? [...f.items, { id: `u${Date.now()}`, kind: 'user', content: message }] : f.items }))
    const sessionId = meta.session_id !== undefined ? meta.session_id : current?.session_id || getSessionId()
    const body = {
      message,
      kind: kind || 'message',
      session_id: sessionId,
      student_id: studentIdRef.current,
      topic: meta.topic || current?.topic || '',
      subject: meta.subject || current?.subject || '',
      grade: meta.grade || current?.grade || '',
    }
    abortRef.current = api.chatStream(body, (ev) => {
      if (ev.event === 'message') {
        setFeed((f) => feedReducer(f, ev))
        setCurrent((c) => ({ ...c, session_id: ev.data.session_id || c?.session_id }))
        if (ev.data.session_id) setSessionId(ev.data.session_id)
      } else if (ev.event === 'done') {
        setBusy(false)
        refreshStudent()
        loadSessions()
        reloadKg()
        loadWiki()
        refreshGraph({ subject: body.subject, grade: body.grade, topic: body.topic })
      } else if (ev.event === 'graph.ready') {
        setFeed((f) => feedReducer(f, ev))
        refreshGraph({ subject: body.subject, grade: body.grade, topic: body.topic })
        reloadKg()
      } else {
        setFeed((f) => feedReducer(f, ev))
      }
    })
  }, [current, refreshStudent, loadSessions, reloadKg, loadWiki, refreshGraph])

  const startTopic = useCallback((meta) => {
    clearSession()
    const session = { session_id: '', topic: meta.topic, subject: meta.subject, grade: meta.grade }
    setCurrent(session)
    setFeed({ items: [], lastStep: null, adaptive: null, error: null })
    runTurn(`Изучаем тему: ${meta.topic}. Объясни её и предложи задание.`, 'message', { ...meta, session_id: '' })
  }, [runTurn])

  const studyNext = useCallback((topic) => {
    clearSession()
    const session = { session_id: '', topic, subject: current?.subject || '', grade: current?.grade || '' }
    setCurrent(session)
    setFeed({ items: [], lastStep: null, adaptive: null, error: null })
    runTurn(`Расскажи про ${topic}`, 'message', { topic, subject: session.subject, grade: session.grade, session_id: '' })
  }, [runTurn, current])

  const startReview = useCallback(() => {
    runTurn('', 'review_request')
  }, [runTurn])

  const handleIntake = useCallback((values) => {
    const name = String(values.name || '').trim()
    const type = String(values.learner_type || '')
    const grade = String(values.grade || '').trim()
    const stored = loadStudentRecord()
    const { studentId: sid, identity, legacy } = resolveStudentId(
      name, type, grade, stored, profileToCard(profile),
    )
    const record = {
      student_id: sid,
      student_name: name,
      learner_type: type,
      grade,
      identity,
      legacy: legacy === true,
    }
    saveStudentRecord(record)
    setProfile(record)
    applyStudentId(sid)
    clearSession()
    api.profile(sid, { name, learner_type: type, grade }).catch(() => {
      /* fail-soft: серверная запись повторится при следующем запуске */
    })
    startTopic({ subject: String(values.subject || '').trim(), grade, topic: String(values.topic || '').trim() })
  }, [profile, startTopic, applyStudentId])

  const skipIntake = useCallback(() => {
    setIntakeSkipped(true)
  }, [])

  useEffect(() => {
    const rec = loadStudentRecord()
    if (rec?.student_name && rec?.student_id) {
      api.profile(rec.student_id, {
        name: rec.student_name,
        learner_type: rec.learner_type || '',
        grade: rec.grade || '',
      }).catch(() => { /* fail-soft: повторим при следующем запуске */ })
    }
  }, [])

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  // После завершения хода (busy true -> false) доспрашиваем граф, собранный
  // фоном, пока не появятся узлы. Новый ход / размонтирование отменяют опрос.
  useEffect(() => {
    const wasBusy = prevBusyRef.current
    prevBusyRef.current = busy
    if (busy) {
      stopGraphPoll()
      return undefined
    }
    if (!wasBusy) return undefined
    const subj = current?.subject || ''
    const grd = current?.grade || ''
    if (subj || grd) pollGraphUntilReady()
    return undefined
  }, [busy, current?.subject, current?.grade, pollGraphUntilReady, stopGraphPoll])

  useEffect(() => () => {
    stopGraphPoll()
    abortRef.current?.()
  }, [stopGraphPoll])

  return (
    <div className="layout">
      <div className="left">
        <CollapsiblePanel
          title={intakeRequired ? 'Знакомство' : 'Новое занятие'}
          defaultOpen={!intakeRequired}
          right={null}
          hideInnerHeader={true}
        >
          {intakeRequired ? (
            <IntakeCard
              prefill={{ name: profile?.student_name || '', learner_type: profile?.learner_type || '', grade: profile?.grade || '' }}
              onSubmit={handleIntake}
              onSkip={skipIntake}
            />
          ) : (
            <TopicForm
              onStart={startTopic}
              prefill={{ grade: profile?.learner_type === 'schoolchild' ? profile?.grade || '' : '' }}
            />
          )}
        </CollapsiblePanel>
        <SessionList sessions={sessions} currentId={current?.session_id} onNew={() => {
          clearSession()
          setCurrent(null)
          setGraph(EMPTY_GRAPH)
          setFeed({ items: [], lastStep: null, adaptive: null, error: null })
          loadSessions()
        }} onPick={loadHistory} />
      </div>
      <main className="center">
        {(current?.subject || current?.grade || graph.nodes.length > 0) && (
          <CollapsiblePanel title="Созвездие знаний" defaultOpen={true}>
            <KnowledgeGraphPanel
              nodes={graph.nodes}
              edges={graph.edges}
              activeTopic={graph.activeTopic}
              onSelect={(node) => studyNext(node.title)}
              sessionId={current?.session_id || ''}
            />
          </CollapsiblePanel>
        )}
        <Chat
          feed={feed}
          busy={busy}
          onSendUser={(text, kind) => runTurn(text, kind)}
          onDismissBanner={(id) => setFeed((f) => ({ ...f, items: f.items.filter((i) => i.id !== id) }))}
          onGoTopic={(gap) => studyNext(gap)}
        />
      </main>
      <aside className="right">
        <CollapsiblePanel
          title="Адаптивность"
          defaultOpen={true}
          hideInnerHeader={true}
        >
          <AdaptivePanel adaptive={feed.adaptive} onStudy={studyNext} onReview={startReview} busy={busy} />
        </CollapsiblePanel>
        <CollapsiblePanel
          title="Конспекты"
          defaultOpen={true}
          badge={null}
          hideInnerHeader={true}
        >
          <KnowledgeWikiPanel
            studentId={studentId}
            refreshKey={wikiVersion}
            subject={current?.subject || ''}
            grade={current?.grade || ''}
          />
        </CollapsiblePanel>
        <CollapsiblePanel title="Мои знания" defaultOpen={true}>
          <StudentKGPanel
            studentId={studentId}
            subject={current?.subject || ''}
            onStartReview={startReview}
            busy={busy}
            reloadKey={kgReloadKey}
            onStudy={studyNext}
          />
        </CollapsiblePanel>
      </aside>
    </div>
  )
}
