import { apiRequest } from './client'

// Mirrors backend/app/schemas/goal.py::GoalRead. target_amount, progress,
// and remaining stay signed decimal strings, same reasoning as Budget's
// fields in api/budgets.ts. Unlike Budget, `remaining` here is shown
// UNCAPPED and can go negative - overshooting a savings goal is good news,
// not an overspend, and the backend deliberately doesn't hide that.
export interface Goal {
  id: string
  category_id: string
  category_name: string
  target_amount: string
  target_date: string | null
  progress: string
  remaining: string
  created_at: string
  updated_at: string
}

export interface GoalCreateInput {
  category_id: string
  target_amount: string
  target_date?: string | null
}

export type GoalUpdateInput = Partial<GoalCreateInput>

export function listGoals(): Promise<Goal[]> {
  return apiRequest<Goal[]>('/goals')
}

export function createGoal(input: GoalCreateInput): Promise<Goal> {
  return apiRequest<Goal>('/goals', { method: 'POST', body: input })
}

export function updateGoal(id: string, input: GoalUpdateInput): Promise<Goal> {
  return apiRequest<Goal>(`/goals/${id}`, { method: 'PATCH', body: input })
}

export function deleteGoal(id: string): Promise<void> {
  return apiRequest<void>(`/goals/${id}`, { method: 'DELETE' })
}
