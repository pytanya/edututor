import { useState, useMemo } from 'react'

export default function SessionList({ sessions, currentId, onPick, onNew }) {
  const [query, setQuery] = useState('')
  const [collapsed, setCollapsed] = useState(false)

  const filtered = useMemo(() => {
    if (!query.trim()) return sessions
    const q = query.toLowerCase()
    return sessions.filter((s) => {
      const text = (s.topic || s.session_id || '').toLowerCase()
      return text.includes(q)
    })
  }, [sessions, query])

  return (
    <div className="panel session-list">
      <div className="session-list-head">
        <button
          type="button"
          className="collapsible-header"
          onClick={() => setCollapsed((v) => !v)}
          aria-expanded={!collapsed}
        >
          <span className="collapsible-title">Сессии</span>
          {' '}
          <span className={`collapsible-arrow ${collapsed ? '' : 'open'}`}>▾</span>
        </button>
        <button className="btn small" onClick={onNew}>Новая</button>
      </div>

      {!collapsed && (
        <>
          <div className="session-list-search">
            <input
              type="text"
              placeholder="Поиск сессий…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <ul>
            {filtered.map((s) => (
              <li key={s.session_id}>
                <button
                  className={s.session_id === currentId ? 'session active' : 'session'}
                  data-testid="session-item"
                  onClick={() => onPick(s)}
                >
                  {s.topic || s.session_id}
                </button>
              </li>
            ))}
          </ul>
          {filtered.length === 0 && sessions.length > 0 && (
            <p className="muted">Ничего не найдено.</p>
          )}
          {sessions.length === 0 && <p className="muted">Пока пусто.</p>}
        </>
      )}
    </div>
  )
}
