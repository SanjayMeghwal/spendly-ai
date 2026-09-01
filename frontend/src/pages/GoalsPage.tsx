import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { listCategories } from '../api/categories'
import { ApiError } from '../api/client'
import { createGoal, deleteGoal, listGoals, updateGoal, type Goal, type GoalCreateInput } from '../api/goals'
import { GoalForm } from '../components/GoalForm'
import { formatMoney } from '../lib/format'

export function GoalsPage() {
  const queryClient = useQueryClient()
  const [formTarget, setFormTarget] = useState<'create' | Goal | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Goal | null>(null)

  const goalsQuery = useQuery({ queryKey: ['goals'], queryFn: listGoals })
  const categoriesQuery = useQuery({ queryKey: ['categories'], queryFn: listCategories })

  const createMutation = useMutation({
    mutationFn: createGoal,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['goals'] })
      setFormTarget(null)
      setFormError(null)
    },
    onError: (err) => setFormError(err instanceof ApiError ? err.message : 'Something went wrong.'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: GoalCreateInput }) => updateGoal(id, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['goals'] })
      setFormTarget(null)
      setFormError(null)
    },
    onError: (err) => setFormError(err instanceof ApiError ? err.message : 'Something went wrong.'),
  })

  const deleteMutation = useMutation({
    mutationFn: deleteGoal,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['goals'] })
      setDeleteTarget(null)
    },
  })

  function handleSubmit(input: GoalCreateInput) {
    if (formTarget && formTarget !== 'create') {
      // category_id never actually changes here - GoalForm keeps it fixed
      // to the goal's own category in edit mode - but the API still wants
      // it in the body since GoalCreateInput requires it.
      updateMutation.mutate({ id: formTarget.id, input })
    } else {
      createMutation.mutate(input)
    }
  }

  const goals = goalsQuery.data ?? []
  const categories = categoriesQuery.data ?? []
  const goaledCategoryIds = new Set(goals.map((g) => g.category_id))
  const availableCategories = categories.filter((c) => !goaledCategoryIds.has(c.id))

  return (
    <main className="mx-auto max-w-2xl p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <Link to="/" className="text-sm text-slate-500 underline">
            ← Dashboard
          </Link>
          <h1 className="text-2xl font-semibold text-slate-900">Goals</h1>
        </div>
        <button
          type="button"
          disabled={categoriesQuery.isSuccess && availableCategories.length === 0}
          onClick={() => {
            setFormError(null)
            setFormTarget('create')
          }}
          className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          New goal
        </button>
      </div>

      {categoriesQuery.isSuccess && categories.length === 0 && (
        <p className="mb-4 text-sm text-slate-500">
          You need a category before you can set a goal.{' '}
          <Link to="/categories" className="underline">
            Add one.
          </Link>
        </p>
      )}

      {goalsQuery.isLoading && <p className="text-slate-500">Loading…</p>}
      {goalsQuery.isError && (
        <p className="text-red-600">Could not load goals. Try reloading the page.</p>
      )}
      {goalsQuery.isSuccess && goals.length === 0 && <p className="text-slate-500">No goals yet.</p>}

      <ul className="space-y-3">
        {goals.map((g) => {
          const pct = Math.max(
            0,
            Math.min(100, (Number(g.progress) / Number(g.target_amount)) * 100),
          )
          const isMet = Number(g.remaining) <= 0
          return (
            <li key={g.id} className="rounded border border-slate-200 p-4">
              <div className="mb-2 flex items-center justify-between">
                <div>
                  <span className="font-medium text-slate-900">{g.category_name}</span>
                  {g.target_date && (
                    <span className="ml-2 text-sm text-slate-500">by {g.target_date}</span>
                  )}
                </div>
                <div className="flex gap-3 text-sm">
                  <button
                    type="button"
                    onClick={() => {
                      setFormError(null)
                      setFormTarget(g)
                    }}
                    className="text-slate-500 underline"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => setDeleteTarget(g)}
                    className="text-red-600 underline"
                  >
                    Delete
                  </button>
                </div>
              </div>

              <div className="mb-1 h-2 overflow-hidden rounded-full bg-slate-100">
                <div className="h-full bg-green-600" style={{ width: `${pct}%` }} />
              </div>
              <p className="text-sm text-slate-600">
                ${formatMoney(g.progress)} of ${formatMoney(g.target_amount)} saved —{' '}
                {isMet ? (
                  <span className="text-green-600">
                    Goal reached!{' '}
                    {Number(g.remaining) < 0 &&
                      `$${formatMoney(g.remaining.replace('-', ''))} extra saved`}
                  </span>
                ) : (
                  <span>${formatMoney(g.remaining)} to go</span>
                )}
              </p>
            </li>
          )
        })}
      </ul>

      {formTarget && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/30 p-4">
          <GoalForm
            categories={availableCategories}
            initial={formTarget === 'create' ? undefined : formTarget}
            isSubmitting={createMutation.isPending || updateMutation.isPending}
            error={formError}
            onSubmit={handleSubmit}
            onCancel={() => setFormTarget(null)}
          />
        </div>
      )}

      {deleteTarget && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-6">
            <p className="text-slate-900">
              Delete the goal for <strong>{deleteTarget.category_name}</strong>?
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
