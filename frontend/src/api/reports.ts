import { apiRequest } from './client'

// Mirrors backend/app/schemas/report.py. All amounts stay signed decimal
// strings, same reasoning as every other money field in this client.
export interface CategorySpend {
  category_id: string | null
  category_name: string
  spent: string
}

export interface MonthlySummary {
  month: string
  income: string
  expenses: string
  net: string
}

export function getSpendByCategory(month?: string): Promise<CategorySpend[]> {
  const qs = month ? `?month=${month}` : ''
  return apiRequest<CategorySpend[]>(`/reports/spend-by-category${qs}`)
}

export function getMonthlySummary(months = 6): Promise<MonthlySummary[]> {
  return apiRequest<MonthlySummary[]>(`/reports/monthly-summary?months=${months}`)
}
