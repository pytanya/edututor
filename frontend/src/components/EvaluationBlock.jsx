import Latex from './Latex'

export default function EvaluationBlock({ envelope }) {
  const payload = envelope?.payload || {}
  const ok = payload.correct
  return (
    <div className={`block evaluation ${ok ? 'ok' : 'no'}`}>
      <div className="evaluation-mark">{ok ? '✅' : '❌'}</div>
      <Latex text={envelope?.text || ''} />
      {payload.feedback && <div className="evaluation-feedback">{payload.feedback}</div>}
    </div>
  )
}
