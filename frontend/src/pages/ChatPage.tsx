import { useMutation } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { askChat } from '../api/chat'
import { ApiError } from '../api/client'
import type { Transaction } from '../api/transactions'
import { formatMoney } from '../lib/format'

function formatAmount(amount: string): string {
  const value = Number(amount)
  return value < 0 ? `-$${formatMoney(amount.replace('-', ''))}` : `$${formatMoney(amount)}`
}

// Each turn is independent - the backend is stateless (see api/chat.ts), so
// this array is purely a client-side visual log. A page refresh loses it,
// same as the milestone's "no persisted history" decision.
interface Turn {
  question: string
  answer?: string
  sources?: Transaction[]
  error?: string
}

export function ChatPage() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')

  const chatMutation = useMutation({
    mutationFn: askChat,
    onSuccess: (data) => {
      setTurns((prev) =>
        prev.map((t, i) =>
          i === prev.length - 1 ? { ...t, answer: data.answer, sources: data.sources } : t,
        ),
      )
    },
    onError: (err) => {
      const message =
        err instanceof ApiError ? err.message : 'Something went wrong. Please try again.'
      setTurns((prev) => prev.map((t, i) => (i === prev.length - 1 ? { ...t, error: message } : t)))
    },
  })

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const q = question.trim()
    if (!q || chatMutation.isPending) return
    setTurns((prev) => [...prev, { question: q }])
    setQuestion('')
    chatMutation.mutate(q)
  }

  return (
    <main className="mx-auto flex h-screen max-w-2xl flex-col p-6">
      <div className="mb-4">
        <Link to="/" className="text-sm text-slate-500 underline">
          ← Dashboard
        </Link>
        <h1 className="text-2xl font-semibold text-slate-900">Chat</h1>
        <p className="mt-1 text-sm text-slate-500">
          Ask a question about your own transactions. Each question is answered on its own — this
          page doesn&apos;t remember earlier ones in the conversation.
        </p>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto pb-4">
        {turns.length === 0 && (
          <p className="text-sm text-slate-500">
            Try: &ldquo;How much did I spend on groceries last month?&rdquo;
          </p>
        )}

        {turns.map((turn, i) => (
          <div key={i} className="space-y-2">
            <div className="flex justify-end">
              <p className="max-w-[80%] rounded-lg bg-slate-900 px-4 py-2 text-sm text-white">
                {turn.question}
              </p>
            </div>

            {turn.answer && (
              <div className="flex justify-start">
                <div className="max-w-[80%] rounded-lg bg-slate-100 px-4 py-2 text-sm text-slate-900">
                  <p>{turn.answer}</p>
                  {turn.sources && turn.sources.length > 0 && (
                    <details className="mt-2 text-xs text-slate-500">
                      <summary className="cursor-pointer select-none">
                        Sources ({turn.sources.length})
                      </summary>
                      <ul className="mt-1 space-y-1">
                        {turn.sources.map((s) => (
                          <li key={s.id}>
                            {s.occurred_at.slice(0, 10)} — {s.description} (
                            <span className={s.amount.startsWith('-') ? 'text-red-600' : 'text-green-600'}>
                              {formatAmount(s.amount)}
                            </span>
                            )
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              </div>
            )}

            {turn.error && (
              <div className="flex justify-start">
                <p className="max-w-[80%] rounded-lg bg-red-50 px-4 py-2 text-sm text-red-600">
                  {turn.error}
                </p>
              </div>
            )}

            {chatMutation.isPending && i === turns.length - 1 && !turn.answer && !turn.error && (
              <div className="flex justify-start">
                <p className="max-w-[80%] rounded-lg bg-slate-100 px-4 py-2 text-sm text-slate-500">
                  Thinking…
                </p>
              </div>
            )}
          </div>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="flex gap-2 border-t border-slate-200 pt-4">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about your transactions…"
          maxLength={500}
          disabled={chatMutation.isPending}
          className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={chatMutation.isPending || question.trim().length === 0}
          className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </main>
  )
}
