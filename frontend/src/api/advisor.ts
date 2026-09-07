import { apiRequest } from './client'

// Mirrors backend/app/schemas/agents.py::AdvisorResponse.
export interface AdvisorResponse {
  advice: string
  recommended_actions: string[]
  warnings: string[]
}

export function askAdvisor(question?: string): Promise<AdvisorResponse> {
  return apiRequest<AdvisorResponse>('/agents/advice', {
    method: 'POST',
    body: { question: question?.trim() || null },
  })
}
