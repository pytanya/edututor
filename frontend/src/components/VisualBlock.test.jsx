// Тесты для визуальных компонентов: VisualBlock, SafeSvg, MermaidDiagram, FunctionPlotChart.
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import VisualBlock from './VisualBlock'
import SafeSvg from './SafeSvg'

// Мокаем mermaid для тестов (он требует DOM с canvas)
vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn().mockResolvedValue({ svg: '<svg class="mermaid-mock"><text>Mock Diagram</text></svg>' }),
  },
}))

// Мокаем function-plot (он тоже требует DOM)
vi.mock('function-plot', () => ({
  default: vi.fn(),
}))

describe('VisualBlock', () => {
  it('returns null for empty visuals', () => {
    const { container } = render(<VisualBlock visuals={null} />)
    expect(container.innerHTML).toBe('')
  })

  it('returns null for empty array', () => {
    const { container } = render(<VisualBlock visuals={[]} />)
    expect(container.innerHTML).toBe('')
  })

  it('renders SVG visual item', () => {
    const visuals = [
      { kind: 'svg', code: '<svg data-testid="test-svg"><circle r="5"/></svg>', caption: 'Тест' },
    ]
    render(<VisualBlock visuals={visuals} />)
    expect(document.querySelector('.visual-svg')).not.toBeNull()
    expect(screen.getByText('Тест')).toBeInTheDocument()
  })

  it('renders mermaid visual item', () => {
    const visuals = [
      { kind: 'mermaid', code: 'graph TD\n  A-->B', caption: 'Диаграмма' },
    ]
    render(<VisualBlock visuals={visuals} />)
    expect(document.querySelector('.visual-mermaid')).not.toBeNull()
  })

  it('renders function_plot visual item', () => {
    const visuals = [
      { kind: 'function_plot', expressions: ['sin(x)'], x_range: [-6, 6], caption: 'Синус' },
    ]
    render(<VisualBlock visuals={visuals} />)
    expect(document.querySelector('.visual-plot')).not.toBeNull()
  })

  it('skips unknown kind items', () => {
    const visuals = [
      { kind: 'unknown', code: 'test' },
    ]
    render(<VisualBlock visuals={visuals} />)
    // VisualBlock renders a container div but unknown items render as null
    expect(document.querySelector('.visual-block')).toBeNull()
  })

  it('renders multiple visuals', () => {
    const visuals = [
      { kind: 'svg', code: '<svg><circle r="5"/></svg>', caption: 'SVG' },
      { kind: 'mermaid', code: 'pie\n  "A": 50\n  "B": 50', caption: 'Пирог' },
    ]
    render(<VisualBlock visuals={visuals} />)
    expect(document.querySelectorAll('.visual-block').length).toBe(2)
  })
})

describe('SafeSvg', () => {
  it('renders sanitized SVG', () => {
    render(<SafeSvg code='<svg data-testid="safe"><circle r="5"/></svg>' caption="Тест" />)
    expect(document.querySelector('.svg-container')).not.toBeNull()
    expect(screen.getByText('Тест')).toBeInTheDocument()
  })

  it('strips script tags from SVG', () => {
    render(<SafeSvg code='<svg><script>alert(1)</script><circle r="5"/></svg>' />)
    const container = document.querySelector('.svg-container')
    expect(container.innerHTML).not.toContain('<script')
  })

  it('returns null for empty code', () => {
    const { container } = render(<SafeSvg code="" />)
    expect(container.innerHTML).toBe('')
  })

  it('returns null for null code', () => {
    const { container } = render(<SafeSvg code={null} />)
    expect(container.innerHTML).toBe('')
  })
})
