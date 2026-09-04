// markdown — минимальный парсер для ответов репетитора (без LaTeX-логики).
const INLINE_RE = /(\$\$[^$]+?\$\$|\$[^$\n]+?\$)|(`[^`]+`)|(\*\*[^*]+\*\*)|(\[([^\]]+)\]\(([^)\s]+)\))/gs

export function parseInline(text) {
  const tokens = []
  const source = String(text || '')
  let last = 0
  for (const m of source.matchAll(INLINE_RE)) {
    if (m.index > last) tokens.push({ type: 'text', content: source.slice(last, m.index) })
    if (m[1] !== undefined) tokens.push({ type: 'math', content: m[1] })
    else if (m[2] !== undefined) tokens.push({ type: 'code', content: m[2].slice(1, -1) })
    else if (m[3] !== undefined) tokens.push({ type: 'strong', content: m[3].slice(2, -2) })
    else if (m[4] !== undefined) tokens.push({ type: 'link', content: m[5], href: m[6] })
    last = m.index + m[0].length
  }
  if (last < source.length) tokens.push({ type: 'text', content: source.slice(last) })
  return tokens
}

export function parseBlocks(text) {
  const blocks = []
  for (const raw of String(text || '').split(/\n\s*\n/)) {
    const block = raw.trim()
    if (!block) continue
    const heading = block.match(/^(#{1,4})\s+(.*)$/)
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, text: parseInline(heading[2]) })
      continue
    }
    if (block.startsWith('> ')) {
      blocks.push({ type: 'quote', text: parseInline(block.slice(2)) })
      continue
    }
    const listLines = block.split('\n').filter((l) => /^[-*] /.test(l.trim()))
    if (listLines.length === block.split('\n').length && listLines.length > 0) {
      blocks.push({ type: 'list', items: listLines.map((l) => parseInline(l.trim().slice(2))) })
      continue
    }
    blocks.push({ type: 'paragraph', text: parseInline(block) })
  }
  return blocks
}
