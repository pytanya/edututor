import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TheoryBlock from './TheoryBlock'
import PracticeBlock from './PracticeBlock'
import QuizBlock from './QuizBlock'
import EvaluationBlock from './EvaluationBlock'

const theory = { type: 'theory', text: 'Теория $$x^2$$', payload: { next_topic: 'Виета' } }
const practice = { type: 'practice', text: 'Реши $$x+1=0$$', payload: {} }
const quizSingle = { type: 'quiz', text: 'Чему равен x?', payload: { answer_type: 'single', options: ['1', '2'] } }
const quizOpen = { type: 'quiz', text: 'Опиши', payload: { answer_type: 'open' } }
const evaluation = { type: 'evaluation', text: 'Верно', payload: { correct: true, feedback: 'молодец' } }

describe('content blocks', () => {
  it('TheoryBlock offers next topic', async () => {
    const send = vi.fn()
    render(<TheoryBlock envelope={theory} onSend={send} />)
    await userEvent.click(screen.getByText(/Изучить дальше/))
    expect(send).toHaveBeenCalledWith('Расскажи про Виета')
  })

  it('PracticeBlock hint button sends hint_request', async () => {
    const send = vi.fn()
    render(<PracticeBlock envelope={practice} onSend={send} />)
    await userEvent.click(screen.getByRole('button', { name: /Подсказка/ }))
    expect(send).toHaveBeenCalledWith('', 'hint_request')
  })

  it('QuizBlock single sends chosen option', async () => {
    const send = vi.fn()
    render(<QuizBlock envelope={quizSingle} onSend={send} />)
    await userEvent.click(screen.getByRole('button', { name: '2' }))
    expect(send).toHaveBeenCalledWith('Ответ: 2')
  })

  it('QuizBlock open submits typed answer', async () => {
    const send = vi.fn()
    render(<QuizBlock envelope={quizOpen} onSend={send} />)
    await userEvent.type(screen.getByPlaceholderText('Ваш ответ…'), 'abc')
    await userEvent.click(screen.getByRole('button', { name: 'Ответить' }))
    expect(send).toHaveBeenCalledWith('Ответ: abc')
  })

  it('QuizBlock shows review badge when payload.review', () => {
    render(
      <QuizBlock
        envelope={{ type: 'quiz', text: 'Q?', payload: { review: true, answer_type: 'open' } }}
        onSend={() => {}}
      />,
    )
    expect(screen.getByText('повторение')).toBeInTheDocument()
  })

  it('QuizBlock hides review badge by default', () => {
    render(<QuizBlock envelope={quizOpen} onSend={() => {}} />)
    expect(screen.queryByText('повторение')).not.toBeInTheDocument()
  })

  it('EvaluationBlock shows mark and feedback', () => {
    render(<EvaluationBlock envelope={evaluation} />)
    expect(screen.getByText('✅')).toBeInTheDocument()
    expect(screen.getByText('молодец')).toBeInTheDocument()
  })
})
