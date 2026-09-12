// VisualBlock.jsx — диспетчер визуализаций.
// По kind выбирает нужный рендерер: MermaidDiagram, FunctionPlotChart, SafeSvg.
import MermaidDiagram from './MermaidDiagram'
import FunctionPlotChart from './FunctionPlotChart'
import SafeSvg from './SafeSvg'

function VisualItem({ item }) {
  if (!item || !item.kind) return null

  switch (item.kind) {
    case 'mermaid':
      return <MermaidDiagram code={item.code} caption={item.caption} />
    case 'function_plot':
      return (
        <FunctionPlotChart
          expressions={item.expressions}
          xRange={item.x_range}
          yRange={item.y_range}
          caption={item.caption}
        />
      )
    case 'svg':
      return <SafeSvg code={item.code} caption={item.caption} />
    default:
      return null
  }
}

export default function VisualBlock({ visuals }) {
  if (!visuals || !Array.isArray(visuals) || visuals.length === 0) return null

  return (
    <div className="visuals-container">
      {visuals.map((item, i) => (
        <VisualItem key={i} item={item} />
      ))}
    </div>
  )
}
