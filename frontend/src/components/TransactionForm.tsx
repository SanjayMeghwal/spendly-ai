import { useState, type FormEvent } from 'react'
import type { Category } from '../api/categories'
import type { Transaction, TransactionCreateInput } from '../api/transactions'

interface TransactionFormProps {
  categories: Category[]
  initial?: Transaction
  isSubmitting: boolean
  error: string | null
  onSubmit: (input: TransactionCreateInput) => void
  onCancel: () => void
}

// Signed decimal string matching the backend's NUMERIC(12,2) column:
// optional '-', up to 10 integer digits, optional 2 decimal places.
const AMOUNT_PATTERN = /^-?\d{1,10}(\.\d{1,2})?$/

function toDateInputValue(isoDatetime: string): string {
  return isoDatetime.slice(0, 10)
}

export function TransactionForm({
  categories,
  initial,
  isSubmitting,
  error,
  onSubmit,
  onCancel,
}: TransactionFormProps) {
  const [amount, setAmount] = useState(initial?.amount ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [categoryId, setCategoryId] = useState(initial?.category_id ?? '')
  const [occurredAt, setOccurredAt] = useState(
    initial ? toDateInputValue(initial.occurred_at) : toDateInputValue(new Date().toISOString()),
  )
  const [notes, setNotes] = useState(initial?.notes ?? '')
  const [amountError, setAmountError] = useState<string | null>(null)

  function handleSubmit(event: FormEvent) {
    event.preventDefault()

    if (!AMOUNT_PATTERN.test(amount)) {
      setAmountError('Use a signed amount like -12.50 or 12.50 (up to 2 decimal places).')
      return
    }
    setAmountError(null)

    onSubmit({
      amount,
      description,
      category_id: categoryId || null,
      occurred_at: `${occurredAt}T00:00:00Z`,
      notes: notes || null,
    })
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-6"
    >
      <h2 className="text-lg font-semibold text-slate-900">
        {initial ? 'Edit transaction' : 'New transaction'}
      </h2>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="space-y-1">
        <label htmlFor="amount" className="block text-sm text-slate-700">
          Amount <span className="text-slate-400">(negative = money out)</span>
        </label>
        <input
          id="amount"
          type="text"
          inputMode="decimal"
          required
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="-12.50"
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
        />
        {amountError && <p className="text-sm text-red-600">{amountError}</p>}
      </div>

      <div className="space-y-1">
        <label htmlFor="description" className="block text-sm text-slate-700">
          Description
        </label>
        <input
          id="description"
          type="text"
          required
          maxLength={255}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
        />
      </div>

      <div className="space-y-1">
        <label htmlFor="category" className="block text-sm text-slate-700">
          Category <span className="text-slate-400">(optional)</span>
        </label>
        <select
          id="category"
          value={categoryId ?? ''}
          onChange={(e) => setCategoryId(e.target.value)}
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="">— none —</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </div>

      <div className="space-y-1">
        <label htmlFor="occurredAt" className="block text-sm text-slate-700">
          Date
        </label>
        <input
          id="occurredAt"
          type="date"
          required
          value={occurredAt}
          onChange={(e) => setOccurredAt(e.target.value)}
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
        />
      </div>

      <div className="space-y-1">
        <label htmlFor="notes" className="block text-sm text-slate-700">
          Notes <span className="text-slate-400">(optional)</span>
        </label>
        <textarea
          id="notes"
          value={notes ?? ''}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
        />
      </div>

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={isSubmitting}
          className="flex-1 rounded bg-slate-900 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {isSubmitting ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="flex-1 rounded border border-slate-300 py-2 text-sm text-slate-700"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}
