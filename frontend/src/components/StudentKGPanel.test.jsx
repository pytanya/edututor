import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import StudentKGPanel, { statusMeta, masteryColor } from './StudentKGPanel'
import api from '../api'

vi.mock('../api', () => ({
  default: {
    getKnowledgeGraph: vi.fn(),
    getReview: vi.fn(),
  },
}))

const topic = (over = {}) => ({
  topic: 'Сила',
  subject: 'физика',
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
  return { student_id: 'stu_x', subject: 'физика', topics, stats }
}

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
  beforeEach(() => {
    api.getKnowledgeGraph.mockReset()
    api.getReview.mockReset()
  })

  it('рендерит тему «Освоено» и кнопку «Повторить (N)»; клик зовёт onStartReview', async () => {
    const user = userEvent.setup()
    const onStartReview = vi.fn()
    api.getKnowledgeGraph.mockResolvedValue(data())
    api.getReview.mockResolvedValue({ stats: { due: 2 }, due: [] })
    render(<StudentKGPanel studentId="stu_x" onStartReview={onStartReview} />)

    expect(await screen.findByText('Сила')).toBeInTheDocument()
    expect(screen.getByText('Освоено')).toBeInTheDocument()
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

  it('скрывает кнопку, когда due=0 или onStartReview не задан', async () => {
    api.getKnowledgeGraph.mockResolvedValue(data())
    api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
    render(<StudentKGPanel studentId="stu_x" onStartReview={vi.fn()} />)
    await screen.findByText('Сила')
    expect(screen.queryByRole('button', { name: /Повторить/ })).not.toBeInTheDocument()
  })

  it('сортирует темы: in_progress → not_studied → mastered, внутри last_seen desc', async () => {
    api.getKnowledgeGraph.mockResolvedValue(
      data(
        {
          'Освоенная': topic({ topic: 'Освоенная', status: 'mastered', last_seen: 9 }),
          'Начатая': topic({ topic: 'Начатая', status: 'in_progress', last_seen: 3 }),
          'Новая': topic({ topic: 'Новая', status: 'not_studied', last_seen: 7 }),
        },
        { mastered: 1, in_progress: 1, not_studied: 1, total: 3 },
      ),
    )
    api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
    const { container } = render(<StudentKGPanel studentId="stu_x" />)
    await screen.findByText('Начатая')
    const names = [...container.querySelectorAll('.kg-topic-name')].map((n) => n.textContent)
    expect(names).toEqual(['Начатая', 'Новая', 'Освоенная'])
  })

  it('внутри статуса — last_seen desc', async () => {
    api.getKnowledgeGraph.mockResolvedValue(
      data(
        {
          'Старая': topic({ topic: 'Старая', status: 'in_progress', last_seen: 1 }),
          'Свежая': topic({ topic: 'Свежая', status: 'in_progress', last_seen: 10 }),
        },
        { mastered: 0, in_progress: 2, not_studied: 0, total: 2 },
      ),
    )
    api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
    const { container } = render(<StudentKGPanel studentId="stu_x" />)
    await screen.findByText('Свежая')
    const names = [...container.querySelectorAll('.kg-topic-name')].map((n) => n.textContent)
    expect(names).toEqual(['Свежая', 'Старая'])
  })

  it('показывает слабые места (первые 2 + …)', async () => {
    api.getKnowledgeGraph.mockResolvedValue(
      data({
        'Сила': topic({ topic: 'Сила', status: 'in_progress', weak_areas: ['Ньютон', 'Векторы', 'Трение'] }),
      }),
    )
    api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
    render(<StudentKGPanel studentId="stu_x" />)
    expect(await screen.findByText(/слабые: Ньютон, Векторы…/)).toBeInTheDocument()
  })

  it('empty-state, когда тем нет', async () => {
    api.getKnowledgeGraph.mockResolvedValue(data({}, { mastered: 0, in_progress: 0, not_studied: 0, total: 0 }))
    api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
    render(<StudentKGPanel studentId="stu_x" />)
    expect(
      await screen.findByText('Тем пока нет. Пройдите квиз по теме — знания накопятся.'),
    ).toBeInTheDocument()
  })

  it('ничего не рендерит без studentId', () => {
    api.getKnowledgeGraph.mockResolvedValue(data())
    api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
    const { container } = render(<StudentKGPanel studentId="" />)
    expect(container.querySelector('.kg-panel')).toBeNull()
  })

  it('onStudy делает имя темы кнопкой, клик зовёт onStudy(topic)', async () => {
    const user = userEvent.setup()
    const onStudy = vi.fn()
    api.getKnowledgeGraph.mockResolvedValue(data())
    api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
    render(<StudentKGPanel studentId="stu_x" onStudy={onStudy} />)
    await user.click(await screen.findByRole('button', { name: 'Сила' }))
    expect(onStudy).toHaveBeenCalledWith('Сила')
  })

  it('перезагружает данные при смене subject/reloadKey', async () => {
    api.getKnowledgeGraph.mockResolvedValue(data())
    api.getReview.mockResolvedValue({ stats: { due: 0 }, due: [] })
    const { rerender } = render(<StudentKGPanel studentId="stu_x" subject="физика" />)
    await screen.findByText('Сила')
    rerender(<StudentKGPanel studentId="stu_x" subject="физика" reloadKey={1} />)
    expect(await screen.findByText('Сила')).toBeInTheDocument()
    expect(api.getKnowledgeGraph).toHaveBeenCalledTimes(2)
    expect(api.getKnowledgeGraph).toHaveBeenLastCalledWith('stu_x', 'физика')
  })
})
