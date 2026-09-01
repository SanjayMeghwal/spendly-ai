import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  createCategory,
  deleteCategory,
  listCategories,
  renameCategory,
  type Category,
} from '../api/categories'
import { ApiError } from '../api/client'

export function CategoriesPage() {
  const queryClient = useQueryClient()
  const [newName, setNewName] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')
  const [editError, setEditError] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<Category | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const categoriesQuery = useQuery({ queryKey: ['categories'], queryFn: listCategories })

  const createMutation = useMutation({
    mutationFn: createCategory,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['categories'] })
      setNewName('')
      setCreateError(null)
    },
    onError: (err) => setCreateError(err instanceof ApiError ? err.message : 'Something went wrong.'),
  })

  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => renameCategory(id, name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['categories'] })
      setEditingId(null)
      setEditError(null)
    },
    onError: (err) => setEditError(err instanceof ApiError ? err.message : 'Something went wrong.'),
  })

  const deleteMutation = useMutation({
    mutationFn: deleteCategory,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['categories'] })
      setDeleteTarget(null)
      setDeleteError(null)
    },
    onError: (err) => {
      setDeleteError(err instanceof ApiError ? err.message : 'Something went wrong.')
    },
  })

  function handleCreate(event: FormEvent) {
    event.preventDefault()
    createMutation.mutate(newName)
  }

  function startEditing(category: Category) {
    setEditingId(category.id)
    setEditingName(category.name)
    setEditError(null)
  }

  function handleRename(event: FormEvent) {
    event.preventDefault()
    if (editingId) renameMutation.mutate({ id: editingId, name: editingName })
  }

  const categories = categoriesQuery.data ?? []

  return (
    <main className="mx-auto max-w-lg p-6">
      <Link to="/" className="text-sm text-slate-500 underline">
        ← Dashboard
      </Link>
      <h1 className="mb-6 text-2xl font-semibold text-slate-900">Categories</h1>

      <form onSubmit={handleCreate} className="mb-6 flex gap-2">
        <input
          type="text"
          required
          maxLength={100}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="New category name"
          className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={createMutation.isPending}
          className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          Add
        </button>
      </form>
      {createError && <p className="mb-4 text-sm text-red-600">{createError}</p>}

      {categoriesQuery.isLoading && <p className="text-slate-500">Loading…</p>}
      {categoriesQuery.isError && (
        <p className="text-red-600">Could not load categories. Try reloading the page.</p>
      )}
      {categoriesQuery.isSuccess && categories.length === 0 && (
        <p className="text-slate-500">No categories yet.</p>
      )}

      <ul className="space-y-2">
        {categories.map((c) => (
          <li
            key={c.id}
            className="flex items-center justify-between rounded border border-slate-200 px-3 py-2"
          >
            {editingId === c.id ? (
              <form onSubmit={handleRename} className="flex flex-1 items-center gap-2">
                <input
                  type="text"
                  required
                  maxLength={100}
                  value={editingName}
                  onChange={(e) => setEditingName(e.target.value)}
                  autoFocus
                  className="flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
                />
                <button
                  type="submit"
                  disabled={renameMutation.isPending}
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
                <span className="text-slate-900">{c.name}</span>
                <div className="flex gap-3 text-sm">
                  <button
                    type="button"
                    onClick={() => startEditing(c)}
                    className="text-slate-500 underline"
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setDeleteError(null)
                      setDeleteTarget(c)
                    }}
                    className="text-red-600 underline"
                  >
                    Delete
                  </button>
                </div>
              </>
            )}
          </li>
        ))}
      </ul>
      {editError && <p className="mt-2 text-sm text-red-600">{editError}</p>}

      {deleteTarget && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-6">
            <p className="text-slate-900">
              Delete <strong>{deleteTarget.name}</strong>?
            </p>
            {deleteError && <p className="text-sm text-red-600">{deleteError}</p>}
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
