import { clearTokens, getAccessToken, getRefreshToken, setTokens } from './token-storage'

const API_URL = import.meta.env.VITE_API_URL
const API_V1_PREFIX = '/api/v1'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

interface RequestOptions {
  method?: string
  body?: unknown
  /** Skip attaching the access token - only /auth/register and /auth/login need this. */
  skipAuth?: boolean
}

// Concurrent requests that all hit an expired access token must not each
// trigger their own refresh - the backend's reuse detection would treat the
// second refresh call as a stolen token and revoke the whole session. This
// promise is shared so only the first 401 actually calls /auth/refresh;
// everyone else awaits the same result.
let refreshPromise: Promise<boolean> | null = null

async function refreshAccessToken(): Promise<boolean> {
  refreshPromise ??= (async () => {
    const refreshToken = getRefreshToken()
    if (!refreshToken) return false

    const res = await fetch(`${API_URL}${API_V1_PREFIX}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })

    if (!res.ok) {
      clearTokens()
      return false
    }

    const data = (await res.json()) as { access_token: string; refresh_token: string }
    setTokens(data.access_token, data.refresh_token)
    return true
  })()

  try {
    return await refreshPromise
  } finally {
    refreshPromise = null
  }
}

export async function apiRequest<T>(
  path: string,
  { method = 'GET', body, skipAuth = false }: RequestOptions = {},
  isRetry = false,
): Promise<T> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  if (!skipAuth) {
    const token = getAccessToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
  }

  const res = await fetch(`${API_URL}${API_V1_PREFIX}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (res.status === 401 && !skipAuth && !isRetry) {
    const refreshed = await refreshAccessToken()
    if (refreshed) return apiRequest<T>(path, { method, body, skipAuth }, true)
  }

  if (!res.ok) {
    const problem = (await res.json().catch(() => null)) as { detail?: string } | null
    throw new ApiError(res.status, problem?.detail ?? `Request failed with ${res.status}`)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}
