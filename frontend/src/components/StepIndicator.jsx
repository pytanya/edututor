// Показывает «репетитор думает…» и последний шаг агента.
export default function StepIndicator({ lastStep }) {
  if (!lastStep) {
    return <div className="step-indicator"><span className="dot" /><span className="dot" /><span className="dot" /></div>
  }
  const tool = lastStep.tool ? ` · инструмент: ${lastStep.tool}` : ''
  return <div className="step-indicator">{lastStep.reason || `репетитор думает${tool}`}</div>
}
