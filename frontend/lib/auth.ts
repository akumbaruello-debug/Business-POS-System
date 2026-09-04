import { api } from './api-client'

export interface SessionUser {
  id: number
  username: string
  full_name: string
  email?: string | null
  is_active: boolean
  role_id: number
  role_name: string
  capabilities: string[]
}

interface LoginResponse {
  access_token: string
  refresh_token: string
  expires_in: number
  refresh_expires_in: number
  user: SessionUser
}

export async function login(username: string, password: string): Promise<SessionUser> {
  // Backend requires Idempotency-Key header; generate a UUID.
  const idempotencyKey = crypto.randomUUID()
  const res = await api.post<LoginResponse>('/auth/login', { username, password }, {
    headers: {
      'Idempotency-Key': idempotencyKey,
    },
  })
  localStorage.setItem('access_token', res.access_token)
  localStorage.setItem('refresh_token', res.refresh_token)
  return res.user
}

export function logout(): void {
  // Optionally call /auth/logout, but we can just clear local state.
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  // Redirect will be handled by the app.
}

export function getAccessToken(): string | null {
  return localStorage.getItem('access_token')
}

export function isAuthenticated(): boolean {
  return !!getAccessToken()
}

export async function getCurrentUser(): Promise<SessionUser | null> {
  if (!isAuthenticated()) return null
  try {
    const user = await api.get<SessionUser>('/auth/me')
    return user
  } catch {
    return null
  }
}