import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Category } from '../api/categories'
import { TransactionForm } from './TransactionForm'

const categories: Category[] = [
  { id: 'groceries', name: 'Groceries', created_at: '', updated_at: '' },
  { id: 'travel', name: 'Travel', created_at: '', updated_at: '' },
]

function renderForm(onSuggestCategory: (description: string, amount: string) => Promise<string | null>) {
  render(
    <TransactionForm
      categories={categories}
      isSubmitting={false}
      error={null}
      onSubmit={vi.fn()}
      onSuggestCategory={onSuggestCategory}
      onCancel={vi.fn()}
    />,
  )
}

describe('TransactionForm category suggestions', () => {
  it('selects a matching suggested category', async () => {
    const suggest = vi.fn().mockResolvedValue('Groceries')
    renderForm(suggest)

    fireEvent.change(screen.getByLabelText('Amount (negative = money out)'), {
      target: { value: '-12.50' },
    })
    fireEvent.change(screen.getByLabelText('Description'), {
      target: { value: 'Market' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Suggest category' }))

    await waitFor(() => expect(screen.getByLabelText('Category (optional)')).toHaveValue('groceries'))
    expect(suggest).toHaveBeenCalledWith('Market', '-12.50')
  })

  it('shows an error when the suggestion is not one of the categories', async () => {
    renderForm(vi.fn().mockResolvedValue('Unknown'))

    fireEvent.change(screen.getByLabelText('Amount (negative = money out)'), {
      target: { value: '-12.50' },
    })
    fireEvent.change(screen.getByLabelText('Description'), {
      target: { value: 'Mystery purchase' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Suggest category' }))

    expect(await screen.findByText('No matching category suggestion was found.')).toBeInTheDocument()
  })
})
