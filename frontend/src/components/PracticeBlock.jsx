import Latex from './Latex'

export default function PracticeBlock({ envelope, onSend }) {
  return (
    <div className="block practice">
      <div className="block-label">Задача</div>
      <Latex text={envelope?.text || ''} />
      <button className="btn hint" onClick={() => onSend('', 'hint_request')}>💡 Подсказка</button>
    </div>
  )
}
