// MermaidDiagram.jsx — рендер Mermaid-кода в SVG.
// Используется для блок-схем, графов, деревьев, UML, timeline, mindmap.
import { useEffect, useRef, useState } from 'react'

let mermaidInit = null

function ensureMermaid() {
  if (mermaidInit) return mermaidInit
  mermaidInit = import('mermaid').then((mod) => {
    const mermaid = mod.default
    mermaid.initialize({
      startOnLoad: false,
      theme: 'dark',
      securityLevel: 'strict',
      fontFamily: 'Inter, system-ui, sans-serif',
    })
    return mermaid
  })
  return mermaidInit
}

let idCounter = 0

// LaTeX → Unicode fallback for Mermaid node labels (LLM sometimes ignores
// the prompt rule and emits $\alpha$ inside diagrams).
// Keys use single backslash — that's what the LLM literally outputs.
const LATEX_UNICODE = [
  ['\\alpha', '\u03B1'], ['\\beta', '\u03B2'], ['\\gamma', '\u03B3'], ['\\delta', '\u03B4'],
  ['\\epsilon', '\u03B5'], ['\\zeta', '\u03B6'], ['\\eta', '\u03B7'], ['\\theta', '\u03B8'],
  ['\\lambda', '\u03BB'], ['\\mu', '\u03BC'], ['\\nu', '\u03BD'], ['\\pi', '\u03C0'],
  ['\\rho', '\u03C1'], ['\\sigma', '\u03C3'], ['\\tau', '\u03C4'], ['\\phi', '\u03C6'],
  ['\\psi', '\u03C8'], ['\\omega', '\u03C9'],
  ['\\Alpha', '\u0391'], ['\\Beta', '\u0392'], ['\\Gamma', '\u0393'], ['\\Delta', '\u0394'],
  ['\\Sigma', '\u03A3'], ['\\Omega', '\u03A9'], ['\\Pi', '\u03A0'], ['\\Phi', '\u03A6'],
  ['\\Psi', '\u03A8'], ['\\Lambda', '\u039B'], ['\\Theta', '\u0398'],
  ['\\infty', '\u221E'], ['\\approx', '\u2248'], ['\\neq', '\u2260'], ['\\ne', '\u2260'],
  ['\\leq', '\u2264'], ['\\le', '\u2264'], ['\\geq', '\u2265'], ['\\ge', '\u2265'],
  ['\\pm', '\u00B1'], ['\\times', '\u00D7'], ['\\cdot', '\u00B7'],
  ['\\rightarrow', '\u2192'], ['\\leftarrow', '\u2190'], ['\\leftrightarrow', '\u2194'],
  ['\\Rightarrow', '\u21D2'], ['\\Leftarrow', '\u21D0'],
  ['\\sum', '\u03A3'], ['\\prod', '\u03A0'], ['\\sqrt', '\u221A'],
  ['\\partial', '\u2202'], ['\\nabla', '\u2207'],
  ['^2', '\u00B2'], ['^3', '\u00B3'],
]

// Matches $...\cmd...$ inline LaTeX patterns
const LATEX_INLINE_RE = /\$([^$]+)\$/g

function sanitizeMermaidLatex(code) {
  return code.replace(LATEX_INLINE_RE, (_match, inner) => {
    // Normalize double backslashes from JSON escaping (\\beta → \beta)
    let result = inner.replaceAll('\\\\', '\\')
    for (const [latex, unicode] of LATEX_UNICODE) {
      result = result.replaceAll(latex, unicode)
    }
    // Remove remaining backslashes from unrecognized commands (e.g. \text{})
    result = result.replace(/\\[a-zA-Z]+/g, '')
    // Clean up leftover braces
    result = result.replace(/[{}]/g, '')
    // Remove any stray backslashes left after replacements
    result = result.replaceAll('\\', '')
    return result
  })
}

export default function MermaidDiagram({ code, caption }) {
  const ref = useRef(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!code || !ref.current) return
    let cancelled = false
    const id = `mermaid-${++idCounter}`
    const cleanCode = sanitizeMermaidLatex(code.trim())

    ensureMermaid()
      .then(async (mermaid) => {
        if (cancelled) return
        try {
          const { svg } = await mermaid.render(id, cleanCode)
          if (!cancelled && ref.current) {
            ref.current.innerHTML = svg
            setError(null)
          }
        } catch (e) {
          if (!cancelled) {
            setError(`Ошибка Mermaid: ${e.message || e}`)
          }
        }
      })
      .catch((e) => {
        if (!cancelled) setError(`Не удалось загрузить Mermaid: ${e.message}`)
      })

    return () => { cancelled = true }
  }, [code])

  if (!code) return null

  return (
    <figure className="visual-block visual-mermaid">
      <div ref={ref} className="mermaid-container" />
      {error && <div className="visual-error">{error}</div>}
      {caption && <figcaption className="visual-caption">{caption}</figcaption>}
    </figure>
  )
}
