import { describe, it, expect } from 'vitest'
import { parseInline, parseBlocks, normalizeLatexDelimiters } from './markdown'

describe('parseInline', () => {
  it('extracts math, code, strong and link', () => {
    const t = parseInline('смотри $x$ на `коде` и **важно** и [ссылку](http://a.b) конец')
    const types = t.map((x) => x.type)
    expect(types).toEqual(['text', 'math', 'text', 'code', 'text', 'strong', 'text', 'link', 'text'])
  })

  it('normalizes \\(...\\) and \\[...\\] to dollar delimiters', () => {
    const t = parseInline('дробь \\(\\frac{p}{q}\\) и блок \\[E = mc^2\\] и обычная $x$')
    const math = t.filter((x) => x.type === 'math').map((x) => x.content)
    expect(math).toEqual(['$\\frac{p}{q}$', '$$E = mc^2$$', '$x$'])
  })

  it('normalizeLatexDelimiters leaves plain text and links intact', () => {
    expect(normalizeLatexDelimiters('a \\(x\\) b \\[y\\] c')).toBe('a $x$ b $$y$$ c')
    expect(normalizeLatexDelimiters('[link](http://a.b) \\frac')).toBe('[link](http://a.b) \\frac')
  })
})

describe('parseBlocks', () => {
  it('parses heading, list and paragraph', () => {
    const blocks = parseBlocks('# Заголовок\n\n- a\n- b\n\nПараграф')
    expect(blocks[0]).toMatchObject({ type: 'heading', level: 1 })
    expect(blocks[1]).toMatchObject({ type: 'list' })
    expect(blocks[1].items).toHaveLength(2)
    expect(blocks[2]).toMatchObject({ type: 'paragraph' })
  })

  it('keeps a paragraph with single newlines as soft line breaks', () => {
    const blocks = parseBlocks('Строка 1\nСтрока 2\nСтрока 3')
    expect(blocks).toHaveLength(1)
    expect(blocks[0].type).toBe('paragraph')
    const kinds = blocks[0].text.map((t) => t.type)
    expect(kinds).toEqual(['text', 'br', 'text', 'br', 'text'])
  })

  it('detects a bullet list right after a lead-in line (no blank line)', () => {
    const blocks = parseBlocks('Основные свойства:\n- первое\n- второе\nВывод.')
    expect(blocks.map((b) => b.type)).toEqual(['paragraph', 'list', 'paragraph'])
    expect(blocks[1].items).toHaveLength(2)
  })

  it('separates paragraphs around an empty line', () => {
    const blocks = parseBlocks('Первый абзац.\n\nВторой абзац.')
    expect(blocks.map((b) => b.type)).toEqual(['paragraph', 'paragraph'])
    expect(blocks[0].text.map((t) => t.type)).toEqual(['text'])
  })

  it('parses fenced mermaid code block', () => {
    const blocks = parseBlocks('Текст до\n\n```mermaid\ngraph TD\n  A-->B\n```\n\nТекст после')
    expect(blocks.map((b) => b.type)).toEqual(['paragraph', 'code', 'paragraph'])
    expect(blocks[1].lang).toBe('mermaid')
    expect(blocks[1].content).toBe('graph TD\n  A-->B')
  })

  it('parses generic fenced code block', () => {
    const blocks = parseBlocks('Пример:\n\n```js\nconst x = 1\n```')
    expect(blocks.map((b) => b.type)).toEqual(['paragraph', 'code'])
    expect(blocks[1].lang).toBe('js')
    expect(blocks[1].content).toBe('const x = 1')
  })

  it('handles unclosed fenced code block', () => {
    const blocks = parseBlocks('```mermaid\ngraph TD\n  A-->B')
    expect(blocks).toHaveLength(1)
    expect(blocks[0].type).toBe('code')
    expect(blocks[0].lang).toBe('mermaid')
    expect(blocks[0].content).toBe('graph TD\n  A-->B')
  })

  it('parses code block without language tag', () => {
    const blocks = parseBlocks('```\nhello\nworld\n```')
    expect(blocks).toHaveLength(1)
    expect(blocks[0].type).toBe('code')
    expect(blocks[0].lang).toBe('')
    expect(blocks[0].content).toBe('hello\nworld')
  })

  it('mixes mermaid blocks with other content', () => {
    const text = '# Title\n\n```mermaid\npie\n  "A": 50\n  "B": 50\n```\n\n- item 1\n- item 2'
    const blocks = parseBlocks(text)
    expect(blocks.map((b) => b.type)).toEqual(['heading', 'code', 'list'])
    expect(blocks[1].lang).toBe('mermaid')
  })
})

