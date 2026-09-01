import { apiRequest } from './client'

// Mirrors backend/app/schemas/transaction.py::TransactionRead.
//
// `amount` is a string, not a number - the backend serializes its
// NUMERIC(12,2)/Decimal column as a JSON string ("-12.50") to preserve exact
// decimal precision. Parsing it into a float here would reintroduce the
// binary-rounding problem CLAUDE.md's money rule exists to avoid, so it
// stays a string end-to-end: displayed as-is, and round-tripped unparsed on
// update.
export interface Transaction {
  id: string
  amount: string
  description: string
  category_id: string | null
  category_name: string | null
  notes: string | null
  occurred_at: string
  created_at: string
  updated_at: string
}

export interface TransactionCreateInput {
  amount: string
  description: string
  category_id?: string | null
  occurred_at: string
  notes?: string | null
}

export type TransactionUpdateInput = Partial<TransactionCreateInput>

export interface ListTransactionsParams {
  limit?: number
  offset?: number
}

export function listTransactions(params: ListTransactionsParams = {}): Promise<Transaction[]> {
  const query = new URLSearchParams()
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  if (params.offset !== undefined) query.set('offset', String(params.offset))
  const qs = query.toString()
  return apiRequest<Transaction[]>(`/transactions${qs ? `?${qs}` : ''}`)
}

export function createTransaction(input: TransactionCreateInput): Promise<Transaction> {
  return apiRequest<Transaction>('/transactions', { method: 'POST', body: input })
}

export function updateTransaction(
  id: string,
  input: TransactionUpdateInput,
): Promise<Transaction> {
  return apiRequest<Transaction>(`/transactions/${id}`, { method: 'PATCH', body: input })
}

export function deleteTransaction(id: string): Promise<void> {
  return apiRequest<void>(`/transactions/${id}`, { method: 'DELETE' })
}
