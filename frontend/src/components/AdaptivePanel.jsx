// Панель адаптивности: уровень, прогресс темы, рекомендация.
function Bar({ value, label }) {
  const pct = Math.round((value || 0) * 100)
  return (
    <div className="ad-bar">
      <span className="ad-bar-label">{label}</span>
      <div className="ad-bar-track"><div className="ad-bar-fill" style={{ width: `${pct}%` }} /></div>
      <span className="ad-bar-value">{pct}%</span>
    </div>
  )
}

export default function AdaptivePanel({ adaptive, onStudy, onReview, busy }) {
  const a = adaptive || {}
  const reviewDue = a.review_due || 0
  return (
    <aside className="adaptive panel">
      <h3>Адаптивность</h3>
      {!a.student_id && <p className="muted">Начните занятие, чтобы увидеть прогресс.</p>}
      {a.student_id && (
        <>
          <Bar label="Уровень знаний" value={a.current_knowledge_level} />
          {a.topic && <Bar label={`Тема: ${a.topic}`} value={a.topic_level} />}
          <div className="ad-row"><span>Попытки</span><span>{a.attempts || 0} / {a.correct || 0} ✓</span></div>
          <div className="ad-row"><span>Сложность</span><span>{a.difficulty}</span></div>
          {onReview && reviewDue > 0 && (
            <button className="btn review" disabled={busy} onClick={() => onReview()}>
              Повторить ({reviewDue})
            </button>
          )}
          {a.recommended_next && (
            <button className="btn next-topic" onClick={() => onStudy(a.recommended_next)}>
              Изучить: {a.recommended_next} →
            </button>
          )}
        </>
      )}
    </aside>
  )
}
