import { apiRequest } from './client'

// Mirrors backend/app/schemas/user.py::UserRead and auth.py::TokenResponse.
// Kept as plain interfaces (not derived from a shared codegen step) since
// there's no OpenAPI-to-TS generation set up yet - a deliberate scope call
// for this milestone, revisit if the two shapes start drifting.

export interface User {
  id: string
  email: string
  full_name: string | null
  is_active: boolean
  created_at: string
}

interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export function register(input: {
  email: string
  password: string
  full_name?: string
}): Promise<User> {
  return apiRequest<User>('/auth/register', { method: 'POST', body: input, skipAuth: true })
}

export function login(input: { email: string; password: string }): Promise<TokenResponse> {
  return apiRequest<TokenResponse>('/auth/login', { method: 'POST', body: input, skipAuth: true })
}

export function logout(refreshToken: string): Promise<void> {
  return apiRequest<void>('/auth/logout', {
    method: 'POST',
    body: { refresh_token: refreshToken },
    skipAuth: true,
  })
}

export function getMe(): Promise<User> {
  return apiRequest<User>('/auth/me')
}
