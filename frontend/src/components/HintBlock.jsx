import Latex from './Latex'
import VisualBlock from './VisualBlock'

export default function HintBlock({ envelope }) {
  const visuals = envelope?.payload?.visuals
  return (
    <div className="block hint">
      <div className="block-label">💡 Подсказка</div>
      <Latex text={envelope?.text || ''} />
      <VisualBlock visuals={visuals} />
    </div>
  )
}

