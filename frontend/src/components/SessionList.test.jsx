import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SessionList from './SessionList'

const many = Array.from({ length: 8 }, (_, i) => ({
  session_id: `sess_${i + 1}`,
  topic: `Тема ${i + 1}`,
}))

describe('<SessionList/>', () => {
  const sessions = [
    { session_id: 'sess_1', topic: 'Квадратные уравнения' },
    { session_id: 'sess_2', topic: 'Теорема Пифагора' },
    { session_id: 'sess_3', topic: 'Тригонометрия' },
  ]

  async function expand(user) {
    await user.click(screen.getByRole('button', { name: /^Сессии/ }))
  }

  it('свёрнут по умолчанию и показывает счётчик сессий', () => {
    render(<SessionList sessions={sessions} onPick={vi.fn()} onNew={vi.fn()} />)

    expect(screen.getByRole('button', { name: /^Сессии \(3\)/ })).toBeInTheDocument()
    expect(screen.queryByText('Квадратные уравнения')).not.toBeInTheDocument()
    expect(screen.getByText('Новая')).toBeInTheDocument()
  })

  it('разворот по клику показывает список сессий', async () => {
    const user = userEvent.setup()
    const { container } = render(<SessionList sessions={sessions} onPick={vi.fn()} onNew={vi.fn()} />)

    await expand(user)
    expect(screen.getByText('Квадратные уравнения')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-testid="session-item"]')).toHaveLength(3)
  })

  it('клик на пункт сессии вызывает onPick с правильным sessionId', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    render(<SessionList sessions={sessions} currentId="sess_1" onPick={onPick} onNew={vi.fn()} />)

    await expand(user)
    await user.click(screen.getByRole('button', { name: 'Теорема Пифагора' }))

    expect(onPick).toHaveBeenCalledWith(sessions[1])
  })

  it('активный элемент получает класс "active"', async () => {
    const user = userEvent.setup()
    const { container } = render(<SessionList sessions={sessions} currentId="sess_2" onPick={vi.fn()} onNew={vi.fn()} />)

    await expand(user)
    const buttons = container.querySelectorAll('.session')
    expect(buttons[0]).not.toHaveClass('active')
    expect(buttons[1]).toHaveClass('active')
    expect(buttons[2]).not.toHaveClass('active')
  })

  it('поиск/фильтр по тексту фильтрует список', async () => {
    const user = userEvent.setup()
    render(<SessionList sessions={sessions} onPick={vi.fn()} onNew={vi.fn()} />)

    await expand(user)
    expect(screen.getByText('Квадратные уравнения')).toBeInTheDocument()
    expect(screen.getByText('Теорема Пифагора')).toBeInTheDocument()
    expect(screen.getByText('Тригонометрия')).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('Поиск сессий…'), 'Пифагор')

    expect(screen.getByText('Теорема Пифагора')).toBeInTheDocument()
    expect(screen.queryByText('Квадратные уравнения')).not.toBeInTheDocument()
    expect(screen.queryByText('Тригонометрия')).not.toBeInTheDocument()
  })

  it('показывает "Ничего не найдено", когда фильтр пустой', async () => {
    const user = userEvent.setup()
    render(<SessionList sessions={sessions} onPick={vi.fn()} onNew={vi.fn()} />)

    await expand(user)
    await user.type(screen.getByPlaceholderText('Поиск сессий…'), 'zzzzzzz')

    expect(screen.getByText('Ничего не найдено.')).toBeInTheDocument()
  })

  it('показывает "Пока пусто." когда sessions пустой', async () => {
    const user = userEvent.setup()
    render(<SessionList sessions={[]} onPick={vi.fn()} onNew={vi.fn()} />)

    expect(screen.queryByText('Пока пусто.')).not.toBeInTheDocument()
    await expand(user)
    expect(screen.getByText('Пока пусто.')).toBeInTheDocument()
  })

  it('показывает не более 5 сессий и кнопку «Показать все (N)»', async () => {
    const user = userEvent.setup()
    const { container } = render(<SessionList sessions={many} onPick={vi.fn()} onNew={vi.fn()} />)

    await expand(user)
    expect(screen.getByRole('button', { name: /^Сессии \(8\)/ })).toBeInTheDocument()
    expect(container.querySelectorAll('[data-testid="session-item"]')).toHaveLength(5)
    expect(screen.queryByText('Тема 6')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Показать все (3)' }))
    expect(container.querySelectorAll('[data-testid="session-item"]')).toHaveLength(8)
    expect(screen.getByText('Тема 8')).toBeInTheDocument()
  })

  it('кнопка "Новая" вызывает onNew', async () => {
    const user = userEvent.setup()
    const onNew = vi.fn()
    render(<SessionList sessions={sessions} onPick={vi.fn()} onNew={onNew} />)

    await user.click(screen.getByText('Новая'))
    expect(onNew).toHaveBeenCalledTimes(1)
  })

  it('сворачивание панели скрывает содержимое', async () => {
    const user = userEvent.setup()
    render(<SessionList sessions={sessions} onPick={vi.fn()} onNew={vi.fn()} />)

    await expand(user)
    expect(screen.getByText('Квадратные уравнения')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^Сессии/ }))

    expect(screen.queryByText('Квадратные уравнения')).not.toBeInTheDocument()
    expect(screen.getByText('Новая')).toBeInTheDocument()
  })

  it('поиск сессий использует класс search-input', async () => {
    const user = userEvent.setup()
    const { container } = render(<SessionList sessions={sessions} onPick={vi.fn()} onNew={vi.fn()} />)

    await expand(user)
    const input = container.querySelector('.session-list-search input')
    expect(input).toBeTruthy()
    expect(input.className).toContain('search-input')
  })
})
