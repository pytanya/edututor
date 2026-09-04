import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Chat, { feedReducer } from './Chat'

const emptyFeed = { items: [], lastStep: null, adaptive: null, error: null }

function feedWith(items) {
  return { items, lastStep: null, adaptive: null, error: null }
}

describe('<Chat/>', () => {
  it('renders an empty-state when there are no messages and not busy', () => {
    render(<Chat feed={emptyFeed} busy={false} onSendUser={vi.fn()} />)
    expect(screen.getByText('Здравствуйте!')).toBeInTheDocument()
    expect(screen.getByText(/выберите тему слева или задайте вопрос/i)).toBeInTheDocument()
  })

  it('sends trimmed text and clears the textarea on «Отправить» click', async () => {
    const user = userEvent.setup()
    const send = vi.fn()
    render(<Chat feed={emptyFeed} busy={false} onSendUser={send} />)
    const input = screen.getByRole('textbox', { name: /сообщение репетитору/i })
    await user.type(input, '  Расскажи про Виета  ')
    await user.click(screen.getByRole('button', { name: 'Отправить' }))
    expect(send).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenCalledWith('Расскажи про Виета', 'message')
    expect(input).toHaveValue('')
  })

  it('submits on plain Enter', async () => {
    const user = userEvent.setup()
    const send = vi.fn()
    render(<Chat feed={emptyFeed} busy={false} onSendUser={send} />)
    const input = screen.getByRole('textbox', { name: /сообщение репетитору/i })
    await user.type(input, 'Спрошу')
    await user.keyboard('{Enter}')
    expect(send).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenCalledWith('Спрошу', 'message')
    expect(input).toHaveValue('')
  })

  it('does not submit on Shift+Enter', async () => {
    const user = userEvent.setup()
    const send = vi.fn()
    render(<Chat feed={emptyFeed} busy={false} onSendUser={send} />)
    const input = screen.getByRole('textbox', { name: /сообщение репетитору/i })
    await user.type(input, 'Многострочный')
    await user.keyboard('{Shift>}{Enter}{/Shift}')
    expect(send).not.toHaveBeenCalled()
    expect(input).toHaveValue('Многострочный\n')
  })

  it('disables the send button while busy or with only whitespace', async () => {
    const user = userEvent.setup()
    const send = vi.fn()
    const { rerender } = render(<Chat feed={emptyFeed} busy={false} onSendUser={send} />)
    const input = screen.getByRole('textbox', { name: /сообщение репетитору/i })
    const button = screen.getByRole('button', { name: 'Отправить' })
    expect(button).toBeDisabled()
    await user.type(input, '   ')
    expect(button).toBeDisabled()
    await user.type(input, 'x')
    expect(button).toBeEnabled()
    rerender(<Chat feed={emptyFeed} busy={true} onSendUser={send} />)
    expect(button).toBeDisabled()
  })

  it('renders a user bubble and an agent theory bubble', () => {
    const feed = {
      items: [
        { id: 'u1', kind: 'user', content: 'Расскажи про Виета' },
        { id: 'a1', kind: 'agent', envelope: { type: 'theory', text: 'Теорема Виета', payload: {} }, content: '' },
      ],
      lastStep: null,
      adaptive: null,
      error: null,
    }
    render(<Chat feed={feed} busy={false} onSendUser={vi.fn()} />)
    expect(screen.getByText('Расскажи про Виета')).toBeInTheDocument()
    expect(screen.getByText('Теорема Виета')).toBeInTheDocument()
  })
})

describe('feedReducer & баннер mastery.gate', () => {
  it('feedReducer adds mastery.gate banner', () => {
    const feed = feedReducer(emptyFeed, {
      event: 'system',
      data: { kind: 'mastery.gate', message: 'Совет: прежде чем «B», повторите «Сила».', gaps: ['Сила'], topic: 'B' },
    })
    expect(feed.items[0].kind).toBe('mastery.gate')
    expect(feed.items[0].message).toBe('Совет: прежде чем «B», повторите «Сила».')
    expect(feed.items[0].gaps).toEqual(['Сила'])
    expect(feed.items[0].topic).toBe('B')
  })

  it('banner renders message and gap button; dismiss and go-topic callbacks fire', async () => {
    const user = userEvent.setup()
    const dismiss = vi.fn()
    const go = vi.fn()
    const feed = feedWith([
      { id: 'g0', kind: 'mastery.gate', message: 'Совет: прежде чем «B», повторите «Сила».', gaps: ['Сила'], topic: 'B' },
    ])
    render(
      <Chat feed={feed} busy={false} onSendUser={vi.fn()} onDismissBanner={dismiss} onGoTopic={go} />,
    )
    expect(screen.getByText(/Совет: прежде чем «B»/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Всё равно продолжить' }))
    expect(dismiss).toHaveBeenCalledWith('g0')
    await user.click(screen.getByRole('button', { name: 'Перейти к «Сила»' }))
    expect(go).toHaveBeenCalledWith('Сила', 'B')
  })

  it('renders banner with several gaps', async () => {
    const user = userEvent.setup()
    const go = vi.fn()
    const feed = feedWith([
      { id: 'g1', kind: 'mastery.gate', message: 'Совет', gaps: ['Сила', 'Векторы'], topic: 'B' },
    ])
    render(<Chat feed={feed} busy={false} onSendUser={vi.fn()} onGoTopic={go} />)
    await user.click(screen.getByRole('button', { name: 'Перейти к «Векторы»' }))
    expect(go).toHaveBeenCalledWith('Векторы', 'B')
  })

  it('graph.ready adds a system note', () => {
    const feed = feedReducer(emptyFeed, { event: 'graph.ready', data: { root: { id: 'book:x' }, stats: { nodes: 5 } } })
    expect(feed.items[0].kind).toBe('system-note')
    expect(feed.items[0].content).toBe('Построен граф знаний: 5 тем.')
  })

  it('renders a system-note as a plain system line', () => {
    const feed = feedWith([{ id: 's0', kind: 'system-note', content: 'Построен граф знаний: 5 тем.' }])
    render(<Chat feed={feed} busy={false} onSendUser={vi.fn()} />)
    expect(screen.getByText('Построен граф знаний: 5 тем.')).toBeInTheDocument()
  })

  it('feedReducer leaves non-gate system events untouched', () => {
    const feed = feedReducer(emptyFeed, { event: 'system', data: { kind: 'other', message: 'x' } })
    expect(feed.items).toHaveLength(0)
  })
})
