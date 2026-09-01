import { apiRequest } from './client'

// Mirrors backend/app/schemas/budget.py::BudgetRead. limit_amount, spent,
// and remaining are all signed decimal strings, same reasoning as
// Transaction.amount in api/transactions.ts - never parsed to a float.
export interface Budget {
  id: string
  category_id: string
  category_name: string
  limit_amount: string
  spent: string
  remaining: string
  created_at: string
  updated_at: string
}

export interface BudgetCreateInput {
  category_id: string
  limit_amount: string
}

export type BudgetUpdateInput = Partial<BudgetCreateInput>

// No ?month= param yet - always the current UTC month, which is the
// backend's own default. A month picker is a deferred follow-up, not an
// oversight; see app/api/routes/budgets.py's _MONTH_QUERY for what the
// backend already supports.
export function listBudgets(): Promise<Budget[]> {
  return apiRequest<Budget[]>('/budgets')
}

export function createBudget(input: BudgetCreateInput): Promise<Budget> {
  return apiRequest<Budget>('/budgets', { method: 'POST', body: input })
}

export function updateBudget(id: string, input: BudgetUpdateInput): Promise<Budget> {
  return apiRequest<Budget>(`/budgets/${id}`, { method: 'PATCH', body: input })
}

export function deleteBudget(id: string): Promise<void> {
  return apiRequest<void>(`/budgets/${id}`, { method: 'DELETE' })
}
