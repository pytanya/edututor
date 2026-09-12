import Latex from './Latex'
import VisualBlock from './VisualBlock'

export default function TheoryBlock({ envelope, onSend }) {
  const next = envelope?.payload?.next_topic
  const visuals = envelope?.payload?.visuals
  return (
    <div className="block theory">
      <Latex text={envelope?.text || ''} />
      <VisualBlock visuals={visuals} />
      {next && (
        <button className="btn next-topic" onClick={() => onSend(`Расскажи про ${next}`)}>
          Изучить дальше: {next} →
        </button>
      )}
    </div>
  )
}

