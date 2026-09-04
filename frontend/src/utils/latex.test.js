import { describe, it, expect } from 'vitest'
import { escapeHtml, tokenizeInline, hasLatex, renderLatex } from './latex'

describe('tokenizeInline', () => {
  it('splits display then inline math from text', () => {
    const t = tokenizeInline('Корень $$\\sqrt{x}$$ и $x^2$ рядом')
    expect(t.map((x) => x.type)).toEqual(['text', 'display-math', 'text', 'inline-math', 'text'])
    expect(t[1].content).toBe('\\sqrt{x}')
    expect(t[3].content).toBe('x^2')
  })
  it('returns one text token when no math', () => {
    expect(tokenizeInline('просто текст')).toEqual([{ type: 'text', content: 'просто текст' }])
  })
  it('keeps unmatched dollar as text', () => {
    const t = tokenizeInline('цена 5$ и всё')
    expect(t.some((x) => x.type === 'inline-math')).toBe(false)
  })
})

describe('hasLatex', () => {
  it('detects formulas', () => {
    expect(hasLatex('формула $a=b$')).toBe(true)
    expect(hasLatex('нет формул')).toBe(false)
  })
})

describe('escapeHtml', () => {
  it('escapes html', () => {
    expect(escapeHtml('<b>&</b>')).toBe('&lt;b&gt;&amp;&lt;/b&gt;')
  })
})

describe('renderLatex', () => {
  it('renders valid latex', () => {
    const html = renderLatex('x^2', false)
    expect(html).toContain('katex')
  })
  it('falls back on broken latex without throwing', () => {
    const html = renderLatex('\\frac{', false)
    expect(html).toBe('$\\frac{$')
  })
})
