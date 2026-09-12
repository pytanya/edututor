// StepIndicator.jsx — мини-лог шагов агента: показывает весь путь
// «🔍 Ищу в базе знаний… → 📝 Генерирую проверочный вопрос… → ✍️ Формирую ответ…»

const TOOL_ICONS = {
  rag_search: '🔍',
  web_search: '🌐',
  generate_quiz: '📝',
}

const ACTION_ICONS = {
  final: '✍️',
  tool: '⚙️',
}

function StepLine({ step, isLast }) {
  const icon = step.tool
    ? (TOOL_ICONS[step.tool] || '⚙️')
    : (ACTION_ICONS[step.action] || '💭')
  const text = step.reason || (step.tool ? `инструмент: ${step.tool}` : 'думает…')
  const failed = step.status === 'error'

  return (
    <span className={`step-line${isLast ? ' step-active' : ' step-done'}${failed ? ' step-error' : ''}`}>
      <span className="step-icon">{icon}</span>
      <span className="step-text">{text}</span>
    </span>
  )
}

export default function StepIndicator({ steps }) {
  if (!steps || steps.length === 0) {
    return (
      <div className="step-indicator">
        <span className="dot" /><span className="dot" /><span className="dot" />
      </div>
    )
  }

  return (
    <div className="step-indicator step-log">
      {steps.map((step, i) => (
        <StepLine key={i} step={step} isLast={i === steps.length - 1} />
      ))}
    </div>
  )
}
