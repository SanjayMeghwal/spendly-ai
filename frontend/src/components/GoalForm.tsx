import { useState, type FormEvent } from 'react'
import type { Category } from '../api/categories'
import type { Goal, GoalCreateInput } from '../api/goals'

interface GoalFormProps {
  categories: Category[]
  initial?: Goal
  isSubmitting: boolean
  error: string | null
  onSubmit: (input: GoalCreateInput) => void
  onCancel: () => void
}

const AMOUNT_PATTERN = /^\d{1,10}(\.\d{1,2})?$/

export function GoalForm({
  categories,
  initial,
  isSubmitting,
  error,
  onSubmit,
  onCancel,
}: GoalFormProps) {
  const [categoryId, setCategoryId] = useState(initial?.category_id ?? '')
  const [targetAmount, setTargetAmount] = useState(initial?.target_amount ?? '')
  const [targetDate, setTargetDate] = useState(initial?.target_date ?? '')
  const [amountError, setAmountError] = useState<string | null>(null)

  function handleSubmit(event: FormEvent) {
    event.preventDefault()

    if (!AMOUNT_PATTERN.test(targetAmount)) {
      setAmountError('Enter a positive amount like 5000.00 (up to 2 decimal places).')
      return
    }
    setAmountError(null)

    onSubmit({
      category_id: categoryId,
      target_amount: targetAmount,
      // Empty means "no deadline" - null on the wire, matching
      // GoalUpdate.target_date's clear-by-null semantics. On create it's
      // simply omitted-equivalent, since the field is optional there too.
      target_date: targetDate || null,
    })
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-6"
    >
      <h2 className="text-lg font-semibold text-slate-900">
        {initial ? 'Edit goal' : 'New goal'}
      </h2>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="space-y-1">
        <span className="block text-sm text-slate-700">Category</span>
        {initial ? (
          // A goal's category isn't editable here - changing it is a rare
          // edge case (and could 409 against another goal), so this form
          // only touches amount/date once a goal exists. Delete and
          // recreate for a category change.
          <p className="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
            {initial.category_name}
          </p>
        ) : (
          <select
            required
            value={categoryId}
            onChange={(e) => setCategoryId(e.target.value)}
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
          >
            <option value="" disabled>
              Choose…
            </option>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="space-y-1">
        <label htmlFor="targetAmount" className="block text-sm text-slate-700">
          Target amount
        </label>
        <input
          id="targetAmount"
          type="text"
          inputMode="decimal"
          required
          value={targetAmount}
          onChange={(e) => setTargetAmount(e.target.value)}
          placeholder="5000.00"
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm"
        />
        {amountError && <p className="text-sm text-red-600">{amountError}</p>}
      </div>

      <div className="space-y-1">
        <label htmlFor="targetDate" className="block text-sm text-slate-700">
          Target date <span className="text-slate-400">(optional)</span>
        </label>
        <input
          id="targetDate"
          type="date"
          value={targetDate ?? ''}
          onChange={(e) => setTargetDate(e.target.value)}
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
