import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import api from './api'
import { deriveStudentId } from './identity'

vi.mock('./api', () => ({
  default: {
    chatStream: vi.fn(() => () => {}),
    history: vi.fn(() => Promise.reject(new Error('нет'))),
    student: vi.fn(() => Promise.reject(new Error('нет'))),
    studentSessions: vi.fn(() => Promise.resolve({ student_id: 'stu_x', sessions: [] })),
    profile: vi.fn(() => Promise.resolve({})),
    getKnowledgeGraph: vi.fn(() =>
      Promise.resolve({ topics: {}, stats: { total: 0, mastered: 0, in_progress: 0, not_studied: 0 } })),
    getReview: vi.fn(() => Promise.resolve({ stats: { due: 0 } })),
    getGraph: vi.fn(() => Promise.resolve({ nodes: [], edges: [] })),
    getRecommendations: vi.fn(() => Promise.resolve({ recommendations: [] })),
    wiki: vi.fn(() => Promise.resolve({ subjects: [] })),
    wikiArticle: vi.fn(() => Promise.resolve({})),
    enrichWiki: vi.fn(() => Promise.resolve({ article: null, note: '' })),
    deleteWiki: vi.fn(() => Promise.resolve({ deleted: true })),
  },
}))

function seedProfile() {
  localStorage.setItem('edututor_student', JSON.stringify({
    student_id: 'stu_x',
    student_name: 'Иван Иванов',
    learner_type: 'schoolchild',
    grade: '8 класс',
    identity: 'иван иванов|schoolchild|8 класс',
    legacy: false,
  }))
}

describe('<App/>', () => {
  beforeEach(() => {
    localStorage.clear()
    seedProfile()
    api.history.mockRejectedValue(new Error('нет'))
    api.student.mockRejectedValue(new Error('нет'))
    api.studentSessions.mockResolvedValue({ student_id: 'stu_x', sessions: [] })
    api.profile.mockResolvedValue({})
  })

  it('renders shell panels', async () => {
    render(<App />)
    expect(screen.getByText('Новое занятие')).toBeInTheDocument()
    expect(screen.getByText('Адаптивность')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('квадратные уравнения')).toBeInTheDocument()
    await screen.findByText('Пока пусто.')
  })

  it('lists server sessions and loads messages when one is picked', async () => {
    const user = userEvent.setup()
    api.studentSessions.mockResolvedValue({
      student_id: 'stu_x',
      sessions: [{ session_id: 's1', topic: 'Квадратные уравнения', started_at: '2026-09-01', ended_at: null }],
    })
    api.history.mockResolvedValue({
      session_id: 's1',
      messages: [
        { role: 'user', content: 'Помоги с темой' },
        { role: 'assistant', content: 'Давай разберём' },
      ],
    })
    render(<App />)
    const item = await screen.findByText('Квадратные уравнения')
    expect(item).toBeInTheDocument()
    await user.click(item)
    expect(await screen.findByText('Помоги с темой')).toBeInTheDocument()
    expect(api.history).toHaveBeenCalledWith('s1')
  })
})

describe('<App/> session lifecycle (баги привязки сессий)', () => {
  function installStream() {
    const bodies = []
    const emitters = []
    api.chatStream.mockImplementation((body, onEvent) => {
      bodies.push(body)
      emitters.push(onEvent)
      return () => {}
    })
    return {
      bodies,
      emit: (i, ev) => emitters[i](ev),
    }
  }

  async function startTopic(user, topic) {
    const input = screen.getByPlaceholderText('квадратные уравнения')
    await user.clear(input)
    await user.type(input, topic)
    await user.click(screen.getByText('Начать'))
  }

  function reply(sessionId, extra = {}) {
    return {
      event: 'message',
      data: {
        session_id: sessionId,
        content: `ответ по ${sessionId}`,
        envelope: { type: 'theory', text: `ответ по ${sessionId}` },
        adaptive: {},
        ...extra,
      },
    }
  }

  beforeEach(() => {
    localStorage.clear()
    seedProfile()
    api.history.mockRejectedValue(new Error('нет'))
    api.student.mockRejectedValue(new Error('нет'))
    api.studentSessions.mockResolvedValue({ student_id: 'stu_x', sessions: [] })
    api.profile.mockResolvedValue({})
    api.chatStream.mockReset()
    api.chatStream.mockImplementation(() => () => {})
  })

  it('startTopic: новая тема начинает НОВУЮ сессию, а не продолжает старую', async () => {
    const user = userEvent.setup()
    const { bodies, emit } = installStream()
    render(<App />)

    await startTopic(user, 'Тема A')
    expect(bodies).toHaveLength(1)
    expect(bodies[0].session_id).toBe('')
    act(() => {
      emit(0, reply('ses_A'))
      emit(0, { event: 'done', data: { session_id: 'ses_A' } })
    })
    await screen.findByText('ответ по ses_A')

    // Повторный запуск темы не должен уйти в старую сессию ses_A.
    await startTopic(user, 'Тема B')
    expect(bodies).toHaveLength(2)
    expect(bodies[1].session_id).not.toBe('ses_A')
    expect(bodies[1].session_id).toBe('')
    expect(bodies[1].topic).toBe('Тема B')
  })

  it('studyNext: «Изучить» рекомендацию начинает новую сессию', async () => {
    const user = userEvent.setup()
    const { bodies, emit } = installStream()
    render(<App />)

    await startTopic(user, 'Тема A')
    act(() => {
      emit(
        0,
        reply('ses_A', {
          adaptive: { student_id: 'stu_x', recommended_next: 'Тема C' },
        }),
      )
      emit(0, { event: 'done', data: { session_id: 'ses_A' } })
    })
    const studyBtn = await screen.findByRole('button', { name: /Изучить: Тема C/ })

    await user.click(studyBtn)
    expect(bodies).toHaveLength(2)
    expect(bodies[1].session_id).not.toBe('ses_A')
    expect(bodies[1].session_id).toBe('')
    expect(bodies[1].topic).toBe('Тема C')
  })

  it('review button starts review_request turn in current session', async () => {
    const user = userEvent.setup()
    const { bodies, emit } = installStream()
    api.getReview.mockResolvedValue({ stats: { due: 3 } })
    render(<App />)

    await startTopic(user, 'Тема A')
    act(() => {
      emit(
        0,
        reply('ses_A', {
          adaptive: { student_id: 'stu_x', review_due: 3 },
        }),
      )
      emit(0, { event: 'done', data: { session_id: 'ses_A' } })
    })
    const reviewBtn = await screen.findByRole('button', { name: /Повторить \(3\)/ })

    await user.click(reviewBtn)
    expect(bodies).toHaveLength(2)
    expect(bodies[1].kind).toBe('review_request')
    expect(bodies[1].message).toBe('')
    expect(bodies[1].session_id).toBe('ses_A')
  })

  it('mastery.gate system event renders a dismissible banner', async () => {
    const user = userEvent.setup()
    const { emit } = installStream()
    render(<App />)

    await startTopic(user, 'Тема A')
    act(() => {
      emit(0, {
        event: 'system',
        data: { kind: 'mastery.gate', message: 'Совет: сначала повторите «Сила».', gaps: ['Сила'], topic: 'Тема A' },
      })
    })
    expect(screen.getByText(/Совет: сначала повторите «Сила»/)).toBeInTheDocument()
    const dismiss = screen.getByRole('button', { name: 'Всё равно продолжить' })
    await user.click(dismiss)
    expect(screen.queryByText(/Совет: сначала повторите «Сила»/)).not.toBeInTheDocument()
  })

  it('mastery.gate gap button studies that topic in a fresh session', async () => {
    const user = userEvent.setup()
    const { bodies, emit } = installStream()
    render(<App />)

    await startTopic(user, 'Тема A')
    act(() => {
      emit(0, {
        event: 'system',
        data: { kind: 'mastery.gate', message: 'Совет: сначала повторите «Сила».', gaps: ['Сила'], topic: 'Тема A' },
      })
    })
    await user.click(screen.getByRole('button', { name: 'Перейти к «Сила»' }))
    expect(bodies).toHaveLength(2)
    expect(bodies[1].topic).toBe('Сила')
    expect(bodies[1].session_id).toBe('')
  })

  it('loadHistory при ошибке не записывает мёртвый session_id', async () => {
    const user = userEvent.setup()
    const { bodies } = installStream()
    localStorage.setItem('edututor_session', 'ses_dead')
    api.studentSessions.mockResolvedValue({
      student_id: 'stu_x',
      sessions: [{ session_id: 'ses_dead', topic: 'Старая тема', started_at: null, ended_at: null }],
    })
    api.history.mockRejectedValue(new Error('404: сессия не найдена'))
    render(<App />)

    await user.click(await screen.findByText('Старая тема'))
    await waitFor(() => expect(localStorage.getItem('edututor_session')).toBeNull())

    // Новый запрос стартует чистую сессию, а не мёртвую ses_dead.
    await startTopic(user, 'Свежая тема')
    expect(bodies[0].session_id).not.toBe('ses_dead')
    expect(bodies[0].session_id).toBe('')
  })
})

describe('<App/> онбординг (E3)', () => {
  function installStream() {
    const bodies = []
    api.chatStream.mockImplementation((body) => { bodies.push(body); return () => {} })
    return { bodies }
  }

  beforeEach(() => {
    localStorage.clear()
    api.history.mockRejectedValue(new Error('нет'))
    api.student.mockRejectedValue(new Error('нет'))
    api.studentSessions.mockResolvedValue({ student_id: 'stu_x', sessions: [] })
    api.chatStream.mockReset()
    api.chatStream.mockImplementation(() => () => {})
    api.profile.mockReset()
    api.profile.mockResolvedValue({})
  })

  it('новый пользователь видит карточку знакомства вместо формы', async () => {
    render(<App />)
    expect(await screen.findByText('Знакомство и план занятия')).toBeInTheDocument()
    expect(screen.queryByText('Новое занятие')).not.toBeInTheDocument()
  })

  it('сабмит карточки: resolveStudentId → profile POST → занятие под детерминированным id', async () => {
    const user = userEvent.setup()
    const { bodies } = installStream()
    render(<App />)

    const sid = deriveStudentId('Иван Иванов', 'schoolchild', '8 класс')
    await user.type(await screen.findByPlaceholderText('Иван Иванов'), 'Иван Иванов')
    await user.selectOptions(screen.getByLabelText('Ты школьник или студент?'), 'schoolchild')
    await user.type(screen.getByPlaceholderText('8 класс'), '8 класс')
    await user.type(screen.getByPlaceholderText('математика'), 'математика')
    await user.type(screen.getByPlaceholderText('квадратные уравнения'), 'квадратные уравнения')
    await user.click(screen.getByRole('button', { name: 'Начать занятие' }))

    await waitFor(() => expect(api.profile).toHaveBeenCalledWith(
      sid,
      { name: 'Иван Иванов', learner_type: 'schoolchild', grade: '8 класс' },
    ))
    expect(JSON.parse(localStorage.getItem('edututor_student')).student_id).toBe(sid)
    expect(bodies[0].student_id).toBe(sid)
    expect(bodies[0].topic).toBe('квадратные уравнения')
    expect(screen.getByText('Новое занятие')).toBeInTheDocument() // карточка закрыта
  })

  it('«Начать без карточки» скрывает карточку и не создаёт профиль', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Начать без карточки' }))
    expect(screen.getByText('Новое занятие')).toBeInTheDocument()
    expect(api.profile).not.toHaveBeenCalled()
    expect(JSON.parse(localStorage.getItem('edututor_student')).student_name).toBeUndefined()
  })

  it('вернувшийся ученик с полным профилем: сразу TopicForm + grade префилл', async () => {
    seedProfile()
    render(<App />)
    expect(await screen.findByText('Новое занятие')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('8 класс').value).toBe('8 класс')
  })
})
