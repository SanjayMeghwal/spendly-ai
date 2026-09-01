import { useEffect, useState } from 'react'
import { Route, Routes } from 'react-router-dom'

type HealthStatus = 'loading' | 'ok' | 'error'

function HealthCheckPage() {
  const [status, setStatus] = useState<HealthStatus>('loading')

  useEffect(() => {
    fetch(`${import.meta.env.VITE_API_URL}/health`)
      .then((res) => (res.ok ? setStatus('ok') : setStatus('error')))
      .catch(() => setStatus('error'))
  }, [])

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4">
      <h1 className="text-2xl font-semibold text-slate-900">Spendly AI</h1>
      <p className="text-slate-600">
        Backend status:{' '}
        <span
          className={
            status === 'ok'
              ? 'text-green-600'
              : status === 'error'
                ? 'text-red-600'
                : 'text-slate-400'
          }
        >
          {status}
        </span>
      </p>
    </main>
  )
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<HealthCheckPage />} />
    </Routes>
  )
}

export default App
