import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import KnowledgeWikiPanel, { EMPTY_TEXT } from './KnowledgeWikiPanel'
import MasteryWall, { masteryClass } from './MasteryWall'
import api from '../api'

vi.mock('../api', () => ({
  default: {
    wiki: vi.fn(),
    wikiArticle: vi.fn(),
    enrichWiki: vi.fn(),
    deleteWiki: vi.fn(),
    exportCsv: vi.fn(),
    exportSummary: vi.fn(),
    exportOkf: vi.fn(),
  },
}))

const lit = (over = {}) => ({
  subject: 'Литература',
  topic: 'Поэты Серебряного века',
  title: 'Поэты Серебряного века',
  mastery: 0.76,
  accuracy: 0.67,
  attempts: 3,
  body: '## Конспект\n\nМатериал по теме накоплен.',
  ...over,
})

function litGroup(article = lit()) {
  return { subject: article.subject, articles: [article] }
}

beforeEach(() => {
  api.wiki.mockReset()
  api.wikiArticle.mockReset()
  api.enrichWiki.mockReset()
  api.deleteWiki.mockReset()
  api.exportCsv.mockReset()
  api.exportSummary.mockReset()
  api.exportOkf.mockReset()
})

describe('MasteryWall helpers', () => {
  it('masteryClass пороги, включая границы 0.75 / 0.45', () => {
    expect(masteryClass(0.8)).toBe('high')
    expect(masteryClass(0.75)).toBe('high')
    expect(masteryClass(0.74)).toBe('mid')
    expect(masteryClass(0.5)).toBe('mid')
    expect(masteryClass(0.45)).toBe('mid')
    expect(masteryClass(0.44)).toBe('low')
    expect(masteryClass(0.4)).toBe('low')
    expect(masteryClass(NaN)).toBe('low')
  })
})

describe('<MasteryWall/> reuse', () => {
  it('клик по ячейке зовёт onSelect с темой', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<MasteryWall topics={[{ topic: 'Поэты', subject: 'Литература', mastery: 0.76 }]} onSelect={onSelect} />)
    await user.click(screen.getByRole('button', { name: 'Поэты' }))
    expect(onSelect).toHaveBeenCalledWith('Поэты')
  })
})

describe('<KnowledgeWikiPanel/>', () => {
  it('пустое состояние: конспектов нет', async () => {
    api.wiki.mockResolvedValue({ subjects: [] })
    render(<KnowledgeWikiPanel studentId="stu_x" />)
    expect(await screen.findByText(EMPTY_TEXT)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '⬇ CSV' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '⬇ Сводка' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'OKF' })).toBeInTheDocument()
  })

  it('ничего не рендерит без studentId', () => {
    api.wiki.mockResolvedValue({ subjects: [litGroup()] })
    const { container } = render(<KnowledgeWikiPanel studentId="" />)
    expect(container.querySelector('.wiki-panel')).toBeNull()
    expect(api.wiki).not.toHaveBeenCalled()
  })

  it('рендерит группу: subject, бейдж 76%, попыток; клик по названию открывает ридер', async () => {
    const user = userEvent.setup()
    const article = lit()
    api.wiki.mockResolvedValue({ subjects: [litGroup(article)] })
    api.wikiArticle.mockResolvedValue({ ...article })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    expect(await screen.findByText('Литература')).toBeInTheDocument()
    expect(screen.getByText('76%')).toBeInTheDocument()
    expect(screen.getByText('попыток: 3')).toBeInTheDocument()

    const row = container.querySelector('.wiki-article-row')
    await user.click(within(row).getByRole('button', { name: 'Поэты Серебряного века' }))
    expect(api.wikiArticle).toHaveBeenCalledWith('stu_x', 'Литература', 'Поэты Серебряного века')

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Материал по теме накоплен.')).toBeInTheDocument()
  })

  it('клик по ячейке MasteryWall открывает статью', async () => {
    const user = userEvent.setup()
    const article = lit()
    api.wiki.mockResolvedValue({ subjects: [litGroup(article)] })
    api.wikiArticle.mockResolvedValue({ ...article })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    await screen.findByText('Литература')
    const wall = container.querySelector('.mastery-wall')
    const cell = within(wall).getByRole('button', { name: 'Поэты Серебряного века' })
    await user.click(cell)
    expect(api.wikiArticle).toHaveBeenCalledWith('stu_x', 'Литература', 'Поэты Серебряного века')
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('«Обогатить конспект» при пустом теле: зовёт enrichWiki и перезагружает список', async () => {
    const user = userEvent.setup()
    const article = lit({ body: '' })
    api.wiki.mockResolvedValue({ subjects: [litGroup(article)] })
    api.wikiArticle.mockResolvedValue({ ...article })
    api.enrichWiki.mockResolvedValue({
      note: 'Конспект обогащён',
      article: { ...article, body: 'Обогащённый конспект по теме.' },
    })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    await screen.findByText('Литература')
    const row = container.querySelector('.wiki-article-row')
    await user.click(within(row).getByRole('button', { name: 'Поэты Серебряного века' }))
    const enrich = await screen.findByRole('button', { name: 'Обогатить конспект' })
    await user.click(enrich)

    expect(api.enrichWiki).toHaveBeenCalledWith('stu_x', 'Литература', 'Поэты Серебряного века')
    expect(await screen.findByText('Обогащённый конспект по теме.')).toBeInTheDocument()
    expect(screen.getByText('Конспект обогащён')).toBeInTheDocument()
    expect(api.wiki).toHaveBeenCalledTimes(2)
  })

  it('удаление конспекта: deleteWiki и перезагрузка списка', async () => {
    const user = userEvent.setup()
    api.wiki.mockResolvedValue({ subjects: [litGroup()] })
    api.deleteWiki.mockResolvedValue({ deleted: true })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    await screen.findByText('Литература')
    const row = container.querySelector('.wiki-article-row')
    await user.click(within(row).getByRole('button', { name: 'Удалить Поэты Серебряного века' }))
    expect(api.deleteWiki).toHaveBeenCalledWith('stu_x', 'Литература', 'Поэты Серебряного века')
    expect(api.wiki).toHaveBeenCalledTimes(2)
  })
})
