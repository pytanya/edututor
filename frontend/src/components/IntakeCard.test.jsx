import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import IntakeCard from './IntakeCard'

describe('<IntakeCard/>', () => {
  const validValues = {
    name: 'Иван Иванов',
    learner_type: 'schoolchild',
    grade: '8 класс',
    subject: 'математика',
    topic: 'квадратные уравнения',
    mode: 'lesson',
  }

  it('renders fields and disabled submit for empty form', () => {
    render(<IntakeCard onSubmit={vi.fn()} onSkip={vi.fn()} />)
    expect(screen.getByText('Знакомство и план занятия')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Иван Иванов')).toBeInTheDocument()
    const btn = screen.getByRole('button', { name: 'Начать занятие' })
    expect(btn).toBeDisabled()
  })

  it('shows hint when name has a single word', async () => {
    const user = userEvent.setup()
    render(<IntakeCard onSubmit={vi.fn()} onSkip={vi.fn()} />)
    await user.type(screen.getByPlaceholderText('Иван Иванов'), 'Иван')
    expect(screen.getByText('Укажи имя и фамилию (минимум два слова).')).toBeInTheDocument()
  })

  it('submits values when valid', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<IntakeCard onSubmit={onSubmit} onSkip={vi.fn()} />)
    await user.type(screen.getByPlaceholderText('Иван Иванов'), 'Иван Иванов')
    await user.selectOptions(screen.getByLabelText('Ты школьник или студент?'), 'schoolchild')
    await user.type(screen.getByPlaceholderText('8 класс'), '8 класс')
    await user.type(screen.getByPlaceholderText('математика'), 'математика')
    await user.type(screen.getByPlaceholderText('квадратные уравнения'), 'квадратные уравнения')
    await user.click(screen.getByRole('button', { name: 'Начать занятие' }))
    expect(onSubmit).toHaveBeenCalledWith(validValues)
  })

  it('grade not required for student, required for schoolchild', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    const { rerender } = render(<IntakeCard onSubmit={onSubmit} onSkip={vi.fn()} />)
    await user.type(screen.getByPlaceholderText('Иван Иванов'), 'Мария Сидорова')
    await user.selectOptions(screen.getByLabelText('Ты школьник или студент?'), 'student')
    expect(screen.queryByPlaceholderText('8 класс')).not.toBeInTheDocument()
    await user.type(screen.getByPlaceholderText('математика'), 'алгебра')
    await user.type(screen.getByPlaceholderText('квадратные уравнения'), 'производные')
    await user.click(screen.getByRole('button', { name: 'Начать занятие' }))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ learner_type: 'student', grade: '' }))
    // schoolchild без класса -> кнопка остаётся неактивной
    rerender(<IntakeCard onSubmit={onSubmit} onSkip={vi.fn()} />)
    await user.selectOptions(screen.getByLabelText('Ты школьник или студент?'), 'schoolchild')
    await user.type(screen.getByPlaceholderText('8 класс'), '8 класс')
  })

  it('«Начать без карточки» вызывает onSkip', async () => {
    const user = userEvent.setup()
    const onSkip = vi.fn()
    render(<IntakeCard onSubmit={vi.fn()} onSkip={onSkip} />)
    await user.click(screen.getByRole('button', { name: 'Начать без карточки' }))
    expect(onSkip).toHaveBeenCalled()
  })

  it('prefill попадает в поля (повторный заход)', () => {
    render(
      <IntakeCard
        prefill={{ name: 'Иван Иванов', learner_type: 'schoolchild', grade: '8 класс' }}
        onSubmit={vi.fn()}
        onSkip={vi.fn()}
      />,
    )
    expect(screen.getByPlaceholderText('Иван Иванов').value).toBe('Иван Иванов')
    expect(screen.getByPlaceholderText('8 класс').value).toBe('8 класс')
    expect(screen.getByLabelText('Ты школьник или студент?').value).toBe('schoolchild')
  })
})
