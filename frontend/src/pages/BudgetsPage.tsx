import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  createBudget,
  deleteBudget,
  listBudgets,
  updateBudget,
  type Budget,
} from '../api/budgets'
import { listCategories } from '../api/categories'
import { ApiError } from '../api/client'

const AMOUNT_PATTERN = /^\d{1,10}(\.\d{1,2})?$/

function formatAmount(amount: string): string {
  // Same display-only Number() use as transactions.ts's formatAmount -
  // never used for storage or calculation, only formatting.
  return Number(amount).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export function BudgetsPage() {
  const queryClient = useQueryClient()
  const [newCategoryId, setNewCategoryId] = useState('')
  const [newLimit, setNewLimit] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingLimit, setEditingLimit] = useState('')
  const [editError, setEditError] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Budget | null>(null)

  const budgetsQuery = useQuery({ queryKey: ['budgets'], queryFn: listBudgets })
  const categoriesQuery = useQuery({ queryKey: ['categories'], queryFn: listCategories })

  const createMutation = useMutation({
    mutationFn: createBudget,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['budgets'] })
      setNewCategoryId('')
      setNewLimit('')
      setCreateError(null)
    },
    onError: (err) => setCreateError(err instanceof ApiError ? err.message : 'Something went wrong.'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, limit_amount }: { id: string; limit_amount: string }) =>
      updateBudget(id, { limit_amount }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['budgets'] })
      setEditingId(null)
      setEditError(null)
    },
    onError: (err) => setEditError(err instanceof ApiError ? err.message : 'Something went wrong.'),
  })

  const deleteMutation = useMutation({
    mutationFn: deleteBudget,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['budgets'] })
      setDeleteTarget(null)
    },
  })

  function handleCreate(event: FormEvent) {
    event.preventDefault()
    if (!AMOUNT_PATTERN.test(newLimit)) {
      setCreateError('Enter a positive amount like 500.00 (up to 2 decimal places).')
      return
    }
    createMutation.mutate({ category_id: newCategoryId, limit_amount: newLimit })
  }

  function startEditing(budget: Budget) {
    setEditingId(budget.id)
    setEditingLimit(budget.limit_amount)
    setEditError(null)
  }

  function handleSaveEdit(event: FormEvent) {
    event.preventDefault()
    if (!editingId) return
    if (!AMOUNT_PATTERN.test(editingLimit)) {
      setEditError('Enter a positive amount like 500.00 (up to 2 decimal places).')
      return
    }
    updateMutation.mutate({ id: editingId, limit_amount: editingLimit })
  }

  const categories = categoriesQuery.data ?? []
  const budgets = budgetsQuery.data ?? []
  // A budget already exists per category (the backend enforces this with a
  // 409) - only offer categories that don't have one yet, so the common
  // case never hits that error.
  const budgetedCategoryIds = new Set(budgets.map((b) => b.category_id))
  const availableCategories = categories.filter((c) => !budgetedCategoryIds.has(c.id))

  return (
    <main className="mx-auto max-w-2xl p-6">
      <Link to="/" className="text-sm text-slate-500 underline">
        ← Dashboard
      </Link>
      <h1 className="mb-1 text-2xl font-semibold text-slate-900">Budgets</h1>
      <p className="mb-6 text-sm text-slate-500">This month's spending limits, by category.</p>

      <form onSubmit={handleCreate} className="mb-6 flex flex-wrap gap-2">
        <select
          required
          value={newCategoryId}
          onChange={(e) => setNewCategoryId(e.target.value)}
          className="rounded border border-slate-300 px-3 py-2 text-sm"
        >
          <option value="" disabled>
            Category…
          </option>
          {availableCategories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <input
          type="text"
          inputMode="decimal"
          required
          placeholder="500.00"
          value={newLimit}
          onChange={(e) => setNewLimit(e.target.value)}
          className="w-32 rounded border border-slate-300 px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={createMutation.isPending || availableCategories.length === 0}
          className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          Add budget
        </button>
      </form>
      {createError && <p className="mb-4 text-sm text-red-600">{createError}</p>}
      {categoriesQuery.isSuccess && categories.length === 0 && (
        <p className="mb-4 text-sm text-slate-500">
          You need a category before you can set a budget. <Link to="/categories" className="underline">Add one.</Link>
        </p>
      )}

      {budgetsQuery.isLoading && <p className="text-slate-500">Loading…</p>}
      {budgetsQuery.isError && (
        <p className="text-red-600">Could not load budgets. Try reloading the page.</p>
      )}
      {budgetsQuery.isSuccess && budgets.length === 0 && (
        <p className="text-slate-500">No budgets yet.</p>
      )}

      <ul className="space-y-3">
        {budgets.map((b) => {
          const isOverBudget = b.remaining.startsWith('-')
          return (
            <li key={b.id} className="rounded border border-slate-200 p-4">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-medium text-slate-900">{b.category_name}</span>
                <div className="flex gap-3 text-sm">
                  <button
                    type="button"
                    onClick={() => startEditing(b)}
                    className="text-slate-500 underline"
                  >
                    Edit limit
                  </button>
                  <button
                    type="button"
                    onClick={() => setDeleteTarget(b)}
                    className="text-red-600 underline"
                  >
                    Delete
                  </button>
                </div>
              </div>

              {editingId === b.id ? (
                <form onSubmit={handleSaveEdit} className="flex items-center gap-2">
                  <input
                    type="text"
                    inputMode="decimal"
                    required
                    value={editingLimit}
                    onChange={(e) => setEditingLimit(e.target.value)}
                    autoFocus
                    className="w-32 rounded border border-slate-300 px-2 py-1 text-sm"
                  />
                  <button
                    type="submit"
                    disabled={updateMutation.isPending}
                    className="text-sm text-slate-900 underline disabled:opacity-50"
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditingId(null)}
                    className="text-sm text-slate-500 underline"
                  >
                    Cancel
                  </button>
                </form>
              ) : (
                <>
                  <div className="mb-1 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className={`h-full ${isOverBudget ? 'bg-red-600' : 'bg-slate-900'}`}
                      style={{
                        width: `${Math.min(100, (Number(b.spent) / Number(b.limit_amount)) * 100)}%`,
                      }}
                    />
                  </div>
                  <p className="text-sm text-slate-600">
                    ${formatAmount(b.spent)} of ${formatAmount(b.limit_amount)} spent —{' '}
                    <span className={isOverBudget ? 'text-red-600' : 'text-green-600'}>
                      {isOverBudget ? 'over by' : ''} ${formatAmount(b.remaining.replace('-', ''))}{' '}
                      {isOverBudget ? '' : 'remaining'}
                    </span>
                  </p>
                </>
              )}
              {editingId === b.id && editError && (
                <p className="mt-1 text-sm text-red-600">{editError}</p>
              )}
            </li>
          )
        })}
      </ul>

      {deleteTarget && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-6">
            <p className="text-slate-900">
              Delete the budget for <strong>{deleteTarget.category_name}</strong>?
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate(deleteTarget.id)}
                className="flex-1 rounded bg-red-600 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
              </button>
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="flex-1 rounded border border-slate-300 py-2 text-sm text-slate-700"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  )
}
