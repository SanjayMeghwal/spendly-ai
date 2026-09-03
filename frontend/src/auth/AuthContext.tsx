import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useState, type ReactNode } from 'react'
import * as authApi from '../api/auth'
import { clearTokens, getAccessToken, getRefreshToken, setTokens } from '../api/token-storage'

interface AuthContextValue {
  user: authApi.User | undefined
  isLoading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, fullName?: string) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  // Whether we currently hold an access token - drives whether the `me`
  // query below is allowed to run. Not the same thing as "user is loaded":
  // a stale/expired token still flips this true until apiRequest's refresh
  // logic proves otherwise.
  const [hasToken, setHasToken] = useState(() => getAccessToken() !== null)

  const { data: user, isLoading } = useQuery({
    queryKey: ['me'],
    queryFn: authApi.getMe,
    enabled: hasToken,
    retry: false,
  })

  async function login(email: string, password: string): Promise<void> {
    const tokens = await authApi.login({ email, password })
    setTokens(tokens.access_token, tokens.refresh_token)
    // Flips `enabled` on the query above, which triggers its own fetch -
    // no need to call getMe or invalidate manually.
    setHasToken(true)
  }

  async function register(email: string, password: string, fullName?: string): Promise<void> {
    await authApi.register({ email, password, full_name: fullName })
    // /auth/register returns the created account, not a token pair - log in
    // with the same credentials immediately after so registering also signs
    // the user in, rather than bouncing them to a second form.
    await login(email, password)
  }

  async function logout(): Promise<void> {
    const refreshToken = getRefreshToken()
    clearTokens()
    setHasToken(false)
    // setQueryData(key, undefined) is a no-op in TanStack Query - it leaves
    // the previous user cached. removeQueries is what actually clears it.
    queryClient.removeQueries({ queryKey: ['me'] })
    if (refreshToken) {
      // Best-effort: the user is logged out client-side regardless of
      // whether this network call succeeds.
      await authApi.logout(refreshToken).catch(() => undefined)
    }
  }

  return (
    <AuthContext.Provider value={{ user, isLoading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
