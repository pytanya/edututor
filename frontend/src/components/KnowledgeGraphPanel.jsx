import { useEffect, useRef, useState } from 'react'
import api from '../api'

export const EDGE_COLORS = { part_of: '#64DFDF', prerequisite: '#FFB703', related: '#B388FF' }
export const EDGE_LABELS = { part_of: 'входит в', prerequisite: 'опирается на', related: 'связан' }
export const TYPE_LABELS = {
  book: 'Учебник',
  section: 'Раздел',
  lesson: 'Урок',
  topic: 'Тема',
  concept: 'Понятие',
}

const BG = '#0d1117'
const EDGE_FALLBACK = '#8b949e'
const NODE_FALLBACK = '#c9d1d9'
const ACTIVE_COLOR = '#58a6ff'
const ZOOM_MIN = 0.3
const ZOOM_MAX = 3
const REPULSION = 1500
const HOOKE = 0.02
const SPRING_LEN = 140
const GRAVITY = 0.003
const DAMPING = 0.86
const MAX_SPEED = 6
const MASTERY_STOPS = [
  { min: 0.61, color: '#4ade80', label: 'высокое' },
  { min: 0.31, color: '#fbbf24', label: 'среднее' },
  { min: 0, color: '#f87171', label: 'низкое' },
]

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))

const stars = (() => {
  let seed = 42
  const next = () => {
    seed = (seed * 16807) % 2147483647
    return seed / 2147483647
  }
  const out = []
  for (let i = 0; i < 90; i += 1) out.push({ x: next(), y: next(), r: 0.4 + next() * 1.1, a: 0.12 + next() * 0.3 })
  return out
})()

export function masteryColor(mastery) {
  if (mastery === undefined || mastery === null) return null
  if (mastery >= 0.61) return '#4ade80'
  if (mastery >= 0.31) return '#fbbf24'
  return '#f87171'
}

export function filterStructural(nodes, showStructural = true) {
  if (showStructural) return nodes
  return nodes.filter((n) => n?.type !== 'section' && n?.type !== 'lesson')
}

function nodeColor(node) {
  return masteryColor(node?.mastery) || node?.color || NODE_FALLBACK
}

function typeLabel(node) {
  return TYPE_LABELS[node?.type] || node?.type || 'Узел'
}

function nodeRadius(node, degree) {
  if (node.type === 'book') return 9
  return 3.5 + Math.min(degree * 0.8, 5)
}

function pct(x) {
  return Math.round((x || 0) * 100)
}

function accuracyOf(node) {
  if (typeof node.accuracy === 'number') return node.accuracy
  if (node.attempts > 0) return node.correct / node.attempts
  return null
}

function simTick(layout) {
  if (layout.alpha <= 0) return
  layout.alpha = Math.max(0, layout.alpha - 0.004)
  const { nodes, edges, pos } = layout
  const k = REPULSION * (0.4 + layout.alpha)
  const force = {}
  for (const n of nodes) force[n.id] = { x: 0, y: 0 }
  for (let i = 0; i < nodes.length; i += 1) {
    const a = nodes[i]
    const pa = pos[a.id]
    if (!pa) continue
    for (let j = i + 1; j < nodes.length; j += 1) {
      const b = nodes[j]
      const pb = pos[b.id]
      if (!pb) continue
      let dx = pa.x - pb.x
      let dy = pa.y - pb.y
      let d = Math.hypot(dx, dy) || 1
      if (d < 24) d = 24
      const f = k / (d * d)
      const fx = (dx / d) * f
      const fy = (dy / d) * f
      force[a.id].x += fx
      force[a.id].y += fy
      force[b.id].x -= fx
      force[b.id].y -= fy
    }
    if (pa.x !== 0 || pa.y !== 0) {
      force[a.id].x -= pa.x * GRAVITY
      force[a.id].y -= pa.y * GRAVITY
    }
  }
  for (const e of edges) {
    const s = pos[e.source]
    const t = pos[e.target]
    if (!s || !t) continue
    const dx = t.x - s.x
    const dy = t.y - s.y
    const d = Math.hypot(dx, dy) || 1
    const f = (d - SPRING_LEN) * HOOKE
    const fx = (dx / d) * f
    const fy = (dy / d) * f
    force[e.source].x += fx
    force[e.source].y += fy
    force[e.target].x -= fx
    force[e.target].y -= fy
  }
  for (const n of nodes) {
    if (n.type === 'book') continue
    const p = pos[n.id]
    if (!p) continue
    const f = force[n.id]
    p.vx = (p.vx + f.x) * DAMPING
    p.vy = (p.vy + f.y) * DAMPING
    const speed = Math.hypot(p.vx, p.vy)
    if (speed > MAX_SPEED) {
      p.vx = (p.vx / speed) * MAX_SPEED
      p.vy = (p.vy / speed) * MAX_SPEED
    }
    p.x += p.vx
    p.y += p.vy
  }
}

function drawScene(ctx, size, layout, cam, data, ui) {
  const { w, h } = size
  const { nodes, edges } = data
  const { showStructural, query, selectedId, activeTopic, hoverId } = ui
  ctx.save()
  ctx.fillStyle = BG
  ctx.fillRect(0, 0, w, h)
  ctx.globalAlpha = 1
  ctx.fillStyle = '#e6edf3'
  for (const s of stars) {
    ctx.beginPath()
    ctx.arc(s.x * w, s.y * h, s.r, 0, Math.PI * 2)
    ctx.globalAlpha = s.a
    ctx.fill()
  }
  ctx.globalAlpha = 1

  const visible = filterStructural(nodes, showStructural)
  const ids = new Set(visible.map((n) => n.id))
  const visibleEdges = edges.filter((e) => ids.has(e.source) && ids.has(e.target))
  const degree = {}
  for (const n of visible) degree[n.id] = 0
  for (const e of visibleEdges) {
    if (degree[e.source] != null) degree[e.source] += 1
    if (degree[e.target] != null) degree[e.target] += 1
  }

  const q = (query || '').trim().toLowerCase()
  const match = (title) => !q || String(title || '').toLowerCase().includes(q)

  ctx.save()
  ctx.translate(cam.x, cam.y)
  ctx.scale(cam.z, cam.z)

  ctx.lineWidth = 1 / cam.z
  for (const e of visibleEdges) {
    const s = layout.pos[e.source]
    const t = layout.pos[e.target]
    if (!s || !t) continue
    ctx.beginPath()
    ctx.moveTo(s.x, s.y)
    ctx.lineTo(t.x, t.y)
    ctx.strokeStyle = EDGE_COLORS[e.relation] || EDGE_FALLBACK
    ctx.globalAlpha = 0.42
    ctx.stroke()
  }
  ctx.globalAlpha = 1

  for (const n of visible) {
    const p = layout.pos[n.id]
    if (!p) continue
    const r = nodeRadius(n, degree[n.id] || 0)
    const color = nodeColor(n)
    const dimmed = !match(n.title)
    ctx.globalAlpha = dimmed ? 0.25 : 1
    ctx.beginPath()
    ctx.arc(p.x, p.y, r + 3, 0, Math.PI * 2)
    ctx.fillStyle = color
    ctx.globalAlpha = dimmed ? 0.12 : 0.18
    ctx.fill()
    ctx.globalAlpha = dimmed ? 0.25 : 1
    ctx.beginPath()
    ctx.arc(p.x, p.y, r, 0, Math.PI * 2)
    ctx.fillStyle = color
    ctx.fill()
    ctx.strokeStyle = 'rgba(13,17,23,0.75)'
    ctx.lineWidth = 1 / cam.z
    ctx.stroke()
  }
  ctx.globalAlpha = 1
  ctx.restore()

  for (const n of visible) {
    const p = layout.pos[n.id]
    if (!p) continue
    const sx = p.x * cam.z + cam.x
    const sy = p.y * cam.z + cam.y
    const ring = n.id === selectedId ? '#ffffff' : n.id === activeTopic ? ACTIVE_COLOR : n.id === hoverId ? 'rgba(230,237,243,0.75)' : null
    if (ring) {
      ctx.beginPath()
      ctx.arc(sx, sy, 15, 0, Math.PI * 2)
      ctx.strokeStyle = ring
      ctx.lineWidth = 2
      ctx.setLineDash(n.id === activeTopic ? [4, 3] : [])
      ctx.stroke()
      ctx.setLineDash([])
    }
    const label = n.type === 'book' ? '📚' : ui.hoverId === n.id || ui.selectedId === n.id || ui.activeTopic === n.id ? n.title : null
    if (!label) continue
    ctx.font = n.type === 'book' ? '15px sans-serif' : '600 12px system-ui, -apple-system, "Segoe UI", sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    if (n.type === 'book') {
      ctx.fillText(label, sx, sy + 1)
    } else {
      const tw = ctx.measureText(label).width
      const bx = sx - tw / 2 - 5
      const by = sy + 15
      ctx.fillStyle = 'rgba(13,17,23,0.78)'
      ctx.beginPath()
      ctx.roundRect(bx, by - 10, tw + 10, 17, 6)
      ctx.fill()
      ctx.fillStyle = '#e6edf3'
      ctx.fillText(label, sx, by + 1)
    }
  }
  ctx.restore()
}

export default function KnowledgeGraphPanel({ nodes = [], edges = [], activeTopic = null, onSelect = null, sessionId = null }) {
  const stageRef = useRef(null)
  const canvasRef = useRef(null)
  const ctxRef = useRef(null)
  const sizeRef = useRef({ w: 0, h: 0, dpr: 1 })
  const camRef = useRef({ x: 0, y: 0, z: 1 })
  const camInitRef = useRef(false)
  const layoutRef = useRef({ nodes: [], edges: [], alpha: 0, pos: {} })
  const propsRef = useRef({ nodes, edges, sessionId })
  const uiRef = useRef({})
  const dragRef = useRef(null)
  const rafRef = useRef(0)
  const roRef = useRef(null)
  const observedRef = useRef(null)

  const [query, setQuery] = useState('')
  const [showStructural, setShowStructural] = useState(true)
  const [selectedId, setSelectedId] = useState(null)
  const [info, setInfo] = useState(null)
  const [hover, setHover] = useState(null)

  propsRef.current = { nodes, edges, sessionId }
  uiRef.current = { showStructural, query, selectedId, activeTopic, hoverId: hover ? hover.node.id : null }

  const refreshSizes = () => {
    const canvas = canvasRef.current
    if (!canvas || !canvas.isConnected) return
    const stage = stageRef.current
    if (!stage) return
    const w = stage.clientWidth
    const h = stage.clientHeight
    if (w <= 0 || h <= 0) return
    const dpr = window.devicePixelRatio || 1
    if (canvas.width !== Math.round(w * dpr)) canvas.width = Math.round(w * dpr)
    if (canvas.height !== Math.round(h * dpr)) canvas.height = Math.round(h * dpr)
    if (!ctxRef.current || ctxRef.current.canvas !== canvas) ctxRef.current = canvas.getContext('2d')
    sizeRef.current = { w, h, dpr }
    if (!camInitRef.current) {
      camRef.current = { x: w / 2, y: h / 2, z: 1 }
      camInitRef.current = true
    }
  }

  const ensureObserver = () => {
    const stage = stageRef.current
    if (!stage || observedRef.current === stage) return
    if (typeof ResizeObserver === 'undefined') return
    if (roRef.current) roRef.current.disconnect()
    roRef.current = new ResizeObserver(() => refreshSizes())
    roRef.current.observe(stage)
    observedRef.current = stage
  }

  const startLoop = () => {
    if (rafRef.current) return
    const tick = () => {
      rafRef.current = requestAnimationFrame(tick)
      const layout = layoutRef.current
      simTick(layout)
      const canvas = canvasRef.current
      if (!canvas || !canvas.isConnected) return
      const ctx = ctxRef.current
      const size = sizeRef.current
      if (!ctx || size.w <= 0) return
      drawScene(ctx, size, layout, camRef.current, propsRef.current, uiRef.current)
    }
    rafRef.current = requestAnimationFrame(tick)
  }

  const stopLoop = () => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = 0
    }
  }

  useEffect(() => {
    const layout = layoutRef.current
    layout.nodes = nodes
    layout.edges = edges
    layout.alpha = 1
    const pos = layout.pos
    let fresh = 0
    for (const n of nodes) {
      if (pos[n.id]) continue
      if (n.type === 'book') {
        pos[n.id] = { x: 0, y: 0, vx: 0, vy: 0 }
      } else {
        const a = Math.random() * Math.PI * 2
        const r = 70 + (fresh % 9) * 26
        fresh += 1
        pos[n.id] = { x: Math.cos(a) * r, y: Math.sin(a) * r, vx: 0, vy: 0 }
      }
    }
    if (nodes.length === 0) {
      stopLoop()
      return undefined
    }
    const canvas = canvasRef.current
    const onWheel = (e) => {
      e.preventDefault()
      const rect = canvas.getBoundingClientRect()
      const px = e.clientX - rect.left
      const py = e.clientY - rect.top
      zoomAt(px, py, Math.exp(-e.deltaY * 0.0012))
    }
    ensureObserver()
    refreshSizes()
    startLoop()
    if (canvas) canvas.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      if (canvas) canvas.removeEventListener('wheel', onWheel)
    }
  }, [nodes, edges])

  useEffect(() => {
    const onResize = () => refreshSizes()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      stopLoop()
      if (roRef.current) roRef.current.disconnect()
    }
  }, [])

  const zoomAt = (px, py, factor) => {
    const cam = camRef.current
    const z = clamp(cam.z * factor, ZOOM_MIN, ZOOM_MAX)
    const k = z / cam.z
    cam.x = px - (px - cam.x) * k
    cam.y = py - (py - cam.y) * k
    cam.z = z
  }

  const zoomBy = (factor) => {
    const canvas = canvasRef.current
    if (!canvas) return
    zoomAt(canvas.clientWidth / 2, canvas.clientHeight / 2, factor)
  }

  const resetView = () => {
    const canvas = canvasRef.current
    camRef.current = { x: (canvas ? canvas.clientWidth : 0) / 2, y: (canvas ? canvas.clientHeight : 0) / 2, z: 1 }
  }

  const findNodeAt = (clientX, clientY) => {
    const canvas = canvasRef.current
    if (!canvas) return null
    const rect = canvas.getBoundingClientRect()
    const px = clientX - rect.left
    const py = clientY - rect.top
    const cam = camRef.current
    const wx = (px - cam.x) / cam.z
    const wy = (py - cam.y) / cam.z
    const layout = layoutRef.current
    const data = propsRef.current
    const ui = uiRef.current
    const visible = filterStructural(data.nodes, ui.showStructural)
    const deg = {}
    for (const n of visible) deg[n.id] = 0
    for (const e of data.edges) {
      if (deg[e.source] != null) deg[e.source] += 1
      if (deg[e.target] != null) deg[e.target] += 1
    }
    let best = null
    let bestD = Infinity
    for (const n of visible) {
      const p = layout.pos[n.id]
      if (!p) continue
      const d = Math.hypot(p.x - wx, p.y - wy)
      if (d <= nodeRadius(n, deg[n.id] || 0) + 4 && d < bestD) {
        bestD = d
        best = n
      }
    }
    return best
  }

  const loadDetails = (node) => {
    setSelectedId(node.id)
    setInfo({ node, status: 'loading', wiki: null, related: null })
    const sid = propsRef.current.sessionId
    if (!sid) {
      setInfo({ node, status: 'idle', wiki: null, related: null })
      return
    }
    Promise.allSettled([api.getGraphRelated(sid, node.id), api.getGraphWiki(sid, node.id)])
      .then(([relatedRes, wikiRes]) => {
        setInfo((prev) => {
          if (!prev || prev.node.id !== node.id) return prev
          return {
            node,
            status: 'idle',
            wiki: wikiRes.status === 'fulfilled' ? wikiRes.value?.wiki ?? null : null,
            related: relatedRes.status === 'fulfilled' ? relatedRes.value?.related ?? null : null,
          }
        })
      })
      .catch(() => {
        setInfo((prev) => (prev && prev.node.id === node.id ? { node, status: 'error', wiki: null, related: null } : prev))
      })
  }

  const closeDetails = () => {
    setSelectedId(null)
    setInfo(null)
  }

  const onPointerDown = (e) => {
    if (e.button !== 0) return
    const canvas = canvasRef.current
    const cam = camRef.current
    dragRef.current = { pointerId: e.pointerId, x: e.clientX, y: e.clientY, camX: cam.x, camY: cam.y, moved: false, node: findNodeAt(e.clientX, e.clientY) }
    setHover(null)
    if (canvas) canvas.setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e) => {
    const drag = dragRef.current
    if (drag) {
      const dx = e.clientX - drag.x
      const dy = e.clientY - drag.y
      if (Math.hypot(dx, dy) > 3) drag.moved = true
      if (drag.moved) {
        camRef.current.x = drag.camX + dx
        camRef.current.y = drag.camY + dy
      }
      return
    }
    const node = findNodeAt(e.clientX, e.clientY)
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    if (!node) {
      setHover(null)
      return
    }
    setHover({ node, x: e.clientX - rect.left, y: e.clientY - rect.top })
  }

  const onPointerUp = (e) => {
    const drag = dragRef.current
    if (!drag) return
    dragRef.current = null
    const canvas = canvasRef.current
    if (canvas && canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId)
    if (!drag.moved && drag.node) loadDetails(drag.node)
  }

  const onPointerLeave = () => setHover(null)

  if (nodes.length === 0) {
    return (
      <div className="kgp-panel kgp-empty-panel">
        <div className="kgp-empty">Загрузите учебник или найдите источник — здесь появится карта темы.</div>
      </div>
    )
  }

  const selectedNode = info ? info.node : null
  const related = Array.isArray(info ? info.related : null) ? info.related : []
  const connectedCount = (id) => edges.filter((e) => e.source === id || e.target === id).length

  return (
    <section className="kgp-panel">
      <div className="kgp-toolbar">
        <input
          type="search"
          className="kgp-search"
          placeholder="🔍 Найти тему…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Найти тему"
        />
        <label className="kgp-check">
          <input type="checkbox" checked={showStructural} onChange={(e) => setShowStructural(e.target.checked)} />
          Показываем разделы и уроки
        </label>
        <div className="kgp-zoom" role="group" aria-label="Масштаб карты">
          <button type="button" className="kgp-zoom-btn" onClick={() => zoomBy(1.3)} aria-label="Приблизить">+</button>
          <button type="button" className="kgp-zoom-btn" onClick={() => zoomBy(1 / 1.3)} aria-label="Отдалить">−</button>
          <button type="button" className="kgp-zoom-btn" onClick={resetView} aria-label="Сбросить масштаб">⊡</button>
        </div>
      </div>

      <div className="kgp-stage" ref={stageRef}>
        <canvas
          ref={canvasRef}
          className="kgp-canvas"
          role="img"
          aria-label="Карта знаний"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerLeave}
        />
        {hover && hover.node && (
          <div className="kgp-tip" style={{ left: Math.min(hover.x + 14, sizeRef.current.w - 250), top: hover.y + 14 }}>
            <div className="kgp-tip-title">{hover.node.title}</div>
            <div className="kgp-tip-meta">
              {typeLabel(hover.node)}
              {hover.node.section_number ? ` · §${hover.node.section_number}` : ''}
              {connectedCount(hover.node.id) > 0 ? ` · связей: ${connectedCount(hover.node.id)}` : ''}
            </div>
            {typeof hover.node.mastery === 'number' && (
              <div className="kgp-tip-meta">
                Освоение {pct(hover.node.mastery)}%{hover.node.attempts > 0 && ` · ${hover.node.correct || 0}/${hover.node.attempts}`}
                {accuracyOf(hover.node) != null && ` · точность ${pct(accuracyOf(hover.node))}%`}
              </div>
            )}
            <div className="kgp-tip-hint">Клик — детали</div>
          </div>
        )}

        {selectedNode && (
          <div className="kgp-detail">
            <div className="kgp-detail-head">
              <span className="kgp-detail-title">{selectedNode.title}</span>
              <button type="button" className="kgp-close" onClick={closeDetails} aria-label="Закрыть">×</button>
            </div>
            <div className="kgp-detail-type">
              {typeLabel(selectedNode)}
              {selectedNode.section_number ? ` · §${selectedNode.section_number}` : ''}
            </div>
            {typeof selectedNode.mastery === 'number' && (
              <div className="kgp-mastery">
                <div className="kgp-mastery-row">
                  <span>Освоение</span>
                  <span className="kgp-mastery-val">{pct(selectedNode.mastery)}%</span>
                </div>
                <div className="kgp-mastery-track">
                  <div className="kgp-mastery-fill" style={{ width: `${clamp(pct(selectedNode.mastery), 0, 100)}%`, background: masteryColor(selectedNode.mastery) }} />
                </div>
                {selectedNode.attempts > 0 && (
                  <div className="kgp-mastery-row kgp-detail-meta">
                    <span>Попытки</span>
                    <span className="kgp-mastery-val">
                      {selectedNode.correct || 0}/{selectedNode.attempts}
                      {accuracyOf(selectedNode) != null && ` · ${pct(accuracyOf(selectedNode))}%`}
                    </span>
                  </div>
                )}
              </div>
            )}
            {onSelect && (
              <button type="button" className="kgp-study" onClick={() => onSelect(selectedNode)}>
                📖 Изучить тему
              </button>
            )}
            {info && info.status === 'loading' && <div className="kgp-detail-meta">Загружаю детали…</div>}
            {info && info.status === 'error' && <div className="kgp-detail-meta kgp-detail-error">Не удалось загрузить детали.</div>}
            {info && info.status === 'idle' && !info.wiki && !info.related && !sessionId && (
              <div className="kgp-detail-meta">Детали появятся после подключения сессии.</div>
            )}
            {info && info.status === 'idle' && info.wiki === null && info.related === null && sessionId && (
              <div className="kgp-detail-meta">Статья ещё не создана — пройдите квиз по теме.</div>
            )}
            {info && info.wiki && info.wiki.body && (
              <div className="kgp-wiki">
                {String(info.wiki.body).slice(0, 320)}
                {String(info.wiki.body).length > 320 ? '…' : ''}
              </div>
            )}
            {related.length > 0 && (
              <div className="kgp-related">
                {related.map((r, i) => (
                  <button
                    type="button"
                    key={`${r.source}-${r.target}-${i}`}
                    className="kgp-related-chip"
                    onClick={() => {
                      const t = nodes.find((n) => n.id === r.target)
                      if (t) loadDetails(t)
                    }}
                    title={r.target_title}
                  >
                    {r.target_title}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="kgp-legend">
        <span className="kgp-legend-title">Легенда</span>
        {Object.keys(EDGE_LABELS).map((rel) => (
          <span key={rel} className="kgp-swatch">
            <i className="kgp-edge" style={{ background: EDGE_COLORS[rel] }} />
            {EDGE_LABELS[rel]}
          </span>
        ))}
        {MASTERY_STOPS.map((s) => (
          <span key={s.label} className="kgp-swatch">
            <i style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
    </section>
  )
}
