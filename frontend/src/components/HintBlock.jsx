import Latex from './Latex'

export default function HintBlock({ envelope }) {
  return (
    <div className="block hint">
      <div className="block-label">💡 Подсказка</div>
      <Latex text={envelope?.text || ''} />
    </div>
  )
}
