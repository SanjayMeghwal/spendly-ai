import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { getMonthlySummary, getSpendByCategory } from '../api/reports'
import { useAuth } from '../auth/AuthContext'
import { MonthlyTrendChart } from '../components/MonthlyTrendChart'
import { SpendByCategoryChart } from '../components/SpendByCategoryChart'
import { StatTile } from '../components/StatTile'
import { formatMoney } from '../lib/format'

export function DashboardPage() {
  const { user, logout } = useAuth()

  const summaryQuery = useQuery({
    queryKey: ['reports', 'monthly-summary'],
    queryFn: () => getMonthlySummary(6),
  })
  const spendQuery = useQuery({
    queryKey: ['reports', 'spend-by-category'],
    queryFn: () => getSpendByCategory(),
  })

  const summary = summaryQuery.data ?? []
  // Oldest-first from the API - the last entry is the current UTC month.
  const currentMonth = summary.at(-1)
  // spend-by-category returns NET spend per category, which is negative
  // for a category that's net income that month (e.g. a "Salary" category
  // with more money in than out) - the backend aggregates every category
  // touched, not just ones that were actually spent from. A chart titled
  // "spend by category" only makes sense for the positive subset; showing
  // a negative bar for an income category here would be wrong, not just
  // ugly. Filtered client-side, not a backend change - the endpoint is a
  // generic net-per-category aggregation, and other consumers may want
  // the income rows too.
  const spend = (spendQuery.data ?? []).filter((row) => Number(row.spent) > 0)

  return (
    <main className="mx-auto max-w-3xl p-6">
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-slate-900">Welcome, {user?.email}</h1>
        <button
          type="button"
          onClick={() => void logout()}
          className="rounded border border-slate-300 px-4 py-2 text-sm text-slate-700"
        >
          Log out
        </button>
      </div>

      <nav className="mb-8 flex flex-wrap gap-4 text-sm">
        <Link to="/transactions" className="text-slate-900 underline">
          Transactions
        </Link>
        <Link to="/categories" className="text-slate-900 underline">
          Categories
        </Link>
        <Link to="/budgets" className="text-slate-900 underline">
          Budgets
        </Link>
        <Link to="/goals" className="text-slate-900 underline">
          Goals
        </Link>
        <Link to="/chat" className="text-slate-900 underline">
          Chat
        </Link>
      </nav>

      {currentMonth && (
        <div className="mb-8 grid grid-cols-3 gap-4">
          <StatTile label="Income this month" value={`$${formatMoney(currentMonth.income)}`} />
          <StatTile label="Expenses this month" value={`$${formatMoney(currentMonth.expenses)}`} />
          <StatTile
            label="Net this month"
            value={`${currentMonth.net.startsWith('-') ? '-' : ''}$${formatMoney(
              currentMonth.net.replace('-', ''),
            )}`}
            valueClassName={currentMonth.net.startsWith('-') ? 'text-red-600' : 'text-green-600'}
          />
        </div>
      )}

      <section className="mb-8 rounded-lg border border-slate-200 p-4">
        <h2 className="mb-4 text-sm font-medium text-slate-700">Income vs. expenses, last 6 months</h2>
        {summaryQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {summaryQuery.isError && (
          <p className="text-sm text-red-600">Could not load this chart. Try reloading the page.</p>
        )}
        {summaryQuery.isSuccess && summary.length === 0 && (
          <p className="text-sm text-slate-500">Add some transactions to see this chart.</p>
        )}
        {summary.length > 0 && <MonthlyTrendChart data={summary} />}
      </section>

      <section className="rounded-lg border border-slate-200 p-4">
        <h2 className="mb-4 text-sm font-medium text-slate-700">Spend by category, this month</h2>
        {spendQuery.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        {spendQuery.isError && (
          <p className="text-sm text-red-600">Could not load this chart. Try reloading the page.</p>
        )}
        {spendQuery.isSuccess && spend.length === 0 && (
          <p className="text-sm text-slate-500">No spending recorded this month yet.</p>
        )}
        {spend.length > 0 && <SpendByCategoryChart data={spend} />}
      </section>
    </main>
  )
}
