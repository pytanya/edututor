import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import KnowledgeGraphPanel, { masteryColor, filterStructural, EDGE_COLORS, TYPE_LABELS } from './KnowledgeGraphPanel'

describe('KnowledgeGraphPanel helpers', () => {
  it('masteryColor thresholds', () => {
    expect(masteryColor(0.7)).toBe('#4ade80')
    expect(masteryColor(0.5)).toBe('#fbbf24')
    expect(masteryColor(0.2)).toBe('#f87171')
    expect(masteryColor(undefined)).toBeNull()
    expect(masteryColor(null)).toBeNull()
  })

  it('edge colors по relation', () => {
    expect(EDGE_COLORS.part_of).toBe('#64DFDF')
    expect(EDGE_COLORS.prerequisite).toBe('#FFB703')
    expect(EDGE_COLORS.related).toBe('#B388FF')
  })

  it('TYPE_LABELS покрывает типы узлов', () => {
    expect(TYPE_LABELS.book).toBe('Учебник')
    expect(TYPE_LABELS.topic).toBe('Тема')
    expect(TYPE_LABELS.concept).toBe('Понятие')
  })

  it('filterStructural прячет section/lesson', () => {
    const nodes = [
      { id: 'book:x', type: 'book' },
      { id: 'sec:x:1', type: 'section' },
      { id: 'sec:x:2', type: 'lesson' },
      { id: 'topic:Сила', type: 'topic' },
    ]
    expect(filterStructural(nodes, true).map((n) => n.id)).toEqual(['book:x', 'sec:x:1', 'sec:x:2', 'topic:Сила'])
    expect(filterStructural(nodes, false).map((n) => n.id)).toEqual(['book:x', 'topic:Сила'])
    expect(filterStructural(nodes).map((n) => n.id)).toEqual(['book:x', 'sec:x:1', 'sec:x:2', 'topic:Сила'])
  })
})

describe('<KnowledgeGraphPanel/>', () => {
  it('empty state renders without crash', () => {
    render(<KnowledgeGraphPanel nodes={[]} edges={[]} />)
    expect(screen.getByText('Загрузите учебник или найдите источник — здесь появится карта темы.')).toBeInTheDocument()
    expect(document.querySelector('canvas')).toBeNull()
  })

  it('renders canvas with data', () => {
    const nodes = [
      { id: 'book:x', title: 'Учебник «x»', type: 'book', color: '#F4A261' },
      { id: 'topic:Сила', title: 'Сила', type: 'topic', color: '#69F0AE', mastery: 0.9 },
    ]
    const { container } = render(
      <KnowledgeGraphPanel
        nodes={nodes}
        edges={[{ source: 'book:x', target: 'topic:Сила', relation: 'part_of' }]}
      />,
    )
    const canvas = container.querySelector('canvas')
    expect(canvas).toBeTruthy()
    expect(canvas.getAttribute('role')).toBe('img')
  })
})
