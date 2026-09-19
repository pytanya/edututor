import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TopicArticle, { isStubBody } from './TopicArticle'

describe('TopicArticle helpers', () => {
  it('isStubBody распознаёт заглушку и пустоту', () => {
    expect(isStubBody('')).toBe(true)
    expect(isStubBody('Материал по теме «X» накапливается по мере прохождения квизов.')).toBe(true)
    expect(isStubBody('Реальный конспект')).toBe(false)
  })
})

const art = (over = {}) => ({
  title: 'Крымская война',
  subject: 'История',
  grade: '9',
  mastery: 0.58,
  accuracy: 0.71,
  attempts: 4,
  body: 'Крымская война 1853–1856 — конфликт России против коалиции.',
  concepts: ['Парижский мир 1856'],
  weak_areas: ['причины и повод'],
  ...over,
})

describe('<TopicArticle/>', () => {
  it('placeholder при теле-заглушке и кнопка «Обогатить»', () => {
    const a = art({ body: 'Материал по теме «X» накапливается по мере прохождения квизов.' })
    render(<TopicArticle article={a} onEnrich={vi.fn()} />)
    expect(screen.getByText(/ИИ ещё не написал конспект/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Обогатить конспект' })).toBeInTheDocument()
  })

  it('полное тело рендерится без placeholder', () => {
    render(<TopicArticle article={art()} />)
    expect(screen.queryByText(/ИИ ещё не написал конспект/)).toBeNull()
    expect(screen.getByText(/Крымская война 1853/)).toBeInTheDocument()
  })

  it('не рендерит «Заметки» — вопросы и ответы в «Моих знаниях»', () => {
    const a = art({
      notes: [
        { date: '2026-09-15', feedback: 'Неверно', question: 'Кто командовал?', student_answer: 'Нахимов', correct_answer: 'Нахимов' },
      ],
    })
    render(<TopicArticle article={a} />)
    expect(screen.queryByText(/Заметки/)).toBeNull()
    expect(screen.queryByText('Кто командовал?')).toBeNull()
  })

  it('показывает статус обогащения внутри ридера', () => {
    render(<TopicArticle article={art()} enrichNote="Конспект уже заполнен." />)
    expect(screen.getByText('Конспект уже заполнен.')).toBeInTheDocument()
  })

  it('показывает источник в мета', () => {
    render(<TopicArticle article={art({ source: 'https://ru.wikipedia.org' })} />)
    expect(screen.getByText(/📎 https:\/\/ru\.wikipedia\.org/)).toBeInTheDocument()
  })

  it('навигация пред./след. зовёт onNavigate', async () => {
    const user = userEvent.setup()
    const onNav = vi.fn()
    const siblings = [
      { subject: 'История', topic: 'Предыдущая' },
      { subject: 'История', topic: 'Крымская война' },
      { subject: 'История', topic: 'Реформы' },
    ]
    render(<TopicArticle article={art()} siblings={siblings} topicIndex={1} onNavigate={onNav} />)
    await user.click(screen.getByRole('button', { name: /← Предыдущая/ }))
    expect(onNav).toHaveBeenCalledWith({ subject: 'История', topic: 'Предыдущая' })
    await user.click(screen.getByRole('button', { name: /Реформы →/ }))
    expect(onNav).toHaveBeenCalledWith({ subject: 'История', topic: 'Реформы' })
  })
})