import { useMutation } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { askAdvisor, type AdvisorResponse } from '../api/advisor'
import { ApiError } from '../api/client'

export function AdvisorPage() {
  const [question, setQuestion] = useState('')
  const [response, setResponse] = useState<AdvisorResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const advisorMutation = useMutation({
    mutationFn: askAdvisor,
    onSuccess: (data) => {
      setResponse(data)
      setError(null)
    },
    onError: (err) => {
      setResponse(null)
      setError(err instanceof ApiError ? err.message : 'Something went wrong. Please try again.')
    },
  })

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (advisorMutation.isPending) return
    advisorMutation.mutate(question)
  }

  return (
    <main className="mx-auto max-w-2xl p-6">
      <div className="mb-6">
        <Link to="/" className="text-sm text-slate-500 underline">
          ← Dashboard
        </Link>
        <h1 className="text-2xl font-semibold text-slate-900">Budget advisor</h1>
        <p className="mt-1 text-sm text-slate-500">
          Get a recommendation based on your budgets, goals, and recent spending.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
        <label htmlFor="advisor-question" className="block text-sm text-slate-700">
          What would you like to improve?
        </label>
        <textarea
          id="advisor-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="How can I reduce my spending next month?"
          maxLength={500}
          rows={4}
          disabled={advisorMutation.isPending}
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={advisorMutation.isPending}
          className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {advisorMutation.isPending ? 'Reviewing your finances…' : 'Get advice'}
        </button>
      </form>

      {error && <p className="mt-6 text-sm text-red-600">{error}</p>}

      {response && (
        <section className="mt-8 space-y-6" aria-live="polite">
          <div className="rounded-lg border border-slate-200 bg-white p-5">
            <h2 className="mb-2 text-sm font-medium text-slate-500">Recommendation</h2>
            <p className="text-slate-900">{response.advice}</p>
          </div>

          {response.recommended_actions.length > 0 && (
            <div>
              <h2 className="mb-2 text-sm font-medium text-slate-700">Recommended actions</h2>
              <ul className="list-disc space-y-2 pl-5 text-sm text-slate-700">
                {response.recommended_actions.map((action, index) => (
                  <li key={`${action}-${index}`}>{action}</li>
                ))}
              </ul>
            </div>
          )}

          {response.warnings.length > 0 && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
              <h2 className="mb-2 text-sm font-medium text-amber-900">Warnings</h2>
              <ul className="list-disc space-y-2 pl-5 text-sm text-amber-900">
                {response.warnings.map((warning, index) => (
                  <li key={`${warning}-${index}`}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </main>
  )
}
