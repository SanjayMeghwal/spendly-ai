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
