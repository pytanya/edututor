import { describe, it, expect } from 'vitest'
import { parseInline, parseBlocks } from './markdown'

describe('parseInline', () => {
  it('extracts math, code, strong and link', () => {
    const t = parseInline('смотри $x$ на `коде` и **важно** и [ссылку](http://a.b) конец')
    const types = t.map((x) => x.type)
    expect(types).toEqual(['text', 'math', 'text', 'code', 'text', 'strong', 'text', 'link', 'text'])
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
})
