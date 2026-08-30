import { render, screen } from '@testing-library/react'
import { Pill } from './Pill'

test('a failing pill announces its state to a screen reader', () => {
  render(<Pill kind="fail">1 failing</Pill>)
  expect(screen.getByText('1 failing')).toBeInTheDocument()
})

test('the pill kind drives the class, not an inline colour', () => {
  const { container } = render(<Pill kind="pass">All held</Pill>)
  expect(container.firstChild).toHaveClass('pill', 'pass')
})
