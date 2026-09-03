import { apiRequest } from './client'
import type { Transaction } from './transactions'

// Mirrors backend/app/schemas/chat.py::ChatResponse.
export interface ChatResponse {
  answer: string
  sources: Transaction[]
}

// Stateless - each call is independent, there is no conversation id to pass.
export function askChat(question: string): Promise<ChatResponse> {
  return apiRequest<ChatResponse>('/chat', { method: 'POST', body: { question } })
}
