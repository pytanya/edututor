// FunctionPlotChart.jsx — интерактивный график математической функции.
// Использует function-plot (обёртка над D3) для рендера f(x).
import { useEffect, useRef, useState } from 'react'

export default function FunctionPlotChart({ expressions, xRange, yRange, caption }) {
  const ref = useRef(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!expressions || expressions.length === 0 || !ref.current) return
    let cancelled = false

    import('function-plot')
      .then((mod) => {
        if (cancelled || !ref.current) return
        const functionPlot = mod.default || mod
        try {
          // Очищаем контейнер перед рендером
          ref.current.innerHTML = ''
          functionPlot({
            target: ref.current,
            width: Math.min(ref.current.clientWidth || 500, 600),
            height: 350,
            grid: true,
            xAxis: { domain: xRange || [-10, 10] },
            yAxis: { domain: yRange || [-10, 10] },
            data: expressions.map((fn) => ({
              fn,
              graphType: 'polyline',
            })),
          })
          setError(null)
        } catch (e) {
          setError(`Ошибка графика: ${e.message || e}`)
        }
      })
      .catch((e) => {
        if (!cancelled) setError(`Не удалось загрузить function-plot: ${e.message}`)
      })

    return () => { cancelled = true }
  }, [expressions, xRange, yRange])

  if (!expressions || expressions.length === 0) return null

  return (
    <figure className="visual-block visual-plot">
      <div ref={ref} className="plot-container" />
      {error && <div className="visual-error">{error}</div>}
      {caption && <figcaption className="visual-caption">{caption}</figcaption>}
    </figure>
  )
}
