// CollapsiblePanel — универсальный аккордеон-обёртка.
// Заголовок + кнопка-стрелка, контент сворачивается через grid-template-rows
// (не требует измерения высоты — работает и в jsdom, и с динамическим контентом).
import { useState } from 'react'

export default function CollapsiblePanel({
  title,
  defaultOpen = true,
  children,
  badge = null,
  right = null,
  className = '',
  hideInnerHeader = false,
}) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className={`collapsible-panel ${className}`}>
      <button
        type="button"
        className="collapsible-header"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="collapsible-title">
          {title}
          {badge !== null && <span className="collapsible-badge">{badge}</span>}
        </span>
        {' '}
        <span className={`collapsible-arrow ${open ? 'open' : ''}`}>▾</span>
        {right && <span className="collapsible-right">{right}</span>}
      </button>
      <div className={`collapsible-body ${open ? 'open' : ''}`}>
        <div className={`collapsible-content ${hideInnerHeader ? 'hide-inner-header' : ''}`}>
          {children}
        </div>
      </div>
    </div>
  )
}
