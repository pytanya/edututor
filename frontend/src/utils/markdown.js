// markdown — минимальный парсер для ответов репетитора (без LaTeX-логики).
const INLINE_RE = /(\$\$[^$]+?\$\$|\$[^$\n]+?\$)|(`[^`]+`)|(\*\*[^*]+\*\*)|(\[([^\]]+)\]\(([^)\s]+)\))/gs

// Некоторые модели оформляют формулы как \(...\) / \[...\]. Рендерер ждёт
// $...$ / $$...$$, поэтому делимитеры нормализуются до разбора. В replace
// возвращаем функцию: '$$' в строке замены означает один доллар.
export function normalizeLatexDelimiters(text) {
  return String(text || '')
    .replace(/\\\[/g, () => '$$')
    .replace(/\\\]/g, () => '$$')
    .replace(/\\\(/g, () => '$')
    .replace(/\\\)/g, () => '$')
}

export function parseInline(text) {
  const tokens = []
  const source = normalizeLatexDelimiters(text)
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

function paragraphTokens(lines) {
  // Строки абзаца соединяются мягким переносом <br/>, чтобы материал урока не
  // «склеивался» в одну строку: HTML в <p> схлопывает обычные переводы строк.
  const tokens = []
  lines.forEach((line, i) => {
    if (i > 0) tokens.push({ type: 'br' })
    tokens.push(...parseInline(line))
  })
  return tokens
}

export function parseBlocks(text) {
  const blocks = []
  const paraLines = []
  let list = null
  // Fenced code block state (```mermaid, ```js, etc.)
  let fenceLines = null
  let fenceLang = ''

  const flushPara = () => {
    if (paraLines.length > 0) {
      blocks.push({ type: 'paragraph', text: paragraphTokens(paraLines) })
      paraLines.length = 0
    }
  }
  const flushList = () => {
    if (list !== null) {
      blocks.push({ type: 'list', items: list })
      list = null
    }
  }
  const flush = () => {
    flushPara()
    flushList()
  }

  for (const raw of String(text || '').split('\n')) {
    const line = raw.trim()

    // Inside a fenced code block — accumulate or close
    if (fenceLines !== null) {
      if (line === '```') {
        blocks.push({ type: 'code', lang: fenceLang, content: fenceLines.join('\n') })
        fenceLines = null
        fenceLang = ''
      } else {
        fenceLines.push(raw)  // preserve original indentation inside fence
      }
      continue
    }

    // Opening fence: ```mermaid, ```js, etc.
    const fenceMatch = line.match(/^```(\w*)$/)
    if (fenceMatch) {
      flush()
      fenceLines = []
      fenceLang = fenceMatch[1] || ''
      continue
    }

    if (!line) {
      flush()
      continue
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/)
    if (heading) {
      flush()
      blocks.push({ type: 'heading', level: heading[1].length, text: parseInline(heading[2]) })
      continue
    }
    const bullet = line.match(/^([-*+])\s+(.*)$/)
    if (bullet) {
      flushPara()
      list = list || []
      list.push(parseInline(bullet[2]))
      continue
    }
    flushList()
    if (line.startsWith('> ')) {
      flushPara()
      blocks.push({ type: 'quote', text: parseInline(line.slice(2)) })
      continue
    }
    paraLines.push(line)
  }
  // If fence was never closed, emit remaining as code block anyway
  if (fenceLines !== null) {
    blocks.push({ type: 'code', lang: fenceLang, content: fenceLines.join('\n') })
  }
  flush()
  return blocks
}

