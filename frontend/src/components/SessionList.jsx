export default function SessionList({ sessions, currentId, onPick, onNew }) {
  return (
    <div className="panel session-list">
      <div className="session-list-head">
        <h3>Сессии</h3>
        <button className="btn small" onClick={onNew}>Новая</button>
      </div>
      <ul>
        {sessions.map((s) => (
          <li key={s.session_id}>
            <button className={s.session_id === currentId ? 'session active' : 'session'} onClick={() => onPick(s)}>
              {s.topic || s.session_id}
            </button>
          </li>
        ))}
      </ul>
      {sessions.length === 0 && <p className="muted">Пока пусто.</p>}
    </div>
  )
}
