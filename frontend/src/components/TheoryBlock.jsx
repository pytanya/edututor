import Latex from './Latex'

export default function TheoryBlock({ envelope, onSend }) {
  const next = envelope?.payload?.next_topic
  return (
    <div className="block theory">
      <Latex text={envelope?.text || ''} />
      {next && (
        <button className="btn next-topic" onClick={() => onSend(`Расскажи про ${next}`)}>
          Изучить дальше: {next} →
        </button>
      )}
    </div>
  )
}
