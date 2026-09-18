import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import KnowledgeWikiPanel, { EMPTY_TEXT } from './KnowledgeWikiPanel'
import { masteryClass } from './MasteryWall'
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
  last_studied: '2026-09-15T10:00:00',
  body: '## Конспект\n\nМатериал по теме накоплен.',
  ...over,
})

const litGroup = (article = lit()) => ({ subject: article.subject, articles: [article] })

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
  it('masteryClass пороги', () => {
    expect(masteryClass(0.8)).toBe('high')
    expect(masteryClass(0.75)).toBe('high')
    expect(masteryClass(0.74)).toBe('mid')
    expect(masteryClass(0.44)).toBe('low')
    expect(masteryClass(NaN)).toBe('low')
  })
})

describe('<KnowledgeWikiPanel/>', () => {
  it('пустое состояние: конспектов нет', async () => {
    api.wiki.mockResolvedValue({ subjects: [] })
    render(<KnowledgeWikiPanel studentId="stu_x" />)
    expect(await screen.findByText(EMPTY_TEXT)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '⬇ Журнал (CSV)' })).toBeInTheDocument()
  })

  it('рендерит stats-бар, карточку с прогрессом и датой; клик открывает ридер', async () => {
    const user = userEvent.setup()
    const article = lit()
    api.wiki.mockResolvedValue({ subjects: [litGroup(article)] })
    api.wikiArticle.mockResolvedValue({ ...article })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    expect(await screen.findByRole('button', { name: /^Литература/ })).toBeInTheDocument()
    expect(container.querySelector('.wiki-pct').textContent).toBe('76%')
    expect(screen.getByText('попыток: 3', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('2026-09-15', { exact: false })).toBeInTheDocument()
    expect(container.querySelector('.wiki-stats')).not.toBeNull()

    await user.click(screen.getByRole('button', { name: 'Поэты Серебряного века' }))
    expect(api.wikiArticle).toHaveBeenCalledWith('stu_x', 'Литература', 'Поэты Серебряного века')
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('активный предмет сессии — первой группой и раскрытой; остальные свёрнуты', async () => {
    const math = lit({ subject: 'Математика', topic: 'Дроби', title: 'Дроби' })
    api.wiki.mockResolvedValue({
      subjects: [litGroup(), { subject: 'Математика', articles: [math] }],
    })
    api.wikiArticle.mockResolvedValue({ ...math })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" subject="Математика" />)

    await screen.findByRole('button', { name: /^Математика/ })
    const groups = [...container.querySelectorAll('.wiki-group')]
    expect(groups[0].querySelector('.wiki-subject').textContent).toBe('Математика')
    expect(groups[0].querySelector('.wiki-article-title')).not.toBeNull()
    expect(groups[1].querySelector('.wiki-article-title')).toBeNull()
  })

  it('чип-фильтр: клик по предмету оставляет только его', async () => {
    const user = userEvent.setup()
    const math = lit({ subject: 'Математика', topic: 'Дроби', title: 'Дроби' })
    api.wiki.mockResolvedValue({
      subjects: [litGroup(), { subject: 'Математика', articles: [math] }],
    })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    await screen.findByRole('button', { name: /^Математика/ })
    await user.click(screen.getByRole('button', { name: /^Математика/ }))
    expect(container.querySelectorAll('.wiki-group').length).toBe(1)
    expect([...container.querySelectorAll('.wiki-group')][0].querySelector('.wiki-subject').textContent).toBe('Математика')
  })

  it('поиск ищет по концепциям', async () => {
    const user = userEvent.setup()
    const a = lit({ concepts: ['Символизм'] })
    const b = lit({ topic: 'Другая тема', title: 'Другая тема' })
    api.wiki.mockResolvedValue({ subjects: [{ subject: 'Литература', articles: [a, b] }] })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    const titles = () =>
      [...container.querySelectorAll('.wiki-article-title')].map((n) => n.textContent)

    await screen.findByRole('button', { name: /^Литература/ })
    expect(titles().length).toBe(2)
    await user.type(screen.getByPlaceholderText('Поиск по конспектам…'), 'Символизм')
    expect(titles()).toEqual(['Поэты Серебряного века'])
  })

  it('сортировка по last_studied: свежие сверху', async () => {
    const old = lit({ topic: 'Старая тема', title: 'Старая тема', last_studied: '2026-09-01T00:00:00' })
    const fresh = lit({ topic: 'Свежая тема', title: 'Свежая тема', last_studied: '2026-09-18T00:00:00' })
    api.wiki.mockResolvedValue({ subjects: [{ subject: 'Литература', articles: [old, fresh] }] })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    const titles = () =>
      [...container.querySelectorAll('.wiki-article-title')].map((n) => n.textContent)

    await screen.findByRole('button', { name: /^Литература/ })
    expect(titles()).toEqual(['Свежая тема', 'Старая тема'])
  })

  it('«Обогатить конспект» видна при теле-заглушке', async () => {
    const user = userEvent.setup()
    const article = lit({ body: 'Материал по теме «Поэты» накапливается по мере прохождения квизов.' })
    api.wiki.mockResolvedValue({ subjects: [litGroup(article)] })
    api.wikiArticle.mockResolvedValue({ ...article })
    api.enrichWiki.mockResolvedValue({ note: 'ok', article: { ...article, body: 'Полный конспект.' } })
    render(<KnowledgeWikiPanel studentId="stu_x" />)

    await screen.findByRole('button', { name: /^Литература/ })
    await user.click(screen.getByRole('button', { name: 'Поэты Серебряного века' }))
    const enrich = await screen.findByRole('button', { name: 'Обогатить конспект' })
    await user.click(enrich)
    expect(api.enrichWiki).toHaveBeenCalledWith('stu_x', 'Литература', 'Поэты Серебряного века')
    expect(await screen.findByText('Полный конспект.')).toBeInTheDocument()
  })

  it('удаление конспекта: deleteWiki и перезагрузка', async () => {
    const user = userEvent.setup()
    api.wiki.mockResolvedValue({ subjects: [litGroup()] })
    api.deleteWiki.mockResolvedValue({ deleted: true })
    render(<KnowledgeWikiPanel studentId="stu_x" />)

    await screen.findByRole('button', { name: /^Литература/ })
    await user.click(screen.getByRole('button', { name: 'Удалить Поэты Серебряного века' }))
    expect(api.deleteWiki).toHaveBeenCalledWith('stu_x', 'Литература', 'Поэты Серебряного века')
    expect(api.wiki).toHaveBeenCalledTimes(2)
  })

  it('сворачивание subject: клик скрывает статьи предмета', async () => {
    const user = userEvent.setup()
    const math = lit({ subject: 'Математика', topic: 'Дроби', title: 'Дроби' })
    api.wiki.mockResolvedValue({ subjects: [litGroup(), { subject: 'Математика', articles: [math] }] })
    api.wikiArticle.mockResolvedValue({ ...lit(), subject: 'Литература' })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_x" />)

    await screen.findByRole('button', { name: /^Литература/ })
    const groupOf = (subj) =>
      [...container.querySelectorAll('.wiki-group')].find(
        (g) => g.querySelector('.wiki-subject')?.textContent === subj,
      )
    await user.click(within(groupOf('Литература')).getByRole('heading', { name: 'Литература' }))
    expect(groupOf('Литература').querySelector('.wiki-article-title')).toBeNull()
  })
})