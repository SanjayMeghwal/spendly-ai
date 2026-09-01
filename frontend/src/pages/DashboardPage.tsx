import { useAuth } from '../auth/AuthContext'

export function DashboardPage() {
  const { user, logout } = useAuth()

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4">
      <h1 className="text-2xl font-semibold text-slate-900">Welcome, {user?.email}</h1>
      <button
        type="button"
        onClick={() => void logout()}
        className="rounded border border-slate-300 px-4 py-2 text-sm text-slate-700"
      >
        Log out
      </button>
    </main>
  )
}
