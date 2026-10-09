import { api } from './api-client'
import type { ApiError } from './api-client'

export interface SessionUser {
  id: number
  username: string
  full_name: string
  email?: string | null
  is_active?: boolean
  role_id: number
  role_name: string
  capabilities: string[]
  has_permission?: 'owner'
}

interface LoginResponse {
  access_token: string
  refresh_token: string
  expires_in: number
  refresh_expires_in: number
  user: SessionUser
}

export async function login(username: string, password: string): Promise<SessionUser> {
  const res = await api.post<LoginResponse>('/auth/login', { username, password }, {
    idempotencyKey: true,
  })
  // Store tokens in localStorage for client components.
  // (Backend also sets httpOnly cookies for server components.)
  localStorage.setItem('access_token', res.access_token)
  localStorage.setItem('refresh_token', res.refresh_token)
  return res.user
}

export async function logout(): Promise<void> {
  // Best-effort server revocation; local state clears regardless.
  try {
    await api.post('/auth/logout', {})
  } catch {
    // Session may already be expired/revoked — still sign out locally.
  } finally {
    // Clear localStorage tokens (client components still use these)
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    // Redirect to login (cookies cleared server-side by /auth/logout)
    window.location.href = '/login'
  }
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
    return await api.get<SessionUser>('/auth/me')
  } catch (err) {
    if (err instanceof Error && (err as ApiError).status === 401) {
      logout()
      return null
    }
    if (err instanceof Error && (err.message === 'Unauthorized' || (err as ApiError).code === 'unauthorized')) {
      logout()
      return null
    }
    throw err
  }
}