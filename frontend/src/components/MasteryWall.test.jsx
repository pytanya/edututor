import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import MasteryWall, { masteryClass } from './MasteryWall'

const topics = [
  { topic: 'Сила', subject: 'физика', mastery: 0.83, status: 'mastered' },
  { topic: 'Кинематика', subject: 'физика', mastery: 0.5 },
  { topic: 'Оптика', subject: 'физика', mastery: 0.2 },
]

describe('MasteryWall helpers', () => {
  it('masteryClass thresholds', () => {
    expect(masteryClass(0.8)).toBe('high')
    expect(masteryClass(0.5)).toBe('mid')
    expect(masteryClass(0.2)).toBe('low')
  })
})

describe('<MasteryWall/>', () => {
  it('рендерит заголовок, счётчик и ячейки по классам мастерства', () => {
    const { container } = render(<MasteryWall topics={topics} />)
    expect(screen.getByText('Усвоение')).toBeInTheDocument()
    expect(screen.getByText('Освоено 1/3 · 33%')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Сила' })).toHaveClass('high')
    expect(screen.getByRole('button', { name: 'Кинематика' })).toHaveClass('mid')
    expect(screen.getByRole('button', { name: 'Оптика' })).toHaveClass('low')
    expect(container.querySelectorAll('.mw-cell')).toHaveLength(3)
  })

  it('клик по ячейке зовёт onSelect(topic)', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<MasteryWall topics={topics} onSelect={onSelect} />)
    await user.click(screen.getByRole('button', { name: 'Кинематика' }))
    expect(onSelect).toHaveBeenCalledWith('Кинематика')
  })

  it('не рендерится без тем', () => {
    const { container } = render(<MasteryWall topics={[]} />)
    expect(container.querySelector('.mastery-wall')).toBeNull()
  })
})
