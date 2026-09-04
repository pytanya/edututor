import { describe, it, expect, vi, afterEach } from 'vitest'
import { parseSSEChunk } from './api'
import api from './api'

afterEach(() => {
  delete global.fetch
})

describe('parseSSEChunk', () => {
  it('parses event+data frames and returns leftover buffer', () => {
    const seen = []
    let rest = parseSSEChunk('event: agent.step\ndata: {"step":1}\n\n', (e) => seen.push(e))
    expect(seen).toEqual([{ event: 'agent.step', data: { step: 1 } }])
    expect(rest).toBe('')
  })
  it('handles partial frame across chunks', () => {
    const seen = []
    let rest = parseSSEChunk('event: message\ndata: {"a":1}\n\nev', (e) => seen.push(e))
    expect(seen).toHaveLength(1)
    rest = parseSSEChunk(rest + 'ent: done\ndata: {}\n\n', (e) => seen.push(e))
    expect(seen).toHaveLength(2)
    expect(seen[1].event).toBe('done')
  })
  it('skips malformed json data', () => {
    const seen = []
    parseSSEChunk('event: x\ndata: not-json\n\n', (e) => seen.push(e))
    expect(seen).toHaveLength(0)
  })
})

describe('api.getReview', () => {
  it('requests /student/{id}/review and returns stats', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ stats: { total: 2 }, due: [] }) }),
    )
    const out = await api.getReview('stu 1')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/student/stu%201/review'),
      expect.anything(),
    )
    expect(out.stats.total).toBe(2)
  })
})

describe('api knowledge graph endpoints', () => {
  it('getKnowledgeGraph requests /student/{id}/knowledge-graph', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ student_id: 'stu 1', stats: {} }) }),
    )
    const out = await api.getKnowledgeGraph('stu 1')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/student/stu%201/knowledge-graph'),
      expect.anything(),
    )
    expect(out.student_id).toBe('stu 1')
  })

  it('getKnowledgeGraph appends subject query', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }))
    await api.getKnowledgeGraph('stu_x', 'physics')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/student/stu_x/knowledge-graph?subject=physics'),
      expect.anything(),
    )
  })

  it('getRecommendations passes given params', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ recommendations: [] }) }),
    )
    await api.getRecommendations('stu_x', { current_topic: 'Сила', subject: 'physics', limit: 3 })
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/student/stu_x/recommendations?'),
      expect.anything(),
    )
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('current_topic=%D0%A1%D0%B8%D0%BB%D0%B0'),
      expect.anything(),
    )
    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining('subject=physics'), expect.anything())
    expect(global.fetch).toHaveBeenCalledWith(expect.stringContaining('limit=3'), expect.anything())
  })

  it('getRecommendations falls back to default params', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ recommendations: [] }) }),
    )
    await api.getRecommendations('stu_x')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('current_topic=&subject=&limit=5'),
      expect.anything(),
    )
  })

  it('getGraph requests /student/{id}/graph with subject and grade', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ nodes: [] }) }))
    await api.getGraph('stu_x', 'physics', '9')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/student/stu_x/graph?subject=physics&grade=9'),
      expect.anything(),
    )
  })

  it('getGraphRelated and getGraphWiki request nested node endpoints', async () => {
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }))
    await api.getGraphRelated('stu_x', 'node 1')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/student/stu_x/graph/node%201/related'),
      expect.anything(),
    )
    await api.getGraphWiki('stu_x', 'node 1')
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/student/stu_x/graph/node%201/wiki'),
      expect.anything(),
    )
  })
})
