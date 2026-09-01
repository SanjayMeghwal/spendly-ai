import { apiRequest } from './client'

// Mirrors backend/app/schemas/category.py::CategoryRead.
export interface Category {
  id: string
  name: string
  created_at: string
  updated_at: string
}

// Unpaginated - the backend doesn't paginate this list either, since a
// user's category count is small by nature (see app/api/routes/categories.py).
export function listCategories(): Promise<Category[]> {
  return apiRequest<Category[]>('/categories')
}

export function createCategory(name: string): Promise<Category> {
  return apiRequest<Category>('/categories', { method: 'POST', body: { name } })
}

export function renameCategory(id: string, name: string): Promise<Category> {
  return apiRequest<Category>(`/categories/${id}`, { method: 'PATCH', body: { name } })
}

// Does NOT support ?reassign_to= yet - deleting a category still in use by
// transactions, a budget, or a goal returns a 409 explaining why, and the
// caller must clear that first. Reassignment is a deliberately deferred
// follow-up, not an oversight; see app/api/routes/categories.py's
// _REASSIGN_TO_QUERY for what the backend already supports.
export function deleteCategory(id: string): Promise<void> {
  return apiRequest<void>(`/categories/${id}`, { method: 'DELETE' })
}
