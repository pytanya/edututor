// Chat.jsx — лента сообщений из конвертов + события агента.
import { useEffect, useRef, useState } from 'react'
import Latex from './Latex'
import TheoryBlock from './TheoryBlock'
import PracticeBlock from './PracticeBlock'
import HintBlock from './HintBlock'
import QuizBlock from './QuizBlock'
import EvaluationBlock from './EvaluationBlock'
import StepIndicator from './StepIndicator'

export const feedReducer = (feed, event) => {
  const { event: name, data } = event
  if (name === 'agent.step') {
    return { ...feed, lastStep: data }
  }
  if (name === 'agent.tool') {
    return { ...feed, lastStep: { ...feed.lastStep, tool: data.name, status: data.status } }
  }
  if (name === 'message') {
    const envelope = data.envelope
    return {
      ...feed,
      lastStep: null,
      adaptive: data.adaptive || feed.adaptive,
      items: [
        ...feed.items,
        { id: `m${feed.items.length}`, kind: 'agent', envelope, content: data.content || '' },
      ],
    }
  }
  if (name === 'error') {
    return { ...feed, lastStep: null, error: data.message || 'Ошибка' }
  }
  if (name === 'system') {
    if (data.kind === 'mastery.gate') {
      return {
        ...feed,
        items: [
          ...feed.items,
          { id: `g${feed.items.length}`, kind: 'mastery.gate', message: data.message || '', gaps: data.gaps || [], topic: data.topic || '' },
        ],
      }
    }
    return feed
  }
  if (name === 'graph.ready') {
    const n = (data && data.stats && data.stats.nodes) || 0
    return {
      ...feed,
      items: [...feed.items, { id: `s${feed.items.length}`, kind: 'system-note', content: `Построен граф знаний: ${n} тем.` }],
    }
  }
  return feed
}

function AgentMessage({ item, onSend }) {
  const type = item.envelope?.type
  if (type === 'practice') return <PracticeBlock envelope={item.envelope} onSend={onSend} />
  if (type === 'hint') return <HintBlock envelope={item.envelope} />
  if (type === 'quiz') return <QuizBlock envelope={item.envelope} onSend={onSend} />
  if (type === 'evaluation') return <EvaluationBlock envelope={item.envelope} />
  if (type === 'theory') return <TheoryBlock envelope={item.envelope} onSend={onSend} />
  return <div className="bubble agent"><Latex text={item.content} /></div>
}

const EMPTY_HINT = 'Здесь начнётся занятие: выберите тему слева или задайте вопрос в поле ниже.'

export default function Chat({ feed, busy, onSendUser, onDismissBanner = () => {}, onGoTopic = () => {} }) {
  const items = feed.items || []
  const [draft, setDraft] = useState('')
  const streamRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    const el = streamRef.current
    if (!el) return
    if (typeof el.scrollTo === 'function') {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    } else {
      el.scrollTop = el.scrollHeight
    }
  }, [items, busy, feed.error])

  const canSend = !busy && draft.trim().length > 0

  const send = () => {
    if (!canSend) return
    onSendUser(draft.trim(), 'message')
    setDraft('')
    if (inputRef.current) inputRef.current.style.height = ''
  }

  const growInput = (el) => {
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 140)}px`
  }

  const handleChange = (e) => {
    setDraft(e.target.value)
    growInput(e.target)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="chat-panel panel">
      <div className="chat-stream" ref={streamRef}>
        {items.length === 0 && !busy && (
          <div className="chat-empty">
            <p className="chat-empty-title">Здравствуйте!</p>
            <p>{EMPTY_HINT}</p>
          </div>
        )}
        {items.map((item, i) => {
          if (item.kind === 'mastery.gate') {
            return (
              <div key={i} className="banner gate">
                <p className="gate-message">{item.message}</p>
                <div className="gate-actions">
                  <button type="button" className="btn" onClick={() => onDismissBanner(item.id)}>Всё равно продолжить</button>
                  {item.gaps.map((g) => (
                    <button
                      type="button"
                      key={g}
                      className="btn"
                      onClick={() => onGoTopic(g, item.topic)}
                    >
                      Перейти к «{g}»
                    </button>
                  ))}
                </div>
              </div>
            )
          }
          if (item.kind === 'system-note') {
            return <div key={i} className="system-note">{item.content}</div>
          }
          if (item.kind === 'user') {
            return <div key={i} className="bubble user">{item.content}</div>
          }
          return (
            <div key={i} className="bubble agent">
              <AgentMessage item={item} onSend={(text, kind) => onSendUser(text || '', kind || 'message')} />
            </div>
          )
        })}
        {busy && <StepIndicator lastStep={feed.lastStep} />}
        {feed.error && <div className="bubble error">⚠️ {feed.error}</div>}
      </div>
      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault()
          send()
        }}
      >
        <textarea
          ref={inputRef}
          rows={1}
          value={draft}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder="Задайте вопрос репетитору…"
          aria-label="Сообщение репетитору"
        />
        <button className="btn chat-send" type="submit" disabled={!canSend}>Отправить</button>
      </form>
    </div>
  )
}
