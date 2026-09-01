import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { listCategories } from '../api/categories'
import { ApiError } from '../api/client'
import {
  createTransaction,
  deleteTransaction,
  listTransactions,
  updateTransaction,
  type Transaction,
  type TransactionCreateInput,
} from '../api/transactions'
import { TransactionForm } from '../components/TransactionForm'

const PAGE_SIZE = 20

function formatAmount(amount: string): string {
  // amount is a signed decimal string ("-12.50") - Number() here is only for
  // display formatting (adding a $ and thousands separators), never for
  // storage or calculation, so the precision loss it could theoretically
  // introduce never reaches anything that matters.
  const value = Number(amount)
  const formatted = Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return value < 0 ? `-$${formatted}` : `$${formatted}`
}

export function TransactionsPage() {
  const queryClient = useQueryClient()
  const [offset, setOffset] = useState(0)
  const [formTarget, setFormTarget] = useState<'create' | Transaction | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Transaction | null>(null)
  const [formError, setFormError] = useState<string | null>(null)

  const transactionsQuery = useQuery({
    queryKey: ['transactions', { limit: PAGE_SIZE, offset }],
    queryFn: () => listTransactions({ limit: PAGE_SIZE, offset }),
  })

  const categoriesQuery = useQuery({
    queryKey: ['categories'],
    queryFn: listCategories,
  })

  const createMutation = useMutation({
    mutationFn: createTransaction,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['transactions'] })
      setFormTarget(null)
      setFormError(null)
    },
    onError: (err) => setFormError(err instanceof ApiError ? err.message : 'Something went wrong.'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: TransactionCreateInput }) =>
      updateTransaction(id, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['transactions'] })
      setFormTarget(null)
      setFormError(null)
    },
    onError: (err) => setFormError(err instanceof ApiError ? err.message : 'Something went wrong.'),
  })

  const deleteMutation = useMutation({
    mutationFn: deleteTransaction,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['transactions'] })
      setDeleteTarget(null)
    },
  })

  function handleSubmit(input: TransactionCreateInput) {
    if (formTarget && formTarget !== 'create') {
      updateMutation.mutate({ id: formTarget.id, input })
    } else {
      createMutation.mutate(input)
    }
  }

  const categories = categoriesQuery.data ?? []
  const transactions = transactionsQuery.data ?? []

  return (
    <main className="mx-auto max-w-3xl p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <Link to="/" className="text-sm text-slate-500 underline">
            ← Dashboard
          </Link>
          <h1 className="text-2xl font-semibold text-slate-900">Transactions</h1>
        </div>
        <button
          type="button"
          onClick={() => {
            setFormError(null)
            setFormTarget('create')
          }}
          className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white"
        >
          New transaction
        </button>
      </div>

      {transactionsQuery.isLoading && <p className="text-slate-500">Loading…</p>}
      {transactionsQuery.isError && (
        <p className="text-red-600">Could not load transactions. Try reloading the page.</p>
      )}

      {transactionsQuery.isSuccess && transactions.length === 0 && offset === 0 && (
        <p className="text-slate-500">No transactions yet.</p>
      )}

      {transactions.length > 0 && (
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-slate-500">
              <th className="py-2 pr-2">Date</th>
              <th className="py-2 pr-2">Description</th>
              <th className="py-2 pr-2">Category</th>
              <th className="py-2 pr-2 text-right">Amount</th>
              <th className="py-2 pr-2"></th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((t) => (
              <tr key={t.id} className="border-b border-slate-100">
                <td className="py-2 pr-2 whitespace-nowrap text-slate-600">
                  {t.occurred_at.slice(0, 10)}
                </td>
                <td className="py-2 pr-2 text-slate-900">{t.description}</td>
                <td className="py-2 pr-2 text-slate-600">{t.category_name ?? '—'}</td>
                <td
                  className={`py-2 pr-2 text-right whitespace-nowrap ${
                    t.amount.startsWith('-') ? 'text-red-600' : 'text-green-600'
                  }`}
                >
                  {formatAmount(t.amount)}
                </td>
                <td className="py-2 pr-2 text-right whitespace-nowrap">
                  <button
                    type="button"
                    onClick={() => {
                      setFormError(null)
                      setFormTarget(t)
                    }}
                    className="text-slate-500 underline"
                  >
                    Edit
                  </button>{' '}
                  <button
                    type="button"
                    onClick={() => setDeleteTarget(t)}
                    className="text-red-600 underline"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="mt-4 flex justify-between text-sm">
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          className="text-slate-600 underline disabled:text-slate-300 disabled:no-underline"
        >
          ← Previous
        </button>
        <button
          type="button"
          disabled={transactions.length < PAGE_SIZE}
          onClick={() => setOffset(offset + PAGE_SIZE)}
          className="text-slate-600 underline disabled:text-slate-300 disabled:no-underline"
        >
          Next →
        </button>
      </div>

      {formTarget && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/30 p-4">
          <TransactionForm
            categories={categories}
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
              Delete <strong>{deleteTarget.description}</strong>? This cannot be undone.
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
