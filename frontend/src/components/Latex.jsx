import { parseBlocks, parseInline } from '../utils/markdown'
import { renderLatex, escapeHtml } from '../utils/latex'
import MermaidDiagram from './MermaidDiagram'

function Inline({ tokens }) {
  return tokens.map((tok, i) => {
    if (tok.type === 'math') {
      const display = tok.content.startsWith('$$')
      const body = display ? tok.content.slice(2, -2) : tok.content.slice(1, -1)
      const html = renderLatex(body, display)
      return <span key={i} className={display ? 'latex-display' : 'latex-inline'} dangerouslySetInnerHTML={{ __html: html }} />
    }
    if (tok.type === 'strong') return <strong key={i}>{renderPlain(tok.content)}</strong>
    if (tok.type === 'code') return <code key={i}>{tok.content}</code>
    if (tok.type === 'link') return <a key={i} href={tok.href} target="_blank" rel="noopener noreferrer">{tok.content}</a>
    if (tok.type === 'br') return <br key={i} />
    return <span key={i} dangerouslySetInnerHTML={{ __html: escapeHtml(tok.content) }} />
  })
}

function renderPlain(text) {
  // Вложенные токены внутри strong рендерим повторным парсингом (без рекурсии в HTML).
  return <Inline tokens={parseInline(text)} />
}

// InlineText — рендер одного фрагмента (вариант квиза, строка) без block-обёрток:
// поддерживает $...$ / $$...$$, **жирный**, `код` и ссылки.
export function InlineText({ text }) {
  return <span className="inline-text"><Inline tokens={parseInline(text)} /></span>
}

export default function Latex({ text }) {
  const blocks = parseBlocks(text)
  return (
    <div className="latex-body">
      {blocks.map((block, i) => {
        if (block.type === 'heading') {
          const Tag = `h${block.level}`
          return <Tag key={i}><Inline tokens={block.text} /></Tag>
        }
        if (block.type === 'list') {
          return (
            <ul key={i}>
              {block.items.map((item, j) => <li key={j}><Inline tokens={item} /></li>)}
            </ul>
          )
        }
        if (block.type === 'quote') {
          return <blockquote key={i}><Inline tokens={block.text} /></blockquote>
        }
        if (block.type === 'code') {
          if (block.lang === 'mermaid') {
            return <MermaidDiagram key={i} code={block.content} />
          }
          return <pre key={i}><code className={block.lang ? `lang-${block.lang}` : ''}>{block.content}</code></pre>
        }
        return <p key={i}><Inline tokens={block.text} /></p>
      })}
    </div>
  )
}

