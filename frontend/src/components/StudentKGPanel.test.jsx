import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import StudentKGPanel, { statusMeta, masteryColor } from './StudentKGPanel'
import api from '../api'

vi.mock('../api', () => ({
  default: {
    getKnowledgeGraph: vi.fn(),
    getReview: vi.fn(),
    records: vi.fn(),
  },
}))

const topic = (over = {}) => ({
  topic: 'Сила',
  subject: 'Физика',
  status: 'mastered',
  mastery: 0.83,
  attempts: 3,
  correct: 3,
  accuracy: 1,
  weak_areas: [],
  last_seen: 1,
  relations: {},
  ...over,
})

function data(topics = { 'Сила': topic() }, stats = { mastered: 1, in_progress: 0, not_studied: 0, total: 1 }) {
  return { student_id: 'stu_x', subject: '', topics, stats }
}

const record = (over = {}) => ({
  ts: 1,
  subject: 'Физика',
  topic: 'Сила',
  question: 'Какая сила действует?',
  question_id: 'q1',
  student_answer: 'Ньютон',
  correct: 1,
  feedback: 'Верно',
  ...over,
})

beforeEach(() => {
  api.getKnowledgeGraph.mockReset()
  api.getReview.mockReset()
  api.records.mockReset()
  api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
  api.records.mockResolvedValue({ records: [] })
})

describe('StudentKGPanel helpers', () => {
  it('statusMeta labels и порядок сортировки', () => {
    expect(statusMeta.mastered.label).toBe('Освоено')
    expect(statusMeta.in_progress.label).toBe('В процессе')
    expect(statusMeta.not_studied.label).toBe('Не изучалось')
    expect(statusMeta.mastered.color).toBe('#4ade80')
    expect(statusMeta.in_progress.order).toBeLessThan(statusMeta.not_studied.order)
    expect(statusMeta.not_studied.order).toBeLessThan(statusMeta.mastered.order)
  })

  it('masteryColor thresholds', () => {
    expect(masteryColor(0.8)).toBe('high')
    expect(masteryColor(0.5)).toBe('mid')
    expect(masteryColor(0.2)).toBe('low')
  })
})

describe('<StudentKGPanel/>', () => {
  it('группирует темы по предметам; активный предмет сессии раскрыт первым', async () => {
    api.getKnowledgeGraph.mockResolvedValue(
      data({
        'Сила': topic(),
        'Дроби': topic({ topic: 'Дроби', subject: 'Математика', status: 'in_progress' }),
      }),
      { mastered: 1, in_progress: 1, not_studied: 0, total: 2 },
    )
    const { container } = render(<StudentKGPanel studentId="stu_x" subject="Математика" />)

    await screen.findByRole('button', { name: /^Математика/ })
    const groups = [...container.querySelectorAll('.kg-subj-group')]
    expect(groups[0].querySelector('.kg-subj-name').textContent).toBe('Математика')
    expect(groups[0].querySelector('.kg-topic-name')).not.toBeNull()
    expect(groups[1].querySelector('.kg-topic-name')).toBeNull()
  })

  it('чип-фильтр по предмету оставляет только его группы', async () => {
    const user = userEvent.setup()
    api.getKnowledgeGraph.mockResolvedValue(
      data({
        'Сила': topic(),
        'Дроби': topic({ topic: 'Дроби', subject: 'Математика', status: 'in_progress' }),
      }),
      { mastered: 1, in_progress: 1, not_studied: 0, total: 2 },
    )
    const { container } = render(<StudentKGPanel studentId="stu_x" />)

    await screen.findByText('Сила')
    await user.click(screen.getByRole('button', { name: /^Математика/ }))
    expect(container.querySelectorAll('.kg-subj-group').length).toBe(1)
  })

  it('«Вопросы и ответы»: верный и неверный ответ; разворачивание по клику', async () => {
    const user = userEvent.setup()
    const wrong = record({
      ts: 2,
      question_id: 'q2',
      question: 'Что такое вектор?',
      student_answer: 'Число',
      correct: 0,
      feedback: 'Неверно. Вектор — направленный отрезок.',
    })
    api.getKnowledgeGraph.mockResolvedValue(data())
    api.records.mockResolvedValue({ records: [wrong, record()] })

    render(<StudentKGPanel studentId="stu_x" />)
    await screen.findByText('Сила')
    await user.click(screen.getByRole('button', { name: /Вопросы и ответы \(2\)/ }))

    expect(screen.getByText('Какая сила действует?')).toBeInTheDocument()
    expect(screen.getByText('Ваш ответ: Ньютон · верно')).toBeInTheDocument()
    expect(screen.getByText('Что такое вектор?')).toBeInTheDocument()
    expect(screen.getByText('Ваш ответ: Число · неверно')).toBeInTheDocument()
    expect(screen.getByText(/Неверно\. Вектор/)).toBeInTheDocument()
  })

  it('у темы без записей — «Вопросы и ответы (0)» и подсказка', async () => {
    const user = userEvent.setup()
    const empty = topic({ topic: 'Пустая', status: 'not_studied', attempts: 0 })
    api.getKnowledgeGraph.mockResolvedValue(
      data({ 'Пустая': empty }, { mastered: 0, in_progress: 0, not_studied: 1, total: 1 }),
    )
    render(<StudentKGPanel studentId="stu_x" />)
    await screen.findByText('Пустая')
    await user.click(screen.getByRole('button', { name: '▸ Вопросы и ответы (0)' }))
    expect(screen.getByText('По этой теме записей пока нет.')).toBeInTheDocument()
  })

  it('рендерит тему «Освоено», статы и кнопку «Повторить (N)»; клик зовёт onStartReview', async () => {
    const user = userEvent.setup()
    const onStartReview = vi.fn()
    api.getKnowledgeGraph.mockResolvedValue(data())
    api.getReview.mockResolvedValue({ stats: { due: 2 }, due: [] })
    const { container } = render(<StudentKGPanel studentId="stu_x" onStartReview={onStartReview} />)

    expect(await screen.findByText('Сила')).toBeInTheDocument()
    const row = container.querySelector('.kg-topic')
    expect(within(row).getByText('Освоено')).toBeInTheDocument()
    expect(screen.getByText('всего')).toBeInTheDocument()
    expect(screen.getByText('освоено')).toBeInTheDocument()
    expect(screen.getByText('3/3 · 100%')).toBeInTheDocument()

    const review = screen.getByRole('button', { name: 'Повторить (2)' })
    await user.click(review)
    expect(onStartReview).toHaveBeenCalledTimes(1)
  })

  it('кнопка повторения disabled при busy', async () => {
    api.getKnowledgeGraph.mockResolvedValue(data())
    api.getReview.mockResolvedValue({ stats: { due: 2 }, due: [] })
    render(<StudentKGPanel studentId="stu_x" onStartReview={vi.fn()} busy />)
    const review = await screen.findByRole('button', { name: 'Повторить (2)' })
    expect(review).toBeDisabled()
  })

  it('скрывает кнопку, когда due=0', async () => {
    api.getKnowledgeGraph.mockResolvedValue(data())
    api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
    render(<StudentKGPanel studentId="stu_x" onStartReview={vi.fn()} />)
    await screen.findByText('Сила')
    expect(screen.queryByRole('button', { name: /Повторить/ })).not.toBeInTheDocument()
  })

  it('показывает слабые места + onStudy кликом по теме', async () => {
    const user = userEvent.setup()
    const onStudy = vi.fn()
    api.getKnowledgeGraph.mockResolvedValue(
      data({
        'Сила': topic({ status: 'in_progress', weak_areas: ['Ньютон', 'Векторы', 'Трение'] }),
      }),
    )
    render(<StudentKGPanel studentId="stu_x" onStudy={onStudy} />)
    expect(await screen.findByText(/слабые: Ньютон, Векторы…/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Сила' }))
    expect(onStudy).toHaveBeenCalledWith('Сила')
  })

  it('empty-state, когда тем нет', async () => {
    api.getKnowledgeGraph.mockResolvedValue(data({}, { mastered: 0, in_progress: 0, not_studied: 0, total: 0 }))
    render(<StudentKGPanel studentId="stu_x" />)
    expect(
      await screen.findByText('Тем пока нет. Пройдите квиз по теме — знания накопятся.'),
    ).toBeInTheDocument()
  })

  it('ничего не рендерит без studentId', () => {
    api.getKnowledgeGraph.mockResolvedValue(data())
    const { container } = render(<StudentKGPanel studentId="" />)
    expect(container.querySelector('.kg-panel')).toBeNull()
  })
})