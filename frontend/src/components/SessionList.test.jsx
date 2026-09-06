import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SessionList from './SessionList'

describe('<SessionList/>', () => {
  const sessions = [
    { session_id: 'sess_1', topic: 'Квадратные уравнения' },
    { session_id: 'sess_2', topic: 'Теорема Пифагора' },
    { session_id: 'sess_3', topic: 'Тригонометрия' },
  ]

  it('рендерит список сессий из массива sessions', () => {
    const onPick = vi.fn()
    const onNew = vi.fn()
    render(<SessionList sessions={sessions} onPick={onPick} onNew={onNew} />)

    for (const s of sessions) {
      expect(screen.getByText(s.topic)).toBeInTheDocument()
    }
  })

  it('каждый элемент имеет data-testid="session-item"', () => {
    const { container } = render(<SessionList sessions={sessions} onPick={vi.fn()} onNew={vi.fn()} />)
    const items = container.querySelectorAll('[data-testid="session-item"]')
    expect(items).toHaveLength(3)
  })

  it('click на пункт сессии вызывает onSelect с правильным sessionId', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    const onNew = vi.fn()
    render(<SessionList sessions={sessions} currentId="sess_1" onPick={onPick} onNew={onNew} />)

    const button = screen.getAllByRole('button', { name: 'Теорема Пифагора' })[0]
    await user.click(button)

    expect(onPick).toHaveBeenCalledWith(sessions[1])
  })

  it('активный элемент получает класс "active"', () => {
    const { container } = render(<SessionList sessions={sessions} currentId="sess_2" onPick={vi.fn()} onNew={vi.fn()} />)
    const buttons = container.querySelectorAll('.session')
    expect(buttons[0]).not.toHaveClass('active')
    expect(buttons[1]).toHaveClass('active')
    expect(buttons[2]).not.toHaveClass('active')
  })

  it('поиск/filter по тексту фильтрует список', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    const onNew = vi.fn()
    render(<SessionList sessions={sessions} onPick={onPick} onNew={onNew} />)

    // Сначала все видны
    expect(screen.getByText('Квадратные уравнения')).toBeInTheDocument()
    expect(screen.getByText('Теорема Пифагора')).toBeInTheDocument()
    expect(screen.getByText('Тригонометрия')).toBeInTheDocument()

    // Вводим поиск
    const input = screen.getByPlaceholderText('Поиск сессий…')
    await user.type(input, 'Пифагор')

    // Осталась только одна
    expect(screen.getByText('Теорема Пифагора')).toBeInTheDocument()
    expect(screen.queryByText('Квадратные уравнения')).not.toBeInTheDocument()
    expect(screen.queryByText('Тригонометрия')).not.toBeInTheDocument()
  })

  it('показывает "Ничего не найдено" когда фильтр пустой', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    const onNew = vi.fn()
    render(<SessionList sessions={sessions} onPick={onPick} onNew={onNew} />)

    const input = screen.getByPlaceholderText('Поиск сессий…')
    await user.type(input, 'zzzzzzz')

    expect(screen.getByText('Ничего не найдено.')).toBeInTheDocument()
  })

  it('показывает "Пока пусто." когда sessions пустой', () => {
    const onPick = vi.fn()
    const onNew = vi.fn()
    render(<SessionList sessions={[]} onPick={onPick} onNew={onNew} />)

    expect(screen.getByText('Пока пусто.')).toBeInTheDocument()
  })

  it('кнопка "Новая" вызывает onNew', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    const onNew = vi.fn()
    render(<SessionList sessions={sessions} onPick={onPick} onNew={onNew} />)

    await user.click(screen.getByText('Новая'))
    expect(onNew).toHaveBeenCalledTimes(1)
  })

  it('сворачивание панели скрывает содержимое', async () => {
    const user = userEvent.setup()
    const onPick = vi.fn()
    const onNew = vi.fn()
    render(<SessionList sessions={sessions} onPick={onPick} onNew={onNew} />)

    // Изначально панель развёрнута
    expect(screen.getByText('Квадратные уравнения')).toBeInTheDocument()

    // Клик на заголовок — сворачиваем
    await user.click(screen.getByRole('button', { name: 'Сессии ▾' }))

    // Содержимое скрыто, но кнопка «Новая» в шапке остаётся доступной
    expect(screen.queryByText('Квадратные уравнения')).not.toBeInTheDocument()
    expect(screen.getByText('Новая')).toBeInTheDocument()
  })
})
