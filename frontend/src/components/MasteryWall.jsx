// MasteryWall — тепловая карта мастерства тем из /knowledge-graph.
// Квадрат-ячейка на тему, цвет по mastery: >=0.75 high, >=0.45 mid, иначе low.
export const masteryClass = (m) => {
  if (typeof m !== 'number' || Number.isNaN(m)) return 'low'
  if (m >= 0.75) return 'high'
  if (m >= 0.45) return 'mid'
  return 'low'
}

export default function MasteryWall({ topics = [], onSelect = null }) {
  const total = topics.length
  if (total === 0) return null
  const mastered = topics.filter((t) => masteryClass(t.mastery) === 'high').length
  const pct = Math.round((mastered / total) * 100)

  return (
    <section className="panel mastery-wall">
      <div className="mw-head">
        <h3>Усвоение</h3>
        <span className="mw-meta">
          Освоено {mastered}/{total} · {pct}%
        </span>
      </div>
      <div className="mw-grid">
        {topics.map((t) => {
          const name = typeof t?.topic === 'string' ? t.topic : ''
          const m = typeof t?.mastery === 'number' ? t.mastery : 0
          return (
            <button
              key={`${t?.subject || ''}:${name}`}
              type="button"
              className={`mw-cell ${masteryClass(m)}`}
              title={`${name} · ${Math.round(m * 100)}%`}
              onClick={() => onSelect && onSelect(name)}
            >
              {name}
            </button>
          )
        })}
      </div>
    </section>
  )
}
