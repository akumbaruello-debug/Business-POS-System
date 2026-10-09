import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import { serverApi } from '@/lib/server-api-client'

import type { SessionUser } from '@/lib/auth'

/**
 * Server-side auth check. Reads the httpOnly access_token cookie
 * and validates it via the /auth/me endpoint.
 *
 * Returns the current user or null if not authenticated.
 */
export async function getServerSideUser(): Promise<SessionUser | null> {
  const cookieStore = await cookies()
  const token = cookieStore.get('access_token')?.value

  if (!token) {
    return null
  }

  try {
    const user = await serverApi.get<SessionUser>('/auth/me')
    return user
  } catch {
    return null
  }
}

/**
 * Server-side auth check that redirects to /login if not authenticated.
 * Use this in server components that require authentication.
 */
export async function requireUser(): Promise<SessionUser> {
  const user = await getServerSideUser()
  if (!user) {
    redirect('/login')
  }
  return user
}