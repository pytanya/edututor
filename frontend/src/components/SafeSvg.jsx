// SafeSvg.jsx — рендер санитизированного SVG-кода.
// Используется для геометрических фигур (треугольники, окружности, углы).
// Двойная защита: backend (regex) + frontend (DOMPurify).
import { useMemo } from 'react'

// DOMPurify загружается синхронно — он маленький (~15 KB).
import DOMPurify from 'dompurify'

const PURIFY_CONFIG = {
  USE_PROFILES: { svg: true, svgFilters: true },
  ADD_TAGS: [
    'svg', 'circle', 'ellipse', 'line', 'path', 'polygon', 'polyline',
    'rect', 'text', 'tspan', 'g', 'defs', 'marker', 'use', 'clipPath',
    'mask', 'pattern', 'linearGradient', 'radialGradient', 'stop',
    'foreignObject',
  ],
  ADD_ATTR: [
    'viewBox', 'xmlns', 'fill', 'stroke', 'stroke-width', 'stroke-dasharray',
    'd', 'cx', 'cy', 'r', 'rx', 'ry', 'x', 'y', 'x1', 'y1', 'x2', 'y2',
    'width', 'height', 'transform', 'points', 'text-anchor', 'font-size',
    'font-family', 'dominant-baseline', 'opacity', 'stroke-linecap',
    'stroke-linejoin', 'marker-end', 'marker-start', 'id', 'class',
    'dx', 'dy', 'offset', 'stop-color', 'stop-opacity', 'gradientTransform',
  ],
  FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'form'],
  FORBID_ATTR: ['onload', 'onerror', 'onclick', 'onmouseover'],
}

export default function SafeSvg({ code, caption }) {
  const sanitized = useMemo(() => {
    if (!code) return ''
    return DOMPurify.sanitize(code, PURIFY_CONFIG)
  }, [code])

  if (!sanitized) return null

  return (
    <figure className="visual-block visual-svg">
      <div
        className="svg-container"
        dangerouslySetInnerHTML={{ __html: sanitized }}
      />
      {caption && <figcaption className="visual-caption">{caption}</figcaption>}
    </figure>
  )
}
