import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import Latex from './Latex'

describe('<Latex/>', () => {
  it('renders heading and paragraph text', () => {
    render(<Latex text={'# Тема\n\nОбъяснение текста'} />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Тема')
    expect(screen.getByText('Объяснение текста')).toBeInTheDocument()
  })
  it('renders list items', () => {
    render(<Latex text={'- первый\n- второй'} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })
  it('renders latex without crashing on broken formula', () => {
    const { container } = render(<Latex text={'Формула $$\\frac{ незакрытая$$'} />)
    expect(container.querySelector('.latex-display')).toBeInTheDocument()
  })
})
