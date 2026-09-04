// Latex — токенизация $...$ / $$...$$ и рендер через KaTeX (см. контракт).
import katex from 'katex'

const MATH_RE = /\$\$([^$]+?)\$\$|\$([^$\n]+?)\$/gs

export function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

export function tokenizeInline(text) {
  const tokens = []
  const source = String(text || '')
  let last = 0
  for (const m of source.matchAll(MATH_RE)) {
    if (m.index > last) tokens.push({ type: 'text', content: source.slice(last, m.index) })
    if (m[1] !== undefined) tokens.push({ type: 'display-math', content: m[1] })
    else tokens.push({ type: 'inline-math', content: m[2] })
    last = m.index + m[0].length
  }
  if (last < source.length) tokens.push({ type: 'text', content: source.slice(last) })
  return tokens
}

export function hasLatex(text) {
  return /\$[^$]+\$/.test(String(text || ''))
}

export function renderLatex(content, display) {
  try {
    return katex.renderToString(content, {
      throwOnError: true,
      displayMode: display,
      strict: 'ignore',
      trust: true,
    })
  } catch {
    return display ? `$${content}$$` : `$${content}$`
  }
}
