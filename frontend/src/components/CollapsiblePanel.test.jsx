import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import CollapsiblePanel from './CollapsiblePanel'

describe('<CollapsiblePanel/>', () => {
  it('рендерит заголовок и содержимое', () => {
    render(
      <CollapsiblePanel title="Заголовок">
        <p>Содержимое панели</p>
      </CollapsiblePanel>,
    )
    expect(screen.getByText('Заголовок')).toBeInTheDocument()
    expect(screen.getByText('Содержимое панели')).toBeInTheDocument()
  })

  it('по умолчанию развёрнут (defaultOpen=true)', () => {
    const { container } = render(
      <CollapsiblePanel title="Заголовок">
        <p>Содержимое</p>
      </CollapsiblePanel>,
    )
    const body = container.querySelector('.collapsible-body')
    expect(body).toHaveClass('open')
    expect(screen.getByRole('button', { name: 'Заголовок ▾' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('свёрнут при defaultOpen=false', () => {
    const { container } = render(
      <CollapsiblePanel title="Заголовок" defaultOpen={false}>
        <p>Содержимое</p>
      </CollapsiblePanel>,
    )
    const body = container.querySelector('.collapsible-body')
    expect(body).not.toHaveClass('open')
    expect(screen.getByRole('button', { name: 'Заголовок ▾' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('клик на заголовок сворачивает панель', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <CollapsiblePanel title="Заголовок">
        <p>Содержимое</p>
      </CollapsiblePanel>,
    )
    // Убедимся, что открыта
    let body = container.querySelector('.collapsible-body')
    expect(body).toHaveClass('open')

    // Клик — сворачиваем
    await user.click(screen.getByRole('button', { name: 'Заголовок ▾' }))

    body = container.querySelector('.collapsible-body')
    expect(body).not.toHaveClass('open')
    expect(screen.getByRole('button', { name: 'Заголовок ▾' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('клик на заголовок разворачивает панель', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <CollapsiblePanel title="Заголовок" defaultOpen={false}>
        <p>Содержимое</p>
      </CollapsiblePanel>,
    )
    // Убедимся, что свёрнута
    let body = container.querySelector('.collapsible-body')
    expect(body).not.toHaveClass('open')

    // Клик — разворачиваем
    await user.click(screen.getByRole('button', { name: 'Заголовок ▾' }))

    body = container.querySelector('.collapsible-body')
    expect(body).toHaveClass('open')
    expect(screen.getByRole('button', { name: 'Заголовок ▾' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('содержимое скрыто в свёрнутом состоянии', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <CollapsiblePanel title="Заголовок" defaultOpen={false}>
        <div data-testid="panel-content">Важное содержимое</div>
      </CollapsiblePanel>,
    )
    // Свёрнута
    let body = container.querySelector('.collapsible-body')
    expect(body).not.toHaveClass('open')

    // Разворачиваем
    await user.click(screen.getByRole('button', { name: 'Заголовок ▾' }))
    body = container.querySelector('.collapsible-body')
    expect(body).toHaveClass('open')
    expect(screen.getByRole('button', { name: 'Заголовок ▾' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('присутствует CSS-класс collapsible-panel', () => {
    const { container } = render(
      <CollapsiblePanel title="Заголовок">
        <p>Содержимое</p>
      </CollapsiblePanel>,
    )
    const panel = container.querySelector('.collapsible-panel')
    expect(panel).toBeInTheDocument()
  })

  it('поддерживает badge справа от заголовка', () => {
    render(
      <CollapsiblePanel title="Квизы" badge={5}>
        <p>Список квизов</p>
      </CollapsiblePanel>,
    )
    expect(screen.getByText('5')).toBeInTheDocument()
  })

  it('поддерживает right-элемент', () => {
    render(
      <CollapsiblePanel title="Сессии" right={<span data-testid="right-badge">3 новых</span>}>
        <p>Список сессий</p>
      </CollapsiblePanel>,
    )
    expect(screen.getByText('3 новых')).toBeInTheDocument()
  })

  it('поддерживает пользовательский className', () => {
    const { container } = render(
      <CollapsiblePanel title="Заголовок" className="custom-class">
        <p>Содержимое</p>
      </CollapsiblePanel>,
    )
    const panel = container.querySelector('.collapsible-panel')
    expect(panel).toHaveClass('custom-class')
  })

  it('hideInnerHeader убирает внутреннюю шапку контента', () => {
    const { container } = render(
      <CollapsiblePanel title="Заголовок" hideInnerHeader>
        <p>Содержимое без заголовка</p>
      </CollapsiblePanel>,
    )
    const content = container.querySelector('.collapsible-content')
    expect(content).toHaveClass('hide-inner-header')
  })
})
