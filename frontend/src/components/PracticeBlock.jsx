import Latex from './Latex'
import VisualBlock from './VisualBlock'

export default function PracticeBlock({ envelope, onSend }) {
  const visuals = envelope?.payload?.visuals
  return (
    <div className="block practice">
      <div className="block-label">Задача</div>
      <Latex text={envelope?.text || ''} />
      <VisualBlock visuals={visuals} />
      <button className="btn hint" onClick={() => onSend('', 'hint_request')}>💡 Подсказка</button>
    </div>
  )
}

