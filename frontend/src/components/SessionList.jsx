import { useState, useMemo } from 'react'

const MAX_VISIBLE = 5

export default function SessionList({ sessions, currentId, onPick, onNew }) {
  const [query, setQuery] = useState('')
  const [collapsed, setCollapsed] = useState(true)
  const [showAll, setShowAll] = useState(false)

  const filtered = useMemo(() => {
    if (!query.trim()) return sessions
    const q = query.toLowerCase()
    return sessions.filter((s) => {
      const text = (s.topic || s.session_id || '').toLowerCase()
      return text.includes(q)
    })
  }, [sessions, query])

  const visible = useMemo(() => {
    if (query.trim() || showAll) return filtered
    return filtered.slice(0, MAX_VISIBLE)
  }, [filtered, query, showAll])

  const hiddenCount = !query.trim() && !showAll ? filtered.length - MAX_VISIBLE : 0
  const count = sessions.length

  const handleSearch = (e) => {
    setQuery(e.target.value)
    setShowAll(false)
  }

  return (
    <div className="panel session-list">
      <div className="session-list-head">
        <button
          type="button"
          className="collapsible-header"
          onClick={() => setCollapsed((v) => !v)}
          aria-expanded={!collapsed}
        >
          <span className="collapsible-title">Сессии{count > 0 ? ` (${count})` : ''}</span>
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
              className="search-input"
              placeholder="Поиск сессий…"
              value={query}
              onChange={handleSearch}
            />
          </div>
          <ul>
            {visible.map((s) => (
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
          {hiddenCount > 0 && (
            <button
              type="button"
              className="btn small session-list-more"
              onClick={() => setShowAll(true)}
            >
              Показать все ({hiddenCount})
            </button>
          )}
          {filtered.length === 0 && sessions.length > 0 && (
            <p className="muted">Ничего не найдено.</p>
          )}
          {sessions.length === 0 && <p className="muted">Пока пусто.</p>}
        </>
      )}
    </div>
  )
}
